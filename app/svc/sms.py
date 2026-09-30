from __future__ import annotations

import queue
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable


def normalize_phone(phone: str, country: str = "84") -> str:
    value = re.sub(r"[\s().-]", "", phone.strip())
    if value.startswith("00"):
        value = "+" + value[2:]
    elif value.startswith("0"):
        value = "+" + country + value[1:]
    elif value.startswith(country):
        value = "+" + value
    if not re.fullmatch(r"\+[1-9]\d{8,14}", value):
        raise ValueError("So dien thoai khong hop le; nen dung dang +84xxxxxxxxx")
    return value


def gsm_text(text: str, max_chars: int = 155) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


class SerialModem:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 12.0):
        self.port = port
        self.baud = int(baud)
        self.timeout = float(timeout)

    @staticmethod
    def _read_until(serial_obj, tokens: tuple[bytes, ...], timeout: float) -> bytes:
        deadline = time.perf_counter() + timeout
        data = bytearray()
        while time.perf_counter() < deadline:
            waiting = getattr(serial_obj, "in_waiting", 0)
            chunk = serial_obj.read(waiting or 1)
            if chunk:
                data.extend(chunk)
                if any(token in data for token in tokens):
                    return bytes(data)
        raise TimeoutError(f"Dcom timeout. Phan hoi: {data.decode(errors='replace')}")

    def _command(self, serial_obj, command: str, timeout: float | None = None) -> str:
        serial_obj.write((command + "\r").encode("ascii"))
        data = self._read_until(serial_obj, (b"OK", b"ERROR"), timeout or self.timeout)
        text = data.decode(errors="replace")
        if "ERROR" in text:
            raise RuntimeError(f"Dcom loi voi lenh {command}: {text.strip()}")
        return text

    def probe(self) -> str:
        import serial

        with serial.Serial(self.port, self.baud, timeout=0.25, write_timeout=2) as modem:
            modem.reset_input_buffer()
            self._command(modem, "AT")
            sim = self._command(modem, "AT+CPIN?")
            signal = self._command(modem, "AT+CSQ")
            network = self._command(modem, "AT+CREG?")
            return "\n".join((sim, signal, network))

    def send(self, phone: str, message: str) -> str:
        import serial

        with serial.Serial(self.port, self.baud, timeout=0.25, write_timeout=2) as modem:
            modem.reset_input_buffer()
            self._command(modem, "AT")
            self._command(modem, "AT+CMGF=1")
            self._command(modem, 'AT+CSCS="GSM"')
            modem.write(f'AT+CMGS="{phone}"\r'.encode("ascii"))
            self._read_until(modem, (b">", b"ERROR"), self.timeout)
            modem.write(gsm_text(message).encode("ascii") + bytes([26]))
            data = self._read_until(modem, (b"+CMGS:", b"ERROR"), self.timeout * 2)
            if b"ERROR" in data:
                raise RuntimeError(data.decode(errors="replace"))
            tail = b"" if b"OK" in data else self._read_until(modem, (b"OK", b"ERROR"), self.timeout)
            response = (data + tail).decode(errors="replace")
            if "ERROR" in response:
                raise RuntimeError(response)
            return response.strip()


@dataclass(slots=True)
class SmsJob:
    sms_id: int
    phone: str
    message: str


FinishCallback = Callable[[int, str, float | None, str | None], None]


class SmsWorker:
    def __init__(
        self,
        enabled: bool,
        modem: SerialModem,
        retries: int,
        on_finish: FinishCallback,
    ) -> None:
        self.enabled = bool(enabled)
        self.modem = modem
        self.retries = max(0, int(retries))
        self.on_finish = on_finish
        self.jobs: queue.Queue[SmsJob | None] = queue.Queue(maxsize=100)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="sms-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
        if self.thread:
            self.thread.join(timeout=3)

    def enqueue(self, job: SmsJob) -> bool:
        if not self.enabled:
            self.on_finish(job.sms_id, "disabled", None, "SMS_ON=false")
            return False
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            self.on_finish(job.sms_id, "failed", None, "Hang doi SMS da day")
            return False

    def _run(self) -> None:
        while not self.stop_event.is_set():
            job = self.jobs.get()
            if job is None:
                return
            started = time.perf_counter()
            error: str | None = None
            for attempt in range(self.retries + 1):
                try:
                    self.modem.send(job.phone, job.message)
                    elapsed = (time.perf_counter() - started) * 1000
                    self.on_finish(job.sms_id, "sent", elapsed, None)
                    break
                except Exception as exc:  # hardware and carrier errors are expected here
                    error = str(exc)
                    if attempt < self.retries:
                        time.sleep(1.5 * (attempt + 1))
            else:
                elapsed = (time.perf_counter() - started) * 1000
                self.on_finish(job.sms_id, "failed", elapsed, error)
