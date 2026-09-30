from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from time import perf_counter
from typing import Any

import numpy as np

from app.ai.feat import FeatureState, blank_feature
from app.ai.net import GRUEngine
from app.ai.policy import AlertPolicy
from app.ai.pose import PoseEngine
from app.cfg import Settings


@dataclass(slots=True)
class PerfWindow:
    values: deque[float]
    times: deque[float]

    @classmethod
    def create(cls, size: int = 200) -> "PerfWindow":
        return cls(deque(maxlen=size), deque(maxlen=size))

    def add(self, milliseconds: float, now: float) -> None:
        self.values.append(float(milliseconds))
        self.times.append(float(now))

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "fps": 0.0}
        ordered = sorted(self.values)
        p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
        duration = self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0
        fps = (len(self.times) - 1) / duration if duration > 0 else 0.0
        return {
            "p50_ms": round(float(median(ordered)), 2),
            "p95_ms": round(float(ordered[p95_index]), 2),
            "fps": round(float(fps), 2),
        }


class AICore:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.pose = PoseEngine(
            cfg.pose_model,
            cfg.pose_fallback,
            cfg.pose_imgsz,
            cfg.pose_conf,
            cfg.pose_device,
        )
        self.gru = GRUEngine(
            cfg.gru_model, cfg.input_dim, cfg.hidden_dim, cfg.gru_layers
        )

    def load(self) -> None:
        self.pose.load()
        self.gru.load()

    def status(self) -> dict[str, Any]:
        return {
            "pose_ready": self.pose.ready,
            "pose_model": self.pose.loaded_name,
            "pose_error": self.pose.error,
            "gru_ready": self.gru.ready,
            "gru_model": str(self.gru.model_path),
            "gru_error": self.gru.error,
        }

    def new_session(self) -> "FallSession":
        return FallSession(self)


class FallSession:
    def __init__(self, core: AICore):
        self.core = core
        cfg = core.cfg
        meta = core.gru.meta
        self.seq_len = int(meta.get("seq_len", cfg.seq_len))
        self.features = FeatureState(cfg.kp_conf, cfg.min_visible, cfg.clear_visible)
        self.sequence: deque[np.ndarray] = deque(maxlen=self.seq_len)
        self.policy = AlertPolicy(
            float(meta.get("event_threshold", cfg.event_threshold)),
            int(meta.get("confirm_frames", cfg.confirm_frames)),
            int(meta.get("rearm_frames", cfg.rearm_frames)),
            cfg.cooldown_seconds,
        )
        self.missing = 0
        self.perf = PerfWindow.create()

    def reset(self) -> None:
        self.features.reset()
        self.sequence.clear()
        self.policy.reset()
        self.missing = 0

    def process(self, jpeg: bytes) -> dict[str, Any]:
        """Backward-compatible JPEG entry point used by offline tools."""
        started = perf_counter()
        if not self.core.pose.ready:
            return {"ok": False, "error": self.core.pose.error or "YOLO not ready"}

        hit = self.core.pose.predict_bytes(jpeg)
        return self._process_hit(hit, started)

    def process_frame(self, frame: np.ndarray) -> dict[str, Any]:
        """Process one OpenCV BGR frame captured directly by the server."""
        started = perf_counter()
        if not self.core.pose.ready:
            return {"ok": False, "error": self.core.pose.error or "YOLO not ready"}

        hit = self.core.pose.predict_frame(frame)
        return self._process_hit(hit, started)

    def _process_hit(self, hit: Any, started: float) -> dict[str, Any]:
        inference: dict[str, Any] = {
            "posture": "unknown",
            "posture_score": 0.0,
            "event": "unknown",
            "event_score": 0.0,
            "event_probs": [1.0, 0.0, 0.0],
        }
        skeleton: list[list[float]] = []
        bbox: list[float] | None = None
        visible = 0
        observation = "lost"

        if hit.keypoints is None or hit.bbox is None:
            self.missing += 1
            if self.sequence:
                self.sequence.append(blank_feature())
            if self.missing >= self.core.cfg.missing_reset_frames:
                self.features.reset()
                self.sequence.clear()
        else:
            self.missing = 0
            feature = self.features.transform(hit.keypoints, hit.bbox, hit.frame.shape[:2])
            self.sequence.append(feature.vector)
            visible = feature.visible
            observation = feature.observation
            skeleton = hit.keypoints.round(3).tolist()
            bbox = hit.bbox.round(2).tolist()

        gru_ms = 0.0
        if self.core.gru.ready and len(self.sequence) == self.seq_len:
            gru_started = perf_counter()
            inference = self.core.gru.predict(np.stack(self.sequence))
            gru_ms = (perf_counter() - gru_started) * 1000

        policy = self.policy.update(
            inference["event_probs"], observation, perf_counter()
        )
        total_ms = (perf_counter() - started) * 1000
        now = perf_counter()
        self.perf.add(total_ms, now)
        return {
            "ok": True,
            "bbox": bbox,
            "keypoints": skeleton,
            "visible": visible,
            "observation": observation,
            "warmup": max(0, self.seq_len - len(self.sequence)),
            "policy_state": policy.state,
            "alert": policy.alert,
            "positive_count": policy.positive_count,
            "model_ready": self.core.gru.ready,
            **inference,
            "timing": {
                "decode_ms": round(hit.decode_ms, 2),
                "pose_ms": round(hit.pose_ms, 2),
                "gru_ms": round(gru_ms, 2),
                "ai_ms": round(total_ms, 2),
                **self.perf.summary(),
            },
            "snapshot": hit.frame,
        }
