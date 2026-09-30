from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT / value


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _int("PORT", 8000)
    log_level: str = os.getenv("LOG_LEVEL", "info")

    db_path: Path = _path("DB_PATH", "data/app.db")
    img_dir: Path = _path("IMG_DIR", "data/img")
    session_days: int = _int("SESSION_DAYS", 7)
    cookie_secure: bool = _bool("COOKIE_SECURE", False)

    pose_model: Path = _path("POSE_MODEL", "mdl/yolo11n-pose_openvino_model")
    pose_fallback: str = os.getenv("POSE_FALLBACK", "yolo11n-pose.pt")
    pose_device: str = os.getenv("POSE_DEVICE", "cpu")
    pose_imgsz: int = _int("POSE_IMGSZ", 512)
    pose_conf: float = _float("POSE_CONF", 0.35)

    gru_model: Path = _path("GRU_MODEL", "mdl/gru.pt")
    seq_len: int = _int("SEQ_LEN", 24)
    input_dim: int = _int("INPUT_DIM", 92)
    hidden_dim: int = _int("HIDDEN_DIM", 64)
    gru_layers: int = _int("GRU_LAYERS", 2)

    sample_fps: int = _int("SAMPLE_FPS", 8)
    jpeg_quality: float = _float("JPEG_QUALITY", 0.68)
    kp_conf: float = _float("KP_CONF", 0.25)
    min_visible: int = _int("MIN_VISIBLE", 7)
    clear_visible: int = _int("CLEAR_VISIBLE", 12)
    event_threshold: float = _float("EVENT_THRESHOLD", 0.75)
    confirm_frames: int = _int("CONFIRM_FRAMES", 3)
    rearm_frames: int = _int("REARM_FRAMES", 8)
    cooldown_seconds: float = _float("COOLDOWN_SECONDS", 45.0)
    missing_reset_frames: int = _int("MISSING_RESET_FRAMES", 12)

    # Camera is opened by the FastAPI server. Use a numeric index (0, 1, ...)
    # for a USB webcam or an RTSP/HTTP URL for an IP camera.
    camera_source: str = os.getenv("CAMERA_SOURCE", "0").strip()
    camera_name: str = os.getenv("CAMERA_NAME", "Camera 01").strip()
    camera_backend: str = os.getenv("CAMERA_BACKEND", "auto").strip().lower()
    camera_width: int = _int("CAMERA_WIDTH", 1280)
    camera_height: int = _int("CAMERA_HEIGHT", 720)
    camera_reconnect_seconds: float = _float("CAMERA_RECONNECT_SECONDS", 3.0)

    sms_on: bool = _bool("SMS_ON", False)
    sms_port: str = os.getenv("SMS_PORT", "COM5")
    sms_baud: int = _int("SMS_BAUD", 115200)
    sms_timeout: float = _float("SMS_TIMEOUT", 12.0)
    sms_retries: int = _int("SMS_RETRIES", 2)
    sms_country: str = os.getenv("SMS_COUNTRY", "84")

    cert_file: Path = _path("CERT_FILE", "cert/lan.pem")
    key_file: Path = _path("KEY_FILE", "cert/lan-key.pem")

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.img_dir,
            self.gru_model.parent,
            self.cert_file.parent,
            ROOT / "data" / "raw",
            ROOT / "data" / "seq",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
