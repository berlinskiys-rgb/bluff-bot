"""Чтение токена бота: сначала из переменной окружения, потом из файла .env.

Файл .env НЕ должен попадать в git (он в .gitignore).
Формат .env:  ИМЯ=значение  (по одной паре на строку)
"""
import os


def _read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def get_bot_token(name, bot_dir):
    token = os.getenv(name)
    if token:
        return token
    root_dir = os.path.dirname(os.path.abspath(__file__))
    for folder in (bot_dir, root_dir):
        token = _read_env_file(os.path.join(folder, ".env")).get(name)
        if token:
            return token
    raise SystemExit(
        f"Не найден токен {name}. Создай файл .env рядом с ботом "
        f"(см. .env.example) или задай переменную окружения {name}."
    )
