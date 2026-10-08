# main.py — Telegram-бот "Мафия"
import asyncio
import html
import logging
import random
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared_db import (
    init_shared_db,
    ensure_user as shared_ensure_user,
    get_user as shared_get_user,
    add_balance as shared_add_balance,
    spend_balance as shared_spend_balance,
    get_balance as shared_get_balance,
    record_game as shared_record_game,
    get_game_stats as shared_get_game_stats,
    get_all_titles as shared_get_all_titles,
    get_titles_by_game as shared_get_titles_by_game,
    get_title as shared_get_title,
    get_user_titles as shared_get_user_titles,
    award_title as shared_award_title,
    check_and_award_titles as shared_check_and_award_titles,
    get_equipped_titles as shared_get_equipped_titles,
    equip_title as shared_equip_title,
    unequip_title as shared_unequip_title,
    unequip_all_titles as shared_unequip_all_titles,
    get_top_players as shared_get_top_players,
    get_equipped_card_skin as shared_get_equipped_card_skin,
    set_equipped_card_skin as shared_set_equipped_card_skin,
    unequip_card_skin as shared_unequip_card_skin,
    get_profile_skin_info as shared_get_profile_skin_info,
    get_profile_skins_by_source as shared_get_profile_skins_by_source,
    PROFILE_SKINS_INFO as SHARED_PROFILE_SKINS,
)

# ============ НАСТРОЙКИ ============
from env_loader import get_bot_token
BOT_TOKEN = get_bot_token("MAFIA_BOT_TOKEN", os.path.dirname(os.path.abspath(__file__)))
ADMIN_ID = 352018610

REQUIRED_CHANNEL = "@botlabgame"

MIN_PLAYERS = 4
MAX_PLAYERS = 10

NIGHT_TIMEOUT = 60
VOTE_TIMEOUT = 60
DISCUSSION_TIMEOUT = 30

REWARD_WIN = 50
REWARD_LOSE = 10

TIMING_PRESETS = {
    "fast":     {"name": "⚡ Быстрая",  "night": 30,  "day": 15,  "vote": 30},
    "standard": {"name": "⏱ Стандарт", "night": 60,  "day": 30,  "vote": 60},
    "long":     {"name": "🐢 Долгая",   "night": 120, "day": 60,  "vote": 120},
}

# ============ ЛОГИ ============
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mafia_bot")

# ============ БОТ ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ============ БАЗА ============
db = sqlite3.connect("mafia.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = threading.Lock()

# ============ КАРТОЧКА ПРОФИЛЯ (html2image) ============
hti = None
hti_lock = threading.Lock()


def get_hti():
    global hti
    with hti_lock:
        if hti is None:
            from html2image import Html2Image
            hti = Html2Image(
                output_path=tempfile.gettempdir(),
                custom_flags=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--headless=new",
                ],
            )
        return hti


PROFILE_SKINS = {
    "Классическая": {
        "bg": "linear-gradient(135deg, #0f0f0f 0%, #2a0a0a 45%, #8b0000 100%)",
        "accent": "#d4af37",
        "accent2": "#ff4444",
        "card_bg": "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))",
        "card_border": "rgba(212,175,55,0.45)",
    },
    "Дон": {
        "bg": "linear-gradient(135deg, #1a1206 0%, #4a2f0a 45%, #b8860b 100%)",
        "accent": "#fbbf24",
        "accent2": "#fde68a",
        "card_bg": "linear-gradient(180deg, rgba(251,191,36,0.18), rgba(120,53,15,0.35))",
        "card_border": "rgba(251,191,36,0.55)",
    },
    "Кровавая луна": {
        "bg": "linear-gradient(135deg, #1a0000 0%, #4a0000 45%, #ff0000 100%)",
        "accent": "#ff4444",
        "accent2": "#ff8888",
        "card_bg": "linear-gradient(180deg, rgba(255,68,68,0.20), rgba(120,0,0,0.35))",
        "card_border": "rgba(255,68,68,0.55)",
    },
    "Тёмная сторона": {
        "bg": "linear-gradient(135deg, #000000 0%, #1a0033 45%, #4a0080 100%)",
        "accent": "#a855f7",
        "accent2": "#c084fc",
        "card_bg": "linear-gradient(180deg, rgba(168,85,247,0.20), rgba(74,0,128,0.35))",
        "card_border": "rgba(168,85,247,0.55)",
    },
}


def normalize_skin(skin: Optional[str]) -> str:
    value = (skin or "").strip()
    aliases = {
        "classic": "classic",
        "классическая": "classic",
        "gold": "gold",
        "золотой": "gold",
        "cyber": "cyber",
        "киберпанк": "cyber",
        "street": "street",
        "уличный неон": "street",
        "don": "don",
        "дон": "don",
        "rose": "rose",
        "чёрная роза": "rose",
        "black rose": "rose",
        "blood": "blood",
        "кровавая луна": "blood",
    }
    return aliases.get(value.lower(), value if value in {"classic", "gold", "cyber", "street", "don", "rose", "blood"} else "classic")


def render_profile_html(
    username: str,
    balance: int,
    games: int,
    wins: int,
    titles: str,
    skin: str = "classic",
) -> Tuple[str, str]:
    winrate = round((wins / games * 100), 1) if games > 0 else 0.0
    safe_name = html.escape(username)
    safe_titles = html.escape(titles or "не выбраны")
    skin_id = normalize_skin(skin)
    palette = shared_get_profile_skin_info(skin_id)

    css = f"""
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; width: 620px; height: 400px; overflow: hidden; }}
    body {{
      background: {palette['bg']};
      font-family: "Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji", Segoe UI, Tahoma, sans-serif;
      color: #ffffff;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .card {{
      width: 560px;
      height: 340px;
      background: {palette['card_bg']};
      border: 1px solid {palette['card_border']};
      border-radius: 20px;
      padding: 22px 24px;
      box-shadow: 0 18px 40px rgba(0,0,0,0.45);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      overflow: hidden;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      min-height: 100px;
    }}
    .header-left {{ min-width: 0; flex: 1 1 auto; overflow: hidden; }}
    .username {{
      font-size: 24px;
      font-weight: 700;
      color: #f8fafc;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .titles {{
      font-size: 14px;
      color: {palette['accent2']};
      margin-top: 6px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .balance-box {{
      background: linear-gradient(180deg, rgba(245,158,11,0.22), rgba(245,158,11,0.08));
      border: 1px solid rgba(251,191,36,0.45);
      padding: 10px 14px;
      border-radius: 14px;
      text-align: right;
      min-width: 130px;
    }}
    .balance-label {{ font-size: 11px; color: #fbbf24; text-transform: uppercase; }}
    .balance-value {{ font-size: 20px; font-weight: 700; color: #fbbf24; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .stat-card {{
      background: linear-gradient(180deg, rgba(15,23,42,0.55), rgba(15,23,42,0.25));
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 12px 8px;
      text-align: center;
    }}
    .stat-val {{ font-size: 22px; font-weight: 700; color: {palette['accent2']}; }}
    .stat-lbl {{ font-size: 12px; color: #cbd5e1; margin-top: 4px; }}
    """

    body = f"""
    <div class="card">
      <div class="header">
        <div class="header-left">
          <div class="username">{safe_name}</div>
          <div class="titles">🏆 {safe_titles}</div>
        </div>
        <div class="balance-box">
          <div class="balance-label">Баланс</div>
          <div class="balance-value">{balance}🚬</div>
        </div>
      </div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-val">{games}</div>
          <div class="stat-lbl">Сыграно игр</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color:#4ade80;">{wins}</div>
          <div class="stat-lbl">Побед</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color:#f43f5e;">{winrate}%</div>
          <div class="stat-lbl">Винрейт</div>
        </div>
      </div>
    </div>
    """
    return body, css


render_semaphore = asyncio.Semaphore(2)
profile_card_cache: Dict[int, tuple] = {}
CACHE_TTL = 300


async def generate_profile_card(
    username: str,
    balance: int,
    games: int,
    wins: int,
    titles: str,
    skin: str = "Классическая",
    user_id: Optional[int] = None,
) -> str:
    if user_id is not None:
        cached = profile_card_cache.get(user_id)
        if cached:
            cached_path, cached_time = cached
            if time.time() - cached_time < CACHE_TTL and os.path.exists(cached_path):
                return cached_path

    async with render_semaphore:
        if user_id is not None:
            cached = profile_card_cache.get(user_id)
            if cached:
                cached_path, cached_time = cached
                if time.time() - cached_time < CACHE_TTL and os.path.exists(cached_path):
                    return cached_path

        html_body, css = render_profile_html(username, balance, games, wins, titles, skin)
        filename = f"profile_{uuid.uuid4().hex}.png"

        def _render():
            engine = get_hti()
            paths = engine.screenshot(html_str=html_body, css_str=css, save_as=filename, size=(620, 400))
            if paths:
                return paths[0]
            return os.path.join(engine.output_path, filename)

        loop = asyncio.get_running_loop()
        img_path = await loop.run_in_executor(None, _render)

        if user_id is not None:
            old = profile_card_cache.get(user_id)
            if old and os.path.exists(old[0]) and old[0] != img_path:
                try:
                    os.remove(old[0])
                except OSError:
                    pass
            profile_card_cache[user_id] = (img_path, time.time())

        return img_path


def invalidate_profile_cache(user_id: int) -> None:
    entry = profile_card_cache.pop(user_id, None)
    if entry and os.path.exists(entry[0]):
        try:
            os.remove(entry[0])
        except OSError:
            pass


def init_db():
    with db_lock:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                tg_username TEXT,
                balance INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0
            )
        """)
        existing = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "tg_username" not in existing:
            db.execute("ALTER TABLE users ADD COLUMN tg_username TEXT")
        db.execute("""
            CREATE TABLE IF NOT EXISTS titles (
                title_id TEXT PRIMARY KEY,
                name TEXT,
                min_games INTEGER,
                bonus INTEGER,
                icon TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_titles (
                user_id INTEGER,
                title_id TEXT,
                awarded_at INTEGER,
                PRIMARY KEY (user_id, title_id)
            )
        """)
        default_titles = [
            ("rookie",    "Новичок",             0,   0,    "🆕"),
            ("suspect",   "Подозреваемый",       5,   25,   "👀"),
            ("citizen",   "Уважаемый горожанин", 15,  50,   "🏘️"),
            ("authority", "Авторитет",           30,  100,  "🎩"),
            ("sheriff",   "Шериф города",        50,  200,  "🕵️"),
            ("consig",    "Консильери",          80,  350,  "🤵"),
            ("don",       "Дон",                 120, 600,  "👑"),
            ("godfather", "Крёстный отец",       200, 1000, "🎩"),
            ("legend",    "Легенда города",      350, 2000, "💀"),
        ]
        for t in default_titles:
            db.execute("INSERT OR IGNORE INTO titles VALUES (?, ?, ?, ?, ?)", t)
        db.commit()

        db.execute("""
            CREATE TABLE IF NOT EXISTS styles (
                style_id TEXT PRIMARY KEY,
                name TEXT,
                price INTEGER,
                icon_mafia TEXT,
                icon_sheriff TEXT,
                icon_doctor TEXT,
                icon_civilian TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_styles (
                user_id INTEGER,
                style_id TEXT,
                PRIMARY KEY (user_id, style_id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_skins (
                user_id INTEGER,
                skin_id TEXT,
                purchased_at INTEGER,
                PRIMARY KEY (user_id, skin_id)
            )
        """)
        db.execute(
            "INSERT OR IGNORE INTO user_profile_skins (user_id, skin_id, purchased_at) VALUES (?, ?, ?)",
            (0, "classic", int(time.time())),
        )

        default_styles = [
            ("classic", "🎩 Классика", 0,
             "🔪", "🔍", "💉", "👤"),
            ("rose", "🖤 Чёрная роза", 100,
             "🖤", "🌹", "🩸", "🕊️"),
            ("neon", "🌃 Неоновый город", 250,
             "🟣", "🔵", "🟢", "⚪"),
            ("gold", "👑 Золотой клан", 500,
             "👑", "🏅", "💎", "🎖️"),
            ("blood", "🩸 Кровавая луна", 800,
             "🩸", "🌕", "🐺", "☠️"),
            ("dark", "💀 Тёмная сторона", 1500,
             "💀", "👁️", "⚗️", "🕯️"),
        ]
        for s in default_styles:
            db.execute("INSERT OR IGNORE INTO styles VALUES (?, ?, ?, ?, ?, ?, ?)", s)
        db.commit()


def ensure_user(user_id: int, username: str = "Игрок", tg_username: str = None):
    shared_ensure_user(user_id, username, tg_username)


def get_user(user_id: int):
    return shared_get_user(user_id)


def get_all_styles():
    with db_lock:
        return db.execute("SELECT * FROM styles ORDER BY price ASC").fetchall()


def get_user_styles(user_id: int) -> set:
    with db_lock:
        rows = db.execute("SELECT style_id FROM user_styles WHERE user_id = ?", (user_id,)).fetchall()
    return {r["style_id"] for r in rows}


def get_user_profile_skins(user_id: int) -> set:
    with db_lock:
        rows = db.execute(
            "SELECT skin_id FROM user_profile_skins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {r["skin_id"] for r in rows}


def has_style(user_id: int, style_id: str) -> bool:
    return style_id in get_user_styles(user_id)


def buy_style(user_id: int, style_id: str) -> bool:
    with db_lock:
        style = db.execute("SELECT price FROM styles WHERE style_id = ?", (style_id,)).fetchone()
    if not style:
        return False
    price = style["price"]
    if price > 0:
        if not shared_spend_balance(user_id, price, reason="style", game="mafia"):
            return False
    with db_lock:
        db.execute("INSERT OR IGNORE INTO user_styles (user_id, style_id) VALUES (?, ?)", (user_id, style_id))
        db.commit()
    return True


def get_style(style_id: str):
    with db_lock:
        return db.execute("SELECT * FROM styles WHERE style_id = ?", (style_id,)).fetchone()


def inventory_view(user_id: int):
    """Главное меню инвентаря Мафии."""
    styles_count = 0
    try:
        with db_lock:
            row = db.execute(
                "SELECT COUNT(*) as cnt FROM user_styles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row:
            styles_count = row["cnt"]
    except Exception:
        pass

    profile_count = len(["classic", "don", "rose", "blood"])
    text = "🎒 <b>Инвентарь</b>\n\nВыбери категорию:\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎨 Скины профиля ({profile_count})", callback_data="inv_cat:profile")],
        [InlineKeyboardButton(text=f"🎭 Мои стили игры ({styles_count})", callback_data="inv_cat:styles")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu_back")],
    ])
    return text, kb


def inventory_profile_view(user_id: int):
    skins = shared_get_profile_skins_by_source("mafia")
    owned = {"classic"} | get_user_profile_skins(user_id)
    current = shared_get_equipped_card_skin(user_id)

    text = "🎨 <b>Скины профиля</b>\n\n"
    text += f"✅ Сейчас надет: <b>{shared_get_profile_skin_info(current)['name']}</b>\n\n"

    rows = []
    for skin in skins:
        skin_id = skin["skin_id"]
        is_current = skin_id == current
        is_owned = skin_id in owned
        info = shared_get_profile_skin_info(skin_id)

        if is_current:
            text += f"✅ {info['name']} (надет)\n"
            rows.append([InlineKeyboardButton(text=f"✅ {info['name']}", callback_data="skin_nothing")])
        elif is_owned:
            text += f"🔓 {info['name']}\n"
            rows.append([InlineKeyboardButton(
                text=f"Надеть: {info['name']}",
                callback_data=f"skin_equip:{skin_id}",
            )])
        else:
            text += f"🔒 {info['name']} (не куплен)\n"
            rows.append([InlineKeyboardButton(text=f"🔒 {info['name']}", callback_data="skin_nothing")])

    rows.append([InlineKeyboardButton(text="🧹 Снять скин профиля", callback_data="skin_unequip")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="inv_back")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def inventory_styles_view(user_id: int):
    owned_styles = []
    try:
        with db_lock:
            rows = db.execute(
                """
                SELECT s.style_id, s.name
                FROM user_styles us
                JOIN styles s ON s.style_id = us.style_id
                WHERE us.user_id = ?
                """,
                (user_id,),
            ).fetchall()
        owned_styles = rows
    except Exception as e:
        log.warning(f"styles fetch failed: {e}")

    text = "🎭 <b>Мои стили игры</b>\n\n"
    text += "<i>Стили меняют эмодзи ролей во всей игре.</i>\n\n"
    if not owned_styles:
        text += "<i>У тебя пока нет купленных стилей. Загляни в /shop.</i>"
    else:
        for style in owned_styles:
            text += f"• {html.escape(style['name'])}\n"

    kb_rows = [[InlineKeyboardButton(text="🛒 В магазин стилей", callback_data="mf_shop_styles")]]
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="inv_back")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.message(Command("inventory"))
async def cmd_inventory(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    text, markup = inventory_view(msg.from_user.id)
    await msg.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("inv_cat:"))
async def cb_inv_cat(call: types.CallbackQuery):
    cat = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    if cat == "profile":
        text, kb = inventory_profile_view(call.from_user.id)
    elif cat == "styles":
        text, kb = inventory_styles_view(user_id)
    else:
        text, kb = inventory_view(user_id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "inv_back")
async def cb_inv_back(call: types.CallbackQuery):
    text, kb = inventory_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("skin_equip:"))
async def cb_skin_equip(call: types.CallbackQuery):
    skin_id = call.data.split(":", 1)[1]
    owned = {"classic"} | get_user_profile_skins(call.from_user.id)
    if skin_id not in owned:
        await call.answer("Скин не куплен. Купи его в /shop.", show_alert=True)
        return

    shared_set_equipped_card_skin(call.from_user.id, skin_id)
    invalidate_profile_cache(call.from_user.id)
    await call.answer(f"Надет: {shared_get_profile_skin_info(skin_id)['name']}")

    text, markup = inventory_profile_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "skin_unequip")
async def cb_skin_unequip(call: types.CallbackQuery):
    shared_unequip_card_skin(call.from_user.id)
    invalidate_profile_cache(call.from_user.id)
    await call.answer("Скин профиля снят.")

    text, markup = inventory_profile_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)


@dp.callback_query(F.data == "skin_nothing")
async def cb_skin_nothing(call: types.CallbackQuery):
    await call.answer()


def add_balance(user_id: int, username: str = "Игрок", amount: int = 0):
    shared_add_balance(user_id, amount, reason="game", game="mafia")


def spend_balance(user_id: int, username: str = "Игрок", amount: int = 0) -> bool:
    return shared_spend_balance(user_id, amount, reason="purchase", game="mafia")


# ============ ДАТАКЛАССЫ ============
@dataclass
class Player:
    user_id: int
    name: str
    role: str = "civilian"
    alive: bool = True
    voted_for: Optional[int] = None
    checked: List[int] = field(default_factory=list)


@dataclass
class Game:
    chat_id: int
    host_id: int
    players: List[Player] = field(default_factory=list)
    status: str = "lobby"
    day_count: int = 0
    night_kill: Optional[int] = None
    night_seduce: Optional[int] = None
    night_heal: Optional[int] = None
    night_check: Optional[int] = None
    votes: Dict[int, int] = field(default_factory=dict)
    settings: dict = field(default_factory=lambda: {
        "preset": "standard",
        "night": 60,
        "day": 30,
        "vote": 60,
    })
    phase_event: Optional[asyncio.Event] = None
    phase_action: Optional[str] = None
    silence: bool = True
    night_ready: dict = field(default_factory=dict)
    lynch_message_id: Optional[int] = None
    lynch_victim_id: Optional[int] = None
    lynch_votes: dict = field(default_factory=dict)
    style_id: str = "classic"

    def player_by_id(self, user_id: int) -> Optional[Player]:
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    def alive_players(self) -> List[Player]:
        return [p for p in self.players if p.alive]

    def mafia_players(self) -> List[Player]:
        return [p for p in self.players if p.alive and p.role == "mafia"]

    def civilians_alive(self) -> List[Player]:
        return [p for p in self.players if p.alive and p.role != "mafia"]

    def names_line(self) -> str:
        return ", ".join(html.escape(p.name) for p in self.players)


games: Dict[int, Game] = {}
host_presets: Dict[int, str] = {}
host_silence: Dict[int, bool] = {}
host_styles: Dict[int, str] = {}
user_chat: Dict[int, int] = {}


# ============ КЛАВИАТУРЫ ============
def lobby_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎭 Присоединиться", callback_data="mf_join"),
            InlineKeyboardButton(text="🚪 Выйти", callback_data="mf_leave"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="mf_settings"),
            InlineKeyboardButton(text="▶️ Начать игру", callback_data="mf_start"),
        ],
    ])


def settings_kb(game: Game) -> InlineKeyboardMarkup:
    current = game.settings["preset"]
    rows = []
    for key, preset in TIMING_PRESETS.items():
        mark = " ✅" if key == current else ""
        text = f"{preset['name']} ({preset['night']}/{preset['day']}/{preset['vote']}){mark}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"mf_preset:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в лобби", callback_data="mf_back_lobby")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vote_kb(game: Game, voter_id: int) -> InlineKeyboardMarkup:
    rows = []
    for p in game.alive_players():
        if p.user_id == voter_id:
            continue
        rows.append([InlineKeyboardButton(
            text=f"🗳️ {p.name}",
            callback_data=f"mf_vote:{p.user_id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def night_kb(game: Game, role: str, actor_id: int) -> InlineKeyboardMarkup:
    rows = []
    emoji = role_emoji(role, game.style_id)
    if role == "mafia":
        targets = [p for p in game.alive_players() if p.role != "mafia"]
        for p in targets:
            rows.append([InlineKeyboardButton(
                text=f"{emoji} Убить: {p.name}",
                callback_data=f"mf_kill:{p.user_id}"
            )])
    elif role == "sheriff":
        for p in game.alive_players():
            if p.user_id != actor_id:
                rows.append([InlineKeyboardButton(
                    text=f"{emoji} Проверить: {p.name}",
                    callback_data=f"mf_check:{p.user_id}"
                )])
    elif role == "doctor":
        for p in game.alive_players():
            rows.append([InlineKeyboardButton(
                text=f"{emoji} Лечить: {p.name}",
                callback_data=f"mf_heal:{p.user_id}"
            )])
    elif role == "maniac":
        for p in game.alive_players():
            if p.user_id != actor_id:
                rows.append([InlineKeyboardButton(
                    text=f"{emoji} Убить: {p.name}",
                    callback_data=f"mf_maniac:{p.user_id}"
                )])
    elif role == "lover":
        for p in game.alive_players():
            if p.user_id != actor_id:
                rows.append([InlineKeyboardButton(
                    text=f"{emoji} Соблазнить: {p.name}",
                    callback_data=f"mf_seduce:{p.user_id}"
                )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============ ХЕЛПЕР ============
def find_game(user_id: int) -> Optional[Game]:
    chat_id = user_chat.get(user_id)
    if chat_id is None:
        return None
    return games.get(chat_id)


async def can_dm(user_id: int) -> bool:
    try:
        await bot.send_chat_action(user_id, "typing")
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def mute_user(chat_id: int, user_id: int, minutes: int = 60):
    try:
        from datetime import datetime, timedelta
        until = datetime.now() + timedelta(minutes=minutes)
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=types.ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
            ),
            until_date=until,
        )
        return True
    except Exception as e:
        log.warning(f"mute_user failed for {user_id}: {e}")
        return False


async def unmute_user(chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=types.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        return True
    except Exception as e:
        log.warning(f"unmute_user failed for {user_id}: {e}")
        return False


async def mute_all_in_game(game: Game):
    if not game.silence:
        return
    for p in game.players:
        await mute_user(game.chat_id, p.user_id, minutes=120)


async def unmute_all_in_game(game: Game):
    for p in game.players:
        await unmute_user(game.chat_id, p.user_id)


ROLE_NAMES = {
    "mafia": "🔪 Мафия",
    "sheriff": "🔍 Шериф",
    "doctor": "💉 Доктор",
    "civilian": "👤 Мирный житель",
    "maniac": "🔪 Маньяк",
    "lover": "💋 Любовница",
    "mayor": "🏛️ Мэр",
}


ROLES_INFO = {
    "civilian": {
        "name": "👤 Мирный житель",
        "side": "🏘️ Город",
        "desc": "Спит ночью. Днём голосует и обсуждает. Побеждает вместе с городом, когда все мафии и одиночки казнены.",
    },
    "mafia": {
        "name": "🔪 Мафия",
        "side": "🩸 Мафия",
        "desc": "Ночью выбирает жертву вместе с другими мафиями. Днём притворяется мирным. Побеждает, когда мафий становится больше или столько же, сколько мирных.",
    },
    "sheriff": {
        "name": "🕵️ Шериф",
        "side": "🏘️ Город",
        "desc": "Ночью проверяет одного игрока — мафия он или нет. Днём помогает городу вычислять мафию.",
    },
    "doctor": {
        "name": "💉 Доктор",
        "side": "🏘️ Город",
        "desc": "Ночью лечит одного игрока. Если мафия выберет его жертвой — она выживет. Можно лечить себя.",
    },
    "maniac": {
        "name": "🔪 Маньяк",
        "side": "🎭 Одиночка",
        "desc": "Ночью убивает одного игрока. Играет сам за себя — побеждает, если остаётся один в живых. Ему мешают и город, и мафия.",
    },
    "lover": {
        "name": "💋 Любовница",
        "side": "🎭 Одиночка",
        "desc": "Ночью соблазняет одного игрока. Тот не может голосовать днём. Побеждает, если доживёт до конца с любой стороной (но только один).",
    },
    "mayor": {
        "name": "🏛️ Мэр",
        "side": "🏘️ Город",
        "desc": "Обычный мирный, но его голос на дневном голосовании считается за 2. Никто не знает, кто мэр.",
    },
}


def role_emoji(role: str, style_id: str) -> str:
    s = get_style(style_id)
    if not s:
        s = get_style("classic")
    if not s:
        return "❓"
    if role == "mafia":
        return s["icon_mafia"]
    if role == "sheriff":
        return s["icon_sheriff"]
    if role == "doctor":
        return s["icon_doctor"]
    return s["icon_civilian"]


def role_name_with_style(role: str, style_id: str) -> str:
    if role in ("maniac", "lover", "mayor"):
        return ROLES_INFO[role]["name"]
    base = {
        "mafia": "Мафия",
        "sheriff": "Шериф",
        "doctor": "Доктор",
        "civilian": "Мирный житель",
    }.get(role, role)
    return f"{role_emoji(role, style_id)} {base}"


# ============ ПОДПИСКА ============
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        if member.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
        ):
            return True
        if member.status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", False))
        return False
    except Exception as exc:
        log.warning("Channel check skipped: %s", exc)
        return True


def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Подписаться",
            url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
        )],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
    ])


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: types.CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.answer("✅ Подписка подтверждена!", show_alert=True)
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
    else:
        await call.answer("❌ Ты ещё не подписан на канал!", show_alert=True)


# ============ КОМАНДЫ ============
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    if msg.chat.type == ChatType.PRIVATE:
        await msg.answer(
            f"Привет, {html.escape(msg.from_user.first_name or 'игрок')}!\n\n"
            "🎭 Это бот игры <b>Мафия</b>.\n"
            "⚙️ /mytitles — надеть/снять титулы"
        )
        await cmd_mfmenu(msg)
        return

    await msg.answer(
        "🎭 <b>Мафия</b>\n\n"
        "Команды в группе:\n"
        "🎭 /mafia — создать игру\n"
        "📢 /mfcall — позвать всех\n"
        "🎭 /mfroles — описание ролей"
    )


@dp.message(Command("mfmenu"))
async def cmd_mfmenu(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎭 Создать игру", callback_data="menu_create_game")],
            [InlineKeyboardButton(text="📢 Позвать всех", callback_data="menu_call_all")],
            [InlineKeyboardButton(text="🎭 Роли", callback_data="menu_roles")],
        ])
        await msg.answer("🎭 <b>Мафия</b>\n\nВыбери действие:", reply_markup=kb)
        return

    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="🏆 Титулы", callback_data="menu_titles"),
        ],
        [
            InlineKeyboardButton(text="🛒 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton(text="🎭 Роли", callback_data="menu_roles"),
            InlineKeyboardButton(text="📢 Позвать всех", callback_data="menu_call_all"),
        ],
        [
            InlineKeyboardButton(text="🎮 Создать игру", callback_data="menu_create_game"),
        ],
    ])
    await msg.answer(
        "🎭 <b>Мафия — главное меню</b>\n\nВыбери действие:",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "menu_profile")
async def cb_menu_profile(call: types.CallbackQuery):
    await call.answer()
    await cmd_profile(call.message)


@dp.callback_query(F.data == "menu_titles")
async def cb_menu_titles(call: types.CallbackQuery):
    await call.answer()
    await cmd_mftitle(call.message)


@dp.callback_query(F.data == "menu_shop")
async def cb_menu_shop(call: types.CallbackQuery):
    await call.answer()
    await cmd_shop(call.message)


@dp.callback_query(F.data == "menu_roles")
async def cb_menu_roles(call: types.CallbackQuery):
    await call.answer()
    await cmd_mfroles(call.message)


@dp.callback_query(F.data == "menu_call_all")
async def cb_menu_call_all(call: types.CallbackQuery):
    await call.answer()
    if call.message.chat.type == ChatType.PRIVATE:
        await call.message.answer("📢 Команда /mfcall работает только в группе.")
        return
    await cmd_mfcall(call.message)


@dp.callback_query(F.data == "menu_create_game")
async def cb_menu_create_game(call: types.CallbackQuery):
    await call.answer()
    if call.message.chat.type == ChatType.PRIVATE:
        await call.message.answer(
            "🎮 Чтобы создать игру, перейди в групповой чат и отправь там /mafia."
        )
        return
    await cmd_mafia(call.message)


@dp.callback_query(F.data == "menu_settings")
async def cb_menu_settings(call: types.CallbackQuery):
    await call.answer()
    await cmd_mfsettings(call.message)


@dp.callback_query(F.data == "menu_back")
async def cb_menu_back(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="🏆 Титулы", callback_data="menu_titles"),
        ],
        [
            InlineKeyboardButton(text="🛒 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton(text="🎭 Роли", callback_data="menu_roles"),
            InlineKeyboardButton(text="📢 Позвать всех", callback_data="menu_call_all"),
        ],
        [
            InlineKeyboardButton(text="🎮 Создать игру", callback_data="menu_create_game"),
        ],
    ])
    try:
        await call.message.edit_text(
            "🎭 <b>Мафия — главное меню</b>\n\nВыбери действие:",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        await call.message.answer(
            "🎭 <b>Мафия — главное меню</b>\n\nВыбери действие:",
            reply_markup=kb,
        )
    await call.answer()


@dp.message(Command("mfprofile"))
async def cmd_profile(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    u = shared_get_user(msg.from_user.id)
    if not u:
        await msg.answer("Не удалось получить профиль.")
        return

    username = u["username"] or msg.from_user.full_name
    balance = u["balance"] or 0
    games_played = u["games_played"] or 0
    wins = u["wins"] or 0

    equipped = shared_get_equipped_titles(msg.from_user.id)
    if equipped:
        titles_str = " ".join(f"{t['icon']} {html.escape(t['name'])}" for t in equipped)
    else:
        titles_str = "не выбраны"

    skin_id = shared_get_equipped_card_skin(msg.from_user.id)

    status = await msg.answer("Генерирую карточку профиля...")
    caption = (
        f"👤 Профиль <b>{html.escape(username)}</b>\n"
        f"🏆 Титулы: {titles_str}\n"
        f"🚬 Баланс: {balance} бэриков\n"
        f"🎮 Игр: {games_played} | Побед: {wins}"
    )

    try:
        img_path = await generate_profile_card(
            username, balance, games_played, wins, titles_str, skin_id,
            user_id=msg.from_user.id,
        )
        await msg.answer_photo(photo=FSInputFile(img_path), caption=caption)
    except Exception as exc:
        log.exception("Profile card error: %s", exc)
        await msg.answer(caption)
    finally:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


@dp.message(Command("mftop"))
async def cmd_top(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    rows = shared_get_top_players(10)
    if not rows:
        await msg.answer("Пока никто не играл.")
        return
    text = "🏆 <b>Топ игроков (все игры)</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = f"@{r['tg_username']}" if r["tg_username"] else html.escape(r["username"] or "Игрок")
        text += f"{i}. {name} — {r['wins']} побед / {r['games_played']} игр\n"
    await msg.answer(text)


# ============ ЛОББИ ============
@dp.message(Command("mafia"))
async def cmd_mafia(msg: Message):
    if msg.chat.type == ChatType.PRIVATE:
        await msg.answer("Создай игру в групповом чате командой /mafia.")
        return
    if games.get(msg.chat.id):
        await msg.answer("В этом чате уже есть активная игра. /stopmafia чтобы остановить.")
        return

    if not await is_subscribed(msg.from_user.id):
        await msg.answer(
            f"Чтобы создать игру, подпишись на канал {REQUIRED_CHANNEL}.",
            reply_markup=sub_keyboard(),
        )
        return

    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    saved_key = host_presets.get(msg.from_user.id, "standard")
    saved_preset = TIMING_PRESETS[saved_key]
    game = Game(chat_id=msg.chat.id, host_id=msg.from_user.id)
    game.settings["preset"] = saved_key
    game.settings["night"] = saved_preset["night"]
    game.settings["day"] = saved_preset["day"]
    game.settings["vote"] = saved_preset["vote"]
    game.silence = host_silence.get(msg.from_user.id, True)
    game.style_id = host_styles.get(msg.from_user.id, "classic")
    game.players.append(Player(user_id=msg.from_user.id, name=msg.from_user.full_name))
    games[msg.chat.id] = game
    user_chat[msg.from_user.id] = msg.chat.id

    await msg.answer(
        f"🎭 <b>Мафия</b> создана!\n\n"
        f"Хост: {html.escape(msg.from_user.full_name)}\n"
        f"Игроки ({len(game.players)}): {game.names_line()}\n\n"
        f"Нужно {MIN_PLAYERS}–{MAX_PLAYERS} игроков.",
        reply_markup=lobby_kb(),
    )


@dp.message(Command("stopmafia"))
async def cmd_stopmafia(msg: Message):
    game = games.get(msg.chat.id)
    if not game:
        await msg.answer("Активной игры нет.")
        return
    if msg.from_user.id not in (game.host_id, ADMIN_ID):
        await msg.answer("Остановить может только хост или админ.")
        return
    for p in game.players:
        user_chat.pop(p.user_id, None)
    games.pop(msg.chat.id, None)
    await msg.answer("🎭 Игра остановлена.")


@dp.message(Command("mfsettings"))
async def cmd_mfsettings(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        await msg.answer(
            "⚙️ Настройки меняются в личке бота.\n"
            "Напиши мне в личку /mfsettings."
        )
        return

    user_id = msg.from_user.id
    preset_key = host_presets.get(user_id, "standard")
    preset = TIMING_PRESETS[preset_key]

    current_style_id = host_styles.get(user_id, "classic")
    current_style = get_style(current_style_id) or get_style("classic")
    style_name = current_style["name"] if current_style else "🎩 Классика"

    silence_mark = "✅ Вкл" if host_silence.get(user_id, True) else "❌ Выкл"
    rows = []
    for key, p in TIMING_PRESETS.items():
        mark = " ✅" if key == preset_key else ""
        t = f"{p['name']} ({p['night']}/{p['day']}/{p['vote']}){mark}"
        rows.append([InlineKeyboardButton(text=t, callback_data=f"mf_preset:{key}")])
    rows.append([InlineKeyboardButton(
        text=f"🔇 Тишина: {silence_mark}",
        callback_data="mf_silence_toggle"
    )])
    rows.append([InlineKeyboardButton(
        text=f"🎭 Стиль: {style_name}",
        callback_data="mf_style_menu"
    )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    text_msg = (
        "⚙️ <b>Настройки игры</b>\n\n"
        f"Сейчас: {preset['name']}\n"
        f"🌙 Ночь: <b>{preset['night']}</b> сек\n"
        f"💬 День: <b>{preset['day']}</b> сек\n"
        f"🗳️ Голосование: <b>{preset['vote']}</b> сек\n"
        f"🔇 Тишина: <b>{silence_mark}</b>\n"
        f"🎭 Стиль: <b>{style_name}</b>\n\n"
        "Эти настройки применятся к игре, которую ты создашь через /mafia."
    )
    await msg.answer(text_msg, reply_markup=kb)


@dp.callback_query(F.data == "mf_join")
async def cb_join(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Лобби недоступно.", show_alert=True)
        return
    if game.player_by_id(call.from_user.id):
        await call.answer("Ты уже в игре.")
        return

    if not await is_subscribed(call.from_user.id):
        await call.answer(
            f"Подпишись на {REQUIRED_CHANNEL} и нажми ещё раз!",
            show_alert=True,
        )
        return

    if len(game.players) >= MAX_PLAYERS:
        await call.answer("Мест нет.", show_alert=True)
        return

    if not await can_dm(call.from_user.id):
        await call.answer(
            "⚠️ Сначала напиши боту /start в личке, иначе не сможешь играть.",
            show_alert=True,
        )
        return

    ensure_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    game.players.append(Player(user_id=call.from_user.id, name=call.from_user.full_name))
    user_chat[call.from_user.id] = game.chat_id
    await call.answer("Ты в игре.")
    await call.message.edit_text(
        f"🎭 <b>Мафия</b>\nИгроки ({len(game.players)}): {game.names_line()}",
        reply_markup=lobby_kb(),
    )


@dp.callback_query(F.data == "mf_leave")
async def cb_leave(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Сейчас выйти нельзя.", show_alert=True)
        return
    player = game.player_by_id(call.from_user.id)
    if not player:
        await call.answer("Тебя нет в лобби.")
        return
    game.players = [p for p in game.players if p.user_id != call.from_user.id]
    user_chat.pop(call.from_user.id, None)

    if not game.players:
        games.pop(call.message.chat.id, None)
        await call.message.edit_text("Лобби пустое, игра удалена.")
        await call.answer("Вышел.")
        return
    if game.host_id == call.from_user.id:
        game.host_id = game.players[0].user_id
    await call.answer("Вышел.")
    await call.message.edit_text(
        f"🎭 <b>Мафия</b>\nИгроки ({len(game.players)}): {game.names_line()}",
        reply_markup=lobby_kb(),
    )


# ============ НАСТРОЙКИ ЛОББИ ============
@dp.callback_query(F.data == "mf_settings")
async def cb_settings(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Настройки доступны только в лобби.", show_alert=True)
        return
    if call.from_user.id != game.host_id:
        await call.answer("Настройки меняет только хост.", show_alert=True)
        return

    await call.answer(
        "⚙️ Настройки меняются в личке бота: напиши мне /mfsettings",
        show_alert=True,
    )


@dp.callback_query(F.data.startswith("mf_preset:"))
async def cb_preset(call: types.CallbackQuery):
    user_id = call.from_user.id
    key = call.data.split(":", 1)[1]

    if key not in TIMING_PRESETS:
        await call.answer("Неизвестный пресет.", show_alert=True)
        return

    preset = TIMING_PRESETS[key]
    host_presets[user_id] = key

    await call.answer(f"Пресет: {preset['name']}")

    current_style_id = host_styles.get(user_id, "classic")
    current_style = get_style(current_style_id) or get_style("classic")
    style_name = current_style["name"] if current_style else "🎩 Классика"

    silence_mark = "✅ Вкл" if host_silence.get(user_id, True) else "❌ Выкл"
    rows = []
    for k, p in TIMING_PRESETS.items():
        mark = " ✅" if k == key else ""
        t = f"{p['name']} ({p['night']}/{p['day']}/{p['vote']}){mark}"
        rows.append([InlineKeyboardButton(text=t, callback_data=f"mf_preset:{k}")])
    rows.append([InlineKeyboardButton(
        text=f"🔇 Тишина: {silence_mark}",
        callback_data="mf_silence_toggle"
    )])
    rows.append([InlineKeyboardButton(
        text=f"🎭 Стиль: {style_name}",
        callback_data="mf_style_menu"
    )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    text_msg = (
        "⚙️ <b>Настройки игры</b>\n\n"
        f"Сейчас: {preset['name']}\n"
        f"🌙 Ночь: <b>{preset['night']}</b> сек\n"
        f"💬 День: <b>{preset['day']}</b> сек\n"
        f"🗳️ Голосование: <b>{preset['vote']}</b> сек\n"
        f"🔇 Тишина: <b>{silence_mark}</b>\n"
        f"🎭 Стиль: <b>{style_name}</b>\n\n"
        "Эти настройки применятся к игре, которую ты создашь через /mafia."
    )
    try:
        await call.message.edit_text(text_msg, reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "mf_silence_toggle")
async def cb_silence_toggle(call: types.CallbackQuery):
    user_id = call.from_user.id
    current = host_silence.get(user_id, True)
    host_silence[user_id] = not current
    new_mark = "✅ Вкл" if host_silence[user_id] else "❌ Выкл"
    await call.answer(f"Тишина: {new_mark}")

    preset_key = host_presets.get(user_id, "standard")
    preset = TIMING_PRESETS[preset_key]

    current_style_id = host_styles.get(user_id, "classic")
    current_style = get_style(current_style_id) or get_style("classic")
    style_name = current_style["name"] if current_style else "🎩 Классика"

    rows = []
    for key, p in TIMING_PRESETS.items():
        mark = " ✅" if key == preset_key else ""
        t = f"{p['name']} ({p['night']}/{p['day']}/{p['vote']}){mark}"
        rows.append([InlineKeyboardButton(text=t, callback_data=f"mf_preset:{key}")])
    rows.append([InlineKeyboardButton(
        text=f"🔇 Тишина: {new_mark}",
        callback_data="mf_silence_toggle"
    )])
    rows.append([InlineKeyboardButton(
        text=f"🎭 Стиль: {style_name}",
        callback_data="mf_style_menu"
    )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    text_msg = (
        "⚙️ <b>Настройки игры</b>\n\n"
        f"Сейчас: {preset['name']}\n"
        f"🌙 Ночь: <b>{preset['night']}</b> сек\n"
        f"💬 День: <b>{preset['day']}</b> сек\n"
        f"🗳️ Голосование: <b>{preset['vote']}</b> сек\n"
        f"🔇 Тишина: <b>{new_mark}</b>\n"
        f"🎭 Стиль: <b>{style_name}</b>\n\n"
        "Эти настройки применятся к игре, которую ты создашь через /mafia."
    )
    try:
        await call.message.edit_text(text_msg, reply_markup=kb)
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "mf_style_menu")
async def cb_style_menu(call: types.CallbackQuery):
    user_id = call.from_user.id
    styles = get_all_styles()
    owned = get_user_styles(user_id)
    current_style_id = host_styles.get(user_id, "classic")

    text = "🎭 <b>Выбор стиля игры</b>\n\n"
    rows = []
    for s in styles:
        sid = s["style_id"]
        is_owned = sid in owned or s["price"] == 0
        is_current = sid == current_style_id

        if is_current:
            mark = " 🎯"
        elif is_owned:
            mark = " ✅"
        else:
            mark = " 🔒"

        text += (
            f"<b>{s['name']}</b>{mark}\n"
            f"   {s['icon_mafia']} {s['icon_sheriff']} {s['icon_doctor']} {s['icon_civilian']}\n"
        )
        if not is_owned:
            text += f"   💰 Цена: <b>{s['price']}</b> 🚬 (купить в /shop)\n"
        text += "\n"

        if is_current:
            rows.append([InlineKeyboardButton(text=f"{s['name']} 🎯", callback_data=f"style_set:{sid}")])
        elif is_owned:
            rows.append([InlineKeyboardButton(text=f"{s['name']} ✅", callback_data=f"style_set:{sid}")])
        else:
            rows.append([InlineKeyboardButton(text=f"{s['name']} 🔒", callback_data="style_locked")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="mf_settings_back")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("style_set:"))
async def cb_style_set(call: types.CallbackQuery):
    sid = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    styles_owned = get_user_styles(user_id)
    s = get_style(sid)
    if not s:
        await call.answer("Стиль не найден.", show_alert=True)
        return
    if s["price"] > 0 and sid not in styles_owned:
        await call.answer("Стиль не куплен. Купи его в /shop.", show_alert=True)
        return
    host_styles[user_id] = sid
    await call.answer(f"Выбран: {s['name']}")
    await cb_style_menu(call)


@dp.callback_query(F.data == "style_locked")
async def cb_style_locked(call: types.CallbackQuery):
    await call.answer("🔒 Стиль не куплен. Купи его в /shop.", show_alert=True)


@dp.callback_query(F.data == "mf_settings_back")
async def cb_settings_back(call: types.CallbackQuery):
    user_id = call.from_user.id
    preset_key = host_presets.get(user_id, "standard")
    preset = TIMING_PRESETS[preset_key]
    current_style_id = host_styles.get(user_id, "classic")
    current_style = get_style(current_style_id) or get_style("classic")
    style_name = current_style["name"] if current_style else "🎩 Классика"
    silence_mark = "✅ Вкл" if host_silence.get(user_id, True) else "❌ Выкл"

    rows = []
    for key, p in TIMING_PRESETS.items():
        mark = " ✅" if key == preset_key else ""
        t = f"{p['name']} ({p['night']}/{p['day']}/{p['vote']}){mark}"
        rows.append([InlineKeyboardButton(text=t, callback_data=f"mf_preset:{key}")])
    rows.append([InlineKeyboardButton(
        text=f"🔇 Тишина: {silence_mark}",
        callback_data="mf_silence_toggle"
    )])
    rows.append([InlineKeyboardButton(
        text=f"🎭 Стиль: {style_name}",
        callback_data="mf_style_menu"
    )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    text_msg = (
        "⚙️ <b>Настройки игры</b>\n\n"
        f"Сейчас: {preset['name']}\n"
        f"🌙 Ночь: <b>{preset['night']}</b> сек\n"
        f"💬 День: <b>{preset['day']}</b> сек\n"
        f"🗳️ Голосование: <b>{preset['vote']}</b> сек\n"
        f"🔇 Тишина: <b>{silence_mark}</b>\n"
        f"🎭 Стиль: <b>{style_name}</b>\n\n"
        "Эти настройки применятся к игре, которую ты создашь через /mafia."
    )
    try:
        await call.message.edit_text(text_msg, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await call.answer()


@dp.callback_query(F.data == "mf_back_lobby")
async def cb_back_lobby(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer()
        return
    try:
        await call.message.edit_text(
            f"🎭 <b>Мафия</b>\nИгроки ({len(game.players)}): {game.names_line()}",
            reply_markup=lobby_kb(),
        )
    except TelegramBadRequest:
        pass
    await call.answer()


@dp.callback_query(F.data == "mf_skip")
async def cb_skip(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game:
        await call.answer()
        return
    if call.from_user.id != game.host_id:
        await call.answer("Только хост может пропустить фазу.", show_alert=True)
        return

    if game.status == "night":
        needed = set()
        for p in game.alive_players():
            if p.role == "mafia":
                needed.add("mafia")
            elif p.role == "sheriff":
                needed.add("sheriff")
            elif p.role == "doctor":
                needed.add("doctor")
            elif p.role == "maniac":
                needed.add("maniac")
            elif p.role == "lover":
                needed.add("lover")
        missing = needed - set(game.night_ready.keys())
        if missing:
            await call.answer(
                f"Ещё не все сделали ход: {', '.join(missing)}",
                show_alert=True
            )
            return

    if game.status == "vote":
        can_vote = [p for p in game.alive_players() if p.voted_for != "SEDUCED"]
        voted_count = len(game.votes)
        if voted_count < len(can_vote):
            await call.answer(
                f"Ещё не все проголосовали ({voted_count}/{len(can_vote)}).",
                show_alert=True
            )
            return

    if game.phase_event:
        game.phase_action = "skip"
        game.phase_event.set()
    await call.answer("Фаза пропущена.")


@dp.callback_query(F.data == "mf_plus15")
async def cb_plus15(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game:
        await call.answer()
        return
    if call.from_user.id != game.host_id:
        await call.answer("Только хост может продлить.", show_alert=True)
        return
    if game.phase_event and game.phase_action != "plus15":
        game.phase_action = "plus15"
        game.phase_event.set()
    await call.answer("+15 секунд.")


# ============ СТАРТ ============
@dp.callback_query(F.data == "mf_start")
async def cb_start(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Нельзя начать.", show_alert=True)
        return
    if call.from_user.id != game.host_id:
        await call.answer("Начинает только хост.", show_alert=True)
        return
    if len(game.players) < MIN_PLAYERS:
        await call.answer(f"Нужно минимум {MIN_PLAYERS} игрока.", show_alert=True)
        return

    bad_players = []
    for p in game.players:
        if not await can_dm(p.user_id):
            bad_players.append(p.name)
    if bad_players:
        await call.answer(
            f"⚠️ Эти игроки не написали боту /start: {', '.join(bad_players)}",
            show_alert=True,
        )
        return

    random.shuffle(game.players)
    n = len(game.players)
    roles = []

    if n == 4:
        roles = ["mafia", "civilian", "civilian", "civilian"]
    elif n == 5:
        roles = ["mafia", "sheriff", "civilian", "civilian", "civilian"]
    elif n == 6:
        roles = ["mafia", "sheriff", "doctor", "civilian", "civilian", "civilian"]
    elif n == 7:
        roles = ["mafia", "sheriff", "doctor", "civilian", "civilian", "civilian", "civilian"]
    elif n == 8:
        roles = ["mafia", "mafia", "sheriff", "doctor", "maniac",
                 "civilian", "civilian", "civilian"]
    elif n == 9:
        roles = ["mafia", "mafia", "sheriff", "doctor", "maniac", "lover",
                 "civilian", "civilian", "civilian"]
    elif n >= 10:
        roles = ["mafia", "mafia", "sheriff", "doctor", "maniac", "lover", "mayor",
                 "civilian", "civilian", "civilian"]

    while len(roles) < n:
        roles.append("civilian")
    roles = roles[:n]
    random.shuffle(roles)

    mafia_count = roles.count("mafia")

    for p, role in zip(game.players, roles):
        p.role = role
        p.alive = True
        p.voted_for = None
        p.checked = []
        user_chat[p.user_id] = game.chat_id

    game.status = "night"
    game.day_count = 1
    game.votes = {}

    await call.message.edit_text(
        f"🎭 <b>Игра началась!</b>\n"
        f"Игроков: {n}\n"
        f"Мафий: {mafia_count}\n\n"
        f"🌙 Первая ночь. Проверь личку!"
    )
    await call.answer()

    for p in game.players:
        try:
            await bot.send_message(
                p.user_id,
                f"Твоя роль: <b>{role_name_with_style(p.role, game.style_id)}</b>\n\n"
                f"Не показывай никому!"
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            await call.message.answer(f"⚠️ {html.escape(p.name)}, напиши боту /start в личке!")

    await start_night(game)


# ============ НОЧЬ ============
async def start_night(game: Game):
    game.status = "night"
    game.night_kill = None
    game.night_heal = None
    game.night_check = None
    game.night_seduce = None
    for p in game.players:
        p.voted_for = None
    await mute_all_in_game(game)

    night_sec = game.settings["night"]

    host_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Пропустить ночь", callback_data="mf_skip"),
            InlineKeyboardButton(text="+15 сек", callback_data="mf_plus15"),
        ]
    ])

    await bot.send_message(
        game.chat_id,
        f"🌙 <b>Ночь {game.day_count}</b>\n\n"
        f"Город засыпает. Мафия, шериф и доктор — проверьте личку!\n"
        f"⏱ У вас {night_sec} секунд.",
        reply_markup=host_kb,
    )

    game.night_ready = {}
    for p in game.alive_players():
        if p.role == "civilian":
            continue
        try:
            await bot.send_message(
                p.user_id,
                f"🌙 Ночь {game.day_count}. Твой ход:",
                reply_markup=night_kb(game, p.role, p.user_id),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

    game.phase_event = asyncio.Event()
    game.phase_action = None

    try:
        await asyncio.wait_for(game.phase_event.wait(), timeout=night_sec)
    except asyncio.TimeoutError:
        pass

    if game.phase_action == "plus15":
        game.phase_event = asyncio.Event()
        game.phase_action = None
        await bot.send_message(game.chat_id, "⏱ +15 секунд к ночи.")
        try:
            await asyncio.wait_for(game.phase_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass

    if game.status == "night":
        await resolve_night(game)


@dp.callback_query(F.data.startswith("mf_kill:"))
async def cb_kill(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "night":
        await call.answer("Сейчас не ночь.", show_alert=True)
        return
    actor = game.player_by_id(call.from_user.id)
    if not actor or actor.role != "mafia" or not actor.alive:
        await call.answer("Ты не мафия.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    game.night_kill = target_id
    target = game.player_by_id(target_id)
    await call.answer("Жертва выбрана.")
    await call.message.edit_text(f"🔪 Ты выбрал: {html.escape(target.name)}")
    game.night_ready["mafia"] = True
    try:
        await bot.send_message(game.chat_id, f"{role_emoji('mafia', game.style_id)} Мафия сделала свой выбор.")
    except Exception:
        pass
    await check_night_ready(game)


@dp.callback_query(F.data.startswith("mf_check:"))
async def cb_check(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "night":
        await call.answer("Сейчас не ночь.", show_alert=True)
        return
    actor = game.player_by_id(call.from_user.id)
    if not actor or actor.role != "sheriff" or not actor.alive:
        await call.answer("Ты не шериф.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    target = game.player_by_id(target_id)
    if not target:
        await call.answer("Игрок не найден.")
        return
    actor.checked.append(target_id)
    is_mafia = target.role == "mafia"
    await call.answer(f"{target.name} — {'МАФИЯ 🔪' if is_mafia else 'не мафия 👤'}", show_alert=True)
    await call.message.edit_text(
        f"🔍 Проверка: <b>{html.escape(target.name)}</b> — "
        f"{'МАФИЯ 🔪' if is_mafia else 'не мафия 👤'}"
    )
    game.night_ready["sheriff"] = True
    try:
        await bot.send_message(game.chat_id, f"{role_emoji('sheriff', game.style_id)} Шериф сделал свой выбор.")
    except Exception:
        pass
    await check_night_ready(game)


@dp.callback_query(F.data.startswith("mf_heal:"))
async def cb_heal(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "night":
        await call.answer("Сейчас не ночь.", show_alert=True)
        return
    actor = game.player_by_id(call.from_user.id)
    if not actor or actor.role != "doctor" or not actor.alive:
        await call.answer("Ты не доктор.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    game.night_heal = target_id
    target = game.player_by_id(target_id)
    await call.answer("Лечение выбрано.")
    await call.message.edit_text(f"💉 Ты лечишь: {html.escape(target.name)}")
    game.night_ready["doctor"] = True
    try:
        await bot.send_message(game.chat_id, f"{role_emoji('doctor', game.style_id)} Доктор сделал свой выбор.")
    except Exception:
        pass
    await check_night_ready(game)


@dp.callback_query(F.data.startswith("mf_maniac:"))
async def cb_maniac(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "night":
        await call.answer("Сейчас не ночь.", show_alert=True)
        return
    actor = game.player_by_id(call.from_user.id)
    if not actor or actor.role != "maniac" or not actor.alive:
        await call.answer("Ты не маньяк.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    game.night_kill = target_id
    game.night_ready["maniac"] = True
    target = game.player_by_id(target_id)
    await call.answer("Жертва выбрана.")
    await call.message.edit_text(f"🔪 Ты выбрал: {html.escape(target.name)}")
    try:
        await bot.send_message(game.chat_id, f"{role_emoji('maniac', game.style_id)} Маньяк сделал свой выбор.")
    except Exception:
        pass
    await check_night_ready(game)


@dp.callback_query(F.data.startswith("mf_seduce:"))
async def cb_seduce(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "night":
        await call.answer("Сейчас не ночь.", show_alert=True)
        return
    actor = game.player_by_id(call.from_user.id)
    if not actor or actor.role != "lover" or not actor.alive:
        await call.answer("Ты не любовница.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    game.night_seduce = target_id
    game.night_ready["lover"] = True
    target = game.player_by_id(target_id)
    await call.answer("Соблазнено.")
    await call.message.edit_text(f"💋 Ты соблазнил: {html.escape(target.name)}")
    try:
        await bot.send_message(game.chat_id, f"{role_emoji('lover', game.style_id)} Любовница сделала свой выбор.")
    except Exception:
        pass
    await check_night_ready(game)


async def check_night_ready(game: Game):
    if game.status != "night":
        return
    needed = set()
    for p in game.alive_players():
        if p.role == "mafia":
            needed.add("mafia")
        elif p.role == "sheriff":
            needed.add("sheriff")
        elif p.role == "doctor":
            needed.add("doctor")
        elif p.role == "maniac":
            needed.add("maniac")
        elif p.role == "lover":
            needed.add("lover")
    if needed.issubset(set(game.night_ready.keys())):
        if game.phase_event:
            game.phase_action = "skip"
            game.phase_event.set()


async def resolve_night(game: Game):
    killed = None
    if game.night_kill:
        victim = game.player_by_id(game.night_kill)
        if victim and victim.alive:
            if game.night_heal == victim.user_id:
                pass
            else:
                victim.alive = False
                killed = victim
    if game.night_seduce:
        seduced = game.player_by_id(game.night_seduce)
        if seduced and seduced.alive:
            seduced.voted_for = "SEDUCED"

    await unmute_all_in_game(game)

    text = f"☀️ <b>День {game.day_count}</b>\n\n"
    if killed:
        text += f"💀 Ночью был убит <b>{html.escape(killed.name)}</b>.\n"
    else:
        text += "🕊️ Ночью никто не погиб.\n"

    game.status = "day"
    await bot.send_message(game.chat_id, text)

    if await check_win(game):
        return

    day_sec = game.settings["day"]
    host_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Пропустить день", callback_data="mf_skip"),
            InlineKeyboardButton(text="+15 сек", callback_data="mf_plus15"),
        ]
    ])
    await bot.send_message(
        game.chat_id,
        f"💬 Обсуждение {day_sec} секунд. Кто мафия?",
        reply_markup=host_kb,
    )

    game.phase_event = asyncio.Event()
    game.phase_action = None

    try:
        await asyncio.wait_for(game.phase_event.wait(), timeout=day_sec)
    except asyncio.TimeoutError:
        pass

    if game.phase_action == "plus15":
        game.phase_event = asyncio.Event()
        game.phase_action = None
        await bot.send_message(game.chat_id, "⏱ +15 секунд к обсуждению.")
        try:
            await asyncio.wait_for(game.phase_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass

    await start_vote(game)


# ============ ГОЛОСОВАНИЕ ============
async def start_vote(game: Game):
    game.status = "vote"
    await mute_all_in_game(game)
    game.votes = {}
    for p in game.alive_players():
        if p.voted_for == "SEDUCED":
            continue
        p.voted_for = None

    vote_sec = game.settings["vote"]

    host_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Закончить голосование", callback_data="mf_skip"),
            InlineKeyboardButton(text="+15 сек", callback_data="mf_plus15"),
        ]
    ])

    await bot.send_message(
        game.chat_id,
        f"🗳️ <b>Голосование</b>\n\nГолосуй в личке. У тебя {vote_sec} секунд.",
        reply_markup=host_kb,
    )

    for p in game.alive_players():
        try:
            vote_text = "🗳️ Кого казнить?"
            if p.role == "mayor":
                vote_text = "🗳️ Кого казнить? (🏛️ Твой голос x2)"
            await bot.send_message(
                p.user_id,
                vote_text,
                reply_markup=vote_kb(game, p.user_id),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass

    game.phase_event = asyncio.Event()
    game.phase_action = None

    try:
        await asyncio.wait_for(game.phase_event.wait(), timeout=vote_sec)
    except asyncio.TimeoutError:
        pass

    if game.phase_action == "plus15":
        game.phase_event = asyncio.Event()
        game.phase_action = None
        await bot.send_message(game.chat_id, "⏱ +15 секунд к голосованию.")
        try:
            await asyncio.wait_for(game.phase_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass

    if game.status == "vote":
        await resolve_vote(game)


@dp.callback_query(F.data.startswith("mf_vote:"))
async def cb_vote(call: types.CallbackQuery):
    game = find_game(call.from_user.id)
    if not game or game.status != "vote":
        await call.answer("Сейчас не голосование.", show_alert=True)
        return
    voter = game.player_by_id(call.from_user.id)
    if not voter or not voter.alive:
        await call.answer("Ты не можешь голосовать.", show_alert=True)
        return
    if voter.voted_for == "SEDUCED":
        await call.answer("💋 Тебя соблазнили — сегодня ты не можешь голосовать.", show_alert=True)
        return
    target_id = int(call.data.split(":")[1])
    voter.voted_for = target_id
    weight = 2 if voter.role == "mayor" else 1
    game.votes[voter.user_id] = (target_id, weight)
    target = game.player_by_id(target_id)
    await call.answer("Голос учтён.")
    await call.message.edit_text(f"🗳️ Ты голосуешь против {html.escape(target.name)}")

    if weight == 2:
        try:
            await bot.send_message(
                game.chat_id,
                f"🏛️ <b>{html.escape(voter.name)}</b> (Мэр) проголосовал против <b>{html.escape(target.name)}</b> (x2)"
            )
        except Exception:
            pass
    else:
        try:
            await bot.send_message(
                game.chat_id,
                f"🗳️ <b>{html.escape(voter.name)}</b> проголосовал против <b>{html.escape(target.name)}</b>"
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("mf_lynch:"))
async def cb_lynch(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.lynch_victim_id is None:
        await call.answer("Голосование уже закончилось.", show_alert=True)
        return
    if game.lynch_votes.get(f"user:{call.from_user.id}"):
        await call.answer("Ты уже голосовал.", show_alert=True)
        return
    game.lynch_votes["kill"] = game.lynch_votes.get("kill", 0) + 1
    game.lynch_votes[f"user:{call.from_user.id}"] = "kill"
    await call.answer("Голос за казнь.")
    try:
        await bot.send_message(
            game.chat_id,
            f"👍 {html.escape(call.from_user.full_name)} — за казнь."
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("mf_spare:"))
async def cb_spare(call: types.CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.lynch_victim_id is None:
        await call.answer("Голосование уже закончилось.", show_alert=True)
        return
    if game.lynch_votes.get(f"user:{call.from_user.id}"):
        await call.answer("Ты уже голосовал.", show_alert=True)
        return
    game.lynch_votes["spare"] = game.lynch_votes.get("spare", 0) + 1
    game.lynch_votes[f"user:{call.from_user.id}"] = "spare"
    await call.answer("Голос за помилование.")
    try:
        await bot.send_message(
            game.chat_id,
            f"👎 {html.escape(call.from_user.full_name)} — за помилование."
        )
    except Exception:
        pass


async def resolve_vote(game: Game):
    if not game.votes:
        await bot.send_message(game.chat_id, "🤷 Никто не проголосовал.")
        await end_day(game)
        return

    counts: Dict[int, int] = {}
    for vote_data in game.votes.values():
        if isinstance(vote_data, tuple):
            target_id, weight = vote_data
        else:
            target_id, weight = vote_data, 1
        counts[target_id] = counts.get(target_id, 0) + weight

    max_votes = max(counts.values())
    leaders = [tid for tid, c in counts.items() if c == max_votes]

    if len(leaders) > 1:
        await bot.send_message(game.chat_id, "🤝 Ничья. Никто не казнён.")
    else:
        victim = game.player_by_id(leaders[0])
        if victim and victim.alive:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="👍 Казнить", callback_data=f"mf_lynch:{victim.user_id}"),
                    InlineKeyboardButton(text="👎 Оставить", callback_data=f"mf_spare:{victim.user_id}"),
                ]
            ])
            sent = await bot.send_message(
                game.chat_id,
                f"⚖️ Большинство голосов против <b>{html.escape(victim.name)}</b>.\n\n"
                f"Голосуйте: казнить или оставить?",
                reply_markup=kb,
            )
            game.lynch_message_id = sent.message_id
            game.lynch_victim_id = victim.user_id
            game.lynch_votes = {"kill": 0, "spare": 0}
            # Ставим 30-секундный таймер на лайк/дизлайк
            game.phase_event = asyncio.Event()
            try:
                await asyncio.wait_for(game.phase_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            # После таймера — обработка результата
            if game.lynch_votes["kill"] > game.lynch_votes["spare"]:
                victim.alive = False
                await mute_user(game.chat_id, victim.user_id, minutes=120)
                await bot.send_message(
                    game.chat_id,
                    f"⚖️ <b>{html.escape(victim.name)}</b> казнён.\n"
                    f"Его роль: <b>{ROLE_NAMES[victim.role]}</b>"
                )
            else:
                await bot.send_message(
                    game.chat_id,
                    f"🕊️ <b>{html.escape(victim.name)}</b> помилован."
                )

    if await check_win(game):
        return
    await end_day(game)


async def end_day(game: Game):
    game.day_count += 1
    await asyncio.sleep(2)
    await start_night(game)


# ============ ПОБЕДА ============
async def check_win(game: Game) -> bool:
    mafia_alive = len([p for p in game.players if p.alive and p.role == "mafia"])
    maniac_alive = len([p for p in game.players if p.alive and p.role == "maniac"])
    civ_alive = len([p for p in game.players if p.alive and p.role not in ("mafia", "maniac")])

    if maniac_alive > 0 and mafia_alive == 0 and civ_alive == 0:
        await finish_game(game, "maniac")
        return True
    if mafia_alive > 0 and mafia_alive >= civ_alive and maniac_alive == 0:
        await finish_game(game, "mafia")
        return True
    if mafia_alive == 0 and maniac_alive == 0:
        await finish_game(game, "civilians")
        return True
    return False


async def finish_game(game: Game, winner: str):
    await unmute_all_in_game(game)
    if game.phase_event:
        game.phase_event.set()
    game.status = "end"
    if winner == "mafia":
        text = "🔪 <b>Мафия победила!</b>\n\n"
        winners = [p for p in game.players if p.role == "mafia"]
        losers = [p for p in game.players if p.role != "mafia"]
    elif winner == "maniac":
        text = "🔪 <b>Маньяк победил!</b>\n\n"
        winners = [p for p in game.players if p.role == "maniac"]
        losers = [p for p in game.players if p.role != "maniac"]
    else:
        text = "👥 <b>Мирные победили!</b>\n\n"
        winners = [p for p in game.players if p.role not in ("mafia", "maniac")]
        losers = [p for p in game.players if p.role in ("mafia", "maniac")]

    text += "<b>Роли:</b>\n"
    for p in game.players:
        text += f"• {html.escape(p.name)} — {role_name_with_style(p.role, game.style_id)}\n"

    await bot.send_message(game.chat_id, text)

    names_map = {p.user_id: p.name for p in game.players}
    usernames_map = {}
    for p in game.players:
        try:
            cm = await bot.get_chat_member(game.chat_id, p.user_id)
            usernames_map[p.user_id] = cm.user.username
        except Exception:
            usernames_map[p.user_id] = None
    await record_game(
        [p.user_id for p in winners],
        [p.user_id for p in losers],
        game.chat_id,
        names_map,
        usernames_map,
    )

    for p in game.players:
        user_chat.pop(p.user_id, None)
    games.pop(game.chat_id, None)


# ============ НАЧИСЛЕНИЕ ============
async def record_game(winner_ids: List[int], loser_ids: List[int], chat_id: int, names_map: dict = None, usernames_map: dict = None):
    for uid in winner_ids:
        if names_map and usernames_map:
            ensure_user(uid, names_map.get(uid, "Игрок"), usernames_map.get(uid))
        shared_record_game(uid, "mafia", won=True)
        shared_add_balance(uid, REWARD_WIN, reason="mafia_win", game="mafia")
    for uid in loser_ids:
        if names_map and usernames_map:
            ensure_user(uid, names_map.get(uid, "Игрок"), usernames_map.get(uid))
        shared_record_game(uid, "mafia", won=False)
        shared_add_balance(uid, REWARD_LOSE, reason="mafia_lose", game="mafia")

    # Проверяем повышение титулов
    for uid in winner_ids + loser_ids:
        new_titles = shared_check_and_award_titles(uid, "mafia")
        for t in new_titles:
            name = names_map.get(uid, "Игрок") if names_map else "Игрок"
            try:
                await bot.send_message(
                    chat_id,
                    f"🎉 <b>{html.escape(name)}</b> получил новый титул:\n"
                    f"{t['icon']} <b>{html.escape(t['name'])}</b>"
                    + (f"\n💰 Бонус: <b>+{t['bonus']}</b> 🚬" if t['bonus'] > 0 else "")
                )
            except Exception as e:
                log.warning(f"Не удалось отправить поздравление: {e}")


# ============ ТИТУЛЫ ============
def get_current_title(user_id: int):
    """Возвращает лучший титул Мафии, который заработал игрок, или None."""
    owned = shared_get_user_titles(user_id)
    mafia_titles = shared_get_titles_by_game("mafia")
    best = None
    for t in mafia_titles:
        if t["title_id"] in owned:
            best = t
    return best


def get_next_title(user_id: int):
    """Возвращает следующий титул Мафии, до которого ещё надо играть."""
    stats = shared_get_game_stats(user_id)
    games_in_mafia = 0
    for s in stats:
        if s["game"] == "mafia":
            games_in_mafia = s["games_played"]
    owned = shared_get_user_titles(user_id)
    mafia_titles = shared_get_titles_by_game("mafia")
    for t in mafia_titles:
        if t["title_id"] not in owned and t["min_games"] > games_in_mafia:
            return t
    return None


def get_awarded_titles(user_id: int) -> set:
    return shared_get_user_titles(user_id)


async def check_title_upgrade(user_id: int, username: str, chat_id: int):
    """Проверяет и вручает новые титулы Мафии."""
    new_titles = shared_check_and_award_titles(user_id, "mafia")
    for t in new_titles:
        try:
            await bot.send_message(
                chat_id,
                f"🎉 <b>{html.escape(username)}</b> получил новый титул:\n"
                f"{t['icon']} <b>{html.escape(t['name'])}</b>"
                + (f"\n💰 Бонус: <b>+{t['bonus']}</b> 🚬" if t['bonus'] > 0 else "")
            )
        except Exception as e:
            log.warning(f"Не удалось отправить поздравление: {e}")


@dp.message(Command("mftitle"))
async def cmd_mftitle(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    owned = shared_get_user_titles(msg.from_user.id)
    stats = shared_get_game_stats(msg.from_user.id)
    games_in_mafia = 0
    for s in stats:
        if s["game"] == "mafia":
            games_in_mafia = s["games_played"]

    mafia_titles = shared_get_titles_by_game("mafia")
    text = "🏆 <b>Титулы Мафии</b>\n\n"
    for t in mafia_titles:
        if t["title_id"] in owned:
            status = "✅"
        else:
            status = f"🔒 ({t['min_games']} игр)"
        text += f"{t['icon']} <b>{html.escape(t['name'])}</b> — {status}\n"
        if t["bonus"] > 0:
            text += f"   <i>Бонус: +{t['bonus']} 🚬</i>\n"
    text += f"\n📊 Ты сыграл в Мафию: <b>{games_in_mafia}</b> раз"
    text += "\n\n⚙️ Надеть/снять титул: /mytitles"
    await msg.answer(text)


@dp.message(Command("mytitles"))
async def cmd_mytitles(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    await show_mytitles_menu(msg.from_user.id, msg)


async def show_mytitles_menu(user_id: int, msg: Message):
    owned = shared_get_user_titles(user_id)
    equipped = shared_get_equipped_titles(user_id)
    equipped_map = {e["game"]: e["title_id"] for e in equipped}

    text = "🏆 <b>Мои титулы</b>\n\n"
    rows = []

    for game_id, game_name in [("mafia", "🎭 Мафия"), ("bluff", "🎴 Блеф")]:
        titles = shared_get_titles_by_game(game_id)
        owned_titles = [t for t in titles if t["title_id"] in owned]
        if not owned_titles:
            continue

        current = equipped_map.get(game_id)
        text += f"<b>{game_name}</b>\n"
        if current:
            cur_title = shared_get_title(current)
            if cur_title:
                text += f"✅ Надет: {cur_title['icon']} {html.escape(cur_title['name'])}\n"
        else:
            text += "❌ Ничего не надето\n"
        text += "Доступные:\n"

        for t in owned_titles:
            mark = "✅" if t["title_id"] == current else ""
            text += f"   {t['icon']} {html.escape(t['name'])} {mark}\n"
            rows.append([InlineKeyboardButton(
                text=f"{t['icon']} {t['name']} {mark}",
                callback_data=f"mytitle_equip:{game_id}:{t['title_id']}"
            )])
        rows.append([InlineKeyboardButton(
            text=f"🧹 Снять титул в {game_name}",
            callback_data=f"mytitle_unequip:{game_id}"
        )])
        text += "\n"

    if not owned:
        text += "Ты пока не заработал ни одного титула.\n"

    rows.append([InlineKeyboardButton(text="🧹 Снять ВСЕ титулы", callback_data="mytitle_unequip_all")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await msg.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("mytitle_equip:"))
async def cb_mytitle_equip(call: types.CallbackQuery):
    _, game, title_id = call.data.split(":", 2)
    user_id = call.from_user.id
    success = shared_equip_title(user_id, game, title_id)
    if not success:
        await call.answer("Титул не найден или не заработан.", show_alert=True)
        return
    t = shared_get_title(title_id)
    await call.answer(f"Надет: {t['icon']} {t['name']}")
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_mytitles_menu(user_id, call.message)


@dp.callback_query(F.data.startswith("mytitle_unequip:"))
async def cb_mytitle_unequip(call: types.CallbackQuery):
    _, game = call.data.split(":", 1)
    user_id = call.from_user.id
    shared_unequip_title(user_id, game)
    await call.answer("Титул снят.")
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_mytitles_menu(user_id, call.message)


@dp.callback_query(F.data == "mytitle_unequip_all")
async def cb_mytitle_unequip_all(call: types.CallbackQuery):
    shared_unequip_all_titles(call.from_user.id)
    await call.answer("Все титулы сняты.")
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_mytitles_menu(call.from_user.id, call.message)


@dp.message(Command("mfcall"))
async def cmd_mfcall(msg: Message):
    if msg.chat.type == ChatType.PRIVATE:
        await msg.answer("Команда работает только в группе.")
        return

    chat_id = msg.chat.id
    user_ids = set()

    # Игроки текущей игры
    game = games.get(chat_id)
    if game:
        for p in game.players:
            user_ids.add(p.user_id)

    # Админы группы
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for a in admins:
            if not a.user.is_bot:
                user_ids.add(a.user.id)
    except Exception as e:
        log.warning(f"get_chat_administrators failed: {e}")

    # Если игры нет — берём всех из БД
    if not game:
        with db_lock:
            rows = db.execute("SELECT user_id FROM users LIMIT 100").fetchall()
        for r in rows:
            user_ids.add(r["user_id"])

    if not user_ids:
        await msg.answer("Некого созывать — никто ещё не играл.")
        return

    mentions = []
    for uid in user_ids:
        try:
            cm = await bot.get_chat_member(chat_id, uid)
            user = cm.user
            if user.username:
                mentions.append(f"@{user.username}")
            else:
                name = html.escape(user.full_name or "Игрок")
                mentions.append(f'<a href="tg://user?id={uid}">{name}</a>')
        except Exception:
            # Не удалось получить — просто ссылка по ID
            mentions.append(f'<a href="tg://user?id={uid}">Игрок</a>')

    # Разбиваем по 5 в сообщении, чтобы не превысить лимит
    chunk_size = 5
    chunks = [mentions[i:i+chunk_size] for i in range(0, len(mentions), chunk_size)]

    await msg.answer("📢 <b>Всеобщий сбор!</b>\n\nКто хочет играть в Мафию?")
    for chunk in chunks:
        try:
            await msg.answer(" ".join(chunk))
        except Exception as e:
            log.warning(f"mention chunk failed: {e}")


@dp.message(Command("mfroles"))
async def cmd_mfroles(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        # В группе — проверяем что игра не идёт
        game = games.get(msg.chat.id)
        if game and game.status != "lobby":
            await msg.answer("⏳ Игра идёт — описание ролей недоступно. Загляни после игры.")
            return

    text = "🎭 <b>Роли в Мафии</b>\n\n"
    
    # Группируем по сторонам
    sides = {
        "🏘️ Город": [],
        "🩸 Мафия": [],
        "🎭 Одиночка": [],
    }
    for role_id, info in ROLES_INFO.items():
        side = info["side"]
        if side in sides:
            sides[side].append((role_id, info))
        else:
            sides[side] = [(role_id, info)]

    for side_name, roles in sides.items():
        if not roles:
            continue
        text += f"<b>{side_name}</b>\n"
        for role_id, info in roles:
            text += f"\n{info['name']}\n"
            text += f"<i>{info['desc']}</i>\n"
        text += "\n"

    text += "⚙️ <i>Роли раздаются автоматически при старте игры, в зависимости от количества игроков.</i>"

    await msg.answer(text)


# ============ МАГАЗИН ============
@dp.message(Command("shop"))
async def cmd_shop(msg: Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    u = get_user(msg.from_user.id)
    balance = u["balance"] if u else 0
    text = (
        "🛒 <b>Магазин</b>\n\n"
        f"💰 Твой баланс: <b>{balance}</b> 🚬\n\n"
        "🎭 <b>Стили игры</b> — меняют эмодзи ролей во всей игре.\n\n"
        "Выбери категорию:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 Стили игры", callback_data="shop_styles")],
        [InlineKeyboardButton(text="🎨 Скины профиля", callback_data="mf_shop_skins")],
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="shop_inventory")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
    ])
    await msg.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "shop_inventory")
async def mf_inventory_menu(call: types.CallbackQuery):
    await call.answer()
    ensure_user(call.from_user.id, call.from_user.full_name, call.from_user.username)
    text, markup = inventory_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)


def shop_profile_skins_view(user_id: int):
    skin_prices = {
        "don": 500,
        "rose": 400,
        "blood": 600,
    }
    skin_ids = ["don", "rose", "blood"]

    owned = get_user_profile_skins(user_id)
    text = "🎨 <b>Скины профиля</b>\n\n"
    text += "<i>Меняют цвет карточки профиля. Видны во всех ботах.</i>\n\n"

    kb_rows = []
    for skin_id in skin_ids:
        info = shared_get_profile_skin_info(skin_id)
        price = skin_prices[skin_id]
        if skin_id in owned:
            text += f"✅ <b>{info['name']}</b> — куплено\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"✅ {info['name']}",
                callback_data="skin_nothing",
            )])
        else:
            text += f"🔒 <b>{info['name']}</b> — <b>{price}</b> 🚬\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"Купить: {info['name']} — {price} 🚬",
                callback_data=f"buy_profile_skin:{skin_id}:{price}",
            )])

    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="mf_shop_back")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.callback_query(F.data == "mf_shop_skins")
async def cb_mf_shop_skins(call: types.CallbackQuery):
    text, kb = shop_profile_skins_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("buy_profile_skin:"))
async def cb_buy_profile_skin(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Некорректный запрос покупки.", show_alert=True)
        return

    skin_id = parts[1]
    skin_prices = {"don": 500, "rose": 400, "blood": 600}
    try:
        price = int(parts[2])
    except ValueError:
        await call.answer("Некорректная цена.", show_alert=True)
        return
    if skin_id not in skin_prices or price != skin_prices[skin_id]:
        await call.answer("Некорректный скин или цена.", show_alert=True)
        return

    user_id = call.from_user.id
    ensure_user(user_id, call.from_user.full_name, call.from_user.username)
    if skin_id in get_user_profile_skins(user_id):
        await call.answer("Уже куплено.", show_alert=True)
        return
    if not shared_spend_balance(user_id, price, reason="buy_profile_skin", game="mafia"):
        await call.answer("❌ Недостаточно бэриков!", show_alert=True)
        return

    try:
        with db_lock:
            db.execute(
                "INSERT OR IGNORE INTO user_profile_skins (user_id, skin_id, purchased_at) VALUES (?, ?, ?)",
                (user_id, skin_id, int(time.time())),
            )
            db.commit()
    except Exception as e:
        log.warning(f"profile_skin insert failed: {e}")
        await call.answer("Не удалось сохранить покупку. Обратись к администратору.", show_alert=True)
        return

    info = shared_get_profile_skin_info(skin_id)
    await call.answer(f"✅ Куплено: {info['name']}", show_alert=True)
    text, kb = shop_profile_skins_view(user_id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "shop_styles")
@dp.callback_query(F.data == "mf_shop_styles")
async def cb_shop_styles(call: types.CallbackQuery):
    styles = get_all_styles()
    owned = get_user_styles(call.from_user.id)
    u = get_user(call.from_user.id)
    balance = u["balance"] if u else 0

    text = f"🎭 <b>Стили игры</b>\n💰 Баланс: <b>{balance}</b> 🚬\n\n"
    rows = []
    for s in styles:
        if s["style_id"] in owned:
            mark = " ✅"
            btn_text = f"{s['name']} — куплено{mark}"
        else:
            if s["price"] == 0:
                mark = ""
                btn_text = f"{s['name']} — бесплатно"
            else:
                mark = " 🔒"
                btn_text = f"{s['name']} — {s['price']} 🚬{mark}"
        text += (
            f"<b>{s['name']}</b>\n"
            f"   {s['icon_mafia']} {s['icon_sheriff']} {s['icon_doctor']} {s['icon_civilian']}\n"
        )
        if s["price"] > 0 and s["style_id"] not in owned:
            text += f"   💰 Цена: <b>{s['price']}</b> 🚬\n"
        text += "\n"
        if s["style_id"] in owned:
            rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"style_owned:{s['style_id']}")])
        else:
            rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"style_buy:{s['style_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_back")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("style_buy:"))
async def cb_style_buy(call: types.CallbackQuery):
    style_id = call.data.split(":", 1)[1]
    s = get_style(style_id)
    if not s:
        await call.answer("Стиль не найден.", show_alert=True)
        return
    if has_style(call.from_user.id, style_id):
        await call.answer("Уже куплено.", show_alert=True)
        return
    if not buy_style(call.from_user.id, style_id):
        await call.answer("❌ Недостаточно бэриков!", show_alert=True)
        return
    await call.answer(f"✅ Куплено: {s['name']}", show_alert=True)
    # Обновляем сообщение
    await cb_shop_styles(call)


@dp.callback_query(F.data.startswith("style_owned:"))
async def cb_style_owned(call: types.CallbackQuery):
    style_id = call.data.split(":", 1)[1]
    s = get_style(style_id)
    if not s:
        await call.answer("Стиль не найден.", show_alert=True)
        return
    if not has_style(call.from_user.id, style_id):
        await call.answer("Сначала купи.", show_alert=True)
        return
    await call.answer(
        f"✅ У тебя есть: {s['name']}. Выбрать можно в /mfsettings.",
        show_alert=True,
    )


@dp.callback_query(F.data == "shop_my_styles")
async def cb_shop_my_styles(call: types.CallbackQuery):
    owned = get_user_styles(call.from_user.id)
    if not owned:
        await call.answer("У тебя пока нет купленных стилей.", show_alert=True)
        return
    styles = get_all_styles()
    text = "🎒 <b>Мои стили</b>\n\n"
    for s in styles:
        if s["style_id"] in owned:
            text += f"<b>{s['name']}</b>\n   {s['icon_mafia']} {s['icon_sheriff']} {s['icon_doctor']} {s['icon_civilian']}\n\n"
    text += "Выбрать активный стиль можно в /mfsettings."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_back")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "shop_back")
@dp.callback_query(F.data == "mf_shop_back")
async def cb_shop_back(call: types.CallbackQuery):
    u = get_user(call.from_user.id)
    balance = u["balance"] if u else 0
    text = (
        "🛒 <b>Магазин</b>\n\n"
        f"💰 Твой баланс: <b>{balance}</b> 🚬\n\n"
        "🎭 <b>Стили игры</b> — меняют эмодзи ролей во всей игре.\n\n"
        "Выбери категорию:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 Стили игры", callback_data="shop_styles")],
        [InlineKeyboardButton(text="🎨 Скины профиля", callback_data="mf_shop_skins")],
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="shop_inventory")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu_back")],
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await call.answer()


# ============ ЗРИТЕЛИ ============
@dp.message()
async def filter_zriteli(msg: Message):
    if msg.chat.type != ChatType.GROUP and msg.chat.type != ChatType.SUPERGROUP:
        return
    if not msg.text or not msg.from_user:
        return
    game = games.get(msg.chat.id)
    if not game or game.status == "lobby":
        return
    # Игра идёт
    if msg.from_user.id == bot.id:
        return
    # Хост всегда может писать
    if msg.from_user.id == game.host_id:
        return

    player = game.player_by_id(msg.from_user.id)

    # Если это НЕ игрок — это зритель, удаляем и мьютим
    if not player:
        if game.silence:
            try:
                await msg.delete()
            except Exception:
                pass
            await mute_user(msg.chat.id, msg.from_user.id, minutes=120)
            try:
                await bot.send_message(
                    msg.chat.id,
                    f"🔇 {html.escape(msg.from_user.full_name)} — зрителям нельзя писать во время игры."
                )
            except Exception:
                pass
        return

    # Если игрок мёртв — тоже молчит
    if not player.alive:
        if game.silence:
            try:
                await msg.delete()
            except Exception:
                pass
        return

    # Ночь — молчат все кроме бота
    if game.status == "night" and game.silence:
        try:
            await msg.delete()
        except Exception:
            pass
        return


# ============ ЗАПУСК ============
async def main():
    init_db()
    init_shared_db()
    log.info("Бот Мафии запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
