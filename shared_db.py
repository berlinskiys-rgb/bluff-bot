# shared_db.py — общий модуль для всех ботов (Мафия, Блеф и т.д.)
# Все данные пользователя (баланс, титулы, статистика) хранятся здесь.
# Позже легко перейти на PostgreSQL — меняется только начинка.

import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

SHARED_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared.db")

_db = sqlite3.connect(SHARED_DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db_lock = threading.Lock()


# ============ ИНИЦИАЛИЗАЦИЯ ============
def init_shared_db():
    with _db_lock:
        _db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                tg_username TEXT,
                balance INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                equipped_card_skin TEXT DEFAULT 'classic',
                created_at INTEGER
            )
        """)
        existing = {row[1] for row in _db.execute("PRAGMA table_info(users)").fetchall()}
        if "equipped_card_skin" not in existing:
            _db.execute("ALTER TABLE users ADD COLUMN equipped_card_skin TEXT DEFAULT 'classic'")
        _db.execute("""
            CREATE TABLE IF NOT EXISTS titles (
                title_id TEXT PRIMARY KEY,
                name TEXT,
                icon TEXT,
                game TEXT,
                min_games INTEGER DEFAULT 0,
                bonus INTEGER DEFAULT 0,
                description TEXT
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS user_titles (
                user_id INTEGER,
                title_id TEXT,
                awarded_at INTEGER,
                PRIMARY KEY (user_id, title_id)
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS user_equipped_titles (
                user_id INTEGER,
                game TEXT,
                title_id TEXT,
                PRIMARY KEY (user_id, game)
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                reason TEXT,
                game TEXT,
                created_at INTEGER
            )
        """)
        _db.execute("""
            CREATE TABLE IF NOT EXISTS game_stats (
                user_id INTEGER,
                game TEXT,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, game)
            )
        """)

        # Дефолтные титулы (Мафия)
        mafia_titles = [
            ("mf_rookie",    "Новичок",             "🆕", "mafia", 0,   0,    "Стартовый титул"),
            ("mf_suspect",   "Подозреваемый",       "👀", "mafia", 5,   25,   "Первые шаги"),
            ("mf_citizen",   "Уважаемый горожанин", "🏘️", "mafia", 15,  50,   "Тебе доверяют"),
            ("mf_authority", "Авторитет",           "🎩", "mafia", 30,  100,  "Свой в доску"),
            ("mf_sheriff",   "Шериф города",        "🕵️", "mafia", 50,  200,  "Знает всё о людях"),
            ("mf_consig",    "Консильери",          "🤵", "mafia", 80,  350,  "Правая рука"),
            ("mf_don",       "Дон",                 "👑", "mafia", 120, 600,  "Глава семьи"),
            ("mf_godfather", "Крёстный отец",       "🎩", "mafia", 200, 1000, "Легенда мафии"),
            ("mf_legend",    "Легенда города",      "💀", "mafia", 350, 2000, "Бессмертный"),
        ]
        # Дефолтные титулы (Блеф)
        bluff_titles = [
            ("bf_rookie",   "Новичок Блефа",     "🎴", "bluff", 1,   10,   "Первая игра"),
            ("bf_master",   "Мастер Блефа",      "🎯", "bluff", 10,  100,  "Уверенный игрок"),
            ("bf_legend",   "Легенда Блефа",     "🏆", "bluff", 50,  500,  "О тебе говорят"),
            ("bf_emperor",  "Император Блефа",   "👑", "bluff", 100, 1000, "Правитель стола"),
            ("bf_lord",     "Владыка Обмана",    "🎭", "bluff", 200, 2000, "Мастер интриг"),
            ("bf_shadow",   "Тень Блефа",        "💀", "bluff", 350, 3500, "Тебя никто не видит"),
            ("bf_absolute", "Абсолютный Блеф",   "🌌", "bluff", 500, 5000, "Ты стал самим блефом"),
        ]
        all_titles = mafia_titles + bluff_titles
        for t in all_titles:
            _db.execute(
                "INSERT OR IGNORE INTO titles (title_id, name, icon, game, min_games, bonus, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                t,
            )
        # Чистим старые титулы Блефа, если остались
        old_bluff_titles = ["bf_magnate"]
        for old_id in old_bluff_titles:
            _db.execute("DELETE FROM titles WHERE title_id = ?", (old_id,))
            _db.execute("DELETE FROM user_titles WHERE title_id = ?", (old_id,))
            _db.execute("DELETE FROM user_equipped_titles WHERE title_id = ?", (old_id,))
        _db.commit()


# Общий справочник скинов карточки профиля
# source — в каком боте скин продаётся (для фильтра в магазине)
PROFILE_SKINS_INFO = {
    "classic": {
        "name": "🎩 Классическая",
        "source": "any",
        "bg": "linear-gradient(135deg, #0f172a 0%, #1e1b4b 45%, #4c1d95 100%)",
        "accent": "#a78bfa",
        "accent2": "#38bdf8",
        "card_bg": "linear-gradient(180deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04))",
        "card_border": "rgba(255,255,255,0.18)",
    },
    "gold": {
        "name": "🥇 Золотой",
        "source": "bluff",
        "bg": "linear-gradient(135deg, #1a1206 0%, #4a2f0a 45%, #b8860b 100%)",
        "accent": "#fbbf24",
        "accent2": "#fde68a",
        "card_bg": "linear-gradient(180deg, rgba(251,191,36,0.18), rgba(120,53,15,0.35))",
        "card_border": "rgba(251,191,36,0.55)",
    },
    "cyber": {
        "name": "⚡ Киберпанк",
        "source": "bluff",
        "bg": "linear-gradient(135deg, #0b001f 0%, #2a0a5e 45%, #00e5ff 100%)",
        "accent": "#22d3ee",
        "accent2": "#a855f7",
        "card_bg": "linear-gradient(180deg, rgba(168,85,247,0.25), rgba(34,211,238,0.10))",
        "card_border": "rgba(34,211,238,0.55)",
    },
    "street": {
        "name": "🌃 Уличный Неон",
        "source": "bluff",
        "bg": "linear-gradient(135deg, #050b2e 0%, #111a52 40%, #ff2d95 100%)",
        "accent": "#ff2d95",
        "accent2": "#38bdf8",
        "card_bg": "linear-gradient(180deg, rgba(255,45,149,0.20), rgba(56,189,248,0.10))",
        "card_border": "rgba(255,45,149,0.55)",
    },
    "don": {
        "name": "👑 Дон",
        "source": "mafia",
        "bg": "linear-gradient(135deg, #0f0f0f 0%, #2a0a0a 45%, #8b0000 100%)",
        "accent": "#d4af37",
        "accent2": "#ff4444",
        "card_bg": "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))",
        "card_border": "rgba(212,175,55,0.55)",
    },
    "rose": {
        "name": "🌹 Чёрная роза",
        "source": "mafia",
        "bg": "linear-gradient(135deg, #0a0014 0%, #2a0033 45%, #800060 100%)",
        "accent": "#ec4899",
        "accent2": "#f472b6",
        "card_bg": "linear-gradient(180deg, rgba(236,72,153,0.20), rgba(128,0,96,0.35))",
        "card_border": "rgba(236,72,153,0.55)",
    },
    "blood": {
        "name": "🩸 Кровавая луна",
        "source": "mafia",
        "bg": "linear-gradient(135deg, #1a0000 0%, #4a0000 45%, #ff0000 100%)",
        "accent": "#ff4444",
        "accent2": "#ff8888",
        "card_bg": "linear-gradient(180deg, rgba(255,68,68,0.20), rgba(120,0,0,0.35))",
        "card_border": "rgba(255,68,68,0.55)",
    },
}


def get_profile_skin_info(skin_id: str) -> dict:
    return PROFILE_SKINS_INFO.get(skin_id) or PROFILE_SKINS_INFO["classic"]


def get_profile_skins_by_source(source: str) -> list:
    result = []
    for skin_id, info in PROFILE_SKINS_INFO.items():
        if info["source"] in (source, "any"):
            result.append({"skin_id": skin_id, **info})
    return result


# ============ ПОЛЬЗОВАТЕЛИ ============
def ensure_user(user_id: int, username: str = "Игрок", tg_username: Optional[str] = None):
    name = username or "Игрок"
    with _db_lock:
        row = _db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            _db.execute(
                "INSERT INTO users (user_id, username, tg_username, balance, games_played, wins, created_at) "
                "VALUES (?, ?, ?, 0, 0, 0, ?)",
                (user_id, name, tg_username, int(time.time())),
            )
        else:
            _db.execute(
                "UPDATE users SET username = ?, tg_username = COALESCE(?, tg_username) WHERE user_id = ?",
                (name, tg_username, user_id),
            )
        _db.commit()


def get_user(user_id: int):
    with _db_lock:
        return _db.execute(
            "SELECT user_id, username, tg_username, balance, games_played, wins, equipped_card_skin FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


# ============ БАЛАНС ============
def add_balance(user_id: int, amount: int, reason: str = "", game: str = ""):
    ensure_user(user_id)
    with _db_lock:
        _db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        _db.execute(
            "INSERT INTO transactions (user_id, amount, reason, game, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, reason, game, int(time.time())),
        )
        _db.commit()


def spend_balance(user_id: int, amount: int, reason: str = "", game: str = "") -> bool:
    ensure_user(user_id)
    with _db_lock:
        row = _db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["balance"] >= amount:
            _db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
            _db.execute(
                "INSERT INTO transactions (user_id, amount, reason, game, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, -amount, reason, game, int(time.time())),
            )
            _db.commit()
            return True
        return False


def get_balance(user_id: int) -> int:
    row = get_user(user_id)
    return row["balance"] if row else 0


def get_equipped_card_skin(user_id: int) -> str:
    with _db_lock:
        row = _db.execute(
            "SELECT equipped_card_skin FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row and row["equipped_card_skin"]:
        return row["equipped_card_skin"]
    return "classic"


def set_equipped_card_skin(user_id: int, skin_id: str) -> bool:
    if skin_id not in PROFILE_SKINS_INFO:
        return False
    ensure_user(user_id)
    with _db_lock:
        _db.execute(
            "UPDATE users SET equipped_card_skin = ? WHERE user_id = ?",
            (skin_id, user_id),
        )
        _db.commit()
    return True


def unequip_card_skin(user_id: int) -> bool:
    return set_equipped_card_skin(user_id, "classic")


# ============ СТАТИСТИКА ============
def record_game(user_id: int, game: str, won: bool):
    ensure_user(user_id)
    with _db_lock:
        # общая статистика
        _db.execute(
            "UPDATE users SET games_played = games_played + 1 WHERE user_id = ?",
            (user_id,),
        )
        if won:
            _db.execute("UPDATE users SET wins = wins + 1 WHERE user_id = ?", (user_id,))
        # статистика по игре
        row = _db.execute(
            "SELECT user_id FROM game_stats WHERE user_id = ? AND game = ?",
            (user_id, game),
        ).fetchone()
        if not row:
            _db.execute(
                "INSERT INTO game_stats (user_id, game, games_played, wins) VALUES (?, ?, 1, ?)",
                (user_id, game, 1 if won else 0),
            )
        else:
            _db.execute(
                "UPDATE game_stats SET games_played = games_played + 1 WHERE user_id = ? AND game = ?",
                (user_id, game),
            )
            if won:
                _db.execute(
                    "UPDATE game_stats SET wins = wins + 1 WHERE user_id = ? AND game = ?",
                    (user_id, game),
                )
        _db.commit()


def get_game_stats(user_id: int) -> List:
    with _db_lock:
        return _db.execute(
            "SELECT game, games_played, wins FROM game_stats WHERE user_id = ?",
            (user_id,),
        ).fetchall()


# ============ ТИТУЛЫ ============
def get_all_titles() -> List:
    with _db_lock:
        return _db.execute("SELECT * FROM titles ORDER BY game, min_games ASC").fetchall()


def get_titles_by_game(game: str) -> List:
    with _db_lock:
        return _db.execute(
            "SELECT * FROM titles WHERE game = ? ORDER BY min_games ASC",
            (game,),
        ).fetchall()


def get_title(title_id: str):
    with _db_lock:
        return _db.execute("SELECT * FROM titles WHERE title_id = ?", (title_id,)).fetchone()


def get_user_titles(user_id: int) -> Set[str]:
    with _db_lock:
        rows = _db.execute(
            "SELECT title_id FROM user_titles WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {r["title_id"] for r in rows}


def award_title(user_id: int, title_id: str) -> bool:
    with _db_lock:
        existing = _db.execute(
            "SELECT 1 FROM user_titles WHERE user_id = ? AND title_id = ?",
            (user_id, title_id),
        ).fetchone()
        if existing:
            return False
        _db.execute(
            "INSERT INTO user_titles (user_id, title_id, awarded_at) VALUES (?, ?, ?)",
            (user_id, title_id, int(time.time())),
        )
        _db.commit()
        return True


def check_and_award_titles(user_id: int, game: str) -> List:
    """Проверяет и вручает новые титулы. Возвращает список вручённых."""
    ensure_user(user_id)
    stats_row = None
    with _db_lock:
        stats_row = _db.execute(
            "SELECT games_played FROM game_stats WHERE user_id = ? AND game = ?",
            (user_id, game),
        ).fetchone()
    if not stats_row:
        return []
    games_played = stats_row["games_played"]

    awarded_set = get_user_titles(user_id)
    titles = get_titles_by_game(game)
    new_titles = []
    for t in titles:
        if t["title_id"] in awarded_set:
            continue
        if games_played >= t["min_games"]:
            if award_title(user_id, t["title_id"]):
                new_titles.append(t)
                if t["bonus"] and t["bonus"] > 0:
                    add_balance(user_id, t["bonus"], f"title:{t['title_id']}", game)
    return new_titles


# ============ НАДЕТЫЕ ТИТУЛЫ (по одному от каждой игры) ============
def get_equipped_titles(user_id: int) -> List:
    with _db_lock:
        rows = _db.execute(
            """
            SELECT e.game, e.title_id, t.name, t.icon
            FROM user_equipped_titles e
            JOIN titles t ON t.title_id = e.title_id
            WHERE e.user_id = ?
            ORDER BY e.game
            """,
            (user_id,),
        ).fetchall()
    return rows


def equip_title(user_id: int, game: str, title_id: str) -> bool:
    """Надевает титул в игре. Максимум 1 на игру."""
    owned = get_user_titles(user_id)
    if title_id not in owned:
        return False
    with _db_lock:
        existing = _db.execute(
            "SELECT 1 FROM user_equipped_titles WHERE user_id = ? AND game = ?",
            (user_id, game),
        ).fetchone()
        if existing:
            _db.execute(
                "UPDATE user_equipped_titles SET title_id = ? WHERE user_id = ? AND game = ?",
                (title_id, user_id, game),
            )
        else:
            _db.execute(
                "INSERT INTO user_equipped_titles (user_id, game, title_id) VALUES (?, ?, ?)",
                (user_id, game, title_id),
            )
        _db.commit()
        return True


def unequip_title(user_id: int, game: str) -> bool:
    with _db_lock:
        _db.execute(
            "DELETE FROM user_equipped_titles WHERE user_id = ? AND game = ?",
            (user_id, game),
        )
        _db.commit()
        return True


def unequip_all_titles(user_id: int) -> bool:
    with _db_lock:
        _db.execute("DELETE FROM user_equipped_titles WHERE user_id = ?", (user_id,))
        _db.commit()
        return True


# ============ ТОП ============
def get_top_players(limit: int = 10) -> List:
    with _db_lock:
        return _db.execute(
            "SELECT username, tg_username, balance, games_played, wins "
            "FROM users ORDER BY wins DESC, games_played DESC LIMIT ?",
            (limit,),
        ).fetchall()