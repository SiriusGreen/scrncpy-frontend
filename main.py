#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа: scrcpy master — GUI-лаунчер для scrcpy на CustomTkinter."""
from gui import App


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
