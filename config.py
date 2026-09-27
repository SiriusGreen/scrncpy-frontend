"""
config.py — загрузка и сохранение конфигурации приложения (пути к бинарникам,
профили запуска scrcpy) в JSON-файл в домашней директории пользователя.

Набор полей DEFAULT_PROFILE соответствует возможностям scrcpy 1.25:
без выбора видеокодека, аудио и камеры — эти флаги появились только в 2.x.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".config" / "scrcpy-master"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Все числовые/текстовые поля хранятся строками — так проще биндить их
# к tkinter StringVar и парсить уже в runner.py, не гадая с типами при
# загрузке/сохранении JSON.
DEFAULT_PROFILE: Dict[str, Any] = {
    # --- видео ---
    "max_size": "0",                       # -m, "0" = без ограничения
    "bit_rate": "8M",                      # -b
    "max_fps": "0",                        # --max-fps, "0" = не ограничивать
    "crop": "",                            # --crop W:H:X:Y
    "lock_video_orientation": "unlocked",  # unlocked/initial/0/1/2/3
    "display_id": "",                      # --display N (доп. дисплеи)

    # --- окно ---
    "window_title": "",
    "fullscreen": False,        # -f
    "always_on_top": False,
    "window_borderless": False,
    "window_x": "",
    "window_y": "",
    "window_width": "",
    "window_height": "",

    # --- управление / поведение устройства ---
    "no_control": False,        # -n, режим "только просмотр"
    "stay_awake": False,        # -w
    "turn_screen_off": False,   # -S
    "show_touches": False,      # -t
    "disable_screensaver": False,
    "power_off_on_close": False,
    "render_driver": "",

    # --- HID / OTG (только Linux + USB) ---
    "hid_keyboard": False,      # -K
    "hid_mouse": False,         # -M
    "otg": False,               # отдельный режим без зеркалирования

    # --- запись ---
    "record_enabled": False,
    "record_path": "",          # путь к файлу; поддерживает {name} и {date}
    "record_format": "",        # mp4/mkv, пусто = определить по расширению

    # --- цель подключения ---
    "target": "",                # пусто = использовать устройство, выбранное
                                  # в списке слева; иначе serial или ip:port

    # --- произвольные доп. флаги ---
    "extra_args": "",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "settings": {
        "adb_path": "adb",
        "scrcpy_path": "scrcpy",
        "records_dir": str(Path.home() / "scrcpy_records"),
        "poll_interval_ms": 3000,
    },
    "profiles": {
        "По умолчанию": copy.deepcopy(DEFAULT_PROFILE),
    },
    "last_used_profile": "По умолчанию",
    # Список "ip:port" устройств, к которым когда-либо подключались по
    # Wi-Fi — переживает `adb kill-server`/перезапуск adb-сервера, т.к.
    # `adb devices` после этого показывает только то, что подключено
    # заново. Позволяет одной кнопкой переподключить всё сразу.
    "known_network_devices": [],
}


def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _merge_defaults(data)
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp_file.replace(CONFIG_FILE)


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """Дополняет старый/неполный конфиг недостающими ключами — например,
    если после обновления программы в профиле появились новые поля."""
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged["settings"].update(data.get("settings", {}))

    profiles = data.get("profiles") or {}
    if profiles:
        merged["profiles"] = {}
        for name, profile in profiles.items():
            full_profile = copy.deepcopy(DEFAULT_PROFILE)
            full_profile.update(profile)
            merged["profiles"][name] = full_profile

    if data.get("last_used_profile") in merged["profiles"]:
        merged["last_used_profile"] = data["last_used_profile"]
    else:
        merged["last_used_profile"] = next(iter(merged["profiles"]))

    known = data.get("known_network_devices") or []
    # Отбрасываем дубликаты, сохраняя порядок, и игнорируем мусор не-строкового типа.
    seen = set()
    merged["known_network_devices"] = []
    for item in known:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            merged["known_network_devices"].append(item)

    return merged


def new_profile() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_PROFILE)


def add_known_device(config: Dict[str, Any], ip_port: str) -> None:
    known = config.setdefault("known_network_devices", [])
    if ip_port not in known:
        known.append(ip_port)


def remove_known_device(config: Dict[str, Any], ip_port: str) -> None:
    known = config.setdefault("known_network_devices", [])
    if ip_port in known:
        known.remove(ip_port)
