from __future__ import annotations

from dataclasses import dataclass

import numpy as np


KEYPOINTS = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

SKELETON = (
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (0, 5),
    (0, 6),
)


@dataclass(slots=True)
class FeatureResult:
    vector: np.ndarray
    visible: int
    observation: str


class FeatureState:
    """Normalize a COCO-17 skeleton and keep only short-term delta state."""

    def __init__(self, kp_conf: float = 0.25, min_visible: int = 7, clear_visible: int = 12):
        self.kp_conf = float(kp_conf)
        self.min_visible = int(min_visible)
        self.clear_visible = int(clear_visible)
        self.prev_xy: np.ndarray | None = None
        self.prev_mask: np.ndarray | None = None
        self.prev_center: np.ndarray | None = None

    def reset(self) -> None:
        self.prev_xy = None
        self.prev_mask = None
        self.prev_center = None

    def transform(
        self, keypoints: np.ndarray, bbox: np.ndarray, frame_shape: tuple[int, int]
    ) -> FeatureResult:
        points = np.asarray(keypoints, dtype=np.float32)
        if points.shape != (17, 3):
            raise ValueError(f"Expected keypoints (17,3), got {points.shape}")
        bbox = np.asarray(bbox, dtype=np.float32).reshape(4)

        xy = points[:, :2].copy()
        conf = np.nan_to_num(points[:, 2], nan=0.0, posinf=0.0, neginf=0.0)
        mask = conf >= self.kp_conf
        visible = int(mask.sum())

        hips = [idx for idx in (11, 12) if mask[idx]]
        shoulders = [idx for idx in (5, 6) if mask[idx]]
        if hips:
            center = xy[hips].mean(axis=0)
        elif shoulders:
            center = xy[shoulders].mean(axis=0)
        else:
            center = np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2])

        scale = max(float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1]), 1.0)
        norm_xy = (xy - center) / scale
        norm_xy[~mask] = 0.0

        delta = np.zeros_like(norm_xy)
        if self.prev_xy is not None and self.prev_mask is not None:
            valid_delta = mask & self.prev_mask
            delta[valid_delta] = norm_xy[valid_delta] - self.prev_xy[valid_delta]

        per_joint = np.concatenate(
            (norm_xy, conf[:, None].clip(0.0, 1.0), delta), axis=1
        )
        frame_h, frame_w = max(1, int(frame_shape[0])), max(1, int(frame_shape[1]))
        bbox_center = np.array(
            [(bbox[0] + bbox[2]) / (2 * frame_w), (bbox[1] + bbox[3]) / (2 * frame_h)],
            dtype=np.float32,
        )
        bbox_w = float(max(0.0, bbox[2] - bbox[0]) / frame_w)
        bbox_h = float(max(0.0, bbox[3] - bbox[1]) / frame_h)
        aspect = float(np.clip(bbox_w / max(bbox_h, 1e-6), 0.0, 4.0))
        center_delta = (
            bbox_center - self.prev_center
            if self.prev_center is not None
            else np.zeros(2, dtype=np.float32)
        )
        global_features = np.array(
            [
                bbox_center[0],
                bbox_center[1],
                bbox_w,
                bbox_h,
                aspect,
                center_delta[0],
                center_delta[1],
            ],
            dtype=np.float32,
        )
        vector = np.concatenate((per_joint.reshape(-1), global_features)).astype(np.float32)

        self.prev_xy = norm_xy.copy()
        self.prev_mask = mask.copy()
        self.prev_center = bbox_center.copy()

        if visible >= self.clear_visible:
            observation = "clear"
        elif visible >= self.min_visible:
            observation = "partial"
        else:
            observation = "occluded"
        return FeatureResult(vector=vector, visible=visible, observation=observation)


def blank_feature() -> np.ndarray:
    return np.zeros(17 * 5 + 7, dtype=np.float32)


def augment_sequence(
    sequence: np.ndarray,
    rng: np.random.Generator,
    joint_dropout: float = 0.12,
    frame_dropout: float = 0.05,
    noise_std: float = 0.008,
) -> np.ndarray:
    """Skeleton augmentation used only for training GRU robustness."""
    x = np.asarray(sequence, dtype=np.float32).copy()
    if x.ndim != 2 or x.shape[1] != 92:
        raise ValueError("Sequence must have shape (T,92)")
    joints = x[:, :85].reshape(x.shape[0], 17, 5)

    joint_mask = rng.random((x.shape[0], 17)) < joint_dropout
    joints[joint_mask] = 0.0
    frame_mask = rng.random(x.shape[0]) < frame_dropout
    joints[frame_mask] = 0.0

    visible = joints[:, :, 2] > 0
    noise = rng.normal(0.0, noise_std, size=(x.shape[0], 17, 4)).astype(np.float32)
    joints[:, :, (0, 1, 3, 4)] += noise * visible[:, :, None]
    global_features = x[:, 85:].copy()
    global_features += rng.normal(0.0, noise_std / 2, size=global_features.shape).astype(
        np.float32
    )
    global_features[frame_mask] = 0.0
    return np.concatenate((joints.reshape(x.shape[0], 85), global_features), axis=1).astype(
        np.float32
    )
