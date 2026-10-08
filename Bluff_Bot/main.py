import asyncio
import html
import logging
import os
import random
import sqlite3
import tempfile
import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import sys
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

from env_loader import get_bot_token
BOT_TOKEN = get_bot_token("BLUFF_BOT_TOKEN", os.path.dirname(os.path.abspath(__file__)))
ADMIN_ID = 352018610
REQUIRED_CHANNEL = "@botlabgame"

SWAP_CARDS_PRICE = 10
INSURANCE_PRICE = 15
HUNCH_PRICE = 20
WIN_REWARD = 20
MAX_PLAYERS = 6
MIN_PLAYERS = 2
CARDS_PER_PLAYER = 6
MAX_PLAY_CARDS = 3

RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["♠", "♥", "♦", "♣"]
RANK_NAMES = {
    "6": "шестёрки",
    "7": "семёрки",
    "8": "восьмёрки",
    "9": "девятки",
    "10": "десятки",
    "J": "вальты",
    "Q": "дамы",
    "K": "короли",
    "A": "тузы",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bluff_bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db = sqlite3.connect("database.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = threading.Lock()

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


def init_db():
    with db_lock:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                equipped_title TEXT DEFAULT 'Новичок',
                equipped_card_skin TEXT DEFAULT 'Классическая'
            )
            """
        )
        existing_cols = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        new_columns = {
            "username": "TEXT",
            "balance": "INTEGER DEFAULT 0",
            "games_played": "INTEGER DEFAULT 0",
            "wins": "INTEGER DEFAULT 0",
            "equipped_title": "TEXT DEFAULT 'Новичок'",
            "equipped_card_skin": "TEXT DEFAULT 'Классическая'",
        }
        for col_name, col_type in new_columns.items():
            if col_name not in existing_cols:
                db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS shop (
                item_id TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                price INTEGER,
                description TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER,
                item_id TEXT,
                is_equipped INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, item_id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile_skins (
                user_id INTEGER,
                skin_id TEXT,
                purchased_at INTEGER,
                PRIMARY KEY (user_id, skin_id)
            )
            """
        )

        # перенос владений из legacy-таблицы (должен идти ДО чистки дубликатов)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "user_inventory" in tables:
            db.execute(
                """
                INSERT OR IGNORE INTO inventory (user_id, item_id, is_equipped)
                SELECT user_id, item_id, is_equipped FROM user_inventory
                """
            )

        default_items = [
            ("skin_gold", "Скин карт 'Золотой' 🥇", "skin", 300, "Золотая рубашка карт в игре"),
            ("skin_cyber", "Скин карт 'Киберпанк' ⚡", "skin", 600, "Неоновый фиолетово-голубой градиент профиля"),
            ("skin_street", "Скин карт 'Уличный Неон' 🌃", "skin", 600, "Тёмно-синий с розовым неоном"),
        ]
        for item in default_items:
            db.execute("INSERT OR IGNORE INTO shop VALUES (?, ?, ?, ?, ?)", item)

        db.execute(
            """
            INSERT OR IGNORE INTO user_profile_skins (user_id, skin_id, purchased_at)
            SELECT user_id,
                   CASE item_id
                       WHEN 'skin_gold' THEN 'gold'
                       WHEN 'skin_cyber' THEN 'cyber'
                       WHEN 'skin_street' THEN 'street'
                   END,
                   CAST(strftime('%s', 'now') AS INTEGER)
            FROM inventory
            WHERE item_id IN ('skin_gold', 'skin_cyber', 'skin_street')
            """
        )

        # синхронизация названий скинов с БД (эмодзи применяются и к старым записям)
        canonical_names = {
            "skin_gold": "Скин карт 'Золотой' 🥇",
            "skin_cyber": "Скин карт 'Киберпанк' ⚡",
            "skin_street": "Скин карт 'Уличный Неон' 🌃",
        }
        old_to_new_names = {
            "Скин карт 'Золотой'": "Скин карт 'Золотой' 🥇",
            "Скин карт 'Киберпанк'": "Скин карт 'Киберпанк' ⚡",
            "Скин карт 'Уличный Неон'": "Скин карт 'Уличный Неон' 🌃",
        }
        for item_id, new_name in canonical_names.items():
            db.execute("UPDATE shop SET name = ? WHERE item_id = ?", (new_name, item_id))
        for old_name, new_name in old_to_new_names.items():
            db.execute(
                "UPDATE users SET equipped_card_skin = ? WHERE equipped_card_skin = ?",
                (new_name, old_name),
            )

        # --- чистка магазина: legacy-дубликаты скинов сливаем в актуальные товары ---
        legacy_merges = {
            "skin_street_neon": "skin_street",
            "skin_gold_cyber": "skin_cyber",
            "skin_neon": "skin_cyber",
        }
        for old_id, new_id in legacy_merges.items():
            db.execute(
                "INSERT OR IGNORE INTO inventory (user_id, item_id, is_equipped) "
                "SELECT user_id, ?, 0 FROM inventory WHERE item_id = ?",
                (new_id, old_id),
            )
            db.execute("DELETE FROM inventory WHERE item_id = ?", (old_id,))
            db.execute("DELETE FROM shop WHERE item_id = ?", (old_id,))

        # переименования старых названий скинов в профилях игроков
        renames = {
            "🌃 Уличный Неон": "Скин карт 'Уличный Неон' 🌃",
            "⚡ Киберпанк Голд": "Скин карт 'Киберпанк' ⚡",
            "Скин карт 'Неоновый'": "Скин карт 'Киберпанк' ⚡",
        }
        for old_name, new_name in renames.items():
            db.execute(
                "UPDATE users SET equipped_card_skin = ? WHERE equipped_card_skin = ?",
                (new_name, old_name),
            )

        # is_equipped приводим в порядок: надето ровно то, что указано в профиле
        db.execute("UPDATE inventory SET is_equipped = 0")
        db.execute(
            """
            UPDATE inventory SET is_equipped = 1
            WHERE EXISTS (
                SELECT 1 FROM shop s
                WHERE s.item_id = inventory.item_id
                  AND (
                        (s.category = 'title' AND s.name = (
                            SELECT u.equipped_title FROM users u WHERE u.user_id = inventory.user_id
                        ))
                     OR (s.category = 'skin' AND s.name = (
                            SELECT u.equipped_card_skin FROM users u WHERE u.user_id = inventory.user_id
                        ))
                  )
            )
            """
        )

        # титулы больше не продаются — они зарабатываются через shared_db
        db.execute("DELETE FROM shop WHERE category = 'title'")
        db.execute("DELETE FROM inventory WHERE item_id NOT IN (SELECT item_id FROM shop)")

        db.commit()


def ensure_user(user_id: int, username: str = "Игрок", tg_username: str = None):
    shared_ensure_user(user_id, username, tg_username)


def get_user(user_id: int):
    return shared_get_user(user_id)


def add_bariki(user_id: int, username: str = "Игрок", amount: int = 0):
    shared_add_balance(user_id, amount, reason="bluff_game", game="bluff")


def spend_bariki(user_id: int, username: str = "Игрок", amount: int = 0) -> bool:
    return shared_spend_balance(user_id, amount, reason="bluff_purchase", game="bluff")


def get_shop_items():
    with db_lock:
        return db.execute(
            "SELECT item_id, name, category, price, description FROM shop ORDER BY price"
        ).fetchall()


def get_user_inventory(user_id: int):
    with db_lock:
        return db.execute(
            """
            SELECT i.item_id, s.name, s.category, i.is_equipped
            FROM inventory i
            JOIN shop s ON i.item_id = s.item_id
            WHERE i.user_id = ?
            """,
            (user_id,),
        ).fetchall()


async def record_game_result(winner_id: int, player_ids: List[int], names_map: dict = None):
    for uid in player_ids:
        won = (uid == winner_id)
        shared_record_game(uid, "bluff", won=won)
        if won:
            shared_add_balance(uid, WIN_REWARD, reason="bluff_win", game="bluff")

    for uid in player_ids:
        new_titles = shared_check_and_award_titles(uid, "bluff")
        for t in new_titles:
            name = names_map.get(uid, "Игрок") if names_map else "Игрок"
            try:
                # если бот умеет писать в чат - отправь в чат, если нет - в личку
                # в Блефе бот пишет в чат через game.chat_id, но у нас его нет
                # отправим поздравление в личку
                await bot.send_message(
                    uid,
                    f"🎉 Ты получил новый титул:\n"
                    f"{t['icon']} <b>{html.escape(t['name'])}</b>"
                    + (f"\n💰 Бонус: <b>+{t['bonus']}</b> 🚬" if t['bonus'] > 0 else "")
                )
            except Exception as e:
                log.warning(f"Не удалось отправить поздравление: {e}")


PROFILE_SKINS = {
    "Классическая": {
        "bg": "linear-gradient(135deg, #0f172a 0%, #1e1b4b 45%, #4c1d95 100%)",
        "accent": "#a78bfa",
        "accent2": "#38bdf8",
        "card_bg": "linear-gradient(180deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04))",
        "card_border": "rgba(255,255,255,0.18)",
    },
    "Золотой": {
        "bg": "linear-gradient(135deg, #1a1206 0%, #4a2f0a 45%, #b8860b 100%)",
        "accent": "#fbbf24",
        "accent2": "#fde68a",
        "card_bg": "linear-gradient(180deg, rgba(251,191,36,0.18), rgba(120,53,15,0.35))",
        "card_border": "rgba(251,191,36,0.55)",
    },
    "Киберпанк": {
        "bg": "linear-gradient(135deg, #0b001f 0%, #2a0a5e 45%, #00e5ff 100%)",
        "accent": "#22d3ee",
        "accent2": "#a855f7",
        "card_bg": "linear-gradient(180deg, rgba(168,85,247,0.25), rgba(34,211,238,0.10))",
        "card_border": "rgba(34,211,238,0.55)",
    },
    "Уличный Неон": {
        "bg": "linear-gradient(135deg, #050b2e 0%, #111a52 40%, #ff2d95 100%)",
        "accent": "#ff2d95",
        "accent2": "#38bdf8",
        "card_bg": "linear-gradient(180deg, rgba(255,45,149,0.20), rgba(56,189,248,0.10))",
        "card_border": "rgba(255,45,149,0.55)",
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
    title: str,
    skin: str = "classic",
) -> Tuple[str, str]:
    winrate = round((wins / games * 100), 1) if games > 0 else 0.0
    safe_name = html.escape(username)
    safe_title = html.escape(title or "Новичок")
    palette = shared_get_profile_skin_info(normalize_skin(skin))

    css = f"""
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      width: 620px;
      height: 360px;
      overflow: hidden;
    }}
    body {{
      background: {palette['bg']};
      font-family: "Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji",
                   Segoe UI, Tahoma, Geneva, Verdana, sans-serif;
      color: #ffffff;
      display: flex;
      align-items: center;
      justify-content: center;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }}
    .card {{
      width: 560px;
      height: 300px;
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
      min-height: 84px;
    }}
    .header-left {{
      min-width: 0;
      flex: 1 1 auto;
      overflow: hidden;
    }}
    .title-badge {{
      display: inline-block;
      background: linear-gradient(90deg, {palette['accent']}, {palette['accent2']});
      color: #111827;
      font-weight: 800;
      font-size: 12px;
      padding: 4px 12px;
      border-radius: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 8px;
      max-width: 100%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .username {{
      font-size: 24px;
      font-weight: 700;
      color: #f8fafc;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
    }}
    .balance-box {{
      background: linear-gradient(180deg, rgba(245,158,11,0.22), rgba(245,158,11,0.08));
      border: 1px solid rgba(251,191,36,0.45);
      padding: 10px 14px;
      border-radius: 14px;
      text-align: right;
      min-width: 130px;
      flex: 0 0 auto;
    }}
    .balance-label {{ font-size: 11px; color: #fbbf24; text-transform: uppercase; }}
    .balance-value {{ font-size: 20px; font-weight: 700; color: #fbbf24; }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }}
    .stat-card {{
      background: linear-gradient(180deg, rgba(15,23,42,0.55), rgba(15,23,42,0.25));
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 12px 8px;
      text-align: center;
      overflow: hidden;
    }}
    .stat-val {{ font-size: 22px; font-weight: 700; color: {palette['accent2']}; }}
    .stat-lbl {{ font-size: 12px; color: #cbd5e1; margin-top: 4px; }}
    """

    body = f"""
    <div class="card">
      <div class="header">
        <div class="header-left">
          <div class="title-badge">{safe_title}</div>
          <div class="username">{safe_name}</div>
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


async def generate_profile_card(
    username: str,
    balance: int,
    games: int,
    wins: int,
    title: str,
    skin: str = "Классическая",
    user_id: Optional[int] = None,
) -> str:
    # Проверяем кэш (только если передан user_id)
    if user_id is not None:
        cached = profile_card_cache.get(user_id)
        if cached:
            cached_path, cached_time = cached
            if time.time() - cached_time < CACHE_TTL and os.path.exists(cached_path):
                log.info("Profile card cache hit for user %s", user_id)
                return cached_path

    # Ограничиваем: не более 2 одновременных рендеров Chromium
    async with render_semaphore:
        # Ещё раз проверяем кэш — вдруг другая корутина его уже заполнила
        if user_id is not None:
            cached = profile_card_cache.get(user_id)
            if cached:
                cached_path, cached_time = cached
                if time.time() - cached_time < CACHE_TTL and os.path.exists(cached_path):
                    return cached_path

        html_body, css = render_profile_html(username, balance, games, wins, title, skin)
        filename = f"profile_{uuid.uuid4().hex}.png"

        def _render():
            engine = get_hti()
            paths = engine.screenshot(
                html_str=html_body,
                css_str=css,
                save_as=filename,
                size=(620, 360),
            )
            if paths:
                return paths[0]
            return os.path.join(engine.output_path, filename)

        loop = asyncio.get_running_loop()
        img_path = await loop.run_in_executor(None, _render)

        # Сохраняем в кэш
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
        ]
    )


def make_deck() -> List[str]:
    return [f"{rank}{suit}" for rank in RANKS for suit in SUITS]


def card_rank(card: str) -> str:
    return card[:-1]


def skin_prefix(skin: Optional[str]) -> str:
    value = skin or ""
    if "Золот" in value:
        return "🥇"
    if "Кибер" in value:
        return "🌐"
    if "Улич" in value or "Неон" in value:
        return "💠"
    return "🃏"


def format_card(card: str, skin: Optional[str] = None) -> str:
    return f"{skin_prefix(skin)}{card}"


@dataclass
class Player:
    user_id: int
    name: str
    cards: List[str] = field(default_factory=list)
    insurance: bool = False
    selected: List[int] = field(default_factory=list)


@dataclass
class Game:
    chat_id: int
    host_id: int
    players: List[Player] = field(default_factory=list)
    status: str = "lobby"
    turn: int = 0
    responder: int = 0
    last_cards: List[str] = field(default_factory=list)
    last_claim: Optional[str] = None
    last_player_id: Optional[int] = None
    waiting_challenge: bool = False
    deck: List[str] = field(default_factory=list)
    phase: str = "idle"

    def player_by_id(self, user_id: int) -> Optional[Player]:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None

    def current_player(self) -> Player:
        return self.players[self.turn]

    def next_index(self, idx: Optional[int] = None) -> int:
        return ((idx if idx is not None else self.turn) + 1) % len(self.players)

    def names_line(self) -> str:
        return ", ".join(html.escape(p.name) for p in self.players)


games: Dict[int, Game] = {}
user_chat: Dict[int, int] = {}
render_semaphore = asyncio.Semaphore(2)
profile_card_cache: Dict[int, tuple] = {}  # user_id -> (путь_к_png, время_создания)
CACHE_TTL = 300  # 5 минут — столько живёт закэшированная карточка


def lobby_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Присоединиться", callback_data="g_join"),
                InlineKeyboardButton(text="Выйти", callback_data="g_leave"),
            ],
            [InlineKeyboardButton(text="Начать игру", callback_data="g_start")],
        ]
    )


def challenge_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Верю", callback_data="g_believe"),
                InlineKeyboardButton(text="Не верю", callback_data="g_bluff"),
            ],
            [
                InlineKeyboardButton(text=f"Обмен ({SWAP_CARDS_PRICE})", callback_data="g_swap"),
                InlineKeyboardButton(text=f"Страховка ({INSURANCE_PRICE})", callback_data="g_insur"),
            ],
            [InlineKeyboardButton(text=f"Предчувствие ({HUNCH_PRICE})", callback_data="g_hunch")],
        ]
    )


def hand_kb(player: Player) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for idx, card in enumerate(player.cards):
        mark = "✅" if idx in player.selected else ""
        row.append(InlineKeyboardButton(text=f"{mark}{card}", callback_data=f"g_sel:{idx}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Сыграть выбранные", callback_data="g_claim")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ranks_kb() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for rank in RANKS:
        row.append(InlineKeyboardButton(text=rank, callback_data=f"g_rank:{rank}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Назад к картам", callback_data="g_backhand")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def find_game_for_user(user_id: int) -> Optional[Game]:
    chat_id = user_chat.get(user_id)
    if chat_id is None:
        return None
    return games.get(chat_id)


async def send_hand(player: Player, text: str):
    try:
        await bot.send_message(player.user_id, text, reply_markup=hand_kb(player))
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def start_turn(game: Game, chat_id: int):
    player = game.current_player()
    player.selected = []
    game.phase = "play"
    game.waiting_challenge = False
    mention = html.escape(player.name)

    # Одно большое сообщение с ходом + подсказкой про /карты
    await bot.send_message(
        chat_id,
        f"🎲 <b>Ход игрока {mention}</b>\n\n"
        f"Нужно положить 1–{MAX_PLAY_CARDS} карты лицом вниз и назвать номинал.\n\n"
        f"📌 {mention}, если карты <b>не пришли</b> в личку — открой бота и напиши ему "
        f"команду <b>/карты</b>.",
    )

    ok = await send_hand(
        player,
        f"Твои карты ({len(player.cards)}). "
        f"Выбери от 1 до {MAX_PLAY_CARDS} и нажми «Сыграть выбранные».\n\n"
        f"Если что — всегда можно написать /карты ещё раз.",
    )

    if not ok:
        await bot.send_message(
            chat_id,
            f"⚠️ <b>{mention}</b>, я не могу написать тебе в личку.\n\n"
            f"Открой бота в личке и напиши ему <b>/start</b>. "
            f"Потом можешь повторить свои карты командой <b>/карты</b> — "
            f"иначе не получится сделать ход.",
        )


async def finish_game(game: Game, winner: Player, chat_id: int):
    ids = [p.user_id for p in game.players]
    names_map = {p.user_id: p.name for p in game.players}
    await record_game_result(winner.user_id, ids, names_map)
    for p in game.players:
        user_chat.pop(p.user_id, None)
    games.pop(chat_id, None)
    await bot.send_message(
        chat_id,
        f"🏆 Победитель: <b>{html.escape(winner.name)}</b>!\n"
        f"Награда: {WIN_REWARD} бэриков.\n"
        f"Статистика обновлена. Новая игра: /newgame",
    )


async def after_play(game: Game, player: Player, claim: str, chat_id: int):

    responder = game.players[game.responder]
    await bot.send_message(
        chat_id,
        f"🃏 <b>{html.escape(player.name)}</b> кладёт {len(game.last_cards)} карт(ы) как "
        f"<b>{RANK_NAMES.get(claim, claim)}</b>.\n\n"
        f"👉 Отвечает <b>{html.escape(responder.name)}</b>: <b>Верю</b> или <b>Не верю</b>.\n\n"
        f"📌 <b>{html.escape(responder.name)}</b>, если после ответа тебе не придут "
        f"твои карты — напиши боту в личку <b>/карты</b>.",
        reply_markup=challenge_kb(),
    )
    if not player.cards:
        await bot.send_message(
            chat_id,
            f"⚡ <b>{html.escape(player.name)}</b> сбросил последнюю карту. "
            f"Если ему поверят или блеф не подтвердится — победа.",
        )


@dp.message(CommandStart())
async def cmd_start(msg: Message):
    ensure_user(msg.from_user.id, msg.from_user.full_name)
    if not await is_subscribed(msg.from_user.id):
        await msg.answer(
            f"Чтобы пользоваться ботом, подпишись на канал {REQUIRED_CHANNEL}.",
            reply_markup=sub_keyboard(),
        )
        return
    await msg.answer(
        f"Привет, {html.escape(msg.from_user.first_name or 'игрок')}!\n\n"
        "Это бот игры «Блеф».\n"
        "Команды:\n"
        "🥰/profile — карточка профиля\n"
        "🛒/shop — магазин титулов и скинов\n"
        "🎒/inventory — инвентарь\n"
        "🏆 /mytitles — надеть/снять титулы\n"
        "🎯 /titles — титулы Блефа\n"
        "♠️ /newgame — создать игру в групповом чате\n"
        "♠️/stopgame — остановить игру (хост или админ)"
    )


@dp.message(Command("profile"))
async def cmd_profile(msg: Message):
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    u = shared_get_user(msg.from_user.id)
    if not u:
        await msg.answer("Не удалось получить профиль.")
        return

    username = u["username"] or msg.from_user.full_name
    balance = u["balance"] or 0
    games_played = u["games_played"] or 0
    wins = u["wins"] or 0

    # Скин карточки — общий
    skin_id = shared_get_equipped_card_skin(msg.from_user.id)

    # Титулы — из shared
    equipped = shared_get_equipped_titles(msg.from_user.id)
    if equipped:
        titles_str = " ".join(f"{t['icon']} {html.escape(t['name'])}" for t in equipped)
    else:
        titles_str = "не выбраны"

    status = await msg.answer("Генерирую карточку профиля...")
    caption = (
        f"👤 Профиль <b>{html.escape(username)}</b>\n"
        f"🏆 Титулы: {titles_str}\n"
        f"🚬 Баланс: {balance} бэриков\n"
        f"🎮 Игр: {games_played} | Побед: {wins}"
    )

    invalidate_profile_cache(msg.from_user.id)

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


@dp.message(Command("mytitles"))
async def cmd_mytitles(msg: Message):
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
    invalidate_profile_cache(user_id)
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
    invalidate_profile_cache(user_id)
    await call.answer("Титул снят.")
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_mytitles_menu(user_id, call.message)


@dp.callback_query(F.data == "mytitle_unequip_all")
async def cb_mytitle_unequip_all(call: types.CallbackQuery):
    shared_unequip_all_titles(call.from_user.id)
    invalidate_profile_cache(call.from_user.id)
    await call.answer("Все титулы сняты.")
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_mytitles_menu(call.from_user.id, call.message)


@dp.message(Command("titles"))
async def cmd_titles(msg: Message):
    ensure_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username)
    owned = shared_get_user_titles(msg.from_user.id)
    stats = shared_get_game_stats(msg.from_user.id)
    games_in_bluff = 0
    for s in stats:
        if s["game"] == "bluff":
            games_in_bluff = s["games_played"]

    bluff_titles = shared_get_titles_by_game("bluff")
    text = "🏆 <b>Титулы Блефа</b>\n\n"
    for t in bluff_titles:
        if t["title_id"] in owned:
            status = "✅"
        else:
            status = f"🔒 ({t['min_games']} игр)"
        text += f"{t['icon']} <b>{html.escape(t['name'])}</b> — {status}\n"
        if t["bonus"] > 0:
            text += f"   <i>Бонус: +{t['bonus']} 🚬</i>\n"
    text += f"\n📊 Ты сыграл в Блеф: <b>{games_in_bluff}</b> раз"
    text += "\n\n⚙️ Надеть/снять титул: /mytitles"
    await msg.answer(text)


@dp.message(Command("top"))
async def cmd_top(msg: Message):
    rows = shared_get_top_players(10)
    if not rows:
        await msg.answer("Пока никто не играл.")
        return
    text = "🏆 <b>Топ игроков (все игры)</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = f"@{r['tg_username']}" if r["tg_username"] else html.escape(r["username"] or "Игрок")
        text += f"{i}. {name} — {r['wins']} побед / {r['games_played']} игр\n"
    await msg.answer(text)


SHOP_CATEGORY_ICONS = {"skin": "🎴", "title": "🏆", "other": "🎁"}
SHOP_CATEGORY_TITLES = {"skin": "Скины карт", "title": "Титулы", "other": "Прочее"}


def shop_group(category: str) -> str:
    return category if category in ("skin", "title") else "other"


def shop_menu_view() -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        "🛒 <b>Магазин</b>\n\n"
        "🎴 <b>Скины карт</b> — оформление карт в игре.\n"
        "🎨 <b>Скины профиля</b> — оформление карточки профиля.\n\n"
        "Выбери категорию:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎴 Скины карт", callback_data="shopcat:skin")],
        [InlineKeyboardButton(text="🎨 Скины профиля", callback_data="shop_profile_skins")],
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="shopcat:inv")],
    ])
    return text, kb


def shop_category_view(user_id: int, group: str) -> Tuple[str, InlineKeyboardMarkup]:
    icon = SHOP_CATEGORY_ICONS[group]
    owned = {row["item_id"] for row in get_user_inventory(user_id)}
    items = [row for row in get_shop_items() if shop_group(row["category"]) == group]
    text = f"{icon} <b>{SHOP_CATEGORY_TITLES[group]}</b>\n\n"
    kb = []
    for row in items:
        mark = " ✅" if row["item_id"] in owned else ""
        text += f"{icon} <b>{html.escape(row['name'])}</b>{mark} — <b>{row['price']}</b> 🚬\n"
        if row["description"]:
            text += f"   <i>{html.escape(row['description'])}</i>\n"
        text += "\n"
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {row['name']} — {row['price']} 🚬{mark}",
                    callback_data=f"buy_{row['item_id']}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="shopcat:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


def shop_profile_skins_view(user_id: int):
    """Список скинов профиля Блефа для покупки."""
    skin_prices = {
        "gold": 200,
        "cyber": 400,
        "street": 300,
    }
    skin_ids = ["gold", "cyber", "street"]

    with db_lock:
        rows = db.execute(
            "SELECT skin_id FROM user_profile_skins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    owned = {row["skin_id"] for row in rows}

    text = "🎨 <b>Скины профиля</b>\n\n"
    text += "<i>Меняют цвет карточки профиля. Видны во всех ботах.</i>\n\n"

    kb_rows = []
    for skin_id in skin_ids:
        info = shared_get_profile_skin_info(skin_id)
        price = skin_prices[skin_id]
        if skin_id in owned:
            text += f"✅ <b>{info['name']}</b> — куплено (<b>{price}</b> 🚬)\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"✅ {info['name']} — {price} 🚬",
                callback_data="skin_nothing",
            )])
        else:
            text += f"🔒 <b>{info['name']}</b> — <b>{price}</b> 🚬\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"Купить: {info['name']} — {price} 🚬",
                callback_data=f"buy_profile_skin:{skin_id}:{price}",
            )])

    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="shop_back")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.message(Command("shop"))
async def cmd_shop(msg: Message):
    ensure_user(msg.from_user.id, msg.from_user.full_name)
    text, markup = shop_menu_view()
    await msg.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("shopcat:"))
async def cb_shop_category(call: CallbackQuery):
    group = call.data.split(":", 1)[1]
    if group == "menu":
        text, markup = shop_menu_view()
    elif group == "inv":
        text, markup = inventory_view(call.from_user.id)
    elif group in SHOP_CATEGORY_ICONS:
        text, markup = shop_category_view(call.from_user.id, group)
    else:
        await call.answer("Категория не найдена.", show_alert=True)
        return
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)
    await call.answer()


@dp.callback_query(F.data == "shop_profile_skins")
async def cb_shop_profile_skins(call: CallbackQuery):
    text, kb = shop_profile_skins_view(call.from_user.id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "shop_back")
async def cb_shop_back(call: CallbackQuery):
    text, kb = shop_menu_view()
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("buy_profile_skin:"))
async def cb_buy_profile_skin(call: CallbackQuery):
    parts = call.data.split(":")
    skin_prices = {"gold": 200, "cyber": 400, "street": 300}
    if len(parts) != 3 or parts[1] not in skin_prices:
        await call.answer("Некорректный скин.", show_alert=True)
        return
    skin_id = parts[1]
    try:
        price = int(parts[2])
    except ValueError:
        await call.answer("Некорректная цена.", show_alert=True)
        return
    if price != skin_prices[skin_id]:
        await call.answer("Некорректная цена.", show_alert=True)
        return

    user_id = call.from_user.id
    ensure_user(user_id, call.from_user.full_name, call.from_user.username)
    with db_lock:
        owned = db.execute(
            "SELECT 1 FROM user_profile_skins WHERE user_id = ? AND skin_id = ?",
            (user_id, skin_id),
        ).fetchone()
    if owned:
        await call.answer("Уже куплено.", show_alert=True)
        return
    if not shared_spend_balance(user_id, price, reason="buy_profile_skin", game="bluff"):
        await call.answer("❌ Недостаточно бэриков!", show_alert=True)
        return

    try:
        with db_lock:
            db.execute(
                "INSERT OR IGNORE INTO user_profile_skins (user_id, skin_id, purchased_at) VALUES (?, ?, ?)",
                (user_id, skin_id, int(time.time())),
            )
            db.commit()
    except Exception as exc:
        log.warning("profile_skin insert failed: %s", exc)
        await call.answer("Не удалось сохранить покупку.", show_alert=True)
        return

    info = shared_get_profile_skin_info(skin_id)
    await call.answer(f"✅ Куплено: {info['name']}", show_alert=True)
    text, kb = shop_profile_skins_view(user_id)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_item(call: CallbackQuery):
    item_id = call.data.replace("buy_", "", 1)
    user_id = call.from_user.id
    ensure_user(user_id, call.from_user.full_name)

    with db_lock:
        item = db.execute(
            "SELECT name, price, category FROM shop WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        has_item = db.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        ).fetchone()

    if not item:
        await call.answer("Товар не найден!", show_alert=True)
        return
    if has_item:
        await call.answer("Этот предмет уже куплен. Надень его в /inventory.", show_alert=True)
        return
    if not spend_bariki(user_id, call.from_user.full_name, item["price"]):
        await call.answer("❌ Недостаточно бэриков 🚬!", show_alert=True)
        return

    # покупка сразу применяется к профилю: предмет надевается автоматически
    category = item["category"]
    with db_lock:
        db.execute(
            "UPDATE inventory SET is_equipped = 0 "
            "WHERE user_id = ? AND item_id IN (SELECT item_id FROM shop WHERE category = ?)",
            (user_id, category),
        )
        db.execute(
            "INSERT INTO inventory (user_id, item_id, is_equipped) VALUES (?, ?, 1)",
            (user_id, item_id),
        )
        if category == "title":
            db.execute("UPDATE users SET equipped_title = ? WHERE user_id = ?", (item["name"], user_id))
        elif category == "skin":
            db.execute("UPDATE users SET equipped_card_skin = ? WHERE user_id = ?", (item["name"], user_id))
        db.commit()

    invalidate_profile_cache(user_id)
    await call.answer(f"✅ Куплено и надето: {item['name']}", show_alert=True)

    text, markup = shop_category_view(user_id, shop_group(category))
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)


def inventory_view(user_id: int):
    """Главное меню инвентаря — категории."""
    with db_lock:
        rows = db.execute(
            """
            SELECT i.item_id, s.name, s.category
            FROM inventory i
            JOIN shop s ON i.item_id = s.item_id
            WHERE i.user_id = ? AND s.category = 'skin'
            """,
            (user_id,)
        ).fetchall()
        profile_rows = db.execute(
            "SELECT skin_id FROM user_profile_skins WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    cards_count = len(rows)
    owned_profile = {"classic"} | {row["skin_id"] for row in profile_rows}
    profile_count = len(owned_profile)

    text = "🎒 <b>Инвентарь</b>\n\n"
    text += "Выбери категорию:\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎴 Скины карт ({cards_count})", callback_data="inv_cat:cards")],
        [InlineKeyboardButton(text=f"🎨 Скины профиля ({profile_count})", callback_data="inv_cat:profile")],
    ])
    return text, kb


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
    if cat == "cards":
        text, kb = inventory_cards_view(user_id)
    elif cat == "profile":
        text, kb = inventory_profile_view(user_id)
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


def inventory_cards_view(user_id: int):
    with db_lock:
        rows = db.execute(
            """
            SELECT i.item_id, s.name, i.is_equipped
            FROM inventory i
            JOIN shop s ON i.item_id = s.item_id
            WHERE i.user_id = ? AND s.category = 'skin'
            """,
            (user_id,)
        ).fetchall()

    text = "🎴 <b>Скины карт</b>\n\n"
    if not rows:
        text += "<i>Пусто. Загляни в /shop.</i>"
    else:
        for r in rows:
            status = " ✅" if r["is_equipped"] else ""
            text += f"• {html.escape(r['name'])}{status}\n"

    kb_rows = []
    for r in rows:
        btn_text = f"Снять: {r['name']}" if r["is_equipped"] else f"Надеть: {r['name']}"
        kb_rows.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"equip_{r['item_id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="inv_back")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


def inventory_profile_view(user_id: int):
    available_skin_ids = ["classic", "gold", "cyber", "street"]
    with db_lock:
        rows = db.execute(
            "SELECT skin_id FROM user_profile_skins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    owned_profile = {"classic"} | {row["skin_id"] for row in rows}

    current_skin = shared_get_equipped_card_skin(user_id)

    text = "🎨 <b>Скины профиля</b>\n\n"
    text += f"✅ Сейчас надет: <b>{shared_get_profile_skin_info(current_skin)['name']}</b>\n\n"

    kb_rows = []
    for skin_id in available_skin_ids:
        info = shared_get_profile_skin_info(skin_id)
        is_owned = skin_id in owned_profile
        is_current = skin_id == current_skin

        if is_current:
            text += f"✅ {info['name']} (надет)\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"✅ {info['name']}",
                callback_data="skin_nothing"
            )])
        elif is_owned:
            text += f"🔓 {info['name']}\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"Надеть: {info['name']}",
                callback_data=f"skin_equip:{skin_id}"
            )])
        else:
            text += f"🔒 {info['name']} (не куплен)\n"
            kb_rows.append([InlineKeyboardButton(
                text=f"🔒 {info['name']}",
                callback_data="skin_nothing"
            )])

    kb_rows.append([InlineKeyboardButton(
        text="🧹 Снять скин профиля",
        callback_data="skin_unequip"
    )])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад в магазин", callback_data="shop_profile_skins")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.callback_query(F.data.startswith("skin_equip:"))
async def cb_skin_equip(call: types.CallbackQuery):
    skin_id = call.data.split(":", 1)[1]
    with db_lock:
        rows = db.execute(
            "SELECT skin_id FROM user_profile_skins WHERE user_id = ?",
            (call.from_user.id,),
        ).fetchall()
    owned_skin_ids = {"classic"} | {row["skin_id"] for row in rows}

    if skin_id not in owned_skin_ids:
        await call.answer("Скин не куплен. Купи в /shop.", show_alert=True)
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


@dp.callback_query(F.data.startswith("equip_"))
async def cb_equip_item(call: CallbackQuery):
    item_id = call.data.replace("equip_", "", 1)
    user_id = call.from_user.id
    ensure_user(user_id, call.from_user.full_name)

    with db_lock:
        inv_item = db.execute(
            "SELECT is_equipped FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        ).fetchone()
        shop_item = db.execute(
            "SELECT name, category FROM shop WHERE item_id = ?",
            (item_id,),
        ).fetchone()

        if not inv_item or not shop_item:
            await call.answer("Предмет не найден!", show_alert=True)
            return

        is_equipped = inv_item["is_equipped"]
        item_name, category = shop_item["name"], shop_item["category"]

        if is_equipped:
            db.execute(
                "UPDATE inventory SET is_equipped = 0 WHERE user_id = ? AND item_id = ?",
                (user_id, item_id),
            )
            if category == "title":
                db.execute("UPDATE users SET equipped_title = 'Новичок' WHERE user_id = ?", (user_id,))
            elif category == "skin":
                db.execute(
                    "UPDATE users SET equipped_card_skin = 'Классическая' WHERE user_id = ?",
                    (user_id,),
                )
            await call.answer(f"Снято: {item_name}", show_alert=True)
        else:
            db.execute(
                """
                UPDATE inventory SET is_equipped = 0
                WHERE user_id = ? AND item_id IN (SELECT item_id FROM shop WHERE category = ?)
                """,
                (user_id, category),
            )
            db.execute(
                "UPDATE inventory SET is_equipped = 1 WHERE user_id = ? AND item_id = ?",
                (user_id, item_id),
            )
            if category == "title":
                db.execute("UPDATE users SET equipped_title = ? WHERE user_id = ?", (item_name, user_id))
            elif category == "skin":
                db.execute("UPDATE users SET equipped_card_skin = ? WHERE user_id = ?", (item_name, user_id))
            await call.answer(f"Надето: {item_name}", show_alert=True)
        db.commit()

    invalidate_profile_cache(user_id)
    text, markup = inventory_cards_view(user_id)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=markup)


@dp.message(Command("карты", "cards"))
async def cmd_cards(msg: Message):
    game = find_game_for_user(msg.from_user.id)
    if not game or game.status != "playing":
        await msg.answer("Ты сейчас не в активной игре.")
        return
    player = game.player_by_id(msg.from_user.id)
    if not player:
        await msg.answer("Тебя нет в игре.")
        return
    await msg.answer(
        f"Твои карты ({len(player.cards)}):",
        reply_markup=hand_kb(player),
    )


@dp.message(Command("give"))
async def cmd_give(msg: Message, command: CommandObject):
    if msg.from_user.id != ADMIN_ID:
        return
    if not command.args:
        await msg.answer("Использование: <code>/give user_id amount</code>")
        return
    args = command.args.split()
    if len(args) < 2:
        await msg.answer("Использование: <code>/give user_id amount</code>")
        return
    try:
        target_id = int(args[0])
        amount = int(args[1])
    except ValueError:
        await msg.answer("user_id и amount должны быть числами.")
        return
    add_bariki(target_id, "Пользователь", amount)
    await msg.answer(f"Выдано {amount} бэриков пользователю <code>{target_id}</code>.")


@dp.message(Command("newgame"))
async def cmd_newgame(msg: Message):
    if msg.chat.type == ChatType.PRIVATE:
        await msg.answer("Создай игру в групповом чате командой /newgame.")
        return
    if not await is_subscribed(msg.from_user.id):
        await msg.answer(
            f"Сначала подпишись на {REQUIRED_CHANNEL}.",
            reply_markup=sub_keyboard(),
        )
        return
    existing = games.get(msg.chat.id)
    if existing:
        await msg.answer("В этом чате уже есть игра. Хост может остановить её /stopgame.")
        return
    ensure_user(msg.from_user.id, msg.from_user.full_name)
    game = Game(chat_id=msg.chat.id, host_id=msg.from_user.id)
    game.players.append(Player(user_id=msg.from_user.id, name=msg.from_user.full_name))
    games[msg.chat.id] = game
    user_chat[msg.from_user.id] = msg.chat.id
    await msg.answer(
        f"Игра «Блеф» создана.\nХост: {html.escape(msg.from_user.full_name)}\n"
        f"Игроки: {game.names_line()}\nНужно {MIN_PLAYERS}–{MAX_PLAYERS} участников.",
        reply_markup=lobby_kb(),
    )


@dp.message(Command("stopgame"))
async def cmd_stopgame(msg: Message):
    game = games.get(msg.chat.id)
    if not game:
        await msg.answer("Активной игры нет.")
        return
    if msg.from_user.id not in (game.host_id, ADMIN_ID):
        await msg.answer("Остановить игру может только хост или админ.")
        return
    for p in game.players:
        user_chat.pop(p.user_id, None)
    games.pop(msg.chat.id, None)
    await msg.answer("Игра остановлена.")


@dp.callback_query(F.data == "g_join")
async def cb_join(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Лобби недоступно.", show_alert=True)
        return
    if not await is_subscribed(call.from_user.id):
        await call.answer(f"Подпишись на {REQUIRED_CHANNEL}", show_alert=True)
        return
    if game.player_by_id(call.from_user.id):
        await call.answer("Ты уже в игре.")
        return
    if len(game.players) >= MAX_PLAYERS:
        await call.answer("Мест нет.", show_alert=True)
        return
    ensure_user(call.from_user.id, call.from_user.full_name)
    game.players.append(Player(user_id=call.from_user.id, name=call.from_user.full_name))
    user_chat[call.from_user.id] = game.chat_id
    await call.answer("Ты в игре.")
    await call.message.edit_text(
        f"Лобби «Блеф»\nИгроки ({len(game.players)}): {game.names_line()}",
        reply_markup=lobby_kb(),
    )


@dp.callback_query(F.data == "g_leave")
async def cb_leave(call: CallbackQuery):
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
        f"Лобби «Блеф»\nИгроки ({len(game.players)}): {game.names_line()}",
        reply_markup=lobby_kb(),
    )


@dp.callback_query(F.data == "g_start")
async def cb_start(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    if not game or game.status != "lobby":
        await call.answer("Игру нельзя начать.", show_alert=True)
        return
    if call.from_user.id != game.host_id:
        await call.answer("Начинает только хост.", show_alert=True)
        return
    if len(game.players) < MIN_PLAYERS:
        await call.answer(f"Нужно минимум {MIN_PLAYERS} игрока.", show_alert=True)
        return

    deck = make_deck()
    random.shuffle(deck)
    for player in game.players:
        player.cards = sorted(deck[:CARDS_PER_PLAYER], key=lambda c: (RANKS.index(card_rank(c)), c))
        deck = deck[CARDS_PER_PLAYER:]
        player.insurance = False
        player.selected = []
        user_chat[player.user_id] = game.chat_id
    game.deck = deck
    game.status = "playing"
    game.turn = 0
    random.shuffle(game.players)
    await call.message.edit_text(
        f"Игра началась! Игроки: {game.names_line()}\n"
        f"Правила: положи 1–{MAX_PLAY_CARDS} карты лицом вниз и назови номинал. "
        f"Следующий может поверить или проверить блеф."
    )
    await start_turn(game, call.message.chat.id)
    await call.answer()


@dp.callback_query(F.data.startswith("g_sel:"))
async def cb_select_card(call: CallbackQuery):
    game = find_game_for_user(call.from_user.id)
    if not game or game.status != "playing" or game.phase != "play":
        await call.answer("Сейчас нельзя выбрать карты.", show_alert=True)
        return
    player = game.current_player()
    if player.user_id != call.from_user.id:
        await call.answer("Сейчас не твой ход.", show_alert=True)
        return
    idx = int(call.data.split(":")[1])
    if idx < 0 or idx >= len(player.cards):
        await call.answer("Карта уже неактуальна.", show_alert=True)
        return
    if idx in player.selected:
        player.selected.remove(idx)
    else:
        if len(player.selected) >= MAX_PLAY_CARDS:
            await call.answer(f"Максимум {MAX_PLAY_CARDS} карты.", show_alert=True)
            return
        player.selected.append(idx)
    try:
        await call.message.edit_reply_markup(reply_markup=hand_kb(player))
    except TelegramBadRequest:
        pass
    await call.answer()


@dp.callback_query(F.data == "g_claim")
async def cb_claim(call: CallbackQuery):
    game = find_game_for_user(call.from_user.id)
    if not game or game.phase != "play":
        await call.answer("Нельзя сыграть сейчас.", show_alert=True)
        return
    player = game.current_player()
    if player.user_id != call.from_user.id:
        await call.answer("Сейчас не твой ход.", show_alert=True)
        return
    if not player.selected:
        await call.answer("Выбери хотя бы одну карту.", show_alert=True)
        return
    game.phase = "rank"
    await call.message.edit_text(
        f"Выбрано карт: {len(player.selected)}. Какой номинал заявляешь?",
        reply_markup=ranks_kb(),
    )
    await call.answer()


@dp.callback_query(F.data == "g_backhand")
async def cb_backhand(call: CallbackQuery):
    game = find_game_for_user(call.from_user.id)
    if not game:
        await call.answer()
        return
    player = game.player_by_id(call.from_user.id)
    if not player:
        await call.answer()
        return
    game.phase = "play"
    await call.message.edit_text("Выбери карты:", reply_markup=hand_kb(player))
    await call.answer()


@dp.callback_query(F.data.startswith("g_rank:"))
async def cb_rank(call: CallbackQuery):
    game = find_game_for_user(call.from_user.id)
    if not game or game.phase != "rank":
        await call.answer("Сначала выбери карты.", show_alert=True)
        return
    player = game.current_player()
    if player.user_id != call.from_user.id:
        await call.answer("Сейчас не твой ход.", show_alert=True)
        return
    claim = call.data.split(":")[1]
    selected = sorted(player.selected, reverse=True)
    played = []
    for idx in selected:
        played.append(player.cards.pop(idx))
    player.selected = []
    game.last_cards = played
    game.last_claim = claim
    game.last_player_id = player.user_id
    game.waiting_challenge = True
    game.phase = "challenge"
    game.responder = game.next_index(game.turn)
    await call.message.edit_text(
        f"Сыграно {len(played)} карт как {RANK_NAMES.get(claim, claim)}. Жди ответ в чате."
    )
    await after_play(game, player, claim, game.chat_id)
    await call.answer()


def resolve_responder(game: Game, user_id: int) -> Optional[Player]:
    if not game or not game.waiting_challenge:
        return None
    responder = game.players[game.responder]
    if responder.user_id != user_id:
        return None
    return responder


@dp.callback_query(F.data == "g_believe")
async def cb_believe(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    responder = resolve_responder(game, call.from_user.id) if game else None
    if not responder:
        await call.answer("Сейчас отвечаешь не ты.", show_alert=True)
        return
    liar = game.player_by_id(game.last_player_id)
    await call.answer("Верю.")
    await call.message.edit_text(
        f"{html.escape(responder.name)} верит {html.escape(liar.name if liar else 'игроку')}."
    )
    if liar and not liar.cards:
        await finish_game(game, liar, call.message.chat.id)
        return
    game.turn = game.responder
    await start_turn(game, call.message.chat.id)


@dp.callback_query(F.data == "g_bluff")
async def cb_bluff(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    responder = resolve_responder(game, call.from_user.id) if game else None
    if not responder:
        await call.answer("Сейчас отвечаешь не ты.", show_alert=True)
        return
    player = game.player_by_id(game.last_player_id)
    truth = all(card_rank(card) == game.last_claim for card in game.last_cards)
    shown = " ".join(game.last_cards)
    await call.answer()
    if truth:
        responder.cards.extend(game.last_cards)
        responder.cards.sort(key=lambda c: (RANKS.index(card_rank(c)), c))
        text = (
            f"Проверка: карты {shown} — это правда ({RANK_NAMES.get(game.last_claim, game.last_claim)}).\n"
            f"{html.escape(responder.name)} забирает карты."
        )
        loser = responder
        winner_if_empty = player
    else:
        if player and player.insurance:
            player.insurance = False
            game.deck.extend(game.last_cards)
            random.shuffle(game.deck)
            text = (
                f"Проверка: карты {shown} — блеф! Но у {html.escape(player.name)} сработала страховка, "
                f"карты уходят в колоду."
            )
            loser = None
            winner_if_empty = player
        else:
            if player:
                player.cards.extend(game.last_cards)
                player.cards.sort(key=lambda c: (RANKS.index(card_rank(c)), c))
            text = (
                f"Проверка: карты {shown} — блеф!\n"
                f"{html.escape(player.name if player else 'Игрок')} забирает карты."
            )
            loser = player
            winner_if_empty = None
    game.last_cards = []
    game.waiting_challenge = False
    await call.message.edit_text(text)
    if winner_if_empty and not winner_if_empty.cards:
        await finish_game(game, winner_if_empty, call.message.chat.id)
        return
    if loser is responder:
        game.turn = game.players.index(responder)
    else:
        game.turn = game.responder
    await start_turn(game, call.message.chat.id)


@dp.callback_query(F.data == "g_swap")
async def cb_swap(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    responder = resolve_responder(game, call.from_user.id) if game else None
    if not responder:
        await call.answer("Сейчас это недоступно.", show_alert=True)
        return
    if not spend_bariki(call.from_user.id, call.from_user.full_name, SWAP_CARDS_PRICE):
        await call.answer("Недостаточно бэриков.", show_alert=True)
        return
    n = len(responder.cards)
    game.deck.extend(responder.cards)
    random.shuffle(game.deck)
    take = min(n, len(game.deck))
    responder.cards = sorted(game.deck[:take], key=lambda c: (RANKS.index(card_rank(c)), c))
    game.deck = game.deck[take:]
    await send_hand(responder, f"Обмен выполнен. Новые карты ({len(responder.cards)}):")
    await call.answer("Карты обменены.")
    await call.message.answer(f"{html.escape(responder.name)} обменял руку за {SWAP_CARDS_PRICE} бэриков.")


@dp.callback_query(F.data == "g_insur")
async def cb_insurance(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    player = game.player_by_id(call.from_user.id) if game else None
    if not game or game.status != "playing" or not player:
        await call.answer("Страховка недоступна.", show_alert=True)
        return
    if player.insurance:
        await call.answer("Страховка уже активна.", show_alert=True)
        return
    if not spend_bariki(call.from_user.id, call.from_user.full_name, INSURANCE_PRICE):
        await call.answer("Недостаточно бэриков.", show_alert=True)
        return
    player.insurance = True
    await call.answer("Страховка куплена.")
    await call.message.answer(
        f"{html.escape(player.name)} купил страховку за {INSURANCE_PRICE}. "
        f"Если его поймают на блефе, карты уйдут в колоду."
    )


@dp.callback_query(F.data == "g_hunch")
async def cb_hunch(call: CallbackQuery):
    game = games.get(call.message.chat.id)
    responder = resolve_responder(game, call.from_user.id) if game else None
    if not responder:
        await call.answer("Предчувствие доступно только отвечающему.", show_alert=True)
        return
    if not game.last_cards:
        await call.answer("Смотреть нечего.", show_alert=True)
        return
    if not spend_bariki(call.from_user.id, call.from_user.full_name, HUNCH_PRICE):
        await call.answer("Недостаточно бэриков.", show_alert=True)
        return
    shown = " ".join(game.last_cards)
    try:
        await bot.send_message(call.from_user.id, f"Предчувствие: на столе {shown}")
        await call.answer("Карты отправлены в личку.")
    except (TelegramForbiddenError, TelegramBadRequest):
        await call.answer("Сначала напиши боту /start в личке.", show_alert=True)


async def main():
    init_db()
    init_shared_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())