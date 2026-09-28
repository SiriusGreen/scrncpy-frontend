"""
gui.py — интерфейс на CustomTkinter: список устройств слева, редактор
профиля (вкладки) и лог/кнопки запуска — справа.
"""
from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict

import customtkinter as ctk

import adb_utils
import config as cfg
from runner import ScrcpyRunner

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ORIENTATIONS = ["unlocked", "initial", "0", "1", "2", "3"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("scrcpy master")
        self.geometry("1080x720")
        self.minsize(920, 620)

        self.config_data = cfg.load_config()
        self.runner = ScrcpyRunner(self.config_data["settings"]["scrcpy_path"])

        self.devices: list[adb_utils.Device] = []
        self.selected_target = tk.StringVar(value="")
        self.current_profile_name = tk.StringVar(
            value=self.config_data.get("last_used_profile", "По умолчанию")
        )
        # виджеты полей профиля создаются в _build_*_tab и хранятся тут по
        # тем же ключам, что и в config.DEFAULT_PROFILE
        self.fields: Dict[str, tk.Variable] = {}

        # Очередь для вызовов UI из фоновых потоков: tkinter не потокобезопасен,
        # поэтому потоки НЕ трогают виджеты и self.after(), а кладут вызовы сюда,
        # а главный поток разбирает очередь сам (см. _drain_ui_queue).
        self._ui_queue: "queue.SimpleQueue" = queue.SimpleQueue()
        self._refresh_busy = False
        self._refresh_pending = False
        self._last_adb_error: "str | None" = None

        self._build_layout()
        self._load_profile_into_form(self.current_profile_name.get())
        self.after(50, self._drain_ui_queue)
        self.refresh_devices()
        self._schedule_poll()

    # ------------------------------------------------- вызовы из потоков
    def call_in_ui(self, fn, *args):
        """Потокобезопасно: выполнить fn(*args) в главном потоке tkinter."""
        self._ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:  # один сбойный колбэк не должен ронять цикл
                    print(f"UI callback error: {exc!r}")
        except queue.Empty:
            pass
        self.after(50, self._drain_ui_queue)

    # ---------------------------------------------------------------- layout
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_left_panel()
        self._build_right_panel()

    def _build_left_panel(self):
        left = ctk.CTkFrame(self, width=330)
        left.grid(row=0, column=0, sticky="nsw", padx=(10, 5), pady=10)
        left.grid_propagate(False)

        ctk.CTkLabel(left, text="Устройства", font=("", 15, "bold")).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        ctk.CTkLabel(
            left, text="Двойной клик — сразу запустить с текущим профилем",
            text_color="gray", font=("", 10),
        ).pack(anchor="w", padx=10, pady=(0, 4))

        self.device_list_frame = ctk.CTkScrollableFrame(left, width=305, height=380)
        self.device_list_frame.pack(fill="both", expand=True, padx=8, pady=4)

        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=4)
        ctk.CTkButton(btns, text="Обновить", command=self.refresh_devices).pack(
            side="left", expand=True, fill="x", padx=2
        )
        ctk.CTkButton(btns, text="Wi-Fi…", command=self._open_wifi_dialog).pack(
            side="left", expand=True, fill="x", padx=2
        )
        ctk.CTkButton(
            left, text="🔄 Переподключить сохранённые Wi-Fi",
            command=self._reconnect_known_devices,
        ).pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkButton(left, text="Перезапустить adb-сервер", command=self._restart_adb).pack(
            fill="x", padx=8, pady=(0, 10)
        )

        ctk.CTkLabel(left, text="Путь к adb / scrcpy", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(2, 2)
        )
        self.adb_path_var = tk.StringVar(value=self.config_data["settings"]["adb_path"])
        self.scrcpy_path_var = tk.StringVar(value=self.config_data["settings"]["scrcpy_path"])
        ctk.CTkEntry(left, textvariable=self.adb_path_var, placeholder_text="adb").pack(
            fill="x", padx=8, pady=2
        )
        ctk.CTkEntry(left, textvariable=self.scrcpy_path_var, placeholder_text="scrcpy").pack(
            fill="x", padx=8, pady=2
        )
        ctk.CTkButton(left, text="Сохранить пути", command=self._save_paths).pack(
            fill="x", padx=8, pady=(4, 8)
        )

    def _build_right_panel(self):
        right = ctk.CTkFrame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right.grid_rowconfigure(3, weight=1)
        right.grid_columnconfigure(0, weight=1)

        profile_bar = ctk.CTkFrame(right, fg_color="transparent")
        profile_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        ctk.CTkLabel(profile_bar, text="Профиль:").pack(side="left", padx=(0, 6))
        self.profile_combo = ctk.CTkOptionMenu(
            profile_bar,
            values=list(self.config_data["profiles"].keys()),
            variable=self.current_profile_name,
            command=self._on_profile_selected,
            width=220,
        )
        self.profile_combo.pack(side="left")
        ctk.CTkButton(profile_bar, text="New", width=70, command=self._new_profile).pack(
            side="left", padx=3
        )
        ctk.CTkButton(profile_bar, text="Clone", width=95, command=self._clone_profile).pack(
            side="left", padx=3
        )
        ctk.CTkButton(profile_bar, text="Del", width=70, command=self._delete_profile).pack(
            side="left", padx=3
        )
        ctk.CTkButton(
            profile_bar, text="Save", command=self._save_current_profile
        ).pack(side="right", padx=3)

        self.tabview = ctk.CTkTabview(right)
        self.tabview.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for tab in ("Видео", "Окно", "Управление", "HID / OTG", "Запись", "Прочее"):
            self.tabview.add(tab)
        self._build_video_tab(self.tabview.tab("Видео"))
        self._build_window_tab(self.tabview.tab("Окно"))
        self._build_control_tab(self.tabview.tab("Управление"))
        self._build_hid_tab(self.tabview.tab("HID / OTG"))
        self._build_record_tab(self.tabview.tab("Запись"))
        self._build_misc_tab(self.tabview.tab("Прочее"))

        ctk.CTkLabel(right, text="Лог:").grid(row=2, column=0, sticky="w", padx=8, pady=(6, 0))
        self.log_box = ctk.CTkTextbox(right, height=180)
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=8, pady=(2, 8))

    # ------------------------------------------------------------- вкладки
    def _add_str_field(self, parent, row, label, key, width=100, col_span=1):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=4)
        var = tk.StringVar()
        self.fields[key] = var
        ctk.CTkEntry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", padx=10, pady=4, columnspan=col_span
        )

    def _add_check_field(self, parent, row, label, key):
        var = tk.BooleanVar()
        self.fields[key] = var
        ctk.CTkCheckBox(parent, text=label, variable=var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=10, pady=4
        )

    def _build_video_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._add_str_field(tab, 0, "Max size, px (0 = без ограничений):", "max_size")
        self._add_str_field(tab, 1, "Bit-rate (напр. 8M):", "bit_rate")
        self._add_str_field(tab, 2, "Max FPS (0 = не ограничивать):", "max_fps")
        self._add_str_field(tab, 3, "Crop (W:H:X:Y):", "crop", width=160)
        ctk.CTkLabel(tab, text="Lock video orientation:").grid(
            row=4, column=0, sticky="w", padx=10, pady=4
        )
        var = tk.StringVar(value="unlocked")
        self.fields["lock_video_orientation"] = var
        ctk.CTkOptionMenu(tab, values=ORIENTATIONS, variable=var, width=140).grid(
            row=4, column=1, sticky="w", padx=10, pady=4
        )
        self._add_str_field(tab, 5, "Display ID (доп. дисплей):", "display_id")

    def _build_window_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._add_str_field(tab, 0, "Заголовок окна:", "window_title", width=220)
        self._add_check_field(tab, 1, "Fullscreen (-f)", "fullscreen")
        self._add_check_field(tab, 2, "Always on top", "always_on_top")
        self._add_check_field(tab, 3, "Без рамки окна", "window_borderless")
        self._add_str_field(tab, 4, "Позиция X:", "window_x", width=80)
        self._add_str_field(tab, 5, "Позиция Y:", "window_y", width=80)
        self._add_str_field(tab, 6, "Ширина:", "window_width", width=80)
        self._add_str_field(tab, 7, "Высота:", "window_height", width=80)

    def _build_control_tab(self, tab):
        self._add_check_field(tab, 0, "Только просмотр (--no-control)", "no_control")
        self._add_check_field(tab, 1, "Не давать спать пока подключено (-w)", "stay_awake")
        self._add_check_field(tab, 2, "Выключить экран при подключении (-S)", "turn_screen_off")
        self._add_check_field(tab, 3, "Показывать касания (-t)", "show_touches")
        self._add_check_field(tab, 4, "Отключить скринсейвер", "disable_screensaver")
        self._add_check_field(tab, 5, "Выключать питание при закрытии окна", "power_off_on_close")
        self._add_str_field(tab, 6, "Render driver (обычно не нужно):", "render_driver", width=140)

    def _build_hid_tab(self, tab):
        ctk.CTkLabel(
            tab, text="Работают только на Linux при подключении по USB",
            text_color="gray",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 8))
        self._add_check_field(tab, 1, "HID-клавиатура (-K)", "hid_keyboard")
        self._add_check_field(tab, 2, "HID-мышь (-M)", "hid_mouse")
        self._add_check_field(tab, 3, "OTG-режим (без зеркалирования)", "otg")

    def _build_record_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._add_check_field(tab, 0, "Записывать сессию", "record_enabled")
        ctk.CTkLabel(tab, text="Путь к файлу (пусто = авто в records_dir):").grid(
            row=1, column=0, sticky="w", padx=10, pady=4
        )
        var = tk.StringVar()
        self.fields["record_path"] = var
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=1, column=1, sticky="w", padx=10, pady=4)
        ctk.CTkEntry(row, textvariable=var, width=260).pack(side="left")
        ctk.CTkButton(row, text="…", width=30, command=lambda: self._pick_record_path(var)).pack(
            side="left", padx=4
        )
        ctk.CTkLabel(tab, text="Формат (mp4/mkv, пусто = по расширению):").grid(
            row=2, column=0, sticky="w", padx=10, pady=4
        )
        fmt_var = tk.StringVar()
        self.fields["record_format"] = fmt_var
        ctk.CTkOptionMenu(tab, values=["", "mp4", "mkv"], variable=fmt_var, width=100).grid(
            row=2, column=1, sticky="w", padx=10, pady=4
        )

    def _build_misc_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            tab, text="Явная цель (serial или ip:port; пусто = как выбрано слева):"
        ).grid(row=0, column=0, sticky="w", padx=10, pady=4)
        self._add_str_field(tab, 0, "", "target", width=220)
        ctk.CTkLabel(tab, text="Доп. аргументы scrcpy (сырые флаги):").grid(
            row=1, column=0, sticky="nw", padx=10, pady=4
        )
        self._add_str_field(tab, 1, "", "extra_args", width=320)

    # --------------------------------------------------------- устройства
    def refresh_devices(self, force_render: bool = True):
        """Обновляет список устройств в фоновом потоке, чтобы зависший adb
        не замораживал окно. Параллельные запросы схлопываются в один."""
        if self._refresh_busy:
            self._refresh_pending = True
            return
        self._refresh_busy = True
        adb_path = self.adb_path_var.get()  # Tk-переменные читаем только в главном потоке
        threading.Thread(
            target=self._refresh_worker, args=(adb_path, force_render), daemon=True
        ).start()

    def _refresh_worker(self, adb_path: str, force_render: bool):
        try:
            devices, error = adb_utils.list_devices(adb_path), None
        except adb_utils.AdbError as exc:
            devices, error = [], str(exc)
        except Exception as exc:
            devices, error = [], repr(exc)
        self.call_in_ui(self._apply_refresh, devices, error, force_render)

    def _apply_refresh(self, devices, error, force_render: bool):
        self._refresh_busy = False
        if error and error != self._last_adb_error:
            self._log(f"ОШИБКА adb: {error}")  # одну и ту же ошибку не повторяем каждые 3 с
        self._last_adb_error = error

        def signature(items):
            return [(d.serial, d.state, d.model) for d in items]

        changed = signature(devices) != signature(self.devices)
        self.devices = devices
        if changed or force_render:
            self._render_device_list()

        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh_devices()

    def _schedule_poll(self):
        interval = self.config_data["settings"].get("poll_interval_ms", 3000)
        self.after(interval, self._poll_devices)

    def _poll_devices(self):
        # Периодический опрос перерисовывает список только при изменениях.
        self.refresh_devices(force_render=False)
        self._schedule_poll()

    def _render_device_list(self):
        for widget in self.device_list_frame.winfo_children():
            widget.destroy()

        if not self.devices:
            ctk.CTkLabel(self.device_list_frame, text="Устройства не найдены").pack(
                anchor="w", padx=6, pady=6
            )
            return

        known_keys = {d.serial for d in self.devices}
        if self.selected_target.get() not in known_keys:
            self.selected_target.set(self.devices[0].serial)

        for device in self.devices:
            running = self.runner.is_running(device.serial)
            status_text = f"{device.display_name} — {device.state}"
            if running:
                status_text += " • запущен"

            row = ctk.CTkFrame(self.device_list_frame, fg_color=("gray86", "gray17"))
            row.pack(fill="x", pady=3, padx=2)

            radio = ctk.CTkRadioButton(
                row, text="", variable=self.selected_target, value=device.serial, width=18,
            )
            radio.pack(side="left", padx=(6, 0), pady=6)

            color = "#43a047" if running else ("#fdd835" if device.state != "device" else None)
            label = ctk.CTkLabel(row, text=status_text, text_color=color, anchor="w")
            label.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=6)
            label.bind("<Double-Button-1>", lambda _e, s=device.serial: self._start_specific(s))

            ctk.CTkButton(
                row, text="■", width=28, fg_color="#b71c1c", hover_color="#7f0000",
                state=("normal" if running else "disabled"),
                command=lambda s=device.serial: self._stop_specific(s),
            ).pack(side="right", padx=(3, 6), pady=6)
            ctk.CTkButton(
                row, text="▶", width=28, fg_color="#2e7d32", hover_color="#1b5e20",
                state=("disabled" if running else "normal"),
                command=lambda s=device.serial: self._start_specific(s),
            ).pack(side="right", padx=(3, 0), pady=6)
            ctk.CTkButton(
                row, text="⧉", width=28,
                command=lambda s=device.serial: self._copy_to_clipboard(s),
            ).pack(side="right", padx=(3, 0), pady=6)

    # --------------------------------------------------------- профили
    def _collect_profile_from_form(self) -> dict:
        profile = cfg.new_profile()
        for key, var in self.fields.items():
            profile[key] = var.get()
        return profile

    def _load_profile_into_form(self, name: str):
        profile = self.config_data["profiles"].get(name, cfg.new_profile())
        for key, var in self.fields.items():
            var.set(profile.get(key, cfg.DEFAULT_PROFILE.get(key, "")))

    def _on_profile_selected(self, name: str):
        self._load_profile_into_form(name)
        self.config_data["last_used_profile"] = name
        cfg.save_config(self.config_data)

    def _save_current_profile(self):
        name = self.current_profile_name.get()
        self.config_data["profiles"][name] = self._collect_profile_from_form()
        cfg.save_config(self.config_data)
        self._log(f"Профиль «{name}» сохранён")

    def _refresh_profile_combo(self, select: str):
        self.profile_combo.configure(values=list(self.config_data["profiles"].keys()))
        self.current_profile_name.set(select)
        self._load_profile_into_form(select)

    def _new_profile(self):
        name = _ask_text(self, "Новый профиль", "Имя профиля:")
        if name is None:
            return
        name = name.strip()
        if not name or name in self.config_data["profiles"]:
            messagebox.showwarning("Внимание", "Введите уникальное имя профиля")
            return
        self.config_data["profiles"][name] = cfg.new_profile()
        cfg.save_config(self.config_data)
        self._refresh_profile_combo(name)

    def _clone_profile(self):
        source = self.current_profile_name.get()
        name = _ask_text(self, "Клонировать профиль", "Имя нового профиля:",
                          default=f"{source} (копия)")
        if name is None:
            return
        name = name.strip()
        if not name or name in self.config_data["profiles"]:
            messagebox.showwarning("Внимание", "Введите уникальное имя профиля")
            return
        self.config_data["profiles"][name] = copy.deepcopy(self.config_data["profiles"][source])
        cfg.save_config(self.config_data)
        self._refresh_profile_combo(name)

    def _delete_profile(self):
        name = self.current_profile_name.get()
        if len(self.config_data["profiles"]) <= 1:
            messagebox.showwarning("Внимание", "Должен остаться хотя бы один профиль")
            return
        if not messagebox.askyesno("Удалить профиль", f"Удалить профиль «{name}»?"):
            return
        del self.config_data["profiles"][name]
        cfg.save_config(self.config_data)
        self._refresh_profile_combo(next(iter(self.config_data["profiles"])))

    # --------------------------------------------------------- запуск
    def _start_selected(self, force_target: "str | None" = None):
        profile = self._collect_profile_from_form()
        explicit_target = (profile.get("target") or "").strip()

        if force_target:
            # Двойной клик по конкретному устройству — это явное намерение
            # пользователя, оно важнее сохранённого в профиле поля target.
            target = force_target
            if explicit_target and explicit_target != force_target:
                self._log(
                    f"Профиль «{self.current_profile_name.get()}» содержит явную "
                    f"цель «{explicit_target}» (вкладка «Прочее») — для этого запуска "
                    f"она проигнорирована, запускаю именно {force_target}."
                )
        else:
            target = explicit_target or self.selected_target.get()
            if explicit_target and self.selected_target.get() and explicit_target != self.selected_target.get():
                self._log(
                    f"Внимание: в профиле «{self.current_profile_name.get()}» задана "
                    f"явная цель «{explicit_target}» (вкладка «Прочее») — она приоритетнее "
                    f"выбора в списке слева. Очистите поле, если хотите запускать то, "
                    f"что выбрано radio-кнопкой."
                )

        if not target:
            messagebox.showwarning("Внимание", "Сначала выберите устройство")
            return

        profile_name = self.current_profile_name.get()
        records_dir = self.config_data["settings"]["records_dir"]
        self.runner.scrcpy_path = self.scrcpy_path_var.get().strip() or "scrcpy"
        try:
            self.runner.start(
                key=target,
                profile=profile,
                profile_name=profile_name,
                records_dir=records_dir,
                on_output=lambda line: self.call_in_ui(self._log, line),
                on_exit=lambda key, code: self.call_in_ui(self._on_session_exit, key, code),
            )
            self._log(f">>> Запуск scrcpy для {target} (профиль «{profile_name}»)")
        except RuntimeError as exc:
            messagebox.showwarning("Внимание", str(exc))
        self._render_device_list()

    def _start_specific(self, serial: str):
        self.selected_target.set(serial)
        self._start_selected(force_target=serial)

    def _stop_specific(self, serial: str):
        # terminate()+wait() могут ждать до 5 с — делаем это вне главного потока.
        def worker():
            self.runner.stop(serial)
            self.call_in_ui(self._log, f">>> scrcpy для {serial} остановлен")
            self.call_in_ui(self._render_device_list)

        threading.Thread(target=worker, daemon=True).start()

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # без этого буфер иногда не фиксируется в X11
        self._log(f"Скопировано в буфер обмена: {text}")

    def _on_session_exit(self, key: str, code: int):
        self._log(f"--- scrcpy для {key} завершился (код {code}) ---")
        self._render_device_list()

    # --------------------------------------------------------- Wi-Fi
    def _open_wifi_dialog(self):
        WifiDialog(self)

    def _reconnect_known_devices(self):
        known = list(self.config_data.get("known_network_devices", []))
        if not known:
            self._log("Список сохранённых Wi-Fi устройств пуст — сначала подключитесь хотя бы раз через «Wi-Fi…».")
            return

        self._log(f">>> Переподключаю {len(known)} сохранённых Wi-Fi устройств…")
        adb_path = self.adb_path_var.get()

        def worker():
            for ip_port in known:
                try:
                    output = adb_utils.connect_tcpip(adb_path, ip_port)
                    self.call_in_ui(self._log, output.strip())
                except adb_utils.AdbError as exc:
                    self.call_in_ui(self._log, f"ОШИБКА ({ip_port}): {exc}")
            self.call_in_ui(self.refresh_devices)

        threading.Thread(target=worker, daemon=True).start()

    def _restart_adb(self):
        adb_path = self.adb_path_var.get()

        def worker():
            try:
                adb_utils.restart_server(adb_path)
                self.call_in_ui(self._log, "adb-сервер перезапущен")
            except adb_utils.AdbError as exc:
                self.call_in_ui(self._log, f"ОШИБКА: {exc}")
            self.call_in_ui(self.refresh_devices)

        threading.Thread(target=worker, daemon=True).start()

    def _save_paths(self):
        self.config_data["settings"]["adb_path"] = self.adb_path_var.get().strip() or "adb"
        self.config_data["settings"]["scrcpy_path"] = self.scrcpy_path_var.get().strip() or "scrcpy"
        self.runner.scrcpy_path = self.config_data["settings"]["scrcpy_path"]
        cfg.save_config(self.config_data)
        self._log("Пути к adb/scrcpy сохранены")

    def _pick_record_path(self, var: tk.StringVar):
        path = filedialog.asksaveasfilename(defaultextension=".mp4")
        if path:
            var.set(path)

    # --------------------------------------------------------- утилиты
    def _log(self, message: str):
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")

    def on_close(self):
        if self.runner.running_keys():
            if not messagebox.askyesno(
                "Есть активные сессии",
                "Есть запущенные сессии scrcpy. Остановить их и выйти?",
            ):
                return
            self.runner.stop_all()
        self._save_current_profile()
        self.destroy()


def _ask_text(parent, title: str, prompt: str, default: str = "") -> "str | None":
    dialog = ctk.CTkInputDialog(text=prompt, title=title)
    if default:
        try:
            dialog._entry.insert(0, default)  # CTkInputDialog не даёт задать default иначе
        except Exception:
            pass
    return dialog.get_input()


class WifiDialog(ctk.CTkToplevel):
    """Подключение по TCP/IP, сканирование локальной сети, беспроводное
    сопряжение (Android 11+) и список уже известных Wi-Fi устройств."""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("Wi-Fi подключение")
        self.geometry("480x640")
        self.grab_set()

        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # --- включить TCP/IP на уже подключённом по USB устройстве ---
        ctk.CTkLabel(
            scroll, text="Уже подключён по USB → включить TCP/IP", font=("", 12, "bold")
        ).pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkButton(
            scroll, text="Включить TCP/IP на 5555 для выбранного устройства",
            command=self._enable_tcpip,
        ).pack(fill="x", padx=8, pady=4)

        # --- подключение по известному адресу вручную ---
        ctk.CTkLabel(scroll, text="Подключиться по IP:PORT", font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(16, 2)
        )
        self.connect_entry = ctk.CTkEntry(scroll, placeholder_text="192.168.1.50:5555")
        self.connect_entry.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(scroll, text="Подключиться", command=self._connect).pack(
            fill="x", padx=8, pady=4
        )

        # --- сканирование локальной сети ---
        ctk.CTkLabel(scroll, text="Найти устройства в сети", font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(16, 2)
        )
        ctk.CTkLabel(
            scroll,
            text="Проверяет порт 5555 (adb tcpip) на всех хостах локальной подсети. "
                 "Двойной клик по найденному адресу — подключиться и запомнить.",
            text_color="gray", wraplength=440, justify="left",
        ).pack(anchor="w", padx=8)

        scan_row = ctk.CTkFrame(scroll, fg_color="transparent")
        scan_row.pack(fill="x", padx=8, pady=4)
        self.scan_button = ctk.CTkButton(scan_row, text="Сканировать", command=self._start_scan)
        self.scan_button.pack(side="left")
        self.scan_status_label = ctk.CTkLabel(scan_row, text="")
        self.scan_status_label.pack(side="left", padx=10)

        self.scan_results_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.scan_results_frame.pack(fill="x", padx=8, pady=(0, 4))

        # --- сохранённые Wi-Fi устройства (переживают перезапуск adb-сервера) ---
        known_header = ctk.CTkFrame(scroll, fg_color="transparent")
        known_header.pack(fill="x", padx=8, pady=(16, 2))
        ctk.CTkLabel(known_header, text="Сохранённые Wi-Fi устройства", font=("", 12, "bold")).pack(
            side="left"
        )
        ctk.CTkButton(
            known_header, text="Подключить все", width=110,
            command=lambda: self.app._reconnect_known_devices(),
        ).pack(side="right")
        ctk.CTkLabel(
            scroll,
            text="Двойной клик по адресу — подключиться заново (например, после "
                 "«Перезапустить adb-сервер», когда список устройств пуст).",
            text_color="gray", wraplength=440, justify="left",
        ).pack(anchor="w", padx=8)

        self.known_devices_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.known_devices_frame.pack(fill="x", padx=8, pady=(2, 4))
        self._render_known_devices()

        # --- беспроводное сопряжение Android 11+ ---
        ctk.CTkLabel(
            scroll, text="Беспроводное сопряжение (Android 11+, без USB)", font=("", 12, "bold"),
        ).pack(anchor="w", padx=8, pady=(16, 2))
        ctk.CTkLabel(
            scroll,
            text="Настройки → Для разработчиков → Отладка по Wi-Fi → Сопряжение по коду",
            text_color="gray", wraplength=440,
        ).pack(anchor="w", padx=8)
        self.pair_addr_entry = ctk.CTkEntry(scroll, placeholder_text="192.168.1.50:41235 (адрес пары)")
        self.pair_addr_entry.pack(fill="x", padx=8, pady=(6, 2))
        self.pair_code_entry = ctk.CTkEntry(scroll, placeholder_text="123456 (код)")
        self.pair_code_entry.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(scroll, text="Сопрячь", command=self._pair).pack(
            fill="x", padx=8, pady=(4, 12)
        )

    # ------------------------------------------------------------ USB → TCP/IP
    def _enable_tcpip(self):
        target = self.app.selected_target.get()
        if not target:
            messagebox.showwarning("Внимание", "Сначала выберите USB-устройство слева")
            return
        if ":" in target:
            messagebox.showwarning(
                "Внимание",
                "Выбранное устройство уже подключено по Wi-Fi (это ip:port, а не "
                "USB-serial). Повторное включение TCP/IP перезапускает adbd прямо "
                "через это же соединение и обрывает его.\n\n"
                "Если связь всё же пропала — попробуйте «Подключиться по IP:PORT» "
                "ниже с тем же адресом, либо переподключите телефон по USB и "
                "включите TCP/IP оттуда.",
            )
            return
        try:
            adb_utils.enable_tcpip(self.app.adb_path_var.get(), target)
            self.app._log(f"TCP/IP включён для {target}.")
        except adb_utils.AdbError as exc:
            self.app._log(f"ОШИБКА: {exc}")
            return

        ip = adb_utils.get_ip_via_usb(self.app.adb_path_var.get(), target)
        if ip:
            self.connect_entry.delete(0, "end")
            self.connect_entry.insert(0, f"{ip}:5555")
            self.app._log(f"Определён IP устройства: {ip}. Проверьте поле ниже и нажмите «Подключиться».")
        else:
            self.app._log("Не удалось автоматически определить IP устройства — введите его вручную ниже.")

    # ------------------------------------------------------------ подключение
    def _connect(self):
        ip_port = self.connect_entry.get().strip()
        if ip_port:
            self._connect_and_remember(ip_port)

    def _connect_and_remember(self, ip_port: str):
        """Подключается по ip:port и запоминает адрес в списке известных
        Wi-Fi устройств, чтобы он не терялся после adb kill-server."""
        # Адрес запоминаем сразу; сам connect (до 10 с) идёт в фоне.
        cfg.add_known_device(self.app.config_data, ip_port)
        cfg.save_config(self.app.config_data)
        self._render_known_devices()
        adb_path = self.app.adb_path_var.get()

        def worker():
            try:
                output = adb_utils.connect_tcpip(adb_path, ip_port)
                self.app.call_in_ui(self.app._log, output.strip())
            except adb_utils.AdbError as exc:
                self.app.call_in_ui(self.app._log, f"ОШИБКА: {exc}")
            self.app.call_in_ui(self.app.refresh_devices)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ сканирование
    def _start_scan(self):
        self.scan_button.configure(state="disabled")
        self.scan_status_label.configure(text="Сканирую сеть…")
        for widget in self.scan_results_frame.winfo_children():
            widget.destroy()

        adb_path = self.app.adb_path_var.get()

        def worker():
            try:
                found = adb_utils.scan_network_for_adb(port=5555, timeout=0.3)
            except Exception as exc:
                found = []
                self.app.call_in_ui(self.app._log, f"Ошибка сканирования сети: {exc}")
            self.app.call_in_ui(self._on_scan_done, found)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, found: "list[str]"):
        self.scan_button.configure(state="normal")
        if not found:
            self.scan_status_label.configure(
                text="Ничего не найдено (порт 5555 нигде не открыт в локальной сети)"
            )
            return
        self.scan_status_label.configure(text=f"Найдено адресов: {len(found)}")
        for ip in found:
            ip_port = f"{ip}:5555"
            row = ctk.CTkFrame(self.scan_results_frame, fg_color=("gray86", "gray17"))
            row.pack(fill="x", pady=2)
            label = ctk.CTkLabel(row, text=ip_port, anchor="w")
            label.pack(side="left", fill="x", expand=True, padx=6, pady=4)
            label.bind(
                "<Double-Button-1>", lambda _e, addr=ip_port: self._connect_and_remember(addr)
            )
            ctk.CTkButton(
                row, text="Подключить", width=90,
                command=lambda addr=ip_port: self._connect_and_remember(addr),
            ).pack(side="right", padx=6, pady=4)

    # ------------------------------------------------------ сохранённые адреса
    def _render_known_devices(self):
        for widget in self.known_devices_frame.winfo_children():
            widget.destroy()

        known = self.app.config_data.get("known_network_devices", [])
        if not known:
            ctk.CTkLabel(self.known_devices_frame, text="Пока пусто", text_color="gray").pack(
                anchor="w", padx=4, pady=2
            )
            return

        for ip_port in known:
            row = ctk.CTkFrame(self.known_devices_frame, fg_color=("gray86", "gray17"))
            row.pack(fill="x", pady=2)
            label = ctk.CTkLabel(row, text=ip_port, anchor="w")
            label.pack(side="left", fill="x", expand=True, padx=6, pady=4)
            label.bind(
                "<Double-Button-1>", lambda _e, addr=ip_port: self._connect_and_remember(addr)
            )
            ctk.CTkButton(
                row, text="Подключить", width=90,
                command=lambda addr=ip_port: self._connect_and_remember(addr),
            ).pack(side="right", padx=(0, 4), pady=4)
            ctk.CTkButton(
                row, text="✕", width=28, fg_color="#b71c1c", hover_color="#7f0000",
                command=lambda addr=ip_port: self._forget_known(addr),
            ).pack(side="right", padx=(0, 4), pady=4)

    def _forget_known(self, ip_port: str):
        cfg.remove_known_device(self.app.config_data, ip_port)
        cfg.save_config(self.app.config_data)
        self._render_known_devices()

    # ------------------------------------------------------------ сопряжение
    def _pair(self):
        addr = self.pair_addr_entry.get().strip()
        code = self.pair_code_entry.get().strip()
        if not addr or not code:
            messagebox.showwarning("Внимание", "Заполните адрес и код сопряжения")
            return
        try:
            output = adb_utils.pair(self.app.adb_path_var.get(), addr, code)
            self.app._log(output.strip())
        except adb_utils.AdbError as exc:
            self.app._log(f"ОШИБКА: {exc}")
        self.app.refresh_devices()
