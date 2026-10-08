# GameBots

Telegram-боты: **Bluff_Bot** (Блеф) и **Mafia_Bot** (Мафия) на aiogram 3.
Оба бота используют общий модуль `shared_db.py` (общий баланс, титулы, статистика).

## Установка
    pip install -r requirements.txt

## Токены
1. Скопируй `.env.example` в `.env` (в корне проекта, рядом с `shared_db.py`).
2. Впиши токены от @BotFather: `BLUFF_BOT_TOKEN=...` и `MAFIA_BOT_TOKEN=...`.

## Запуск
    python Bluff_Bot/main.py
    python Mafia_Bot/main.py

## Важно
- Базы `shared.db`, `database.db`, `mafia.db` и логи в git не попадают (в них данные игроков).
  Они создаются при первом запуске; чтобы сохранить текущие балансы, держи свои файлы `.db` на сервере/ПК.
- Токены нельзя хранить в коде и коммитить в git.
