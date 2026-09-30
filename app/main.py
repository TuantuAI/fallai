from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import cv2
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.ai.pipe import AICore
from app.auth import hash_password, new_session_token, token_hash, verify_password
from app.cfg import ROOT, settings
from app.db import Database
from app.sch import ContactIn, LoginIn, RegisterIn, SmsTestIn
from app.svc.camera import CameraService
from app.svc.sms import SerialModem, SmsJob, SmsWorker, gsm_text, normalize_phone


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("fallai")

SESSION_COOKIE = "fall_session"
WEB_DIR = ROOT / "app" / "web"

db = Database(settings.db_path)
core = AICore(settings)
modem = SerialModem(settings.sms_port, settings.sms_baud, settings.sms_timeout)
sms = SmsWorker(settings.sms_on, modem, settings.sms_retries, db.finish_sms)


def save_event_snapshot(event_id: int, frame: Any) -> str | None:
    target = settings.img_dir / f"{event_id}.jpg"
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        return None
    target.write_bytes(encoded.tobytes())
    url = f"/img/{target.name}"
    db.set_event_image(event_id, url)
    return url


def dispatch_alert(user: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Persist an alert for the account that started server-side monitoring."""
    user_id = int(user["id"])
    event_id = db.add_event(
        user_id=user_id,
        posture=result["posture"],
        event_label=result["event"],
        score=result["event_score"],
        visible=result["visible"],
        observation=result["observation"],
        ai_ms=result["timing"]["ai_ms"],
    )
    image = save_event_snapshot(event_id, result["snapshot"])
    contact = db.get_contact(user_id)
    sms_status = "no_contact"
    if contact and bool(contact["enabled"]):
        phone = contact["phone"]
        sms_id = db.add_sms(event_id, phone)
        message = gsm_text(
            "CANH BAO TE NGA - "
            f"Phat hien nguy co te nga luc {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}. "
            "Vui long kiem tra ngay."
        )
        queued = sms.enqueue(SmsJob(sms_id, phone, message))
        sms_status = "queued" if queued else "disabled"
    return {"event_id": event_id, "image": image, "sms_status": sms_status}


camera_service = CameraService(settings, core, dispatch_alert)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    await asyncio.to_thread(core.load)
    sms.start()
    logger.info("AI status: %s", core.status())
    logger.info("Camera status: %s", camera_service.status())
    try:
        yield
    finally:
        await asyncio.to_thread(camera_service.shutdown)
        sms.stop()


app = FastAPI(title="FallAI", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/img", StaticFiles(directory=settings.img_dir), name="images")


def session_user_from_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return db.user_from_session(token_hash(token))


def current_user(request: Request) -> dict[str, Any]:
    user = session_user_from_token(request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Chua dang nhap")
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    camera = camera_service.status()
    return {
        "ok": True,
        "ai": core.status(),
        "sms_on": settings.sms_on,
        "sms_port": settings.sms_port if settings.sms_on else None,
        "camera_busy": camera["running"],
        "camera": camera,
    }


@app.post("/api/register")
def register(payload: RegisterIn, response: Response) -> dict[str, Any]:
    try:
        user = db.create_user(payload.name, payload.email, hash_password(payload.password))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Email da ton tai") from exc
    token = new_session_token()
    db.create_session(token_hash(token), int(user["id"]), settings.session_days)
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@app.post("/api/login")
def login(payload: LoginIn, response: Response) -> dict[str, Any]:
    auth_user = db.get_user_auth(payload.email)
    if not auth_user or not verify_password(payload.password, auth_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email hoac mat khau khong dung")
    token = new_session_token()
    db.create_session(token_hash(token), int(auth_user["id"]), settings.session_days)
    set_session_cookie(response, token)
    user = {key: auth_user[key] for key in ("id", "name", "email", "created_at")}
    return {"ok": True, "user": user}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {
        "user": user,
        "contact": db.get_contact(int(user["id"])),
        "ai": core.status(),
        "sms_on": settings.sms_on,
        "sample_fps": settings.sample_fps,
        "jpeg_quality": settings.jpeg_quality,
        "camera": camera_service.status(),
    }


@app.get("/api/contact")
def get_contact(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"contact": db.get_contact(int(user["id"]))}


@app.put("/api/contact")
def put_contact(
    payload: ContactIn, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    try:
        phone = normalize_phone(payload.phone, settings.sms_country)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.upsert_contact(int(user["id"]), payload.name, phone, payload.enabled)
    return {"ok": True, "contact": db.get_contact(int(user["id"]))}


@app.get("/api/events")
def events(limit: int = 50, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {
        "events": db.list_events(int(user["id"]), limit),
        "stats": db.stats(int(user["id"])),
    }


@app.post("/api/sms/test")
def sms_test(
    payload: SmsTestIn, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    try:
        phone = normalize_phone(payload.phone, settings.sms_country)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sms_id = db.add_sms(None, phone)
    message = gsm_text(
        f"FALLAI TEST - He thong cua {user['name']} da ket noi Dcom thanh cong."
    )
    queued = sms.enqueue(SmsJob(sms_id=sms_id, phone=phone, message=message))
    return {"ok": queued, "sms_id": sms_id, "sms_on": settings.sms_on}


@app.get("/api/camera")
def camera_status(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, "camera": camera_service.status()}


@app.post("/api/camera/start")
def camera_start(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not core.pose.ready:
        raise HTTPException(status_code=503, detail=core.pose.error or "YOLO Pose chua san sang")
    started, status = camera_service.start(user)
    return {"ok": True, "started": started, "camera": status}


@app.post("/api/camera/stop")
def camera_stop(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    stopped, status = camera_service.stop()
    return {"ok": True, "stopped": stopped, "camera": status}


@app.get("/api/camera/stream")
def camera_stream(user: dict[str, Any] = Depends(current_user)) -> StreamingResponse:
    status = camera_service.status()
    if not status["running"]:
        raise HTTPException(status_code=409, detail="Camera may chu chua duoc bat")
    return StreamingResponse(
        camera_service.mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/live")
async def live_results(websocket: WebSocket) -> None:
    user = session_user_from_token(websocket.cookies.get(SESSION_COOKIE))
    await websocket.accept()
    if not user:
        await websocket.send_json({"type": "error", "error": "Chua dang nhap"})
        await websocket.close(code=4401)
        return

    last_sequence = int(camera_service.status()["frame_id"])
    await websocket.send_json({"type": "status", "camera": camera_service.status()})
    try:
        while True:
            sequence, result = await asyncio.to_thread(
                camera_service.wait_for_result, last_sequence, 2.0
            )
            if result is None:
                await websocket.send_json({"type": "status", "camera": camera_service.status()})
                continue
            last_sequence = sequence
            await websocket.send_json(
                {"type": "frame", "camera": camera_service.status(), "result": result}
            )
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        logger.exception("Live viewer WebSocket failed")


if __name__ == "__main__":
    import uvicorn

    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "log_level": settings.log_level,
    }
    if settings.cert_file.exists() and settings.key_file.exists():
        kwargs.update(ssl_certfile=str(settings.cert_file), ssl_keyfile=str(settings.key_file))
    uvicorn.run("app.main:app", **kwargs)
