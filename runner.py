"""
runner.py — сборка аргументов командной строки scrcpy из профиля настроек
и управление запущенными процессами.

Ключевое отличие от «прототипа на pkill -f scrcpy»: каждая сессия хранится
в словаре по ключу устройства (serial / ip:port), поэтому:
  - можно держать несколько сессий одновременно (телефон + планшет);
  - Stop останавливает конкретный процесс (terminate → kill), а не вообще
    все scrcpy в системе;
  - если процесс упал сам (выдернули кабель) — это видно по poll(),
    а не считается «работающим» просто потому что кнопку никто не нажал.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _nonempty(value) -> bool:
    return str(value if value is not None else "").strip() != ""


@dataclass
class RunningSession:
    key: str                     # serial или ip:port, для которого запущен scrcpy
    process: subprocess.Popen
    profile_name: str
    started_at: float = field(default_factory=time.monotonic)

    def is_alive(self) -> bool:
        return self.process.poll() is None


def build_args(profile: dict, records_dir: str, device_label: str = "device") -> List[str]:
    """Превращает профиль (словарь настроек из config.py) в список
    аргументов командной строки scrcpy 1.25."""
    args: List[str] = []

    def flag(name: str, value=True) -> None:
        args.append(f"--{name}" if value is True else f"--{name}={value}")

    if _int(profile.get("max_size")) > 0:
        flag("max-size", _int(profile["max_size"]))
    if _nonempty(profile.get("bit_rate")):
        flag("bit-rate", profile["bit_rate"])
    if _int(profile.get("max_fps")) > 0:
        flag("max-fps", _int(profile["max_fps"]))
    if _nonempty(profile.get("crop")):
        flag("crop", profile["crop"])
    lock = (profile.get("lock_video_orientation") or "unlocked").strip()
    if lock and lock != "unlocked":
        flag("lock-video-orientation", lock)
    if _nonempty(profile.get("display_id")):
        flag("display", _int(profile["display_id"]))

    if _nonempty(profile.get("window_title")):
        flag("window-title", profile["window_title"])
    if profile.get("fullscreen"):
        flag("fullscreen")
    if profile.get("always_on_top"):
        flag("always-on-top")
    if profile.get("window_borderless"):
        flag("window-borderless")
    if _nonempty(profile.get("window_x")):
        flag("window-x", profile["window_x"])
    if _nonempty(profile.get("window_y")):
        flag("window-y", profile["window_y"])
    if _nonempty(profile.get("window_width")):
        flag("window-width", profile["window_width"])
    if _nonempty(profile.get("window_height")):
        flag("window-height", profile["window_height"])

    if profile.get("no_control"):
        flag("no-control")
    if profile.get("stay_awake"):
        flag("stay-awake")
    if profile.get("turn_screen_off"):
        flag("turn-screen-off")
    if profile.get("show_touches"):
        flag("show-touches")
    if profile.get("disable_screensaver"):
        flag("disable-screensaver")
    if profile.get("power_off_on_close"):
        flag("power-off-on-close")
    if _nonempty(profile.get("render_driver")):
        flag("render-driver", profile["render_driver"])

    if profile.get("hid_keyboard"):
        flag("hid-keyboard")
    if profile.get("hid_mouse"):
        flag("hid-mouse")
    if profile.get("otg"):
        flag("otg")

    if profile.get("record_enabled"):
        record_path = (profile.get("record_path") or "").strip()
        fmt = (profile.get("record_format") or "").strip()
        if not record_path:
            filename = f"{device_label}_{datetime.now():%Y%m%d_%H%M%S}.{fmt or 'mp4'}"
            record_path = str(Path(records_dir) / filename)
        else:
            record_path = record_path.format(
                name=device_label, date=datetime.now().strftime("%Y%m%d_%H%M%S")
            )
        Path(record_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.append(f"--record={record_path}")
        if fmt:
            args.append(f"--record-format={fmt}")

    extra = (profile.get("extra_args") or "").strip()
    if extra:
        args.extend(extra.split())

    return args


class ScrcpyRunner:
    """Держит не более одной scrcpy-сессии на каждый ключ устройства."""

    def __init__(self, scrcpy_path: str = "scrcpy"):
        self.scrcpy_path = scrcpy_path
        self._sessions: Dict[str, RunningSession] = {}
        self._lock = threading.Lock()

    def is_running(self, key: str) -> bool:
        session = self._sessions.get(key)
        return bool(session and session.is_alive())

    def running_keys(self) -> List[str]:
        return [key for key, s in self._sessions.items() if s.is_alive()]

    def start(
        self,
        key: str,
        profile: dict,
        profile_name: str,
        records_dir: str,
        on_output: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        with self._lock:
            if self.is_running(key):
                raise RuntimeError(f"Для {key} уже запущена сессия scrcpy")

            args = build_args(profile, records_dir, device_label=key.replace(":", "_"))
            command = [self.scrcpy_path, "-s", key, *args]

            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"Не найден исполняемый файл scrcpy: {self.scrcpy_path!r}") from exc

            self._sessions[key] = RunningSession(key=key, process=process, profile_name=profile_name)

        if on_output or on_exit:
            def pump():
                if process.stdout:
                    for line in process.stdout:
                        if on_output:
                            on_output(line.rstrip())
                code = process.wait()
                self._sessions.pop(key, None)
                if on_exit:
                    on_exit(key, code)

            threading.Thread(target=pump, daemon=True).start()

    def stop(self, key: str, timeout: float = 5.0) -> None:
        session = self._sessions.get(key)
        if not session:
            return
        session.process.terminate()
        try:
            session.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            session.process.kill()
        self._sessions.pop(key, None)

    def stop_all(self) -> None:
        for key in list(self._sessions.keys()):
            self.stop(key)
