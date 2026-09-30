from __future__ import annotations

import copy
import logging
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from threading import Condition, Event, RLock, Thread, current_thread
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import cv2
import numpy as np

if TYPE_CHECKING:
    from app.ai.pipe import AICore
    from app.cfg import Settings


logger = logging.getLogger("fallai.camera")

SKELETON_PAIRS = (
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12),
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6),
)

AlertHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def parse_camera_source(value: str) -> int | str:
    raw = value.strip()
    if raw.isdecimal():
        return int(raw)
    return raw


def safe_camera_source(value: str) -> str:
    """Return a display value without leaking an RTSP username/password."""
    raw = value.strip()
    if "://" not in raw:
        return f"USB {raw}" if raw.isdecimal() else raw
    parsed = urlsplit(raw)
    host = parsed.hostname or "camera-ip"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


class CameraService:
    """Capture once on the server, run AI once, then share the latest output."""

    def __init__(self, cfg: Settings, core: AICore, alert_handler: AlertHandler) -> None:
        self.cfg = cfg
        self.core = core
        self.alert_handler = alert_handler
        self._condition = Condition(RLock())
        self._stop = Event()
        self._generation = 0
        self._capture_thread: Thread | None = None
        self._process_thread: Thread | None = None
        self._running = False
        self._connected = False
        self._capture_error: str | None = None
        self._ai_error: str | None = None
        self._owner: dict[str, Any] | None = None
        self._started_at: str | None = None
        self._latest_raw: np.ndarray | None = None
        self._raw_sequence = 0
        self._latest_jpeg: bytes | None = None
        self._latest_result: dict[str, Any] | None = None
        self._result_sequence = 0
        self._viewers = 0

    def _status_unlocked(self) -> dict[str, Any]:
        owner = None
        if self._owner:
            owner = {key: self._owner.get(key) for key in ("id", "name", "email")}
        return {
            "running": self._running,
            "connected": self._connected,
            "source": safe_camera_source(self.cfg.camera_source),
            "name": self.cfg.camera_name,
            "error": self._ai_error or self._capture_error,
            "frame_id": self._result_sequence,
            "has_frame": self._latest_jpeg is not None,
            "viewers": self._viewers,
            "started_at": self._started_at,
            "owner": owner,
        }

    def status(self) -> dict[str, Any]:
        with self._condition:
            return self._status_unlocked()

    def start(self, user: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        with self._condition:
            if self._running:
                return False, self._status_unlocked()
            self._stop = Event()
            stop_event = self._stop
            self._generation += 1
            generation = self._generation
            self._running = True
            self._connected = False
            self._capture_error = None
            self._ai_error = None
            self._owner = {key: user.get(key) for key in ("id", "name", "email")}
            self._started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            self._latest_raw = None
            self._latest_jpeg = None
            self._latest_result = None
            self._raw_sequence = 0
            self._result_sequence = 0
            self._capture_thread = Thread(
                target=self._capture_loop,
                args=(stop_event, generation),
                name="fallai-camera-capture",
                daemon=True,
            )
            self._process_thread = Thread(
                target=self._process_loop,
                args=(stop_event, generation),
                name="fallai-camera-ai",
                daemon=True,
            )
            capture_thread = self._capture_thread
            process_thread = self._process_thread
            status = self._status_unlocked()
        capture_thread.start()
        process_thread.start()
        logger.info("Camera service started: %s", safe_camera_source(self.cfg.camera_source))
        return True, status

    def stop(self) -> tuple[bool, dict[str, Any]]:
        with self._condition:
            was_running = self._running
            self._stop.set()
            self._condition.notify_all()
            threads = (self._capture_thread, self._process_thread)
        for thread in threads:
            if thread and thread.is_alive() and thread is not current_thread():
                thread.join(timeout=5.0)
        with self._condition:
            self._running = False
            self._connected = False
            self._latest_raw = None
            self._latest_jpeg = None
            self._capture_thread = None
            self._process_thread = None
            self._condition.notify_all()
            status = self._status_unlocked()
        if was_running:
            logger.info("Camera service stopped")
        return was_running, status

    def shutdown(self) -> None:
        self.stop()

    def wait_for_result(
        self, last_sequence: int, timeout: float = 2.0
    ) -> tuple[int, dict[str, Any] | None]:
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._latest_result is not None
                    and self._result_sequence != last_sequence
                ),
                timeout=timeout,
            )
            if self._result_sequence == last_sequence or self._latest_result is None:
                return last_sequence, None
            return self._result_sequence, copy.deepcopy(self._latest_result)

    def mjpeg_frames(self) -> Iterator[bytes]:
        last_sequence = -1
        with self._condition:
            self._viewers += 1
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: (
                            self._latest_jpeg is not None
                            and self._result_sequence != last_sequence
                        ) or not self._running,
                        timeout=3.0,
                    )
                    running = self._running
                    sequence = self._result_sequence
                    jpeg = self._latest_jpeg
                if jpeg is not None and sequence != last_sequence:
                    last_sequence = sequence
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                        + jpeg
                        + b"\r\n"
                    )
                elif not running:
                    break
        finally:
            with self._condition:
                self._viewers = max(0, self._viewers - 1)

    def _open_capture(self) -> Any:
        source = parse_camera_source(self.cfg.camera_source)
        backend = self.cfg.camera_backend
        capture = None

        if isinstance(source, int) and os.name == "nt" and backend in {"auto", "dshow"}:
            capture = cv2.VideoCapture(source, cv2.CAP_DSHOW)
            if backend == "auto" and not capture.isOpened():
                capture.release()
                capture = cv2.VideoCapture(source)
        elif isinstance(source, str) and backend in {"auto", "ffmpeg"}:
            capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            if backend == "auto" and not capture.isOpened():
                capture.release()
                capture = cv2.VideoCapture(source)
        else:
            capture = cv2.VideoCapture(source)

        if isinstance(source, int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.camera_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.camera_height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def _capture_loop(self, stop_event: Event, generation: int) -> None:
        retry_delay = max(0.5, float(self.cfg.camera_reconnect_seconds))
        try:
            while not stop_event.is_set():
                capture = None
                try:
                    capture = self._open_capture()
                    if not capture.isOpened():
                        raise RuntimeError("Khong mo duoc nguon camera")
                    with self._condition:
                        if generation != self._generation:
                            return
                        self._connected = True
                        self._capture_error = None
                        self._condition.notify_all()

                    failures = 0
                    while not stop_event.is_set():
                        ok, frame = capture.read()
                        if not ok or frame is None:
                            failures += 1
                            if failures < 3:
                                stop_event.wait(0.1)
                                continue
                            raise RuntimeError("Mat ket noi voi camera")
                        failures = 0
                        with self._condition:
                            if generation != self._generation:
                                return
                            self._latest_raw = frame
                            self._raw_sequence += 1
                            self._connected = True
                            self._capture_error = None
                            self._condition.notify_all()
                except Exception as exc:
                    message = str(exc) or exc.__class__.__name__
                    with self._condition:
                        if generation != self._generation:
                            return
                        self._connected = False
                        self._capture_error = message
                        self._condition.notify_all()
                    if not stop_event.is_set():
                        logger.warning("Camera unavailable (%s); retrying", message)
                        stop_event.wait(retry_delay)
                finally:
                    if capture is not None:
                        capture.release()
        finally:
            with self._condition:
                if generation == self._generation:
                    self._connected = False
                    self._condition.notify_all()

    def _process_loop(self, stop_event: Event, generation: int) -> None:
        session = self.core.new_session()
        last_raw_sequence = -1
        interval = 1.0 / max(1, int(self.cfg.sample_fps))
        try:
            while not stop_event.is_set():
                with self._condition:
                    if generation != self._generation:
                        break
                    self._condition.wait_for(
                        lambda: self._raw_sequence != last_raw_sequence or stop_event.is_set(),
                        timeout=1.0,
                    )
                    if stop_event.is_set():
                        break
                    if self._latest_raw is None or self._raw_sequence == last_raw_sequence:
                        continue
                    frame = self._latest_raw.copy()
                    last_raw_sequence = self._raw_sequence
                    owner = dict(self._owner or {})

                cycle_started = perf_counter()
                try:
                    result = session.process_frame(frame)
                    snapshot = result.pop("snapshot", frame)
                    if result.get("alert") and owner:
                        result["snapshot"] = snapshot
                        try:
                            result["alert_info"] = self.alert_handler(owner, result)
                        except Exception as exc:
                            logger.exception("Could not persist camera alert")
                            result["alert_info"] = {"error": str(exc)}
                        finally:
                            result.pop("snapshot", None)
                    result["timing"] = dict(result.get("timing") or {})
                    with self._condition:
                        if generation != self._generation:
                            break
                        next_sequence = self._result_sequence + 1
                    result["frame_id"] = next_sequence
                    annotated = self._annotate(frame, result)
                    quality = max(35, min(95, round(self.cfg.jpeg_quality * 100)))
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, quality]
                    )
                    if not encoded_ok:
                        raise RuntimeError("Khong ma hoa duoc khung hinh JPEG")
                    result["timing"]["frame_ms"] = round(
                        (perf_counter() - cycle_started) * 1000, 2
                    )
                    with self._condition:
                        if generation != self._generation:
                            break
                        self._result_sequence = next_sequence
                        self._latest_result = result
                        self._latest_jpeg = encoded.tobytes()
                        self._ai_error = None
                        self._condition.notify_all()
                except Exception as exc:
                    logger.exception("Camera AI frame failed")
                    with self._condition:
                        if generation != self._generation:
                            break
                        self._ai_error = f"Loi xu ly AI: {exc}"
                        self._condition.notify_all()

                elapsed = perf_counter() - cycle_started
                stop_event.wait(max(0.0, interval - elapsed))
        finally:
            with self._condition:
                if generation == self._generation:
                    self._running = False
                    self._connected = False
                    self._condition.notify_all()

    def _annotate(self, frame: np.ndarray, result: dict[str, Any]) -> np.ndarray:
        canvas = frame.copy()
        keypoints = result.get("keypoints") or []
        bbox = result.get("bbox")
        alert = bool(result.get("alert"))
        event = str(result.get("event") or "unknown")
        color = (70, 205, 120)
        if event in {"falling", "postfall"}:
            color = (40, 170, 255)
        if alert:
            color = (65, 65, 245)

        if bbox and len(bbox) == 4:
            x1, y1, x2, y2 = (int(value) for value in bbox)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

        for first, second in SKELETON_PAIRS:
            if first >= len(keypoints) or second >= len(keypoints):
                continue
            point_a, point_b = keypoints[first], keypoints[second]
            if point_a[2] < self.cfg.kp_conf or point_b[2] < self.cfg.kp_conf:
                continue
            cv2.line(
                canvas,
                (int(point_a[0]), int(point_a[1])),
                (int(point_b[0]), int(point_b[1])),
                color,
                2,
                cv2.LINE_AA,
            )
        for point in keypoints:
            if point[2] >= self.cfg.kp_conf:
                cv2.circle(
                    canvas, (int(point[0]), int(point[1])), 3, (255, 210, 80), -1, cv2.LINE_AA
                )

        score = round(float(result.get("event_score") or 0.0) * 100)
        posture = str(result.get("posture") or "unknown").upper()
        headline = "FALL ALERT" if alert else f"{event.upper()}  {score}%"
        cv2.rectangle(canvas, (12, 12), (310, 70), (5, 13, 22), -1)
        cv2.putText(canvas, headline, (24, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.63, color, 2, cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"POSTURE: {posture}   KP: {int(result.get('visible') or 0)}/17",
            (24, 59),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (215, 225, 235),
            1,
            cv2.LINE_AA,
        )
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cv2.putText(
            canvas,
            timestamp,
            (12, max(22, canvas.shape[0] - 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (230, 235, 240),
            1,
            cv2.LINE_AA,
        )
        return canvas
