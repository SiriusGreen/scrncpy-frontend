"""
adb_utils.py — обёртки над adb: список устройств, подключение по Wi-Fi,
беспроводное сопряжение (Android 11+). Никаких shell=True и Linux-специфичных
утилит (pkill/ip neigh) — только сам adb, поэтому эти функции переносимы,
даже если сегодня используются только на Linux.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Device:
    serial: str
    state: str          # device / unauthorized / offline
    model: str = ""

    @property
    def display_name(self) -> str:
        model = f" ({self.model})" if self.model else ""
        return f"{self.serial}{model}"


class AdbError(RuntimeError):
    pass


def _run(adb_path: str, args: List[str], timeout: float = 10.0) -> str:
    try:
        result = subprocess.run(
            [adb_path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AdbError(f"Не найден исполняемый файл adb: {adb_path!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb {' '.join(args)} не ответил за {timeout} с") from exc

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and not output.strip():
        raise AdbError(f"adb {' '.join(args)} завершился с кодом {result.returncode}")
    return output


def list_devices(adb_path: str = "adb") -> List[Device]:
    output = _run(adb_path, ["devices", "-l"])
    devices: List[Device] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
                break
        devices.append(Device(serial=serial, state=state, model=model))
    return devices


def enable_tcpip(adb_path: str, serial: str, port: int = 5555) -> str:
    """Переводит устройство (уже подключённое по USB) в режим TCP/IP."""
    return _run(adb_path, ["-s", serial, "tcpip", str(port)])


def connect_tcpip(adb_path: str, ip_port: str) -> str:
    return _run(adb_path, ["connect", ip_port])


def pair(adb_path: str, ip_port: str, code: str) -> str:
    """Беспроводное сопряжение Android 11+ (экран "Отладка по Wi-Fi")."""
    return _run(adb_path, ["pair", ip_port, code], timeout=15.0)


def disconnect(adb_path: str, ip_port: Optional[str] = None) -> str:
    args = ["disconnect"] if ip_port is None else ["disconnect", ip_port]
    return _run(adb_path, args)


def restart_server(adb_path: str) -> str:
    _run(adb_path, ["kill-server"])
    return _run(adb_path, ["start-server"])


def get_ip_via_usb(adb_path: str, serial: str) -> Optional[str]:
    """Пытается вытащить IP устройства по wlan0 через уже установленное
    USB-соединение (набор утилит в шеллах разных прошивок отличается,
    поэтому пробуем несколько вариантов)."""
    commands = [
        ["shell", "ip -f inet addr show wlan0"],
        ["shell", "ip route"],
    ]
    for args in commands:
        try:
            output = _run(adb_path, ["-s", serial, *args], timeout=5.0)
        except AdbError:
            continue
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)/\d+", output)
        if match:
            return match.group(1)
        match = re.search(r"src (\d+\.\d+\.\d+\.\d+)", output)
        if match:
            return match.group(1)
    return None


def adb_version(adb_path: str = "adb") -> Optional[str]:
    try:
        output = _run(adb_path, ["version"], timeout=5.0)
    except AdbError:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    return match.group(1) if match else None


def scrcpy_version(scrcpy_path: str = "scrcpy") -> Optional[str]:
    try:
        result = subprocess.run(
            [scrcpy_path, "--version"], capture_output=True, text=True, timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"scrcpy (\d+\.\d+(?:\.\d+)?)", output)
    return match.group(1) if match else None
