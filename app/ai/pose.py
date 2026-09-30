from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class PoseHit:
    frame: np.ndarray
    bbox: np.ndarray | None
    keypoints: np.ndarray | None
    box_conf: float
    decode_ms: float
    pose_ms: float


class PoseEngine:
    def __init__(
        self,
        model_path: Path,
        fallback: str,
        imgsz: int = 512,
        conf: float = 0.35,
        device: str = "cpu",
    ) -> None:
        self.model_path = Path(model_path)
        self.fallback = fallback
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.device = device
        self.model: Any = None
        self.loaded_name: str | None = None
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        try:
            from ultralytics import YOLO

            source = str(self.model_path) if self.model_path.exists() else self.fallback
            self.model = YOLO(source)
            self.loaded_name = source
            self.error = None
        except Exception as exc:
            self.model = None
            self.error = f"Khong nap duoc YOLO Pose: {exc}"

    def predict_bytes(self, jpeg: bytes) -> PoseHit:
        decode_start = perf_counter()
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        decode_ms = (perf_counter() - decode_start) * 1000
        if frame is None:
            raise ValueError("JPEG khong hop le")
        return self.predict_frame(frame, decode_ms=decode_ms)

    def predict_frame(self, frame: np.ndarray, decode_ms: float = 0.0) -> PoseHit:
        if self.model is None:
            raise RuntimeError(self.error or "YOLO Pose is not loaded")

        pose_start = perf_counter()
        results = self.model.predict(
            source=frame,
            imgsz=self.imgsz,
            conf=self.conf,
            device=self.device,
            verbose=False,
        )
        pose_ms = (perf_counter() - pose_start) * 1000
        result = results[0]
        if result.boxes is None or len(result.boxes) == 0 or result.keypoints is None:
            return PoseHit(frame, None, None, 0.0, decode_ms, pose_ms)

        boxes = result.boxes.xyxy.detach().cpu().numpy().astype(np.float32)
        box_conf = result.boxes.conf.detach().cpu().numpy().astype(np.float32)
        key_xy = result.keypoints.xy.detach().cpu().numpy().astype(np.float32)
        if result.keypoints.conf is None:
            key_conf = np.ones(key_xy.shape[:2], dtype=np.float32)
        else:
            key_conf = result.keypoints.conf.detach().cpu().numpy().astype(np.float32)

        areas = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
            0, boxes[:, 3] - boxes[:, 1]
        )
        index = int(np.argmax(areas * np.maximum(box_conf, 0.01)))
        points = np.concatenate((key_xy[index], key_conf[index, :, None]), axis=1)
        if points.shape != (17, 3):
            raise ValueError(f"YOLO returned unsupported keypoint shape {points.shape}")
        return PoseHit(
            frame=frame,
            bbox=boxes[index],
            keypoints=points,
            box_conf=float(box_conf[index]),
            decode_ms=decode_ms,
            pose_ms=pose_ms,
        )
