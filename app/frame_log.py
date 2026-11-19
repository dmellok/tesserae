"""Recent frame exchanges, kept in memory per device."""

import threading
import time
from collections import deque

from flask import Flask

EXCHANGE_LOG_LINES = 60


def _note(app: Flask, device_id: str, way: str, text: str) -> None:
    with app.config.setdefault("FRAME_LOG_LOCK", threading.Lock()):
        logs = app.config.setdefault("FRAME_EXCHANGES", {})
        logs.setdefault(device_id, deque(maxlen=EXCHANGE_LOG_LINES)).append(
            (time.time(), way, text)
        )


def exchanges(app: Flask, device_id: str) -> list[tuple[float, str, str]]:
    with app.config.setdefault("FRAME_LOG_LOCK", threading.Lock()):
        return list(app.config.get("FRAME_EXCHANGES", {}).get(device_id, ()))
