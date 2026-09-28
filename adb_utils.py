"""
adb_utils.py — обёртки над adb: список устройств, подключение по Wi-Fi,
беспроводное сопряжение (Android 11+), сканирование локальной сети на
предмет открытого adb-порта. Никаких shell=True и Linux-специфичных
утилит (pkill/ip neigh) для управления устройствами — только сам adb,
поэтому эти функции переносимы. Сканирование сети использует `ip` (есть
в net-tools/iproute2 на всех современных дистрибутивах Linux).
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
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
        proc = subprocess.Popen(
            [adb_path, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AdbError(f"Не найден исполняемый файл adb: {adb_path!r}") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        # subprocess.run после kill() ждёт закрытия пайпов без ограничения, а
        # демон adb (форк при старте сервера) может держать их открытыми —
        # поэтому дожидаемся коротко и в любом случае выходим.
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        raise AdbError(f"adb {' '.join(args)} не ответил за {timeout} с") from exc

    output = (stdout or "") + (stderr or "")
    if proc.returncode != 0 and not output.strip():
        raise AdbError(f"adb {' '.join(args)} завершился с кодом {proc.returncode}")
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


def get_local_ipv4_networks() -> List[str]:
    """Определяет локальные IPv4-подсети (в виде CIDR-строк) по выводу
    `ip -4 -o addr show` — так можно узнать, какие сети сканировать, не
    зная заранее IP телефона."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    networks: List[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        for i, token in enumerate(parts):
            if token == "inet" and i + 1 < len(parts):
                try:
                    iface = ipaddress.ip_interface(parts[i + 1])
                except ValueError:
                    continue
                if iface.ip.is_loopback:
                    continue
                networks.append(str(iface.network))

    seen = set()
    unique: List[str] = []
    for net in networks:
        if net not in seen:
            seen.add(net)
            unique.append(net)
    return unique


def _port_is_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_network_for_adb(
    port: int = 5555, timeout: float = 0.3, max_workers: int = 64, max_hosts_per_net: int = 1024,
) -> List[str]:
    """Сканирует локальные подсети на предмет открытого TCP-порта adb
    (по умолчанию 5555 — стандартный порт `adb tcpip`). Открытый порт не
    гарантирует, что это именно adbd (может быть любой другой сервис) —
    финальную проверку всё равно делает `adb connect`, это лишь способ
    не искать IP телефона вручную."""
    hosts: List[str] = []
    for cidr in get_local_ipv4_networks():
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if network.num_addresses > max_hosts_per_net:
            # Слишком большая сеть (например, ошибочно распознанный /8) — пропускаем,
            # чтобы не зависнуть на часы.
            continue
        hosts.extend(str(host) for host in network.hosts())

    if not hosts:
        return []

    found: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_port_is_open, host, port, timeout): host for host in hosts
        }
        for future in concurrent.futures.as_completed(futures):
            host = futures[future]
            try:
                if future.result():
                    found.append(host)
            except Exception:
                continue

    return sorted(found, key=lambda ip: tuple(int(p) for p in ip.split(".")))
