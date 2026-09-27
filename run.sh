#!/usr/bin/env bash
# Лаунчер scrcpy master.
# Кладите этот файл ПРЯМО В ПАПКУ ПРОЕКТА (рядом с main.py) — он сам
# определяет свою директорию и не зависит от того, откуда его запустили.

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DIR"

LOG_FILE="$DIR/run.log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== Запуск $(date '+%Y-%m-%d %H:%M:%S') ==="

# Если зависимости ставились в отдельное виртуальное окружение — подхватываем.
if [ -f "venv/bin/activate" ]; then
    echo "Активирую venv/"
    source "venv/bin/activate"
elif [ -f ".venv/bin/activate" ]; then
    echo "Активирую .venv/"
    source ".venv/bin/activate"
fi

echo "python3: $(command -v python3)"
echo "PATH: $PATH"

python3 main.py
STATUS=$?
echo "=== Завершено с кодом $STATUS ==="
exit $STATUS
