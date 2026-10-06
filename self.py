import os
import html
import asyncio
import logging
import re
import aiohttp
import time
import json
import random
import sqlite3
from urllib.parse import quote
from pyrogram import Client, filters, idle
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.enums import ChatType, ChatAction, ParseMode
from pyrogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InlineQueryResultArticle, InputTextMessageContent, MessageEntity
)
from pyrogram.raw import functions
from pyrogram.errors import SessionPasswordNeeded, ChatSendInlineForbidden
from datetime import datetime
from zoneinfo import ZoneInfo
import pyrogram.utils

# =============================================
# ایمپورت دیتابیس
# =============================================
from database import (
    init_session_db,
    save_session_to_db,
    get_all_sessions_from_db,
    get_session_by_user_id,
    delete_session_from_db,
    delete_session_by_user_id,
    get_session_count,
    clear_inactive_sessions
)

# =============================================
# ایمپورت دانلودر
# =============================================
import subprocess
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')

def patch_peer_id_validation():
    original_get_peer_type = pyrogram.utils.get_peer_type

    def patched_get_peer_type(peer_id: int) -> str:
        try:
            return original_get_peer_type(peer_id)
        except ValueError:
            if str(peer_id).startswith("-100"):
                return "channel"
            raise

    pyrogram.utils.get_peer_type = patched_get_peer_type
    logging.info("Pyrogram peer ID validation patched successfully.")

patch_peer_id_validation()

# =============================================
# تنظیمات اصلی
# =============================================
API_ID = 34996139
API_HASH = "a1f3db16cae2919cfb05e61d1e968b8d"

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_BOT_USERNAME = None  # بعد از استارت پر می‌شود
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
# Gemini (Google AI Studio) — متن + تصویر + تحلیل
GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY", "").strip()
    or os.environ.get("GOOGLE_API_KEY", "").strip()
)
GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.0-flash").strip()
GEMINI_IMAGE_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_IMAGE_MODELS",
        "gemini-2.0-flash-preview-image-generation,gemini-2.5-flash-image,gemini-2.0-flash-exp-image-generation"
    ).split(",") if m.strip()
]

# اینلاین هلپر ایموجی پریمیوم (بات ساخته‌شده با اکانت پریمیوم)
HELPER_INLINE_BOT = os.environ.get("HELPER_INLINE_BOT", "helperselfMR01_bot").strip().lstrip("@")
# توکن بات پریمیوم برای اینلاین ایموجی (مثل darkself) — از سرور
PREMIUM_BOT_TOKEN = (os.environ.get("PREMIUM_BOT_TOKEN") or os.environ.get("HELPER_BOT_TOKEN") or "").strip()

# =============================================
# ایموجی پریمیوم برای ربات منیجر (Bot API / tg-emoji)
# =============================================
MANAGER_PREMIUM_EMOJIS = {}  # str(id) -> {"id": int, "fallback": str}




def build_bracket_premium_message(text: str, placeholder: str = "🔥"):
    """
    متن با [custom_emoji_id] → (message_text, entities)
    placeholder باید طول UTF-16 مناسب داشته باشد (معمولاً 2 برای ایموجی رنگی)
    """
    import re as _re
    rx = _re.compile(r"\[(\d{10,})\]")
    text = text or ""
    # اطمینان از placeholder با طول UTF-16 >= 1
    ph = placeholder or "🔥"
    ph_len = len(ph.encode("utf-16-le")) // 2
    if ph_len < 1:
        ph, ph_len = "🔥", 2
    out = []
    entities = []
    utf16 = 0
    last = 0
    for m in rx.finditer(text):
        before = text[last:m.start()]
        out.append(before)
        utf16 += len(before.encode("utf-16-le")) // 2
        entities.append({
            "type": "custom_emoji",
            "offset": int(utf16),
            "length": int(ph_len),
            "custom_emoji_id": str(m.group(1)),
        })
        out.append(ph)
        utf16 += ph_len
        last = m.end()
    out.append(text[last:])
    return "".join(out), entities


def build_bracket_premium_html(text: str, placeholder: str = "🔥") -> str:
    """نسخه HTML tg-emoji برای fallback"""
    import re as _re
    import html as _html
    rx = _re.compile(r"\[(\d{10,})\]")
    text = text or ""
    ph = _html.escape(placeholder or "🔥")
    parts = []
    last = 0
    for m in rx.finditer(text):
        parts.append(_html.escape(text[last:m.start()]))
        parts.append(f'<tg-emoji emoji-id="{m.group(1)}">{ph}</tg-emoji>')
        last = m.end()
    parts.append(_html.escape(text[last:]))
    return "".join(parts)



def html_tg_emoji(custom_emoji_id, fallback: str = "⭐") -> str:
    """ساخت تگ HTML رسمی تلگرام برای ایموجی پریمیوم (Bot API)"""
    try:
        cid = int(custom_emoji_id)
    except Exception:
        return "⭐"
    # فقط fallback ساده BMP — وگرنه کلاینت مربع سفید نشان می‌دهد
    fb = "⭐"
    try:
        raw = (fallback or "").strip()
        if raw and len(raw.encode("utf-16-le")) // 2 <= 2 and "\ufffd" not in raw and "�" not in raw:
            # یک ایموجی ساده قابل قبول است
            fb = raw[:8]
    except Exception:
        fb = "⭐"
    if not fb or fb == "�":
        fb = "⭐"
    return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>'


async def send_premium_emoji_message(client, chat_id, custom_emoji_id, fallback: str = "⭐", caption: str = ""):
    """ارسال مطمئن ایموجی پریمیوم: اول entity، بعد HTML"""
    cid = int(custom_emoji_id)
    fb = "⭐"
    try:
        from pyrogram.enums import MessageEntityType
        from pyrogram.types import MessageEntity
        ln = len(fb.encode("utf-16-le")) // 2
        ents = [MessageEntity(
            type=MessageEntityType.CUSTOM_EMOJI,
            offset=0,
            length=ln,
            custom_emoji_id=cid,
        )]
        text = fb if not caption else (fb + "\n\n" + caption)
        # اگر کپشن دارد، entity فقط روی ایموجی اول
        await client.send_message(chat_id, text, entities=ents)
        return True
    except Exception as e1:
        logging.warning(f"send_premium entity: {e1}")
    try:
        html = html_tg_emoji(cid, fb)
        if caption:
            html = html + "\n\n" + str(caption)
        await client.send_message(chat_id, html, parse_mode=ParseMode.HTML)
        return True
    except Exception as e2:
        logging.warning(f"send_premium html: {e2}")
        return False


def extract_custom_emojis_from_message(message) -> list:
    """لیست (custom_emoji_id, fallback) از پیام"""
    found = []
    if not message:
        return found
    text = message.text or message.caption or ""
    entities = list(getattr(message, "entities", None) or []) + list(
        getattr(message, "caption_entities", None) or []
    )
    # برای استخراج fallback با offsetهای UTF-16
    def _utf16_slice(s: str, offset: int, length: int) -> str:
        try:
            encoded = s.encode("utf-16-le")
            start = offset * 2
            end = (offset + length) * 2
            return encoded[start:end].decode("utf-16-le")
        except Exception:
            return "⭐"

    for ent in entities:
        cid = getattr(ent, "custom_emoji_id", None)
        if not cid:
            # بعضی نسخه‌ها type را جدا دارند
            t = str(getattr(ent, "type", "") or "")
            if "CUSTOM_EMOJI" not in t.upper() and "custom_emoji" not in t.lower():
                continue
            cid = getattr(ent, "custom_emoji_id", None)
        if not cid:
            continue
        off = int(getattr(ent, "offset", 0) or 0)
        ln = int(getattr(ent, "length", 0) or 0)
        fb = _utf16_slice(text, off, ln) if text and ln else "⭐"
        if not fb.strip():
            fb = "⭐"
        found.append((int(cid), fb))
    return found


def save_manager_premium_emoji(custom_emoji_id: int, fallback: str = "⭐"):
    global MANAGER_PREMIUM_EMOJIS
    cid = int(custom_emoji_id)
    MANAGER_PREMIUM_EMOJIS[str(cid)] = {"id": cid, "fallback": fallback or "⭐"}
    try:
        data_manager.data.setdefault("manager_premium_emojis", {})[str(cid)] = {
            "id": cid,
            "fallback": fallback or "⭐",
        }
        data_manager.save_data()
    except Exception as e:
        logging.warning(f"save_manager_premium_emoji: {e}")


def load_manager_premium_emojis():
    global MANAGER_PREMIUM_EMOJIS
    try:
        raw = (data_manager.data or {}).get("manager_premium_emojis") or {}
        for k, v in raw.items():
            if isinstance(v, dict) and v.get("id"):
                MANAGER_PREMIUM_EMOJIS[str(k)] = {
                    "id": int(v["id"]),
                    "fallback": v.get("fallback") or "⭐",
                }
            else:
                try:
                    MANAGER_PREMIUM_EMOJIS[str(k)] = {"id": int(k), "fallback": "⭐"}
                except Exception:
                    pass
        logging.info("manager premium emojis loaded: %s", len(MANAGER_PREMIUM_EMOJIS))
    except Exception as e:
        logging.warning(f"load_manager_premium_emojis: {e}")



def get_post_channel() -> str:
    """کانال ثبت‌شده برای پست ادمین (id یا @username)"""
    try:
        return str((data_manager.data or {}).get("post_channel") or "").strip()
    except Exception:
        return ""


def set_post_channel(channel: str) -> None:
    try:
        data_manager.data["post_channel"] = str(channel).strip()
        data_manager.save_data()
    except Exception as e:
        logging.warning(f"set_post_channel: {e}")


async def resolve_post_channel(client):
    """برگرداندن chat_id عددی کانال ثبت‌شده"""
    ch = get_post_channel()
    if not ch:
        return None, "کانالی ثبت نشده."
    try:
        if ch.lstrip("-").isdigit():
            return int(ch), None
        chat = await client.get_chat(ch)
        return chat.id, None
    except Exception as e:
        return None, f"کانال پیدا نشد: {e}"



async def send_premium_ids_to_channel(client, channel_id, text: str, extra_caption: str = ""):
    """ارسال پست به کانال از روی آیدی عددی custom_emoji (بدون مربع)"""
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import MessageEntity
    ids = re.findall(r"\b(\d{15,22})\b", text or "")
    if not ids:
        return False, "آیدی عددی ایموجی پریمیوم پیدا نشد (۱۵ تا ۲۲ رقم)."
    # متن باقی‌مانده بدون آیدی‌ها
    rest = text or ""
    for i in ids:
        rest = rest.replace(i, " ")
    rest = " ".join(rest.split())
    if extra_caption:
        rest = (rest + "\n" + extra_caption).strip() if rest else extra_caption

    # ساخت متن با placeholder ستاره برای هر ایموجی
    pieces = []
    entities = []
    offset = 0
    for cid in ids[:30]:
        fb = "⭐"
        ln = len(fb.encode("utf-16-le")) // 2
        entities.append(MessageEntity(
            type=MessageEntityType.CUSTOM_EMOJI,
            offset=offset,
            length=ln,
            custom_emoji_id=int(cid),
        ))
        pieces.append(fb)
        offset += ln
        pieces.append(" ")
        offset += 1
    body = "".join(pieces).rstrip()
    if rest:
        body = body + "\n\n" + rest
    try:
        await client.send_message(channel_id, body, entities=entities)
        return True, None
    except Exception as e1:
        logging.warning(f"send_premium_ids entity: {e1}")
    # HTML fallback
    try:
        html = " ".join(html_tg_emoji(int(i), "⭐") for i in ids[:30])
        if rest:
            html = html + "\n\n" + rest
        await client.send_message(channel_id, html, parse_mode=ParseMode.HTML)
        return True, None
    except Exception as e2:
        return False, str(e2)


async def copy_message_to_channel(client, message, channel_id) -> tuple:
    """کپی پیام (با ایموجی پریمیوم) به کانال — بدون مربع"""
    try:
        # بهترین روش: copy_message موجودیت‌ها را حفظ می‌کند
        sent = await client.copy_message(
            chat_id=channel_id,
            from_chat_id=message.chat.id,
            message_id=message.id,
        )
        return True, sent
    except Exception as e1:
        logging.warning(f"copy_message channel: {e1}")
    # فال‌بک: فوروارد
    try:
        sent = await client.forward_messages(
            chat_id=channel_id,
            from_chat_id=message.chat.id,
            message_ids=message.id,
        )
        return True, sent
    except Exception as e2:
        logging.warning(f"forward channel: {e2}")
    # فال‌بک: بازسازی متن + entity کاستوم
    try:
        text = message.text or message.caption or ""
        ents = list(message.entities or message.caption_entities or [])
        if text and ents:
            sent = await client.send_message(channel_id, text, entities=ents)
            return True, sent
        if text:
            # HTML از custom emoji
            found = extract_custom_emojis_from_message(message)
            if found:
                html = text
                # ساده: فقط پیام با tg-emoji اگر متن کوتاه است
                parts = [html_tg_emoji(cid, fb) for cid, fb in found]
                sent = await client.send_message(
                    channel_id, " ".join(parts), parse_mode=ParseMode.HTML
                )
                return True, sent
            sent = await client.send_message(channel_id, text)
            return True, sent
        # مدیا
        if message.media:
            sent = await client.copy_message(
                chat_id=channel_id,
                from_chat_id=message.chat.id,
                message_id=message.id,
            )
            return True, sent
    except Exception as e3:
        return False, str(e3)
    return False, "ارسال ناموفق"

async def manager_reply_premium(message, custom_emoji_id, fallback="⭐", extra_text=""):
    """پاسخ با ایموجی پریمیوم واقعی از طریق Bot API"""
    html = html_tg_emoji(custom_emoji_id, fallback)
    body = html
    if extra_text:
        body = html + "\n\n" + str(extra_text)
    await message.reply_text(body, parse_mode=ParseMode.HTML)


# اگر توکن هلپر جدا از منیجر است در env بگذار؛ وگرنه از BOT_TOKEN استفاده می‌شود
HELPER_BOT_TOKEN = os.environ.get("HELPER_BOT_TOKEN", "").strip()
# اگر توکن هلپر خراب/منقضی است: HELPER_ENABLED=0 بگذار یا توکن را خالی کن
HELPER_BOT_ENABLED = os.environ.get("HELPER_ENABLED", "1").strip() not in ("0", "false", "False", "no", "NO")
HELPER_BOT_INSTANCE = None

PREMIUM_CLIENT = None  # Client سشن اکانت پریمیوم (از سرور)

def load_premium_session_string() -> str:
    """سشن پریمیوم فقط از سرور: env یا فایل — داخل کد هاردکد نمی‌شود"""
    s = (os.environ.get("PREMIUM_SESSION_STRING") or os.environ.get("PREMIUM_SESSION") or "").strip()
    if s:
        logging.info("Premium session loaded from environment")
        return s
    candidates = [
        "/app/premium_session.txt",
        "/data/premium_session.txt",
        os.path.join(os.getcwd(), "premium_session.txt"),
        "premium_session.txt",
    ]
    for p in candidates:
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    s = f.read().strip()
                if s:
                    logging.info("Premium session loaded from file: %s", p)
                    return s
        except Exception as e:
            logging.warning("premium session file %s: %s", p, e)
    logging.warning("No PREMIUM_SESSION_STRING / premium_session.txt found on server")
    return ""


async def ensure_premium_client():
    """استارت یک‌بار Client از سشن پریمیوم سرور"""
    global PREMIUM_CLIENT
    if PREMIUM_CLIENT is not None:
        return PREMIUM_CLIENT
    ss = load_premium_session_string()
    if not ss:
        return None
    try:
        PREMIUM_CLIENT = Client(
            "premium_helper_session",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=ss,
            in_memory=True,
            no_updates=True,
        )
        await PREMIUM_CLIENT.start()
        me = await PREMIUM_CLIENT.get_me()
        logging.info("Premium helper session started as %s (%s)", me.first_name, me.id)
        return PREMIUM_CLIENT
    except Exception as e:
        logging.error("Failed to start premium session: %s", e)
        PREMIUM_CLIENT = None
        return None


if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not found in environment variables!")

GOD_ADMIN_IDS = [6691993264]

# =============================================
# کانال‌های عضویت اجباری
# =============================================

FORCE_CHANNELS = [
    "@SELF_MR0"
]
SUPPORT_USERNAME = "ALONE_88_R"  # بدون @

async def force_subscribe_check(client, message) -> bool:
    """عضویت اجباری — ادمین‌ها مستثنی هستند"""
    try:
        user = message.from_user
        if not user:
            return True
        if user.id in GOD_ADMIN_IDS:
            return True
        if not FORCE_CHANNELS:
            return True
        missing = []
        for ch in FORCE_CHANNELS:
            try:
                member = await client.get_chat_member(ch, user.id)
                status = str(getattr(member, "status", "")).lower()
                if "left" in status or "kicked" in status or "banned" in status:
                    missing.append(ch)
            except Exception:
                missing.append(ch)
        if not missing:
            return True
        # پیام عضویت
        lines = "\n".join(f"• {c}" for c in missing)
        try:
            from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            buttons = []
            for c in missing:
                uname = c.lstrip("@")
                buttons.append([InlineKeyboardButton(f"عضویت در {c}", url=f"https://t.me/{uname}")])
            buttons.append([InlineKeyboardButton("🔄 بررسی مجدد عضویت", callback_data="check_subscription")])
            await message.reply_text(
                f"⚠️ برای استفاده از ربات باید در کانال‌های زیر عضو شوید:\n\n{lines}",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e:
            logging.error(f"force_subscribe msg: {e}")
            try:
                await message.reply_text(f"⚠️ لطفاً ابتدا در کانال‌ها عضو شوید:\n{lines}")
            except Exception:
                pass
        return False
    except Exception as e:
        logging.error(f"force_subscribe_check: {e}")
        return True


def backup_sessions():
    try:
        sessions = get_all_sessions_from_db()
        if sessions:
            logging.info(f"💾 Backed up {len(sessions)} sessions")
    except Exception as e:
        logging.error(f"Backup failed: {e}")


DATA_FILE = "bot_data.json"
DOWNLOAD_PATH = "downloads"
MAX_FILE_SIZE = 50 * 1024 * 1024

TEHRAN_TIMEZONE = ZoneInfo("Asia/Tehran")
LOGIN_STATES = {}
ADMIN_STATES = {}

if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

if not os.path.exists('database_users'):
    os.makedirs('database_users')

# =============================================
# دیتابیس کاربران + سیستم الماس (self MR)
# =============================================
SELF_PRICE = 50          # هزینه فعال‌سازی سلف
HOURLY_COST = 2          # کسر ساعتی
REFERRAL_REWARD = 75     # پاداش زیرمجموعه
MIN_GAME_AMOUNT = 20     # حداقل مبلغ نبرد
TRANSFER_TAX_PERCENT = 10
GAME_TAX_PERCENT = 5
DIAMOND_PRICE_TOMAN = 30  # قیمت هر الماس (تومان)
MIN_BUY_DIAMONDS = 1000  # حداقل خرید
SHOP_STEP = 100  # گام +/-
CARD_NUMBER = os.environ.get("CARD_NUMBER", "6037-****-****-****").strip()
CARD_OWNER = os.environ.get("CARD_OWNER", "self MR").strip()
SHOP_CART = {}  # user_id -> qty
PENDING_RECEIPTS = {}  # user_id -> {qty, amount, ts}
ADMIN_PENDING_ORDERS = {}  # order_id -> {user_id, qty, amount}


# نبردهای فعال: (chat_id, message_id) -> info
active_games = {}
active_dooz = {}
DOOZ_TIMERS = {}  # (chat_id, msg_id) -> asyncio.Task
DOOZ_TURN_SEC = 30
DOOZ_LAST_RESULT = {}  # key -> {text,prize,wbal,lbal}

# ===== مافیا =====
MAFIA_GAMES = {}  # chat_id -> game dict
MAFIA_USER_CHAT = {}  # user_id -> chat_id (بازی فعال)
MAFIA_JOIN_SEC = 90  # ۱ دقیقه و ۳۰ ثانیه لابی
MAFIA_NIGHT_SEC = 45
MAFIA_DISCUSS_SEC = 120
MAFIA_VOTE_SEC = 60  # هر دور ۶۰ ثانیه رای‌گیری
MAFIA_WIN_PRIZE = 50
MAFIA_LOSE_PRIZE = 10
MAFIA_MIN_PLAYERS = 5
MAFIA_MAX_PLAYERS = 15

# ===== عکس استارزی (Telegram Stars paid media) =====
STARZY_PHOTO = {}  # user_id -> {link, time, last_fire}
STARZY_LOCK = None  # lazy asyncio.Lock

def _starzy_lock():
    global STARZY_LOCK
    if STARZY_LOCK is None:
        STARZY_LOCK = asyncio.Lock()
    return STARZY_LOCK



# نقشه رمز ایموجی (حروف فارسی/انگلیسی/عدد)
_EMOJI_CIPHER = {
    "ا": "🍎", "آ": "🍏", "ب": "🐝", "پ": "🅿️", "ت": "🌴", "ث": "🔺",
    "ج": "🎸", "چ": "🍒", "ح": "🏠", "خ": "🦖", "د": "🚪", "ذ": "💎",
    "ر": "🌹", "ز": "⚡", "ژ": "🧊", "س": "⭐", "ش": "🌙", "ص": "🛎️",
    "ض": "🎯", "ط": "🐢", "ظ": "🔮", "ع": "👁️", "غ": "👻", "ف": "🔥",
    "ق": "👑", "ک": "🔑", "گ": "🌸", "ل": "🍋", "م": "🍄", "ن": "🌃",
    "و": "🌊", "ه": "🏠", "ی": "💛", "ء": "✨", "ئ": "✨", "ة": "🌿",
    "a": "🅰️", "b": "🅱️", "c": "🌙", "d": "🐬", "e": "🦅", "f": "🐸",
    "g": "🌟", "h": "🏠", "i": "🍦", "j": "🕹️", "k": "🔑", "l": "🍀",
    "m": "💎", "n": "🌃", "o": "🟠", "p": "🍍", "q": "👑", "r": "🌈",
    "s": "☀️", "t": "🌴", "u": "☂️", "v": "✌️", "w": "🌊", "x": "❌",
    "y": "💛", "z": "⚡",
    "0": "0️⃣", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣",
    "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣",
    " ": "⬜", ".": "⚫", ",": "🔹", "!": "❗", "?": "❓",
}
_EMOJI_CIPHER_REV = {v: k for k, v in _EMOJI_CIPHER.items()}


def _cipher_plain_str(text) -> str:
    """فقط str واقعی — Message/None را امن تبدیل می‌کند"""
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    t = getattr(text, "text", None)
    if t is None:
        t = getattr(text, "caption", None)
    if isinstance(t, str):
        return t
    try:
        return str(text)
    except Exception:
        return ""



_SMR_CRYPT_PREFIX = "SMR1."


def text_encrypt_smr(text: str) -> str:
    """رمزنگاری برگشت‌پذیر (base64 + پیشوند)"""
    import base64
    raw = (text or "").encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return _SMR_CRYPT_PREFIX + token


def text_decrypt_smr(text: str) -> str:
    import base64
    import re as _re
    raw = (text or "").strip()
    # استخراج توکن از متن (با یا بدون پیشوند/ایموجی/بک‌تیک)
    m = _re.search(r"(?:SMR1\.)([A-Za-z0-9_\-]+)", raw)
    if m:
        s = m.group(1)
    else:
        s = raw
        for ch in ("🔐", "`", " ", "\n", "\r"):
            s = s.replace(ch, "")
        if s.startswith(_SMR_CRYPT_PREFIX):
            s = s[len(_SMR_CRYPT_PREFIX):]
        s = s.strip()
    if not s:
        raise ValueError("متن رمز معتبر نیست")
    pad = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad).decode("utf-8")
    except Exception:
        for line in raw.splitlines():
            line = line.strip().strip("`")
            if "SMR1." in line:
                return text_decrypt_smr(line)
        raise ValueError("متن رمز معتبر نیست")


def text_to_emoji_cipher(text) -> str:
    s = _cipher_plain_str(text)
    out = []
    for ch in s:
        low = ch.lower()
        if ch in _EMOJI_CIPHER:
            out.append(_EMOJI_CIPHER[ch])
        elif low in _EMOJI_CIPHER:
            out.append(_EMOJI_CIPHER[low])
        else:
            out.append(ch)
    return "".join(out)


def emoji_cipher_to_text(text) -> str:
    # دیکد حریصانه؛ فقط روی str واقعی
    s = _cipher_plain_str(text)
    if not s:
        return ""
    keys = sorted(_EMOJI_CIPHER_REV.keys(), key=len, reverse=True)
    i = 0
    n = len(s)
    out = []
    while i < n:
        matched = False
        for k in keys:
            kl = len(k)
            if i + kl <= n and s[i:i + kl] == k:
                out.append(_EMOJI_CIPHER_REV[k])
                i += kl
                matched = True
                break
        if not matched:
            out.append(s[i:i + 1])
            i += 1
    return "".join(out)



async def get_user_name(user_id: int) -> str:
    """نام قابل‌نمایش کاربر برای پیام نتیجه نبرد"""
    try:
        u = await manager_bot.get_users(int(user_id))
        name = (u.first_name or str(user_id)).replace("<", "").replace(">", "")
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        try:
            return f"<code>{int(user_id)}</code>"
        except Exception:
            return str(user_id)


async def check_all_channels(user_id: int):
    """لیست کانال‌هایی که کاربر عضو نیست"""
    missing = []
    for ch in FORCE_CHANNELS:
        try:
            member = await manager_bot.get_chat_member(ch, user_id)
            status = str(getattr(member, "status", "")).lower()
            if "left" in status or "kicked" in status or "banned" in status:
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing


def get_user_db(user_id):
    return sqlite3.connect(f'database_users/user_{user_id}.db')

def init_user_db(user_id):
    """ایجاد و آماده‌سازی دیتابیس کاربر"""
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            invited_by INTEGER DEFAULT 0,
            self_start_time INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS self_sessions (
            session_string TEXT,
            is_active INTEGER DEFAULT 1,
            start_time INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER PRIMARY KEY,
            reward_claimed INTEGER DEFAULT 0
        )
    ''')
    # اضافه کردن کاربر اگر وجود نداشته باشد
    cursor.execute('INSERT OR IGNORE INTO users (user_id, balance, banned, invited_by, self_start_time) VALUES (?, 0, 0, 0, 0)', (user_id,))
    db.commit()
    db.close()

def get_balance(user_id):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    db.close()
    return result[0] if result else 0

def add_balance(user_id, amount):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    db.commit()
    db.close()

def deduct_balance(user_id, amount):
    """کم کردن موجودی. اگر موجودی کافی نباشد False برمی‌گرداند"""
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    balance = result[0] if result else 0
    if balance < amount:
        db.close()
        return False
    cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
    db.commit()
    db.close()
    return True

def force_deduct_balance(user_id, amount):
    """کسر اجباری توسط ادمین — موجودی منفی نمی‌شود"""
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    balance = result[0] if result else 0
    amount = int(amount)
    if amount < 0:
        amount = 0
    new_bal = max(0, int(balance) - amount)
    actual = int(balance) - new_bal
    cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_bal, user_id))
    db.commit()
    db.close()
    return actual, new_bal

def is_banned(user_id):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('SELECT banned FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    db.close()
    return bool(result and result[0] == 1)

def set_banned(user_id, status: bool):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('UPDATE users SET banned = ? WHERE user_id = ?', (1 if status else 0, user_id))
    db.commit()
    db.close()

def set_self_start_time(user_id, timestamp=None):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    ts = timestamp if timestamp is not None else int(time.time())
    cursor.execute('UPDATE users SET self_start_time = ? WHERE user_id = ?', (ts, user_id))
    db.commit()
    db.close()

def get_self_start_time(user_id):
    init_user_db(user_id)
    db = get_user_db(user_id)
    cursor = db.cursor()
    cursor.execute('SELECT self_start_time FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    db.close()
    return result[0] if result else 0

def get_session(user_id):
    try:
        db = get_user_db(user_id)
        cursor = db.cursor()
        cursor.execute('SELECT session_string FROM self_sessions WHERE is_active = 1')
        result = cursor.fetchone()
        db.close()
        return result[0] if result else None
    except:
        return None

# =============================================
# تابع دانلود
# =============================================
async def download_media(url, media_type="video"):
    """دانلود از یوتیوب، تیک‌تاک، اینستا و ۱۰۰۰+ سایت"""
    try:
        safe_name = f"{int(time.time())}_{random.randint(1000,9999)}"
        outtmpl = f'{DOWNLOAD_PATH}/{safe_name}.%(ext)s'
        ydl_opts = {
            'outtmpl': outtmpl,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'noplaylist': True,
            'socket_timeout': 30,
            'retries': 3,
        }

        if media_type == "audio":
            ydl_opts.update({
                'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best',
                'merge_output_format': 'mp4',
            })

        def _run():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    return None, None
                filename = ydl.prepare_filename(info)
                if media_type == "audio":
                    base = filename.rsplit('.', 1)[0]
                    for ext in ('.mp3', '.m4a', '.webm', '.opus'):
                        if os.path.exists(base + ext):
                            return base + ('.mp3' if ext != '.mp3' and os.path.exists(base + '.mp3') else ext), info.get('title')
                    if os.path.exists(base + '.mp3'):
                        return base + '.mp3', info.get('title')
                if os.path.exists(filename):
                    return filename, info.get('title')
                # جستجو در پوشه
                for f in os.listdir(DOWNLOAD_PATH):
                    if f.startswith(safe_name):
                        return os.path.join(DOWNLOAD_PATH, f), info.get('title')
                return None, None

        filename, title = await asyncio.to_thread(_run)
        if not filename or not os.path.exists(filename):
            return None, "❌ دانلود ناموفق بود. لینک را بررسی کنید."

        file_size = os.path.getsize(filename)
        if file_size > MAX_FILE_SIZE:
            os.remove(filename)
            return None, "❌ حجم فایل بیشتر از ۵۰ مگابایت است!"
        if file_size < 1000:
            os.remove(filename)
            return None, "❌ فایل خیلی کوچک است یا دانلود ناقص بود."
        return filename, None
    except Exception as e:
        err = str(e)
        if "Unsupported URL" in err:
            return None, "❌ این لینک پشتیبانی نمی‌شود."
        if "Private video" in err or "private" in err.lower():
            return None, "❌ ویدیو خصوصی است."
        return None, f"❌ خطا در دانلود: {err[:120]}"

# =============================================
# تابع پاک‌سازی فایل‌های قدیمی
# =============================================

# =============================================
# قیمت ارز
# =============================================
CURRENCY_ALIASES = {
    "دلار": "usd", "dollar": "usd", "usd": "usd", "دلار آمریکا": "usd",
    "یورو": "eur", "euro": "eur", "eur": "eur",
    "پوند": "gbp", "gbp": "gbp",
    "درهم": "aed", "aed": "aed", "درهم امارات": "aed",
    "لیر": "try", "try": "try", "لیر ترکیه": "try",
    "یوان": "cny", "cny": "cny",
    "روبل": "rub", "rub": "rub",
    "بیتکوین": "btc", "بیت‌کوین": "btc", "btc": "btc", "bitcoin": "btc",
    "اتریوم": "eth", "eth": "eth",
    "تتر": "usdt", "usdt": "usdt",
    "طلا": "gold", "انس": "gold", "انس طلا": "gold",
    "سکه": "coin", "سکه امامی": "coin",
}

CURRENCY_NAMES = {
    "usd": "دلار آمریکا", "eur": "یورو", "gbp": "پوند انگلیس",
    "aed": "درهم امارات", "try": "لیر ترکیه", "cny": "یوان چین",
    "rub": "روبل روسیه", "btc": "بیت‌کوین", "eth": "اتریوم",
    "usdt": "تتر", "gold": "انس طلا", "coin": "سکه امامی",
}

async def fetch_currency_price(key: str):
    """دریافت قیمت از tgju.org"""
    key = key.lower()
    # کلیدهای tgju (مبالغ غالباً به ریال)
    TGJU_KEYS = {
        "usd": ("price_dollar_rl", True, "تومان"),
        "eur": ("price_eur", True, "تومان"),
        "gbp": ("price_gbp", True, "تومان"),
        "aed": ("price_aed", True, "تومان"),
        "try": ("price_try", True, "تومان"),
        "cny": ("price_cny", True, "تومان"),
        "rub": ("price_rub", True, "تومان"),
        "usdt": ("crypto-tether-irr", True, "تومان"),
        "btc": ("crypto-bitcoin-irr", True, "تومان"),
        "eth": ("crypto-ethereum-irr", True, "تومان"),
        "gold": ("ons", False, "دلار"),  # انس جهانی دلاری
        "coin": ("sekee", True, "تومان"),
    }
    mapping = TGJU_KEYS.get(key)
    if not mapping:
        return None, "❌ این ارز پشتیبانی نمی‌شود."

    tgju_key, is_rial, unit = mapping
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.tgju.org/",
    }
    urls = [
        "https://call1.tgju.org/ajax.json",
        "https://call2.tgju.org/ajax.json",
        "https://call3.tgju.org/ajax.json",
    ]
    try:
        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        current = data.get("current") or {}
                        item = current.get(tgju_key)
                        if not item:
                            continue
                        raw = item.get("p") if isinstance(item, dict) else item
                        if raw is None:
                            continue
                        # پاکسازی عدد
                        s = str(raw).replace(",", "").replace(" ", "").replace("٬", "")
                        try:
                            val = float(s)
                        except Exception:
                            return str(raw), None
                        if is_rial:
                            # تبدیل ریال به تومان
                            val = val / 10
                        # فرمت خوانا
                        if val >= 100:
                            formatted = f"{int(round(val)):,}"
                        else:
                            formatted = f"{val:,.2f}"
                        updated = item.get("t", "") if isinstance(item, dict) else ""
                        return f"{formatted} {unit}", updated
                except Exception:
                    continue
        return None, "❌ دریافت قیمت از tgju ممکن نشد. دوباره تلاش کنید."
    except Exception as e:
        return None, f"❌ خطا: {e}"


TTS_VOICE_STATUS = {}  # user_id -> voice key

TTS_VOICES = {
    "زن": {"tl": "fa", "label": "زن (فارسی)"},
    "مرد": {"tl": "fa", "label": "مرد (فارسی)"},
    "انگلیسی": {"tl": "en", "label": "انگلیسی"},
    "عربی": {"tl": "ar", "label": "عربی"},
    "ترکی": {"tl": "tr", "label": "ترکی"},
    "روسی": {"tl": "ru", "label": "روسی"},
}

async def text_to_speech(text: str, voice_key: str = "زن"):
    """تبدیل متن به ویس - برای همه زبان‌ها پایدار"""
    text = (text or "").strip()
    if not text:
        return None, "❌ متن خالی است."
    text = text[:400]

    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    path = f"{DOWNLOAD_PATH}/tts_{int(time.time())}_{random.randint(100,999)}.mp3"
    errors = []

    # چند صدا برای هر زبان (اگر اولی خالی بود بره بعدی)
    edge_voice_list = {
        "زن": ["fa-IR-DilaraNeural", "fa-IR-FaridNeural"],
        "مرد": ["fa-IR-FaridNeural", "fa-IR-DilaraNeural"],
        "انگلیسی": ["en-US-JennyNeural", "en-US-GuyNeural", "en-GB-SoniaNeural", "en-US-AriaNeural"],
        "عربی": ["ar-SA-ZariyahNeural", "ar-EG-SalmaNeural", "ar-SA-HamedNeural"],
        "ترکی": ["tr-TR-EmelNeural", "tr-TR-AhmetNeural"],
        "روسی": ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"],
    }
    gtts_lang = {
        "زن": "fa", "مرد": "fa",
        "انگلیسی": "en", "عربی": "ar", "ترکی": "tr", "روسی": "ru",
    }
    voices = edge_voice_list.get(voice_key, edge_voice_list["زن"])
    tl = gtts_lang.get(voice_key, "fa")

    def _file_ok(p):
        try:
            return os.path.exists(p) and os.path.getsize(p) > 2000
        except Exception:
            return False

    def _cleanup(p):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    # ----- 1) edge-tts -----
    try:
        import edge_tts
        for v in voices:
            try:
                _cleanup(path)
                communicate = edge_tts.Communicate(text, v)
                await communicate.save(path)
                if _file_ok(path):
                    return path, None
                errors.append(f"edge:{v}:empty")
            except Exception as e:
                errors.append(f"edge:{v}:{type(e).__name__}")
                _cleanup(path)
    except ImportError:
        errors.append("edge-tts نصب نیست")
    except Exception as e:
        errors.append(f"edge:{type(e).__name__}:{e}")

    # ----- 2) edge-tts CLI -----
    for v in voices:
        try:
            _cleanup(path)
            proc = await asyncio.create_subprocess_exec(
                "edge-tts",
                "--voice", v,
                "--text", text,
                "--write-media", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0 and _file_ok(path):
                return path, None
            errors.append(f"cli:{v}:rc={proc.returncode}")
            _cleanup(path)
        except FileNotFoundError:
            errors.append("edge-tts-cli نیست")
            break
        except Exception as e:
            errors.append(f"cli:{v}:{type(e).__name__}")
            _cleanup(path)

    # ----- 3) gTTS (برای en/tr/ru خیلی پایدار) -----
    try:
        from gtts import gTTS
        def _gtts():
            # slow=False
            tts = gTTS(text=text, lang=tl, lang_check=False)
            tts.save(path)
        _cleanup(path)
        await asyncio.to_thread(_gtts)
        if _file_ok(path):
            return path, None
        errors.append("gTTS:empty")
        _cleanup(path)
    except ImportError:
        errors.append("gTTS نصب نیست")
    except Exception as e:
        errors.append(f"gTTS:{type(e).__name__}:{e}")
        _cleanup(path)

    # ----- 4) Google HTTP -----
    try:
        q = quote(text)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://translate.google.com/",
        }
        urls = [
            f"https://translate.googleapis.com/translate_tts?ie=UTF-8&q={q}&tl={tl}&client=gtx",
            f"https://translate.google.com/translate_tts?ie=UTF-8&q={q}&tl={tl}&client=tw-ob",
        ]
        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    _cleanup(path)
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if len(data) > 2000:
                                with open(path, "wb") as f:
                                    f.write(data)
                                if _file_ok(path):
                                    return path, None
                        errors.append(f"http:{resp.status}")
                except Exception as e:
                    errors.append(f"http:{type(e).__name__}")
    except Exception as e:
        errors.append(f"http:{e}")

    detail = " | ".join(errors[-5:])
    logging.error(f"TTS failed for voice={voice_key}: {detail}")
    return None, f"❌ ساخت ویس ناموفق بود.\\n🔧 `{detail}`"


async def cleanup_old_files():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        for filename in os.listdir(DOWNLOAD_PATH):
            filepath = os.path.join(DOWNLOAD_PATH, filename)
            if os.path.isfile(filepath):
                if now - os.path.getctime(filepath) > 3600:
                    try:
                        os.remove(filepath)
                    except:
                        pass


# =============================================
# 🎰 سیستم تقلب تاس / بولینگ / اسلات (ضد اسپم)
# =============================================
async def cheat_send_dice(client, chat_id: int, emoji: str, targets: set, max_tries: int = 40, user_id: int = 0):
    """
    ایموجی بازی را دانه‌دانه می‌فرستد تا مقدار دلخواه بیاید.
    پیام‌های ناموفق را پاک می‌کند (گروه و پیوی).
    با ارسال «لغو» متوقف می‌شود.
    """
    async def _safe_delete(msg):
        if msg is None:
            return
        mid = getattr(msg, "id", None)
        try:
            await msg.delete()
            return
        except Exception:
            pass
        if mid is not None:
            for ids in (mid, [mid]):
                try:
                    await client.delete_messages(chat_id, ids)
                    return
                except Exception:
                    pass
            try:
                await client.invoke(functions.messages.DeleteMessages(id=[mid], revoke=True))
            except Exception as e:
                logging.warning(f"cheat delete fail mid={mid}: {e}")

    if user_id:
        CHEAT_CANCEL[user_id] = False
        CHEAT_RUNNING[user_id] = chat_id

    last_msg = None
    cancelled = False
    for attempt in range(1, max_tries + 1):
        if user_id and CHEAT_CANCEL.get(user_id):
            cancelled = True
            break
        try:
            await asyncio.sleep(random.uniform(1.4, 2.6))
            if user_id and CHEAT_CANCEL.get(user_id):
                cancelled = True
                break
            msg = await client.send_dice(chat_id, emoji)
            value = getattr(getattr(msg, "dice", None), "value", None)

            if value is not None and value in targets:
                if last_msg is not None and getattr(last_msg, "id", None) != msg.id:
                    await _safe_delete(last_msg)
                if user_id:
                    CHEAT_RUNNING.pop(user_id, None)
                    CHEAT_CANCEL[user_id] = False
                return True, value, attempt

            if last_msg is not None:
                await _safe_delete(last_msg)
            last_msg = msg

        except Exception as e:
            logging.warning(f"cheat_send_dice error attempt={attempt}: {e}")
            await asyncio.sleep(2.0)
            continue

    if last_msg is not None:
        await _safe_delete(last_msg)
    if user_id:
        CHEAT_RUNNING.pop(user_id, None)
        CHEAT_CANCEL[user_id] = False
    if cancelled:
        return None, None, attempt  # None = لغو شد
    return False, None, max_tries


FRIEND_REPLIES = [
    "رفیق جان دلم برات تنگ شده 💙",
    "هر جا باشی پشتتم داداش 🤝",
    "دوست داشتنت جزو عادت‌های قشنگ منه 🌸",
    "بودنت حال آدم رو خوب می‌کنه ✨",
    "قربون اون قلب مهربونت 🫶",
    "هیچ‌وقت تنهات نمی‌ذارم رفیق 💪",
    "حرفات برام ارزشمنده، همیشه گوش می‌دم 👂",
    "دنیای بدون دوستایی مثل تو بی‌معنیه 🌍",
    "لبخندت روزمو می‌سازه 😊",
    "هر وقت خواستی من اینجام 🌙",
    "رفاقت با تو یه نعمت واقعیه 🙏",
    "دمت گرم که هستی 🔥",
    "بعضی دوستی‌ها مثل طلا می‌مونن؛ مثل مال ما 🥇",
    "از ته دل برات احترام قائلم 🤍",
    "همیشه بهترین‌ها نصیبت بشه دوست خوبم 🌟",
    "با تو حرف زدن آرومم می‌کنه 🍃",
    "تو از اونایی هستی که کم پیدا می‌شن 💎",
    "هر روزت پر از انرژی مثبت باشه ☀️",
    "قربون وفاداریت رفیق 🫂",
    "یادت نره چقدر برات ارزش قائلم 💌",
    "در هر حال و هوایی کنارت می‌مونم 🌈",
    "صداقتت برام گرونه 🫡",
    "لحظه‌های خوبمون تموم‌نشدنیه 📸",
    "تو تکه‌ای از آرامش روزانه‌ای 🕊️",
    "هیچ فاصله‌ای دوستی ما رو کم‌رنگ نمی‌کنه 💫",
    "خوشحالم که تورو دارم 🥰",
    "برای تو هزار تا دلیل دارم که بگم ممنونم 🙌",
    "رفیق واقعی یعنی تو 🎯",
    "قلبم برای دوستی‌مون جا داره همیشه ❤️",
    "تو باعث افتخارمی 🏅",
    "حرف نداره رفاقتت 👌",
    "با تو بودن مثل خونه خود آدمه 🏠",
    "انرژی مثبتت مسریه ⚡",
    "تو بهترین اتفاقی بودی که افتاد برام 🍀",
    "قدر تو رو می‌دونم، همیشه 📿",
    "دلم می‌خواد همیشه بخندی 😁",
    "دوستی ما از جنس موندنه 🕰️",
    "هر پیام تو یه لبخنده برای من 📲",
    "با تو غصه‌ها کوچیک می‌شن 🎈",
    "تو تکیه‌گاه امنمی 🛡️",
    "ممنون که هستی و می‌مونی 🌹",
    "دوست دارم به معنی واقعی کلمه 💞",
    "بعضی آدما نورن؛ تو یکی از اونایی 💡",
    "رفاقت یعنی بی‌ریا بودن، مثل تو 🌿",
    "تو دلیل لبخندای بی‌دلیل منی 😏",
    "همیشه برات دعا می‌کنم 🕌",
    "تو بخش قشنگ داستان زندگی منی 📖",
    "با تو حتی سکوت هم قشنگه 🤫",
    "برای تو وقت می‌ذارم چون می‌ارزی ⏳",
    "دلم تنگ می‌شه وقتی دیر خبر می‌دی 😢",
    "تو ستاره ثابت آسمون رفاقتی ⭐",
    "مراقب خودت باش، برام مهمه 🩹",
    "رفیق جون، دنیا با تو قشنگ‌تره 🌺",
    "تو رو دوست دارم بدون شرط 💖",
    "هر جا بری دعای من باهات میاد ✈️",
    "هیچ‌کس جاتو نمی‌گیره 👑",
    "یادته چقدر خندیدیم با هم؟ 😂",
    "پیامت مثل یه فنجون چای گرمه ☕",
    "تو بهترین شنونده‌ای که می‌شناسم 🎧",
    "دوست دارم بدونی تنها نیستی 🌐",
    "تو روحیه‌ی منی وقتی کم میارم 💪",
    "رفاقت ما از جنس ماندگاره 🏛️",
    "قربون خنده‌ی قشنگت 😆",
    "هر وقت لازم داشتی، یک تماس کافیه 📞",
    "تو سرمایه‌ی عاطفی منی 💰",
    "دوست خوبم، عاشق روح بزرگتم 🦋",
    "لبخند بزن، دنیا قشنگه 🌞",
    "رفیق من، نور چشمی 👁️",
    "با تو بودن یعنی احساس امنیت 🔐",
    "تو بهترین اتفاق این سال‌های منی 📅",
    "تو رو به هیچ‌کس تعویض نمی‌کنم 🔄",
    "هر پیام صبح بخیرت روزمو می‌سازه 🌅",
    "دوست دارم، ساده و بی‌حاشیه 🤍",
    "رفیق جانی، قربونت برم 🌹",
    "با تو خندیدن از هر چیزی بهتره 🤪",
    "دوست دارم همیشه سرت سلامت باشه 🩺",
    "هیچ غمی با حضور تو دوام نمیاره 🧨",
    "قربون اون قلب پاکت 💓",
    "تو گنج پنهان زندگی منی 🗺️",
    "هر روز خدا رو شکر می‌کنم بابت تو 🙌",
    "رفیق من، تا ابد ♾️",
    "با تو بودن مثل نفس کشیدن تو هوای تازه‌ست 🌬️",
    "تو تکیه‌گاه قلبمی 💝",
    "همیشه برات می‌جنگم 🗡️",
    "دوست دارم بدونی چقدر خاصی 🎁",
    "آرامش صدات برام داروه 🎵",
    "هیچ چیز جای حرف زدن با تو رو نمی‌گیره 🗨️",
    "تو رو در شادی و غم می‌خوام کنارم 🎭",
    "هر لحظه کنار تو خاطره‌ست 📷",
    "دوست دارم بدون اینکه دلیل بیارم 💫",
    "بعضی دوستی‌ها تقدیره؛ مال ما هم همینه ✨",
    "تو آرامش بعد از طوفانی 🌊",
    "دوستی با تو افتخاره برام 🎓",
    "تو رو به عنوان دوست زندگی انتخاب کردم 📌",
    "هر روز که می‌گذره دوستی‌مون محکم‌تره 🧱",
    "دمت گرم که مراقب حالمی 🩹",
    "برای موفقیتت از ته دل خوشحالم 🥂",
    "با تو حتی مسیر سخت هم آسونه 🛤️",
    "دلم می‌خواد دنیا بهت لبخند بزنه 🌍",
    "ممنون که تحملم کردی همیشه 🙈",
    "تو دلیل ایمان من به رفاقتی 🌟",
    "دوست داشتنت کار راحته برای من 😄",
    "هیچ‌وقت فراموشت نمی‌کنم دوست عزیزم 🕊️",
    "تو از اون دوستایی هستی که باید نگه داشت 🔒",
    "با تو شدن یعنی خوشحال شدن 🥳",
    "حرفات تو گوشه‌ی ذهنمه همیشه 🧩",
    "تو تکه‌ای از روزای خوب منی ☀️",
    "رفیق واقعی در روزای سخت پیدا می‌شه؛ تو همونی 🏔️",
    "قدرت دوستی ما تو سادگیشه 🪴",
    "هر بار که می‌ای حس خوبی دارم 🌸",
    "تو رو دوست دارم چون خودتی 🌈",
    "هیچ روزی بدون فکر به تو تموم نمی‌شه 💭",
    "تو تکه گم‌شده‌ی پازل آرامشمی 🧩",
    "همیشه یه جای خالی برات دارم 🪑",
    "با تو غصه هم قشنگه 🎭",
    "دوست دارم بدونی که برات ارزش قائلم 📿",
    "تو نوری وسط روزای شلوغ منی 💡",
    "رفاقت یعنی تو 🎯",
    "تا آخرش با همیم 🚀",
]

ENEMY_REPLIES = [
    "کیرم تو رحم اجاره ای و خونی مالی مادرت",
    "دو میلیون شبی پول ویلا بدم تا مادرتو تو گوشه کناراش بگام",
    "احمق مادر کونی من کس مادرت گذاشتم تو بازم داری کسشر میگی",
]

# =============================================
# ابزارهای کمکی جدید (ساعت / آب‌وهوا / ویس / PDF / تگ)
# =============================================
COUNTRY_TZ = {
    "ایران": "Asia/Tehran", "tehran": "Asia/Tehran", "iran": "Asia/Tehran", "تهران": "Asia/Tehran",
    "آمریکا": "America/New_York", "america": "America/New_York", "نیویورک": "America/New_York", "new york": "America/New_York",
    "لس آنجلس": "America/Los_Angeles", "la": "America/Los_Angeles", "california": "America/Los_Angeles",
    "انگلیس": "Europe/London", "london": "Europe/London", "uk": "Europe/London", "لندن": "Europe/London",
    "آلمان": "Europe/Berlin", "berlin": "Europe/Berlin", "germany": "Europe/Berlin", "برلین": "Europe/Berlin",
    "فرانسه": "Europe/Paris", "paris": "Europe/Paris", "france": "Europe/Paris", "پاریس": "Europe/Paris",
    "ترکیه": "Europe/Istanbul", "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul", "استانبول": "Europe/Istanbul",
    "امارات": "Asia/Dubai", "dubai": "Asia/Dubai", "دبی": "Asia/Dubai", "uae": "Asia/Dubai",
    "عراق": "Asia/Baghdad", "baghdad": "Asia/Baghdad", "بغداد": "Asia/Baghdad",
    "هند": "Asia/Kolkata", "india": "Asia/Kolkata", "دهلی": "Asia/Kolkata",
    "چین": "Asia/Shanghai", "china": "Asia/Shanghai", "پکن": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "ژاپن": "Asia/Tokyo", "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "توکیو": "Asia/Tokyo",
    "کره": "Asia/Seoul", "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "روسیه": "Europe/Moscow", "moscow": "Europe/Moscow", "مسکو": "Europe/Moscow", "russia": "Europe/Moscow",
    "استرالیا": "Australia/Sydney", "sydney": "Australia/Sydney", "australia": "Australia/Sydney",
    "کانادا": "America/Toronto", "toronto": "America/Toronto", "canada": "America/Toronto",
    "برزیل": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "مصر": "Africa/Cairo", "cairo": "Africa/Cairo", "egypt": "Africa/Cairo",
    "عربستان": "Asia/Riyadh", "riyadh": "Asia/Riyadh", "saudi": "Asia/Riyadh",
    "اسپانیا": "Europe/Madrid", "madrid": "Europe/Madrid", "spain": "Europe/Madrid", "مادرید": "Europe/Madrid",
    "ایتالیا": "Europe/Rome", "rome": "Europe/Rome", "italy": "Europe/Rome", "رم": "Europe/Rome",
    "هلند": "Europe/Amsterdam", "amsterdam": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "سوئد": "Europe/Stockholm", "stockholm": "Europe/Stockholm", "sweden": "Europe/Stockholm",
    "نروژ": "Europe/Oslo", "oslo": "Europe/Oslo", "norway": "Europe/Oslo",
    "دانمارک": "Europe/Copenhagen", "copenhagen": "Europe/Copenhagen", "denmark": "Europe/Copenhagen",
    "فنلاند": "Europe/Helsinki", "helsinki": "Europe/Helsinki", "finland": "Europe/Helsinki",
    "لهستان": "Europe/Warsaw", "warsaw": "Europe/Warsaw", "poland": "Europe/Warsaw",
    "اوکراین": "Europe/Kyiv", "kyiv": "Europe/Kyiv", "ukraine": "Europe/Kyiv", "کیف": "Europe/Kyiv",
    "یونان": "Europe/Athens", "athens": "Europe/Athens", "greece": "Europe/Athens",
    "پرتغال": "Europe/Lisbon", "lisbon": "Europe/Lisbon", "portugal": "Europe/Lisbon",
    "سوئیس": "Europe/Zurich", "zurich": "Europe/Zurich", "switzerland": "Europe/Zurich",
    "اتریش": "Europe/Vienna", "vienna": "Europe/Vienna", "austria": "Europe/Vienna",
    "بلژیک": "Europe/Brussels", "brussels": "Europe/Brussels", "belgium": "Europe/Brussels",
    "ایرلند": "Europe/Dublin", "dublin": "Europe/Dublin", "ireland": "Europe/Dublin",
    "مکزیک": "America/Mexico_City", "mexico": "America/Mexico_City",
    "آرژانتین": "America/Argentina/Buenos_Aires", "argentina": "America/Argentina/Buenos_Aires", "buenos aires": "America/Argentina/Buenos_Aires",
    "شیلی": "America/Santiago", "chile": "America/Santiago",
    "کلمبیا": "America/Bogota", "colombia": "America/Bogota",
    "افغانستان": "Asia/Kabul", "kabul": "Asia/Kabul", "afghanistan": "Asia/Kabul", "کابل": "Asia/Kabul",
    "پاکستان": "Asia/Karachi", "karachi": "Asia/Karachi", "pakistan": "Asia/Karachi",
    "بنگلادش": "Asia/Dhaka", "dhaka": "Asia/Dhaka", "bangladesh": "Asia/Dhaka",
    "تایلند": "Asia/Bangkok", "bangkok": "Asia/Bangkok", "thailand": "Asia/Bangkok",
    "ویتنام": "Asia/Ho_Chi_Minh", "vietnam": "Asia/Ho_Chi_Minh",
    "اندونزی": "Asia/Jakarta", "jakarta": "Asia/Jakarta", "indonesia": "Asia/Jakarta",
    "مالزی": "Asia/Kuala_Lumpur", "malaysia": "Asia/Kuala_Lumpur",
    "سنگاپور": "Asia/Singapore", "singapore": "Asia/Singapore",
    "فیلیپین": "Asia/Manila", "manila": "Asia/Manila", "philippines": "Asia/Manila",
    "هنگ کنگ": "Asia/Hong_Kong", "hong kong": "Asia/Hong_Kong",
    "تایوان": "Asia/Taipei", "taipei": "Asia/Taipei", "taiwan": "Asia/Taipei",
    "نیوزیلند": "Pacific/Auckland", "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "آفریقای جنوبی": "Africa/Johannesburg", "johannesburg": "Africa/Johannesburg",
    "نیجریه": "Africa/Lagos", "lagos": "Africa/Lagos", "nigeria": "Africa/Lagos",
    "کنیا": "Africa/Nairobi", "nairobi": "Africa/Nairobi", "kenya": "Africa/Nairobi",
    "قطر": "Asia/Qatar", "qatar": "Asia/Qatar", "doha": "Asia/Qatar", "دوحه": "Asia/Qatar",
    "کویت": "Asia/Kuwait", "kuwait": "Asia/Kuwait",
    "بحرین": "Asia/Bahrain", "bahrain": "Asia/Bahrain",
    "عمان": "Asia/Muscat", "muscat": "Asia/Muscat", "oman": "Asia/Muscat",
    "اردن": "Asia/Amman", "amman": "Asia/Amman", "jordan": "Asia/Amman",
    "لبنان": "Asia/Beirut", "beirut": "Asia/Beirut", "lebanon": "Asia/Beirut",
    "سوریه": "Asia/Damascus", "damascus": "Asia/Damascus", "syria": "Asia/Damascus",
    "اسرائیل": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem", "israel": "Asia/Jerusalem",
    "شیراز": "Asia/Tehran", "مشهد": "Asia/Tehran", "اصفهان": "Asia/Tehran", "تبریز": "Asia/Tehran",
    "chicago": "America/Chicago", "chicago": "America/Chicago", "شیکاگو": "America/Chicago",
    "miami": "America/New_York", "میامی": "America/New_York",
}


async def get_time_for_place(place: str) -> str:
    key = (place or "").strip().lower()
    tz_name = COUNTRY_TZ.get(key) or COUNTRY_TZ.get(place.strip()) if place else None
    if not tz_name:
        # جستجوی تقریبی
        for k, v in COUNTRY_TZ.items():
            if key in k.lower() or k.lower() in key:
                tz_name = v
                break
    if not tz_name:
        # try zoneinfo direct
        try:
            ZoneInfo(place)
            tz_name = place
        except Exception:
            return (
                f"❌ منطقه پیدا نشد: `{place}`\n\n"
                f"مثال:\n`.ساعت ایران`\n`.ساعت Tokyo`\n`.ساعت دبی`"
            )
    try:
        now = datetime.now(ZoneInfo(tz_name))
        return (
            f"🕐 **زمان | self MR**\n\n"
            f"📍 `{place}`\n"
            f"🗺 `{tz_name}`\n"
            f"📅 {now.strftime('%Y/%m/%d')}\n"
            f"⏰ {now.strftime('%H:%M:%S')}\n"
            f"📌 {now.strftime('%A')}"
        )
    except Exception as e:
        return f"❌ خطا در دریافت زمان: {e}"


async def get_weather_for_place(place: str) -> str:
    """آب‌وهوای لحظه‌ای از wttr.in — دقیق‌تر (دما، احساس، وضعیت واقعی روز/شب)"""
    place = (place or "").strip()
    if not place:
        return "❌ مثال:\n`.آب و هوا تهران`"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; selfMR/1.0)"}
        url = f"https://wttr.in/{quote(place)}?format=j1&lang=fa"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=20) as r:
                if r.status != 200:
                    return f"❌ دریافت آب‌وهوا ناموفق (کد {r.status}). شهر را بررسی کنید."
                data = await r.json(content_type=None)

        cur_list = data.get("current_condition") or []
        if not cur_list:
            return f"❌ داده آب‌وهوا برای `{place}` پیدا نشد."
        cur = cur_list[0]

        area = place
        try:
            a = (data.get("nearest_area") or [{}])[0]
            area_name = (a.get("areaName") or [{}])[0].get("value") or place
            country = (a.get("country") or [{}])[0].get("value") or ""
            region = (a.get("region") or [{}])[0].get("value") or ""
            area = area_name
            if region:
                area += f"، {region}"
            if country:
                area += f" — {country}"
        except Exception:
            pass

        desc = ""
        try:
            lang_fa = cur.get("lang_fa") or []
            if lang_fa and lang_fa[0].get("value"):
                desc = lang_fa[0]["value"]
            else:
                desc = (cur.get("weatherDesc") or [{}])[0].get("value") or ""
        except Exception:
            desc = ""

        temp = cur.get("temp_C") or "?"
        feels = cur.get("FeelsLikeC") or temp
        humidity = cur.get("humidity") or "?"
        wind = cur.get("windspeedKmph") or "?"
        wind_dir = cur.get("winddir16Point") or ""
        pressure = cur.get("pressure") or "?"
        visibility = cur.get("visibility") or "?"
        cloud = cur.get("cloudcover") or "?"
        uv = cur.get("uvIndex") or "?"
        obs = cur.get("localObsDateTime") or cur.get("observation_time") or ""

        today_line = ""
        tomorrow_line = ""
        try:
            days = data.get("weather") or []
            if days:
                d0 = days[0]
                today_line = (
                    f"📅 امروز: کمینه `{d0.get('mintempC', '?')}°` / بیشینه `{d0.get('maxtempC', '?')}°`"
                )
            if len(days) > 1:
                d1 = days[1]
                tomorrow_line = (
                    f"📅 فردا: کمینه `{d1.get('mintempC', '?')}°` / بیشینه `{d1.get('maxtempC', '?')}°`"
                )
        except Exception:
            pass

        lines = [
            "🌤 **آب و هوا | self MR**",
            "",
            f"📍 {area}",
            f"📊 وضعیت: {desc or '—'}",
            f"🌡 دما: `{temp}°C` (احساس: `{feels}°C`)",
            f"💧 رطوبت: `{humidity}%`",
            f"💨 باد: `{wind} km/h` {wind_dir}".rstrip(),
            f"🌡 فشار: `{pressure} mb` | 👁 دید: `{visibility} km`",
            f"☁️ ابر: `{cloud}%` | ☀️ UV: `{uv}`",
        ]
        if obs:
            lines.append(f"🕐 مشاهده: `{obs}`")
        if today_line:
            lines.extend(["", today_line])
        if tomorrow_line:
            lines.append(tomorrow_line)
        return "\n".join(lines)
    except asyncio.TimeoutError:
        return "❌ زمان درخواست آب‌وهوا تمام شد. دوباره تلاش کنید."
    except Exception as e:
        return f"❌ خطا در دریافت آب‌وهوا: {e}"


async def enhance_photo_quality(client, message):
    """بهبود واقعی‌تر کیفیت عکس + ارسال بدون فشرده‌سازی تلگرام"""
    reply = message.reply_to_message
    if not reply or not (reply.photo or (reply.document and (reply.document.mime_type or "").startswith("image/"))):
        await message.edit_text("❌ روی یک **عکس** ریپلای کنید:\n`.کیفیت عکس`")
        return
    path = None
    out_path = None
    try:
        await message.edit_text("⏳ در حال بهبود کیفیت عکس...")
        # بزرگ‌ترین سایز عکس تلگرام
        path = await client.download_media(reply, file_name=f"{DOWNLOAD_PATH}/enh_{int(time.time())}")
        if not path or not os.path.exists(path):
            await message.edit_text("❌ دانلود عکس ناموفق بود.")
            return

        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        orig_w, orig_h = img.size

        # حذف نویز خیلی ملایم قبل از بزرگ‌کردن
        img = img.filter(ImageFilter.MedianFilter(size=3))

        # بزرگ‌کردن تدریجی تا ~2.5x (نتیجه نرم‌تر از یک‌باره)
        target_scale = 2.5
        target_w = int(orig_w * target_scale)
        target_h = int(orig_h * target_scale)
        max_dim = 4096
        if max(target_w, target_h) > max_dim:
            ratio = max_dim / float(max(target_w, target_h))
            target_w = max(1, int(target_w * ratio))
            target_h = max(1, int(target_h * ratio))

        cur = img
        while cur.size[0] * 1.5 < target_w and cur.size[1] * 1.5 < target_h:
            nw = min(target_w, int(cur.size[0] * 1.5))
            nh = min(target_h, int(cur.size[1] * 1.5))
            cur = cur.resize((nw, nh), resample)
        if cur.size != (target_w, target_h):
            cur = cur.resize((target_w, target_h), resample)

        # بهبود رنگ / کنتراست / شارپ کنترل‌شده
        cur = ImageEnhance.Contrast(cur).enhance(1.12)
        cur = ImageEnhance.Color(cur).enhance(1.10)
        cur = ImageEnhance.Brightness(cur).enhance(1.03)
        cur = ImageEnhance.Sharpness(cur).enhance(1.35)
        cur = cur.filter(ImageFilter.UnsharpMask(radius=1.6, percent=90, threshold=2))

        new_w, new_h = cur.size
        out_path = f"{DOWNLOAD_PATH}/enhanced_{int(time.time())}.jpg"
        cur.save(out_path, "JPEG", quality=97, optimize=True, subsampling=0)

        caption = (
            f"✨ کیفیت بهتر | self MR\n"
            f"📐 `{orig_w}×{orig_h}` → `{new_w}×{new_h}`"
        )

        # send_document = بدون فشرده‌سازی عکس تلگرام (کیفیت واقعی می‌مونه)
        try:
            await client.send_document(
                message.chat.id,
                out_path,
                caption=caption,
                force_document=True,
            )
        except TypeError:
            await client.send_document(message.chat.id, out_path, caption=caption)
        except Exception:
            # fallback
            await client.send_photo(message.chat.id, out_path, caption=caption)

        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        logging.error(f"enhance_photo: {e}")
        try:
            await message.edit_text(f"❌ خطا در بهبود عکس: {e}")
        except Exception:
            pass
    finally:
        for p in (path, out_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def search_songs_list(query: str) -> list:
    """جستجوی آهنگ از چند منبع: iTunes + LRCLIB + YouTube + SoundCloud"""
    query = (query or "").strip()
    tracks = []
    seen = set()
    if not query:
        return tracks

    def _add(ar, tr, extra_q=None):
        ar = (ar or "").strip()
        tr = (tr or "").strip()
        if not tr:
            return
        key = f"{ar}|{tr}".lower()
        if key in seen:
            return
        seen.add(key)
        tracks.append({
            "artist": ar,
            "title": tr,
            "query": (extra_q or f"{ar} {tr}".strip() or tr),
        })

    timeout = aiohttp.ClientTimeout(total=18)

    # 1) iTunes
    try:
        itunes_url = f"https://itunes.apple.com/search?term={quote(query)}&entity=song&limit=25"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(itunes_url) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for item in (data.get("results") or []):
                        _add(item.get("artistName"), item.get("trackName"))
                        if len(tracks) >= 20:
                            break
    except Exception as e:
        logging.warning(f"itunes search: {e}")

    # 2) LRCLIB
    if len(tracks) < 15:
        try:
            lr_url = f"https://lrclib.net/api/search?q={quote(query)}"
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(lr_url) as resp:
                    if resp.status == 200:
                        items = await resp.json()
                        if isinstance(items, list):
                            for it in items:
                                _add(it.get("artistName"), it.get("trackName"))
                                if len(tracks) >= 20:
                                    break
        except Exception as e:
            logging.warning(f"lrclib song search: {e}")

    # 3) YouTube search (بدون دانلود)
    if len(tracks) < 12:
        try:
            def _yt():
                opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
                out = []
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch12:{query}", download=False)
                    for e in (info.get("entries") or []):
                        if not e:
                            continue
                        title = e.get("title") or ""
                        uploader = e.get("uploader") or e.get("channel") or ""
                        vid = e.get("id") or e.get("url") or ""
                        q2 = f"https://www.youtube.com/watch?v={vid}" if vid and len(str(vid)) < 20 else f"{uploader} {title}"
                        out.append((uploader, title, q2))
                return out
            for ar, tr, q2 in await asyncio.to_thread(_yt):
                _add(ar, tr, q2)
                if len(tracks) >= 20:
                    break
        except Exception as e:
            logging.warning(f"yt search: {e}")

    # 4) SoundCloud search flat
    if len(tracks) < 12:
        try:
            def _sc():
                opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
                out = []
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"scsearch8:{query}", download=False)
                    for e in (info.get("entries") or []):
                        if not e:
                            continue
                        title = e.get("title") or ""
                        uploader = e.get("uploader") or ""
                        url = e.get("url") or e.get("webpage_url") or f"{uploader} {title}"
                        out.append((uploader, title, url))
                return out
            for ar, tr, q2 in await asyncio.to_thread(_sc):
                _add(ar, tr, q2)
                if len(tracks) >= 20:
                    break
        except Exception as e:
            logging.warning(f"sc search: {e}")

    return tracks[:20]


async def download_song_audio(search_q: str) -> tuple:
    """دانلود صوت واقعی از یوتیوب — برمی‌گرداند dict یا (None, err)"""
    search_q = (search_q or "").strip()
    if not search_q:
        return None, "کوئری خالی"
    uid = f"{int(time.time())}_{random.randint(100,999)}"
    out_tmpl = f"{DOWNLOAD_PATH}/song_{uid}.%(ext)s"
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "socket_timeout": 45,
        "retries": 3,
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
        ],
    }
    try:
        def _dl():
            with YoutubeDL(opts) as ydl:
                info = None
                # اگر لینک مستقیم بود
                candidates = []
                if search_q.startswith("http://") or search_q.startswith("https://"):
                    candidates.append(search_q)
                candidates.extend([
                    f"ytsearch1:{search_q}",
                    f"ytsearch1:{search_q} official audio",
                    f"ytsearch1:{search_q} audio",
                    f"scsearch1:{search_q}",
                    f"ytsearch1:{search_q} lyrics",
                ])
                last_err = None
                for cand in candidates:
                    try:
                        info = ydl.extract_info(cand, download=True)
                        if info:
                            break
                    except Exception as ee:
                        last_err = ee
                        info = None
                        continue
                if not info:
                    return None, f"نتیجه‌ای پیدا نشد ({last_err})"
                if "entries" in info:
                    entries = [e for e in (info.get("entries") or []) if e]
                    entry = entries[0] if entries else None
                else:
                    entry = info
                if not entry:
                    return None, "آهنگ پیدا نشد"

                title = (entry.get("track") or entry.get("title") or search_q)
                artist = (entry.get("artist") or entry.get("uploader") or entry.get("channel") or "")
                duration = entry.get("duration") or 0

                # فایل صوت
                path = None
                base = f"{DOWNLOAD_PATH}/song_{uid}"
                for ext in ("mp3", "m4a", "webm", "opus", "ogg"):
                    cand = f"{base}.{ext}"
                    if os.path.exists(cand) and os.path.getsize(cand) > 5000:
                        path = cand
                        break
                if not path:
                    for f in os.listdir(DOWNLOAD_PATH):
                        if f.startswith(f"song_{uid}") and not f.endswith((".jpg", ".png", ".webp", ".json")):
                            fp = os.path.join(DOWNLOAD_PATH, f)
                            if os.path.getsize(fp) > 5000:
                                path = fp
                                break
                if not path:
                    return None, "فایل صوت ساخته نشد (yt-dlp/ffmpeg را چک کن)"

                # کاور
                thumb = None
                for ext in ("jpg", "png", "webp"):
                    cand = f"{base}.{ext}"
                    if os.path.exists(cand):
                        thumb = cand
                        break
                if not thumb:
                    # thumbnail از info
                    turl = entry.get("thumbnail")
                    if not turl:
                        ths = entry.get("thumbnails") or []
                        if ths:
                            turl = ths[-1].get("url")
                    if turl and str(turl).startswith("http"):
                        try:
                            import urllib.request
                            thumb = f"{base}_thumb.jpg"
                            urllib.request.urlretrieve(turl, thumb)
                            if not os.path.exists(thumb) or os.path.getsize(thumb) < 500:
                                thumb = None
                        except Exception:
                            thumb = None

                return {
                    "path": path,
                    "title": str(title)[:64],
                    "artist": str(artist)[:64],
                    "duration": int(duration) if duration else 0,
                    "thumb": thumb,
                }, None

        result, err = await asyncio.to_thread(_dl)
        if err:
            return None, err
        return result, None
    except Exception as e:
        err = str(e)
        logging.warning(f"download_song_audio: {err}")
        # پیام‌های واضح
        if "Sign in" in err or "bot" in err.lower():
            return None, "یوتیوب محدود کرده؛ کمی بعد دوباره تلاش کن."
        if "ffmpeg" in err.lower():
            return None, "ffmpeg روی سرور نصب نیست."
        return None, err[:180]


async def send_downloaded_song(client, chat_id, result: dict, status_msg=None):
    """ارسال آهنگ به صورت فایل صوتی تمیز (مثل ربات‌های موزیک)"""
    path = result.get("path")
    title = result.get("title") or "آهنگ"
    artist = result.get("artist") or ""
    duration = result.get("duration") or 0
    thumb = result.get("thumb")
    try:
        kwargs = {
            "chat_id": chat_id,
            "audio": path,
            "title": title,
            "performer": artist,
            "caption": f"🎵 {artist} — {title}\nself MR" if artist else f"🎵 {title}\nself MR",
        }
        if duration:
            kwargs["duration"] = duration
        if thumb and os.path.exists(thumb):
            kwargs["thumb"] = thumb
        await client.send_audio(**kwargs)
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        return True
    except Exception as e:
        logging.error(f"send_downloaded_song: {e}")
        try:
            await client.send_document(chat_id, path, caption=f"🎵 {artist} — {title}")
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            return True
        except Exception as e2:
            if status_msg:
                try:
                    await status_msg.edit_text(f"❌ ارسال ناموفق: {e2}")
                except Exception:
                    pass
            return False
    finally:
        for p in (path, thumb):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def song_download_callback(client, callback: CallbackQuery):
    """کلیک روی دکمه آهنگ (از manager_bot) → دانلود و ارسال صوت"""
    data = callback.data or ""
    if not data.startswith("song_dl_"):
        return
    try:
        parts = data.split("_")
        owner_id = int(parts[2])
        idx = int(parts[3])
    except Exception:
        await callback.answer("❌ داده نامعتبر", show_alert=True)
        return

    if callback.from_user and callback.from_user.id != owner_id:
        await callback.answer("⛔️ فقط خودت می‌تونی دانلود کنی", show_alert=True)
        return

    tracks = SONG_SEARCH_CACHE.get(owner_id) or []
    if idx < 0 or idx >= len(tracks):
        await callback.answer("❌ منقضی شده؛ دوباره سرچ کن", show_alert=True)
        return

    track = tracks[idx]
    q = (track.get("query") or f"{track.get('artist', '')} {track.get('title', '')}").strip()
    title = track.get("title") or "آهنگ"
    artist = track.get("artist") or ""

    try:
        await callback.answer("⏳ دانلود...")
    except Exception:
        pass

    status = None
    try:
        status = await callback.message.reply_text(f"⏳ در حال دانلود:\n🎵 {artist} — {title}")
    except Exception:
        pass

    result, err = await download_song_audio(q)
    if not result and title:
        result, err = await download_song_audio(f"{title} {artist}".strip())
    if not result:
        try:
            if status:
                await status.edit_text(f"❌ دانلود ناموفق:\n{err}")
            else:
                await client.send_message(callback.message.chat.id, f"❌ دانلود ناموفق:\n{err}")
        except Exception:
            pass
        return

    if title:
        result["title"] = title
    if artist:
        result["artist"] = artist
    await send_downloaded_song(client, callback.message.chat.id, result, status_msg=status)


async def search_songs_smart(query: str) -> str:
    """متن ساده — برای سازگاری؛ لیست اصلی از search_songs_list می‌آید"""
    tracks = await search_songs_list(query)
    if not tracks:
        return f"❌ آهنگی برای `{query}` پیدا نشد."
    lines = [f"🎵 سرچ آهنگ | self MR\n\n🔎 {query}\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(f"{i}. {t.get('artist', '')} — {t.get('title', '')}")
    lines.append("\nروی دکمه آهنگ بزن تا فایل صوتی دانلود شود.")
    return "\n".join(lines)


async def voice_to_text(client, message) -> str:
    """تبدیل ویس/صوت به متن (Google STT در صورت موجود بودن)"""
    reply = message.reply_to_message
    if not reply or not (reply.voice or reply.audio or reply.video_note):
        return "❌ روی یک ویس / صوت ریپلای کنید و بفرستید:\n`.ویس به متن`"
    path = None
    wav_path = None
    try:
        path = await client.download_media(reply, file_name=f"{DOWNLOAD_PATH}/stt_{int(time.time())}")
        if not path or not os.path.exists(path):
            return "❌ دانلود ویس ناموفق بود."
        # تبدیل به wav با ffmpeg
        wav_path = f"{path}.wav"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1", wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            wav_path = path
        text = None
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio = r.record(source)
            try:
                text = r.recognize_google(audio, language="fa-IR")
            except Exception:
                text = r.recognize_google(audio, language="en-US")
        except ImportError:
            return (
                "❌ کتابخانه `SpeechRecognition` نصب نیست.\n"
                "روی سرور اضافه کنید: `pip install SpeechRecognition`"
            )
        except Exception as e:
            return f"❌ تشخیص گفتار ناموفق: {e}"
        if not text:
            return "❌ متنی تشخیص داده نشد."
        return f"🎤 **ویس → متن | self MR**\n\n{text}"
    except Exception as e:
        return f"❌ خطا: {e}"
    finally:
        for p in (path, wav_path):
            try:
                if p and os.path.exists(p) and p.endswith((".wav", ".ogg", ".mp3", ".m4a")):
                    # فقط فایل‌های موقت stt
                    if "stt_" in os.path.basename(p) or p.endswith(".wav"):
                        os.remove(p)
            except Exception:
                pass


async def photo_to_pdf(client, message):
    reply = message.reply_to_message
    if not reply or not (reply.photo or (reply.document and (reply.document.mime_type or "").startswith("image/"))):
        await message.edit_text("❌ روی یک **عکس** ریپلای کنید:\n`.عکس به pdf`")
        return
    path = None
    pdf_path = None
    try:
        path = await client.download_media(reply, file_name=f"{DOWNLOAD_PATH}/img2pdf_{int(time.time())}")
        from PIL import Image
        img = Image.open(path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        pdf_path = f"{DOWNLOAD_PATH}/photo_{int(time.time())}.pdf"
        img.save(pdf_path, "PDF", resolution=100.0)
        await client.send_document(message.chat.id, pdf_path, caption="📄 عکس → PDF | self MR")
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        try:
            await message.edit_text(f"❌ خطا: {e}")
        except Exception:
            pass
    finally:
        for p in (path, pdf_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def pdf_to_photo(client, message):
    reply = message.reply_to_message
    is_pdf = False
    if reply and reply.document:
        mime = (reply.document.mime_type or "").lower()
        name = (reply.document.file_name or "").lower()
        is_pdf = "pdf" in mime or name.endswith(".pdf")
    if not is_pdf:
        await message.edit_text("❌ روی یک فایل **PDF** ریپلای کنید:\n`.pdf به عکس`")
        return
    path = None
    out_path = None
    try:
        path = await client.download_media(reply, file_name=f"{DOWNLOAD_PATH}/pdf2img_{int(time.time())}.pdf")
        # PyMuPDF
        try:
            import fitz
            doc = fitz.open(path)
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            out_path = f"{DOWNLOAD_PATH}/pdf_page_{int(time.time())}.png"
            pix.save(out_path)
            doc.close()
            await client.send_photo(message.chat.id, out_path, caption="🖼 PDF → عکس (صفحه ۱) | self MR")
        except ImportError:
            await message.edit_text(
                "❌ برای PDF→عکس نیاز به `pymupdf` است.\n"
                "`pip install pymupdf`"
            )
            return
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        try:
            await message.edit_text(f"❌ خطا: {e}")
        except Exception:
            pass
    finally:
        for p in (path, out_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def tag_admins(client, message):
    chat = message.chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.edit_text("❌ این دستور فقط داخل گروه کار می‌کند.")
        return
    try:
        admins = []
        try:
            from pyrogram.enums import ChatMembersFilter
            admin_filter = ChatMembersFilter.ADMINISTRATORS
        except Exception:
            admin_filter = "administrators"
        async for m in client.get_chat_members(chat.id, filter=admin_filter):
            u = m.user
            if not u or u.is_bot:
                continue
            if u.username:
                admins.append(f"@{u.username}")
            else:
                name = (u.first_name or "Admin").replace("<", "").replace(">", "")
                admins.append(f"[{name}](tg://user?id={u.id})")
        if not admins:
            await message.edit_text("❌ ادمینی پیدا نشد.")
            return
        # ارسال دسته‌ای برای جلوگیری از محدودیت طول
        header = "👑 **تگ ادمین‌ها | self MR**\n\n"
        chunk = header
        for a in admins:
            if len(chunk) + len(a) + 1 > 3500:
                await client.send_message(chat.id, chunk, disable_web_page_preview=True)
                chunk = a + " "
            else:
                chunk += a + " "
        if chunk.strip():
            await client.send_message(chat.id, chunk, disable_web_page_preview=True)
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        await message.edit_text(f"❌ خطا: {e}")


async def tag_members(client, message):
    chat = message.chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.edit_text("❌ این دستور فقط داخل گروه کار می‌کند.")
        return
    try:
        await message.edit_text("⏳ در حال جمع‌آوری اعضا...")
        members = []
        count = 0
        async for m in client.get_chat_members(chat.id):
            u = m.user
            if not u or u.is_bot:
                continue
            if u.username:
                members.append(f"@{u.username}")
            else:
                name = (u.first_name or "User").replace("<", "").replace(">", "")
                members.append(f"[{name}](tg://user?id={u.id})")
            count += 1
            if count >= 200:
                break
            if count % 30 == 0:
                await asyncio.sleep(0.4)
        if not members:
            await message.edit_text("❌ عضوی پیدا نشد.")
            return
        header = f"👥 **تگ اعضا | self MR**\n(حداکثر ۲۰۰ نفر — {len(members)} نفر)\n\n"
        chunk = header
        for a in members:
            if len(chunk) + len(a) + 1 > 3500:
                await client.send_message(chat.id, chunk, disable_web_page_preview=True)
                await asyncio.sleep(1.2)
                chunk = a + " "
            else:
                chunk += a + " "
        if chunk.strip():
            await client.send_message(chat.id, chunk, disable_web_page_preview=True)
        try:
            await message.delete()
        except Exception:
            pass
    except Exception as e:
        try:
            await message.edit_text(f"❌ خطا: {e}")
        except Exception:
            pass


async def first_comment_handler(client, message: Message):
    """کامنت اول خودکار روی پست کانال (گروه بحث)"""
    try:
        # پیدا کردن owner این کلاینت
        owner_id = None
        for uid, (cli, _) in list(ACTIVE_BOTS.items()):
            if cli is client:
                owner_id = uid
                break
        if not owner_id or not FIRST_COMMENT_STATUS.get(owner_id, False):
            return
        if not message or not message.chat:
            return
        # فقط پست کانال
        if message.chat.type != ChatType.CHANNEL:
            return
        text = FIRST_COMMENT_TEXT.get(owner_id) or "🔥"
        try:
            disc = await client.get_discussion_message(message.chat.id, message.id)
            if disc:
                await disc.reply(text)
                return
        except Exception as e:
            logging.debug(f"first_comment discussion: {e}")
        # fallback: linked chat
        try:
            chat = await client.get_chat(message.chat.id)
            linked = getattr(chat, "linked_chat", None)
            if linked:
                await client.send_message(linked.id, text)
        except Exception as e:
            logging.debug(f"first_comment linked: {e}")
    except Exception as e:
        logging.warning(f"first_comment_handler: {e}")


# =============================================
# فونت‌ها
# =============================================
# فونت‌های متن (برای پیام‌ها)
FONT_STYLES = {
    "bold": {'0':'𝟎','1':'𝟏','2':'𝟐','3':'𝟑','4':'𝟒','5':'𝟓','6':'𝟔','7':'𝟕','8':'𝟖','9':'𝟗',':':':'},
    "italic": {'0':'𝟎','1':'𝟏','2':'𝟐','3':'𝟑','4':'𝟒','5':'𝟓','6':'𝟔','7':'𝟕','8':'𝟖','9':'𝟗',':':':'},
    "quote": {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "strikethrough": {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "underline": {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "spoiler": {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "mono": {'0':'𝟶','1':'𝟷','2':'𝟸','3':'𝟹','4':'𝟺','5':'𝟻','6':'𝟼','7':'𝟽','8':'𝟾','9':'𝟿',':':':'},
    "codeblock": {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
}

FONT_KEYS_ORDER = ["bold", "italic", "quote", "strikethrough", "underline", "spoiler", "mono", "codeblock"]

FONT_PERSIAN_NAMES = {
    "bold": "بولد",
    "italic": "ایتالیک",
    "quote": "نقل قول",
    "strikethrough": "خط خورده",
    "underline": "زیرخط",
    "spoiler": "اسپویلر",
    "mono": "مونو (کپی)",
    "codeblock": "کد بلاک",
}

# فونت‌های ساعت (یونیکد واقعی برای اسم پروفایل)
CLOCK_FONT_STYLES = {
    "bold":        {'0':'𝟎','1':'𝟏','2':'𝟐','3':'𝟑','4':'𝟒','5':'𝟓','6':'𝟔','7':'𝟕','8':'𝟖','9':'𝟗',':':'∶'},
    "italic":      {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "mono":        {'0':'𝟶','1':'𝟷','2':'𝟸','3':'𝟹','4':'𝟺','5':'𝟻','6':'𝟼','7':'𝟽','8':'𝟾','9':'𝟿',':':':'},
    "double":      {'0':'𝟘','1':'𝟙','2':'𝟚','3':'𝟛','4':'𝟜','5':'𝟝','6':'𝟞','7':'𝟟','8':'𝟠','9':'𝟡',':':':'},
    "sans":        {'0':'𝟢','1':'𝟣','2':'𝟤','3':'𝟥','4':'𝟦','5':'𝟧','6':'𝟨','7':'𝟩','8':'𝟪','9':'𝟫',':':':'},
    "sans_bold":   {'0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵',':':':'},
    "fullwidth":   {'0':'０','1':'１','2':'２','3':'３','4':'４','5':'５','6':'６','7':'７','8':'８','9':'９',':':'：'},
    "circled":     {'0':'⓪','1':'①','2':'②','3':'③','4':'④','5':'⑤','6':'⑥','7':'⑦','8':'⑧','9':'⑨',':':':'},
    "neg_circled": {'0':'⓿','1':'❶','2':'❷','3':'❸','4':'❹','5':'❺','6':'❻','7':'❼','8':'❽','9':'❾',':':':'},
    "parenthesized":{'0':'0','1':'⑴','2':'⑵','3':'⑶','4':'⑷','5':'⑸','6':'⑹','7':'⑺','8':'⑻','9':'⑼',':':':'},
    "subscript":   {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',':':':'},
    "superscript": {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',':':':'},
    "math_bold":   {'0':'𝟎','1':'𝟏','2':'𝟐','3':'𝟑','4':'𝟒','5':'𝟓','6':'𝟔','7':'𝟕','8':'𝟖','9':'𝟗',':':'꞉'},
    "math_dbl":    {'0':'𝟘','1':'𝟙','2':'𝟚','3':'𝟛','4':'𝟜','5':'𝟝','6':'𝟞','7':'𝟟','8':'𝟠','9':'𝟡',':':'ː'},
    "segment":     {'0':'🯰','1':'🯱','2':'🯲','3':'🯳','4':'🯴','5':'🯵','6':'🯶','7':'🯷','8':'🯸','9':'🯹',':':':'},
    "normal":      {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "dots":        {'0':'⓪','1':'➊','2':'➋','3':'➌','4':'➍','5':'➎','6':'➏','7':'➐','8':'➑','9':'➒',':':':'},
    "square":      {'0':'0','1':'1','2':'2','3':'3','4':'4','5':'5','6':'6','7':'7','8':'8','9':'9',':':':'},
    "roman":       {'0':'0','1':'Ⅰ','2':'Ⅱ','3':'Ⅲ','4':'Ⅳ','5':'Ⅴ','6':'Ⅵ','7':'Ⅶ','8':'Ⅷ','9':'Ⅸ',':':':'},
    "small":       {'0':'⁰','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',':':':'},
    "wide":        {'0':'０','1':'１','2':'２','3':'３','4':'４','5':'５','6':'６','7':'７','8':'８','9':'９',':':'：'},
    "outline":     {'0':'𝟘','1':'𝟙','2':'𝟚','3':'𝟛','4':'𝟜','5':'𝟝','6':'𝟞','7':'𝟟','8':'𝟠','9':'𝟡',':':':'},
    "heavy":       {'0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵',':':':'},
    "fancy":       {'0':'０','1':'❶','2':'❷','3':'❸','4':'❹','5':'❺','6':'❻','7':'❼','8':'❽','9':'❾',':':':'},
    "digital":     {'0':'𝟶','1':'𝟷','2':'𝟸','3':'𝟹','4':'𝟺','5':'𝟻','6':'𝟼','7':'𝟽','8':'𝟾','9':'𝟿',':':'꞉'},
    "bubble":      {'0':'⓪','1':'①','2':'②','3':'③','4':'④','5':'⑤','6':'⑥','7':'⑦','8':'⑧','9':'⑨',':':':'},
    "black_circle":{'0':'⓿','1':'❶','2':'❷','3':'❸','4':'❹','5':'❺','6':'❻','7':'❼','8':'❽','9':'❾',':':':'},
    "keycap":      {'0':'0⃣','1':'1⃣','2':'2⃣','3':'3⃣','4':'4⃣','5':'5⃣','6':'6⃣','7':'7⃣','8':'8⃣','9':'9⃣',':':':'},
    "math_sans":   {'0':'𝟢','1':'𝟣','2':'𝟤','3':'𝟥','4':'𝟦','5':'𝟧','6':'𝟨','7':'𝟩','8':'𝟪','9':'𝟫',':':':'},
    "math_sans_b": {'0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵',':':':'},
    "fw_colon":    {'0':'０','1':'１','2':'２','3':'３','4':'４','5':'５','6':'６','7':'７','8':'８','9':'９',':':'：'},
    "tiny":        {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',':':':'},
    "high":        {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',':':':'},
    "inverted":    {'0':'⓿','1':'➀','2':'➁','3':'➂','4':'➃','5':'➄','6':'➅','7':'➆','8':'➇','9':'➈',':':':'},
    "digital":     {'0':'𝟶','1':'𝟷','2':'𝟸','3':'𝟹','4':'𝟺','5':'𝟻','6':'𝟼','7':'𝟽','8':'𝟾','9':'𝟿',':':'꞉'},
    "bubble":      {'0':'⓪','1':'①','2':'②','3':'③','4':'④','5':'⑤','6':'⑥','7':'⑦','8':'⑧','9':'⑨',':':':'},
    "black_circle":{'0':'⓿','1':'❶','2':'❷','3':'❸','4':'❹','5':'❺','6':'❻','7':'❼','8':'❽','9':'❾',':':':'},
    "keycap":      {'0':'0⃣','1':'1⃣','2':'2⃣','3':'3⃣','4':'4⃣','5':'5⃣','6':'6⃣','7':'7⃣','8':'8⃣','9':'9⃣',':':':'},
    "math_sans":   {'0':'𝟢','1':'𝟣','2':'𝟤','3':'𝟥','4':'𝟦','5':'𝟧','6':'𝟨','7':'𝟩','8':'𝟪','9':'𝟫',':':':'},
    "math_sans_b": {'0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵',':':':'},
    "fw_colon":    {'0':'０','1':'１','2':'２','3':'３','4':'４','5':'５','6':'６','7':'７','8':'８','9':'９',':':'：'},
    "tiny":        {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',':':':'},
    "high":        {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',':':':'},
    "asian":       {'0':'〇','1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九',':':':'},
}

CLOCK_FONT_ORDER = [
    "bold", "mono", "double", "sans", "sans_bold", "fullwidth",
    "circled", "neg_circled", "subscript", "superscript",
    "math_bold", "math_dbl", "segment", "dots", "normal",
    "roman", "wide", "outline", "heavy", "fancy", "inverted",
    "digital", "bubble", "black_circle", "keycap", "math_sans",
    "math_sans_b", "fw_colon", "tiny", "high", "asian",
]

CLOCK_FONT_NAMES = {
    "bold": "بولد",
    "mono": "مونو",
    "double": "دوبل",
    "sans": "سانس",
    "sans_bold": "سانس‌بولد",
    "fullwidth": "کامل",
    "circled": "دایره‌ای",
    "neg_circled": "دایره توپر",
    "subscript": "زیروند",
    "superscript": "بالاوند",
    "math_bold": "ریاضی‌بولد",
    "math_dbl": "ریاضی‌دوبل",
    "segment": "سگمنت",
    "dots": "نقطه‌ای",
    "normal": "معمولی",
    "roman": "رومن",
    "wide": "عریض",
    "outline": "خالی",
    "heavy": "ضخیم",
    "fancy": "فانتزی",
    "inverted": "معکوس",
    "digital": "دیجیتال",
    "bubble": "حبابی",
    "black_circle": "دایره سیاه",
    "keycap": "کیپد",
    "math_sans": "ریاضی‌سانس",
    "math_sans_b": "ریاضی‌سانس‌بولد",
    "fw_colon": "عریض۲",
    "tiny": "ریز",
    "high": "بالا",
    "asian": "آسیایی",
}

ALL_CLOCK_CHARS = "".join(set(char for font in CLOCK_FONT_STYLES.values() for char in font.values()))
# کاراکترهای قدیمی فونت متن هم برای پاکسازی اسم
ALL_CLOCK_CHARS += "".join(set(char for font in FONT_STYLES.values() for char in font.values()))
CLOCK_CHARS_REGEX_CLASS = f"[{re.escape(ALL_CLOCK_CHARS)}]"

# =============================================
# تابع اعمال استایل‌های تلگرامی روی متن
# =============================================
def apply_telegram_style(text: str, style: str) -> str:
    if not text:
        return text
    import html as _html
    t = _html.escape(str(text))
    if style == "bold":
        return f"<b>{t}</b>"
    if style == "italic":
        return f"<i>{t}</i>"
    if style == "underline":
        return f"<u>{t}</u>"
    if style == "strikethrough":
        return f"<s>{t}</s>"
    if style == "spoiler":
        return f"<spoiler>{t}</spoiler>"
    if style == "mono":
        return f"<code>{t}</code>"
    if style == "codeblock":
        return f"<pre>{t}</pre>"
    if style == "quote":
        return f"<blockquote>{t}</blockquote>"
    return text


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


HELP_TEXT = (
"📖 راهنمای کامل self MR\n\n"
"📱 پنل → دستور: پنل\n\n"
"💎 الماس و سلف\n"
"• فعال‌سازی سلف از ربات منیجر\n"
"• کسر ساعتی الماس خودکار\n"
"• زیرمجموعه‌گیری → الماس رایگان\n\n"
"🔐 امنیت\n"
"• هشدار حذف / ویرایش پیام\n"
"• عضویت اجباری پیوی\n"
"• اسکرین | فضول پروفایل | ضدلاگین\n\n"
"✍️ متن و فونت\n"
"• فونت متن و ساعت پروفایل\n"
"• ترجمه (ریپلای + .ترجمه)\n"
"• .هوش متن گسترده + متن\n\n"
"🎨 ابزار رسانه\n"
"• ریپلای + .ذخیره\n"
"• دانلود / صوت + لینک\n"
"• .تبدیل به استیکر | .ویدیو مسیج\n"
"• .ویس به متن | .تبدیل متن به ویس\n"
"• عکس↔PDF | کیفیت عکس | سرچ آهنگ\n"
"• ایموجی پرمیوم: تنظیم/لیست/حذف ایموجی\n\n"
"👤 پروفایل\n"
"• تغییر اسم / بیو / یوزرنیم\n"
"• اسم چرخشی | آهنگ چرخشی | اکشن‌ها\n\n"
"📩 منشی آفلاین\n"
"• .منشی روشن / .منشی خاموش\n"
"• .تنظیم منشی [متن]\n"
"• .ریست منشی\n\n"
"🎰 تقلب\n"
"• .تاس 1-6 | .بولینگ | .بسکتبال | .فوتبال\n"
"• .اسلات 777 و ...\n"
"• توقف: لغو (پیام لغو پاک می‌شود)\n\n"
"📣 دیگر\n"
"• سندر فور | میو | انیمیشن\n"
"• قیمت ارز | QR | آیدی | تگ اعضا\n\n"
"⚠️ جزئیات هر بخش داخل پنل"
)

COMMAND_REGEX = r"^(راهنما|ذخیره|\.ذخیره|تکرار \d+|ریاکشن .*|ریاکشن خاموش|کپی روشن|کپی خاموش|لیست دشمن|تاس|تاس \d+|بولینگ|پنل|panel|تنظیم منشی .*|دانلود .*|صوت .*|آیدی|\.آیدی|\.دلار|\.یورو|\.صدا .*|\.تبدیل متن به ویس.*|\.فونت .*|\..+)$"


# =============================================
# مدیریت داده JSON
# =============================================
class DataManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = self.load_data()

    def load_data(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        data = self.get_default_data()
                    if "users" not in data:
                        data["users"] = {}
                    if "sessions" not in data:
                        data["sessions"] = {}
                    self.data = data
                    logging.info(f"✅ Data loaded from {self.file_path} users={len(data.get('users', {}))}")
                    return data
            except Exception as e:
                logging.error(f"Error loading data: {e}")
                self.data = self.get_default_data()
                return self.data
        else:
            logging.info(f"⚠️ No data file found, creating new one")
            self.data = self.get_default_data()
            return self.data

    def reload(self):
        return self.load_data()

    def get_default_data(self):
        return {"users": {}, "sessions": {}}

    def save_data(self):
        try:
            folder = os.path.dirname(os.path.abspath(self.file_path)) or "."
            os.makedirs(folder, exist_ok=True)
            tmp_path = self.file_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.file_path)
            logging.info(f"💾 Data saved to {self.file_path}")
            return True
        except Exception as e:
            logging.error(f"Error saving data: {e}")
            try:
                if os.path.exists(self.file_path + ".tmp"):
                    os.remove(self.file_path + ".tmp")
            except Exception:
                pass
            return False

    def get_user_data(self, user_id):
        user_id_str = str(user_id)
        default_user_structure = {
            "user_id": user_id,
            "phone": "",
            "first_name": "",
            "username": "",
            "session_string": "",
            "settings": {
                "font": "bold",
                "clock": True,
                "bold": False,
                "text_font": "none",
                "secretary": False,
                "secretary_msg": "",
                "auto_seen": False,
                "pv_lock": False,
                "anti_login": False,
                "typing": False,
                "playing": False,
                "global_enemy": False,
                "copy_mode": False,
                "translate": None,
                "action": None,
                "force_join_pv": False,
                "force_join_channels": [],
                "edit_alert": False,
                "delete_alert": False,
                "rotating_names": [],
                "rotating_interval": 10,
                "rotating_name": False,
                "rotating_music": [],
                "rotating_music_interval": 1,
                "rotating_music_on": False,
                "first_comment": False,
                "first_comment_text": "🔥",
                "tts_voice": "زن",
            },
            "enemies": [],
            "muted": [],
            "reactions": {},
            "replied_users": [],
            "enemy_queue": [],
            "original_profile": {},
        }

        if user_id_str not in self.data["users"]:
            self.data["users"][user_id_str] = default_user_structure
            self.save_data()
            return self.data["users"][user_id_str]

        user_data = self.data["users"][user_id_str]
        changed = False
        for key, value in default_user_structure.items():
            if key not in user_data:
                user_data[key] = value
                changed = True
            elif key == "settings" and isinstance(value, dict):
                if "settings" not in user_data or not isinstance(user_data.get("settings"), dict):
                    user_data["settings"] = {}
                    changed = True
                for setting_key, setting_value in value.items():
                    if setting_key not in user_data["settings"]:
                        user_data["settings"][setting_key] = setting_value
                        changed = True
        if changed:
            self.save_data()
        return user_data

    def update_user_data(self, user_id, updates):
        user_data = self.get_user_data(user_id)
        for key, value in updates.items():
            if key == "settings" and isinstance(value, dict):
                if "settings" not in user_data:
                    user_data["settings"] = {}
                for setting_key, setting_value in value.items():
                    user_data["settings"][setting_key] = setting_value
            else:
                user_data[key] = value
        self.save_data()
        return user_data

    def save_session(self, phone, session_string, user_id, first_name="", username=""):
        self.data["sessions"][phone] = {"string": session_string, "user_id": user_id}
        user_data = self.get_user_data(user_id)
        user_data["phone"] = phone
        user_data["session_string"] = session_string
        user_data["first_name"] = first_name
        user_data["username"] = username
        self.save_data()

    def get_all_sessions(self):
        return self.data["sessions"].items()

    def get_all_users(self):
        return self.data["users"]

    def save_enemies(self, user_id, enemies_set):
        user_data = self.get_user_data(user_id)
        user_data["enemies"] = [list(item) for item in enemies_set]
        self.save_data()

    def get_enemies(self, user_id):
        user_data = self.get_user_data(user_id)
        return set(tuple(item) for item in user_data.get("enemies", []))

    def save_muted(self, user_id, muted_set):
        user_data = self.get_user_data(user_id)
        user_data["muted"] = [list(item) for item in muted_set]
        self.save_data()

    def get_muted(self, user_id):
        user_data = self.get_user_data(user_id)
        return set(tuple(item) for item in user_data.get("muted", []))

    def save_reactions(self, user_id, reactions_dict):
        user_data = self.get_user_data(user_id)
        user_data["reactions"] = reactions_dict
        self.save_data()

    def save_replied_users(self, user_id, replied_set):
        user_data = self.get_user_data(user_id)
        user_data["replied_users"] = list(replied_set)
        self.save_data()

    def save_enemy_queue(self, user_id, queue):
        user_data = self.get_user_data(user_id)
        user_data["enemy_queue"] = queue
        self.save_data()


data_manager = DataManager(DATA_FILE)

# =============================================
# وضعیت‌های حافظه
# =============================================
ACTIVE_BOTS = {}
ACTIVE_ENEMIES = {}
ACTIVE_FRIENDS = {}
FRIEND_REPLY_QUEUES = {}
ENEMY_REPLY_QUEUES = {}
SECRETARY_REPLY_MESSAGE = "آفلاینم فعلا بعدا جواب میدم"
SECRETARY_MODE_STATUS = {}
CHEAT_CANCEL = {}  # user_id -> True وقتی لغو تقلب
CHEAT_RUNNING = {}  # user_id -> chat_id در حال تقلب
SECRETARY_CUSTOM_MESSAGES = {}
USERS_REPLIED_IN_SECRETARY = {}
SECRETARY_LAST_REPLY = {}  # owner_id -> {peer_id: timestamp}
SECRETARY_COOLDOWN_SEC = 600  # هر ۱۰ دقیقه یک‌بار
MUTED_USERS = {}
USER_FONT_CHOICES = {}
CLOCK_STATUS = {}
PROFILE_PHOTO_CLOCK = {}  # user_id -> bool ساعت گرافیکی روی عکس پروفایل
PROFILE_PHOTO_CLOCK_BASE = {}  # user_id -> path عکس اصلی بدون قاب
PROFILE_PHOTO_CLOCK_LAST = {}  # user_id -> file_id آخرین عکس ساعت (فقط همان پاک شود)
PROFILE_PHOTO_CLOCK_IDS = {}  # user_id -> set(file_id) فقط عکس‌هایی که خودمان به‌عنوان ساعت گذاشتیم
PROFILE_PHOTO_CLOCK_LAST_MINUTE = {}  # user_id -> "HH:MM" آخرین دقیقه رسم‌شده
PROFILE_PHOTO_CLOCK_COLOR = {}  # user_id -> color key
PROFILE_PHOTO_CLOCK_STYLE = {}  # user_id -> neon|classic

PHOTO_CLOCK_COLORS = {
    "cyan":   (0, 255, 220),
    "green":  (80, 255, 120),
    "purple": (180, 100, 255),
    "red":    (255, 70, 90),
    "blue":   (80, 160, 255),
    "gold":   (255, 200, 60),
    "white":  (240, 240, 255),
    "pink":   (255, 100, 180),
}


# ========== تاریخ (جلالی / قمری / میلادی) + بیو ==========
BIO_MILADI_DATE = {}  # user_id -> bool
BIO_MILADI_ORIGINAL = {}  # user_id -> متن بیو بدون خط تاریخ
BIO_MILADI_LAST_DAY = {}  # user_id -> "YYYY-MM-DD"
BIO_MILADI_MARKER = "📅"

_PERSIAN_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]
# datetime.weekday(): Mon=0 ... Sun=6 → map to Persian list above
_PERSIAN_WEEKDAYS_DT = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]
_GREG_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_GREG_MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]
_JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
_ARABIC_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_HIJRI_MONTHS = ["محرم", "صفر", "ربيع الأول", "ربيع الثاني", "جمادى الأولى", "جمادى الآخرة",
                 "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة"]


def _gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def _gregorian_to_hijri(gy, gm, gd):
    # الگوریتم تقریبی کوویتی (برای نمایش روزمره کافی است)
    try:
        from datetime import date as _date
        jd = _date(gy, gm, gd).toordinal() + 1721425
        # Islamic calendar approximation
        l = jd - 1948440 + 10632
        n = (l - 1) // 10631
        l = l - 10631 * n + 354
        j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
        l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
        hm = (24 * l) // 709
        hd = l - (709 * hm) // 24
        hy = 30 * n + j - 30
        if hm < 1:
            hm = 1
        if hm > 12:
            hm = 12
        if hd < 1:
            hd = 1
        if hd > 30:
            hd = 30
        return int(hy), int(hm), int(hd)
    except Exception:
        # fallback ثابت نسبی
        return 1447, 1, 1


def format_full_date_block(now=None) -> str:
    """بلوک کامل تاریخ مثل نمونه کاربر"""
    if now is None:
        now = datetime.now(TEHRAN_TIMEZONE)
    gy, gm, gd = now.year, now.month, now.day
    jy, jm, jd = _gregorian_to_jalali(gy, gm, gd)
    hy, hm, hd = _gregorian_to_hijri(gy, gm, gd)
    wd = now.weekday()  # Mon=0
    # روز سال میلادی
    start = datetime(gy, 1, 1, tzinfo=TEHRAN_TIMEZONE)
    day_of_year = (now.date() - start.date()).days + 1
    # سال کبیسه؟
    import calendar
    days_in_year = 366 if calendar.isleap(gy) else 365
    remaining = days_in_year - day_of_year
    pct_done = (day_of_year / days_in_year) * 100
    pct_left = (remaining / days_in_year) * 100
    hhmm = now.strftime("%H:%M")
    return (
        f" ساعت و تاریخ :\n"
        f" ساعت : \n"
        f"‏┘─ {hhmm}\n"
        f" تاریخ امروز : \n"
        f"┘─ {_PERSIAN_WEEKDAYS_DT[wd]} - {jd} {_JALALI_MONTHS[jm-1]} {jy}\n"
        f" تاریخ قمری : \n"
        f"┘─ {_ARABIC_WEEKDAYS[wd]} - {hd} {_HIJRI_MONTHS[hm-1]} {hy}\n"
        f" تاریخ میلادی : \n"
        f"‏┘─ {_GREG_WEEKDAYS[wd]} - {gy} {gd} {_GREG_MONTHS[gm-1]}\n"
        f" روز های سپری شده : \n"
        f"┘─ {day_of_year} روز ( {pct_done:.2f} درصد )\n"
        f" روز های باقی مانده : \n"
        f"┘─ {remaining} روز ( {pct_left:.2f} درصد )"
    )


def miladi_bio_line(now=None) -> str:
    if now is None:
        now = datetime.now(TEHRAN_TIMEZONE)
    return f"{BIO_MILADI_MARKER} {now.strftime('%Y/%m/%d')} | { _GREG_WEEKDAYS[now.weekday()] }"


def merge_bio_with_miladi(base_bio: str, now=None) -> str:
    """بیو اصلی را نگه می‌دارد و خط تاریخ میلادی را به‌روز می‌کند"""
    base = (base_bio or "").strip()
    # حذف خطوط قبلی تاریخ ما
    lines = [ln for ln in base.splitlines() if BIO_MILADI_MARKER not in ln and not ln.strip().startswith("📅")]
    base_clean = "\n".join(lines).strip()
    line = miladi_bio_line(now)
    if base_clean:
        # محدودیت ۷۰ کاراکتر تلگرام برای about
        combined = base_clean + "\n" + line
        if len(combined) > 70:
            # کوتاه کردن بیو اصلی
            max_base = 70 - len(line) - 1
            if max_base < 0:
                return line[:70]
            base_clean = base_clean[:max_base].rstrip()
            combined = base_clean + "\n" + line
        return combined[:70]
    return line[:70]


async def apply_bio_miladi_date(client: Client, user_id: int, force: bool = False):
    """تاریخ میلادی را در بیو می‌گذارد / روزانه آپدیت می‌کند"""
    if not BIO_MILADI_DATE.get(user_id):
        return
    now = datetime.now(TEHRAN_TIMEZONE)
    day_key = now.strftime("%Y-%m-%d")
    if not force and BIO_MILADI_LAST_DAY.get(user_id) == day_key:
        return
    try:
        me = await client.get_me()
        current = ""
        try:
            full = await client.get_chat("me")
            current = getattr(full, "bio", None) or ""
        except Exception:
            current = ""
        # اولین بار بیو اصلی را ذخیره کن
        if user_id not in BIO_MILADI_ORIGINAL:
            lines = [ln for ln in (current or "").splitlines() if BIO_MILADI_MARKER not in ln]
            BIO_MILADI_ORIGINAL[user_id] = "\n".join(lines).strip()
        base = BIO_MILADI_ORIGINAL.get(user_id, "")
        new_bio = merge_bio_with_miladi(base, now)
        await client.update_profile(bio=new_bio)
        BIO_MILADI_LAST_DAY[user_id] = day_key
        logging.info("bio miladi updated uid=%s day=%s", user_id, day_key)
    except Exception as e:
        logging.warning("bio miladi update: %s", e)


async def clear_bio_miladi_date(client: Client, user_id: int):
    """خاموش: خط تاریخ را بردار و بیو اصلی را برگردان"""
    try:
        base = BIO_MILADI_ORIGINAL.get(user_id)
        if base is None:
            me_chat = await client.get_chat("me")
            current = getattr(me_chat, "bio", None) or ""
            lines = [ln for ln in current.splitlines() if BIO_MILADI_MARKER not in ln]
            base = "\n".join(lines).strip()
        await client.update_profile(bio=base or "")
        BIO_MILADI_ORIGINAL.pop(user_id, None)
        BIO_MILADI_LAST_DAY.pop(user_id, None)
    except Exception as e:
        logging.warning("clear bio miladi: %s", e)


async def bio_miladi_daily_task(client: Client, user_id: int):
    await asyncio.sleep(8)
    while user_id in ACTIVE_BOTS:
        try:
            if BIO_MILADI_DATE.get(user_id):
                await apply_bio_miladi_date(client, user_id, force=False)
            # هر ۱۰ دقیقه چک — فقط روز که عوض شد آپدیت می‌کند
            await asyncio.sleep(600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning("bio_miladi_daily_task: %s", e)
            await asyncio.sleep(120)


def profile_clock_base_path(user_id: int) -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)) if "DATA_FILE" in dir() else ".", "profile_bases")
    try:
        # DATA_FILE may be defined later — safe fallback
        pass
    except Exception:
        pass
    try:
        base_dir = os.path.join(os.getcwd(), "profile_bases")
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, f"{int(user_id)}.jpg")
    except Exception:
        return f"/tmp/pf_base_{int(user_id)}.jpg"

ROTATING_NAMES = {}
ROTATING_NAME_INTERVAL = {}
ROTATING_NAME_STATUS = {}
ROTATING_NAME_INDEX = {}
ROTATING_MUSIC = {}          # user_id -> list[{file_id, title}]
ROTATING_MUSIC_INTERVAL = {} # ساعت
ROTATING_MUSIC_STATUS = {}
ROTATING_MUSIC_INDEX = {}
# میو خودکار: user_id -> set/list of chat_id هایی که میو روشن است
MEOW_CHATS = {}

# سندر بنر گروهی: user_id -> { chat_id(str) -> config }
# config: enabled, mode(copy/forward), banner_chat_id, banner_msg_id, delay, hourly_limit, sent_hour, hour_ts
SENDER_CONFIG = {}
# سندر فور همگانی: user_id -> {running, banner_chat_id, banner_msg_id, sent, failed, task}
SENDER_MASS = {}
BOLD_MODE_STATUS = {}
TEXT_FONT_STATUS = {}
SELF_STATUS = {}  # user_id -> True=روشن / False=خاموش (پیش‌فرض روشن)
AUTO_SEEN_STATUS = {}
AUTO_REACTION_TARGETS = {}
IMAGE_SEARCH_HISTORY = {}
PACK_EMOJI_CACHE = {}
PENDING_SONG_PICK = {}
PREMIUM_LETTER_MAP = {}
PROFILE_FLOOD_UNTIL = {}
PROFILE_NAME_FLOOD_UNTIL = {}
PROFILE_PHOTO_FLOOD_UNTIL = {}
PROFILE_PHOTO_LAST_UPLOAD = {}
PV_MSG_CACHE = {}
SONG_SEARCH_CACHE = {}
PV_LOCK_STATUS = {}
PV_FILTER_STICKER = {}
PV_FILTER_GIF = {}
ANTI_LOGIN_STATUS = {}
TYPING_MODE_STATUS = {}
PLAYING_MODE_STATUS = {}
ACTION_STATUS = {}
GLOBAL_ENEMY_STATUS = {}
COPY_MODE_STATUS = {}
AUTO_TRANSLATE_TARGET = {}
PROFILE_SNOOPS = {}
PREMIUM_EMOJI_MAP = {}
EMOJI_PREMIUM_CONVERT = {}
EMOJI_CHAR_TO_PREMIUM = {}
FORCE_JOIN_PV_STATUS = {}
FORCE_JOIN_CHANNELS = {}
EDIT_ALERT_STATUS = {}
DELETE_ALERT_STATUS = {}
FIRST_COMMENT_STATUS = {}
FIRST_COMMENT_TEXT = {}
ORIGINAL_PROFILE_DATA = {}
EMOJI_PREMIUM_TEMPLATES = {}
PEMOJI_MAP = {}  # uid -> {emoji: custom_id}
PEMOJI_STATUS = {}
def is_self_on(user_id) -> bool:
    return bool(SELF_STATUS.get(int(user_id), True))


def panel_main_title(user_id) -> str:
    uid = int(user_id)
    return f"⚡️ مدیریت پیشرفته self MR\n👤 کاربر: {uid}"


def panel_page_title(user_id, page: int) -> str:
    uid = int(user_id)
    titles = {
        1: panel_main_title(uid),
        2: "✏️ حالت متن / فونت‌ها | self MR",
        3: "🛡 بخش امنیتی | self MR",
        4: "⚡ اکشن‌ها | self MR",
        5: "🕐 فونت ساعت | self MR",
        19: "🧠 هوش مصنوعی | self MR\nاز دکمه‌ها یک قابلیت را انتخاب کنید.",
        35: "🐱 میو | self MR",
        51: "🕰 ساعت در پروفایل | self MR\nروشن/خاموش + رنگ نئون\nهر دقیقه عکس پروفایل با عقربه به‌روز می‌شود.",
        52: "📅 تاریخ میلادی بیو | self MR\nروشن/خاموش — تاریخ روز در بیو\nدستور: .تاریخ",
    }
    if page in titles:
        return titles[page]
    return f"⚡️ self MR\n📄 صفحه {page}"


def can_upload_profile_photo(user_id: int) -> tuple:
    """(ok, wait_sec) — آیا الان می‌شود عکس پروفایل گذاشت؟"""
    left = profile_photo_flood_remaining(user_id)
    if left > 0:
        return False, left
    last = PROFILE_PHOTO_LAST_UPLOAD.get(user_id, 0)
    gap = int(time.time() - last)
    need = PROFILE_PHOTO_MIN_GAP - gap
    if need > 0:
        return False, need
    return True, 0


def mark_profile_photo_uploaded(user_id: int):
    PROFILE_PHOTO_LAST_UPLOAD[user_id] = time.time()


def profile_photo_flood_remaining(user_id: int) -> int:

    """ثانیه باقی‌مانده محدودیت آپلود عکس پروفایل (۰ = آزاد)"""
    left = int(PROFILE_PHOTO_FLOOD_UNTIL.get(user_id, 0) - time.time())
    return max(0, left)

def format_flood_wait_fa(sec: int) -> str:
    if sec <= 0:
        return "آزاد"
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h} ساعت و {m} دقیقه"
    if m:
        return f"{m} دقیقه و {s} ثانیه"
    return f"{s} ثانیه"

def note_profile_photo_flood(user_id: int, err: str) -> int:
    m = re.search(r"(\d+)", str(err) or "")
    sec = int(m.group(1)) if m else 600
    PROFILE_PHOTO_FLOOD_UNTIL[user_id] = time.time() + sec + 15
    return sec



def load_all_states():
    users_data = data_manager.get_all_users()
    for user_id_str, user_data in users_data.items():
        try:
            user_id = int(user_id_str)
        except Exception:
            continue
        settings = user_data.get("settings", {}) or {}
        USER_FONT_CHOICES[user_id] = settings.get("font", "bold")
        CLOCK_STATUS[user_id] = settings.get("clock", True)
        PROFILE_PHOTO_CLOCK[user_id] = bool(settings.get("photo_clock", False))
        PROFILE_PHOTO_CLOCK_COLOR[user_id] = settings.get("photo_clock_color") or "cyan"
        PROFILE_PHOTO_CLOCK_STYLE[user_id] = settings.get("photo_clock_style") or "neon"
        BIO_MILADI_DATE[user_id] = bool(settings.get("bio_miladi", False))
        if settings.get("bio_miladi_original") is not None:
            BIO_MILADI_ORIGINAL[user_id] = settings.get("bio_miladi_original") or ""
        BOLD_MODE_STATUS[user_id] = settings.get("bold", False)
        TEXT_FONT_STATUS[user_id] = settings.get("text_font", "none")
        SELF_STATUS[user_id] = bool(settings.get("self_status", True))
        SECRETARY_MODE_STATUS[user_id] = settings.get("secretary", False)
        SECRETARY_CUSTOM_MESSAGES[user_id] = settings.get("secretary_msg", "")
        AUTO_SEEN_STATUS[user_id] = settings.get("auto_seen", False)
        PV_LOCK_STATUS[user_id] = settings.get("pv_lock", False)
        PV_FILTER_STICKER[user_id] = bool(settings.get("pv_filter_sticker", False))
        PV_FILTER_GIF[user_id] = bool(settings.get("pv_filter_gif", False))
        ANTI_LOGIN_STATUS[user_id] = settings.get("anti_login", False)
        TYPING_MODE_STATUS[user_id] = settings.get("typing", False)
        PLAYING_MODE_STATUS[user_id] = settings.get("playing", False)
        ACTION_STATUS[user_id] = settings.get("action")
        GLOBAL_ENEMY_STATUS[user_id] = settings.get("global_enemy", False)
        COPY_MODE_STATUS[user_id] = settings.get("copy_mode", False)
        AUTO_TRANSLATE_TARGET[user_id] = settings.get("translate", None)
        try:
            raw = settings.get("profile_snoops") or {}
            if isinstance(raw, dict):
                PROFILE_SNOOPS[user_id] = {int(k): v for k, v in raw.items()}
            else:
                PROFILE_SNOOPS[user_id] = {}
        except Exception:
            PROFILE_SNOOPS[user_id] = {}
        FORCE_JOIN_PV_STATUS[user_id] = settings.get("force_join_pv", False)
        FORCE_JOIN_CHANNELS[user_id] = list(settings.get("force_join_channels") or [])
        EDIT_ALERT_STATUS[user_id] = settings.get("edit_alert", False)
        DELETE_ALERT_STATUS[user_id] = settings.get("delete_alert", False)
        ROTATING_NAMES[user_id] = list(settings.get("rotating_names") or [])
        ROTATING_NAME_INTERVAL[user_id] = int(settings.get("rotating_interval") or 10)
        ROTATING_NAME_STATUS[user_id] = bool(settings.get("rotating_name", False))
        ROTATING_NAME_INDEX[user_id] = 0
        ROTATING_MUSIC[user_id] = list(settings.get("rotating_music") or [])
        try:
            EMOJI_PREMIUM_CONVERT[user_id] = bool(settings.get("emoji_premium_convert", False))
            _pm = settings.get("pemoji_map") or {}
            if isinstance(_pm, dict):
                PEMOJI_MAP[user_id] = {str(k): str(v) for k, v in _pm.items()}
                # همگام با EMOJI_CHAR_TO_PREMIUM
                bucket = dict(EMOJI_CHAR_TO_PREMIUM.get(user_id) or {})
                for k, v in _pm.items():
                    try:
                        bucket[str(k)] = int(v)
                    except Exception:
                        pass
                EMOJI_CHAR_TO_PREMIUM[user_id] = bucket
            PEMOJI_STATUS[user_id] = bool(settings.get("pemoji_status", EMOJI_PREMIUM_CONVERT.get(user_id, False)))
            ecm = settings.get("emoji_char_map") or {}
            if isinstance(ecm, dict):
                EMOJI_CHAR_TO_PREMIUM[user_id] = {str(k): int(v) for k, v in ecm.items()}
        except Exception:
            pass

        try:
            pe = settings.get("premium_emojis") or {}
            if isinstance(pe, dict):
                PREMIUM_EMOJI_MAP[user_id] = {str(k): int(v) for k, v in pe.items()}
        except Exception:
            PREMIUM_EMOJI_MAP[user_id] = {}

        ROTATING_MUSIC_INTERVAL[user_id] = int(settings.get("rotating_music_interval") or 1)
        ROTATING_MUSIC_STATUS[user_id] = bool(settings.get("rotating_music_on", False))
        try:
            mc = settings.get("meow_chats") or []
            MEOW_CHATS[user_id] = set(int(x) for x in mc) if isinstance(mc, (list, set, tuple)) else set()
        except Exception:
            MEOW_CHATS[user_id] = set()
        try:
            sc = settings.get("sender_config") or {}
            # keys must be str for JSON
            SENDER_CONFIG[user_id] = {str(k): v for k, v in sc.items()} if isinstance(sc, dict) else {}
        except Exception:
            SENDER_CONFIG[user_id] = {}
        ROTATING_MUSIC_INDEX[user_id] = 0
        TTS_VOICE_STATUS[user_id] = settings.get("tts_voice", "زن")
        FIRST_COMMENT_STATUS[user_id] = bool(settings.get("first_comment", False))
        FIRST_COMMENT_TEXT[user_id] = settings.get("first_comment_text", "🔥") or "🔥"
        ACTIVE_ENEMIES[user_id] = set(tuple(item) for item in user_data.get("enemies", []))
        try:
            ACTIVE_FRIENDS[user_id] = set(tuple(item) for item in user_data.get("friends", []))
        except Exception:
            ACTIVE_FRIENDS[user_id] = set()
        MUTED_USERS[user_id] = set(tuple(item) for item in user_data.get("muted", []))
        AUTO_REACTION_TARGETS[user_id] = user_data.get("reactions", {}) or {}
        USERS_REPLIED_IN_SECRETARY[user_id] = set(user_data.get("replied_users", []))
        ENEMY_REPLY_QUEUES[user_id] = user_data.get("enemy_queue", []) or []
        ORIGINAL_PROFILE_DATA[user_id] = user_data.get("original_profile", {}) or {}


def apply_user_settings_from_db(user_id: int):
    try:
        user_data = data_manager.get_user_data(user_id)
        settings = user_data.get("settings") or {}
        USER_FONT_CHOICES[user_id] = settings.get("font", "bold")
        CLOCK_STATUS[user_id] = bool(settings.get("clock", True)) if "clock" in settings else True
        BOLD_MODE_STATUS[user_id] = bool(settings.get("bold", False))
        TEXT_FONT_STATUS[user_id] = settings.get("text_font", "none")
        SELF_STATUS[user_id] = bool(settings.get("self_status", True))
        SECRETARY_MODE_STATUS[user_id] = bool(settings.get("secretary", False))
        _sm = (settings.get("secretary_msg", "") or "").strip()
        if _sm in ("رید", "rid", "test") or len(_sm) < 2:
            _sm = ""
        SECRETARY_CUSTOM_MESSAGES[user_id] = _sm
        AUTO_SEEN_STATUS[user_id] = bool(settings.get("auto_seen", False))
        PV_LOCK_STATUS[user_id] = bool(settings.get("pv_lock", False))
        PV_FILTER_STICKER[user_id] = bool(settings.get("pv_filter_sticker", False))
        PV_FILTER_GIF[user_id] = bool(settings.get("pv_filter_gif", False))
        ANTI_LOGIN_STATUS[user_id] = bool(settings.get("anti_login", False))
        TYPING_MODE_STATUS[user_id] = bool(settings.get("typing", False))
        PLAYING_MODE_STATUS[user_id] = bool(settings.get("playing", False))
        ACTION_STATUS[user_id] = settings.get("action")
        GLOBAL_ENEMY_STATUS[user_id] = bool(settings.get("global_enemy", False))
        COPY_MODE_STATUS[user_id] = bool(settings.get("copy_mode", False))
        AUTO_TRANSLATE_TARGET[user_id] = settings.get("translate", None)
        try:
            raw = settings.get("profile_snoops") or {}
            if isinstance(raw, dict):
                PROFILE_SNOOPS[user_id] = {int(k): v for k, v in raw.items()}
            else:
                PROFILE_SNOOPS[user_id] = {}
        except Exception:
            PROFILE_SNOOPS[user_id] = {}
        FORCE_JOIN_PV_STATUS[user_id] = bool(settings.get("force_join_pv", False))
        FORCE_JOIN_CHANNELS[user_id] = list(settings.get("force_join_channels") or [])

        try:
            sz = settings.get("starzy_photo") or {}
            if isinstance(sz, dict) and (sz.get("link") or sz.get("time")):
                STARZY_PHOTO[user_id] = {
                    "link": str(sz.get("link") or "").strip(),
                    "time": str(sz.get("time") or "").strip(),
                    "last_fire": str(sz.get("last_fire") or ""),
                }
        except Exception:
            pass
        EDIT_ALERT_STATUS[user_id] = bool(settings.get("edit_alert", False))
        DELETE_ALERT_STATUS[user_id] = bool(settings.get("delete_alert", False))
        ROTATING_NAMES[user_id] = list(settings.get("rotating_names") or [])
        ROTATING_NAME_INTERVAL[user_id] = int(settings.get("rotating_interval") or 10)
        ROTATING_NAME_STATUS[user_id] = bool(settings.get("rotating_name", False))
        if user_id not in ROTATING_NAME_INDEX:
            ROTATING_NAME_INDEX[user_id] = 0
        ROTATING_MUSIC[user_id] = list(settings.get("rotating_music") or [])
        try:
            EMOJI_PREMIUM_CONVERT[user_id] = bool(settings.get("emoji_premium_convert", False))
            _pm = settings.get("pemoji_map") or {}
            if isinstance(_pm, dict):
                PEMOJI_MAP[user_id] = {str(k): str(v) for k, v in _pm.items()}
                # همگام با EMOJI_CHAR_TO_PREMIUM
                bucket = dict(EMOJI_CHAR_TO_PREMIUM.get(user_id) or {})
                for k, v in _pm.items():
                    try:
                        bucket[str(k)] = int(v)
                    except Exception:
                        pass
                EMOJI_CHAR_TO_PREMIUM[user_id] = bucket
            PEMOJI_STATUS[user_id] = bool(settings.get("pemoji_status", EMOJI_PREMIUM_CONVERT.get(user_id, False)))
            ecm = settings.get("emoji_char_map") or {}
            if isinstance(ecm, dict):
                EMOJI_CHAR_TO_PREMIUM[user_id] = {str(k): int(v) for k, v in ecm.items()}
        except Exception:
            pass

        try:
            pe = settings.get("premium_emojis") or {}
            if isinstance(pe, dict):
                PREMIUM_EMOJI_MAP[user_id] = {str(k): int(v) for k, v in pe.items()}
        except Exception:
            PREMIUM_EMOJI_MAP[user_id] = {}

        ROTATING_MUSIC_INTERVAL[user_id] = int(settings.get("rotating_music_interval") or 1)
        ROTATING_MUSIC_STATUS[user_id] = bool(settings.get("rotating_music_on", False))
        try:
            mc = settings.get("meow_chats") or []
            MEOW_CHATS[user_id] = set(int(x) for x in mc) if isinstance(mc, (list, set, tuple)) else set()
        except Exception:
            MEOW_CHATS[user_id] = set()
        try:
            sc = settings.get("sender_config") or {}
            # keys must be str for JSON
            SENDER_CONFIG[user_id] = {str(k): v for k, v in sc.items()} if isinstance(sc, dict) else {}
        except Exception:
            SENDER_CONFIG[user_id] = {}
        if user_id not in ROTATING_MUSIC_INDEX:
            ROTATING_MUSIC_INDEX[user_id] = 0
        TTS_VOICE_STATUS[user_id] = settings.get("tts_voice", "زن")
        FIRST_COMMENT_STATUS[user_id] = bool(settings.get("first_comment", False))
        FIRST_COMMENT_TEXT[user_id] = settings.get("first_comment_text", "🔥") or "🔥"
        ACTIVE_ENEMIES[user_id] = set(tuple(item) for item in user_data.get("enemies", []))
        try:
            ACTIVE_FRIENDS[user_id] = set(tuple(item) for item in user_data.get("friends", []))
        except Exception:
            ACTIVE_FRIENDS[user_id] = set()
        MUTED_USERS[user_id] = set(tuple(item) for item in user_data.get("muted", []))
        AUTO_REACTION_TARGETS[user_id] = user_data.get("reactions", {}) or {}
        USERS_REPLIED_IN_SECRETARY[user_id] = set(user_data.get("replied_users", []))
        ENEMY_REPLY_QUEUES[user_id] = user_data.get("enemy_queue", []) or []
        ORIGINAL_PROFILE_DATA[user_id] = user_data.get("original_profile", {}) or {}
    except Exception as e:
        logging.error(f"apply_user_settings_from_db({user_id}): {e}")


def persist_all_user_settings(user_id: int):
    try:
        settings = {
            "font": USER_FONT_CHOICES.get(user_id, "bold"),
            "clock": CLOCK_STATUS.get(user_id, True),
            "photo_clock": bool(PROFILE_PHOTO_CLOCK.get(user_id, False)),
            "photo_clock_color": PROFILE_PHOTO_CLOCK_COLOR.get(user_id) or "cyan",
            "photo_clock_style": PROFILE_PHOTO_CLOCK_STYLE.get(user_id) or "neon",
            "bio_miladi": bool(BIO_MILADI_DATE.get(user_id, False)),
            "bio_miladi_original": BIO_MILADI_ORIGINAL.get(user_id, ""),
            "bold": BOLD_MODE_STATUS.get(user_id, False),
            "text_font": TEXT_FONT_STATUS.get(user_id, "none"),
            "self_status": bool(SELF_STATUS.get(user_id, True)),
            "secretary": SECRETARY_MODE_STATUS.get(user_id, False),
            "secretary_msg": SECRETARY_CUSTOM_MESSAGES.get(user_id, "") or "",
            "auto_seen": AUTO_SEEN_STATUS.get(user_id, False),
            "pv_lock": PV_LOCK_STATUS.get(user_id, False),
            "pv_filter_sticker": bool(PV_FILTER_STICKER.get(user_id, False)),
            "pv_filter_gif": bool(PV_FILTER_GIF.get(user_id, False)),
            "anti_login": ANTI_LOGIN_STATUS.get(user_id, False),
            "typing": TYPING_MODE_STATUS.get(user_id, False),
            "playing": PLAYING_MODE_STATUS.get(user_id, False),
            "action": ACTION_STATUS.get(user_id),
            "global_enemy": GLOBAL_ENEMY_STATUS.get(user_id, False),
            "copy_mode": COPY_MODE_STATUS.get(user_id, False),
            "translate": AUTO_TRANSLATE_TARGET.get(user_id),
            "profile_snoops": PROFILE_SNOOPS.get(user_id) or {},
            "premium_emojis": PREMIUM_EMOJI_MAP.get(user_id) or {},
            "emoji_premium_convert": bool(EMOJI_PREMIUM_CONVERT.get(user_id, False)),
            "pemoji_map": PEMOJI_MAP.get(user_id) or {},
            "pemoji_status": bool(PEMOJI_STATUS.get(user_id, False)),
            "emoji_char_map": EMOJI_CHAR_TO_PREMIUM.get(user_id) or {},
            "force_join_pv": FORCE_JOIN_PV_STATUS.get(user_id, False),
            "force_join_channels": list(FORCE_JOIN_CHANNELS.get(user_id) or []),
            "starzy_photo": STARZY_PHOTO.get(user_id) or {},
            "edit_alert": EDIT_ALERT_STATUS.get(user_id, False),
            "delete_alert": DELETE_ALERT_STATUS.get(user_id, False),
            "rotating_names": list(ROTATING_NAMES.get(user_id) or []),
            "rotating_interval": int(ROTATING_NAME_INTERVAL.get(user_id) or 10),
            "rotating_name": bool(ROTATING_NAME_STATUS.get(user_id, False)),
            "rotating_music": list(ROTATING_MUSIC.get(user_id) or []),
            "rotating_music_interval": int(ROTATING_MUSIC_INTERVAL.get(user_id) or 1),
            "rotating_music_on": bool(ROTATING_MUSIC_STATUS.get(user_id, False)),
            "meow_chats": list(MEOW_CHATS.get(user_id) or []),
            "sender_config": SENDER_CONFIG.get(user_id) or {},
            "first_comment": bool(FIRST_COMMENT_STATUS.get(user_id, False)),
            "first_comment_text": FIRST_COMMENT_TEXT.get(user_id, "🔥") or "🔥",
            "tts_voice": TTS_VOICE_STATUS.get(user_id, "زن"),
        }
        data_manager.update_user_data(user_id, {"settings": settings})
        if user_id in ACTIVE_ENEMIES:
            data_manager.save_enemies(user_id, ACTIVE_ENEMIES[user_id])
        if user_id in MUTED_USERS:
            data_manager.save_muted(user_id, MUTED_USERS[user_id])
    except Exception as e:
        logging.error(f"persist_all_user_settings({user_id}): {e}")


load_all_states()


def stylize_time(time_str: str, style: str) -> str:
    font_map = CLOCK_FONT_STYLES.get(style) or CLOCK_FONT_STYLES.get("bold") or {}
    return "".join(font_map.get(ch, ch) for ch in time_str)


async def perform_clock_update_now(client, user_id):
    try:
        if not is_self_on(user_id):
            return
        # فقط FLOOD اسم — مستقل از ساعت عکس پروفایل
        until = max(PROFILE_NAME_FLOOD_UNTIL.get(user_id, 0), 0)
        if time.time() < until:
            return
        if CLOCK_STATUS.get(user_id, True) and not COPY_MODE_STATUS.get(user_id, False):
            current_font_style = USER_FONT_CHOICES.get(user_id, 'bold')
            me = await client.get_me()
            current_name = me.first_name or ""
            base_name = re.sub(r'(?:\s*' + CLOCK_CHARS_REGEX_CLASS + r'+)+$', '', current_name).strip()
            # اگر اسم چرخشی فعال است، پایه از لیست باشد
            if ROTATING_NAME_STATUS.get(user_id) and (ROTATING_NAMES.get(user_id) or []):
                idx = ROTATING_NAME_INDEX.get(user_id, 0) % len(ROTATING_NAMES[user_id])
                base_name = str(ROTATING_NAMES[user_id][idx])[:40]
            tehran_time = datetime.now(TEHRAN_TIMEZONE)
            current_time_str = tehran_time.strftime("%H:%M")
            stylized_time = stylize_time(current_time_str, current_font_style)
            new_name = f"{base_name} {stylized_time}".strip()[:64]
            if new_name != current_name:
                await client.update_profile(first_name=new_name)
    except Exception as e:
        err = str(e)
        logging.error(f"Immediate clock update failed: {e}")
        if "FLOOD_WAIT" in err:
            m = re.search(r"(\d+)", err)
            sec = int(m.group(1)) if m else 300
            PROFILE_NAME_FLOOD_UNTIL[user_id] = time.time() + sec + 5
            PROFILE_FLOOD_UNTIL[user_id] = PROFILE_NAME_FLOOD_UNTIL[user_id]


async def rotate_profile_name_task(client: Client, user_id: int):
    await asyncio.sleep(3)
    while True:
        try:
            
            if not is_self_on(user_id):
                await asyncio.sleep(3)
                continue
            if user_id not in ACTIVE_BOTS:
                break
            if not ROTATING_NAME_STATUS.get(user_id, False):
                await asyncio.sleep(2)
                continue
            names = ROTATING_NAMES.get(user_id) or []
            if len(names) < 1:
                await asyncio.sleep(3)
                continue
            until = PROFILE_NAME_FLOOD_UNTIL.get(user_id, 0)
            if time.time() < until:
                await asyncio.sleep(min(30, max(1, until - time.time() + 1)))
                continue
            interval = max(3, int(ROTATING_NAME_INTERVAL.get(user_id) or 10))
            idx = ROTATING_NAME_INDEX.get(user_id, 0) % len(names)
            name = str(names[idx])[:64]
            try:
                if CLOCK_STATUS.get(user_id, False):
                    now = datetime.now(TEHRAN_TIMEZONE).strftime("%H:%M")
                    font = USER_FONT_CHOICES.get(user_id, "bold")
                    styled = stylize_time(now, font)
                    await client.update_profile(first_name=f"{name} {styled}".strip()[:64])
                else:
                    await client.update_profile(first_name=name)
            except Exception as e:
                logging.warning(f"rotate name update {user_id}: {e}")
                if "FLOOD_WAIT" in str(e):
                    m = re.search(r"(\d+)", str(e))
                    sec = int(m.group(1)) if m else 300
                    PROFILE_NAME_FLOOD_UNTIL[user_id] = time.time() + sec + 5
            ROTATING_NAME_INDEX[user_id] = (idx + 1) % len(names)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"rotate_profile_name_task: {e}")
            await asyncio.sleep(5)



async def _fetch_profile_music_tracks(client: Client) -> list:
    """خواندن آهنگ‌های پروفایل با چند روش API + پیام‌های ذخیره‌شده"""
    tracks = []
    seen_ids = set()

    def _add_doc(doc, title_fallback="آهنگ"):
        try:
            if not doc:
                return
            doc_id = getattr(doc, "id", None)
            if doc_id is None or int(doc_id) in seen_ids:
                return
            access_hash = getattr(doc, "access_hash", None)
            file_reference = getattr(doc, "file_reference", b"") or b""
            title = title_fallback
            for attr in (getattr(doc, "attributes", None) or []):
                t = getattr(attr, "title", None) or getattr(attr, "file_name", None)
                if t:
                    title = str(t)[:64]
                    break
            seen_ids.add(int(doc_id))
            tracks.append({
                "title": title,
                "doc_id": int(doc_id),
                "access_hash": int(access_hash) if access_hash is not None else 0,
                "file_reference": file_reference if isinstance(file_reference, (bytes, bytearray)) else b"",
                "from_profile": True,
            })
        except Exception as e:
            logging.warning(f"_add_doc: {e}")

    try:
        from pyrogram.raw import types as raw_types
        me = await client.get_me()
        peers = []
        try:
            peers.append(raw_types.InputUserSelf())
        except Exception:
            pass
        try:
            peers.append(await client.resolve_peer("me"))
        except Exception:
            pass
        try:
            peers.append(await client.resolve_peer(me.id))
        except Exception:
            pass

        # روش ۱: account.GetSavedMusic
        for peer in peers:
            try:
                if not hasattr(functions.account, "GetSavedMusic"):
                    break
                result = await client.invoke(
                    functions.account.GetSavedMusic(id=peer, offset=0, limit=50)
                )
                docs = getattr(result, "documents", None) or []
                logging.info(f"GetSavedMusic docs={len(docs)} peer={type(peer).__name__}")
                for i, doc in enumerate(docs):
                    _add_doc(doc, f"آهنگ {i+1}")
                if tracks:
                    break
            except Exception as e:
                logging.warning(f"GetSavedMusic: {e}")

        # روش ۲: GetFullUser — فیلدهای music / saved_music
        if len(tracks) < 2:
            try:
                full = await client.invoke(functions.users.GetFullUser(id=raw_types.InputUserSelf()))
                full_user = getattr(full, "full_user", None) or full
                for attr_name in ("saved_music", "music", "profile_music", "songs"):
                    val = getattr(full_user, attr_name, None)
                    if not val:
                        continue
                    docs = getattr(val, "documents", None) or (val if isinstance(val, list) else [])
                    for i, doc in enumerate(docs):
                        _add_doc(doc, f"آهنگ {i+1}")
                # گاهی documents در خود full هست
                for doc in (getattr(full, "documents", None) or []):
                    _add_doc(doc)
            except Exception as e:
                logging.warning(f"GetFullUser music: {e}")

        # روش ۳: جستجو در Saved Messages برای پیام‌های صوتی اخیر (fallback)
        if len(tracks) < 2:
            try:
                async for msg in client.get_chat_history("me", limit=80):
                    if not msg or not msg.audio:
                        continue
                    try:
                        from pyrogram.file_id import FileId
                        fid = FileId.decode(msg.audio.file_id)
                        doc_id = fid.media_id
                        if doc_id in seen_ids:
                            continue
                        seen_ids.add(doc_id)
                        title = msg.audio.title or msg.audio.file_name or f"آهنگ {len(tracks)+1}"
                        tracks.append({
                            "title": str(title)[:64],
                            "doc_id": int(doc_id),
                            "access_hash": int(fid.access_hash),
                            "file_reference": fid.file_reference or b"",
                            "file_id": msg.audio.file_id,
                            "chat_id": msg.chat.id,
                            "msg_id": msg.id,
                            "from_profile": False,
                        })
                    except Exception:
                        continue
                    if len(tracks) >= 10:
                        break
            except Exception as e:
                logging.warning(f"saved messages music scan: {e}")

        logging.info(f"profile music fetched total={len(tracks)}")
    except Exception as e:
        logging.warning(f"_fetch_profile_music_tracks: {e}")
    return tracks


async def _apply_profile_music(client: Client, user_id: int, track: dict) -> bool:
    """اعمال آهنگ روی موزیک پروفایل — با رفرش file_reference و دانلود مجدد"""
    if not track or not isinstance(track, dict):
        return False
    title = track.get("title") or "آهنگ"
    local_path = track.get("path") or ""
    file_id = track.get("file_id") or ""
    src_chat = track.get("chat_id")
    src_msg = track.get("msg_id")

    try:
        from pyrogram.raw import types as raw_types
        from pyrogram.file_id import FileId
    except Exception as e:
        logging.warning(f"import for music: {e}")
        return False

    if not hasattr(functions.account, "SaveMusic"):
        logging.warning("account.SaveMusic در این نسخه pyrogram موجود نیست")
        return False

    async def _make_input_from_audio_msg(msg) -> object:
        if not msg or not msg.audio:
            return None
        try:
            fid = FileId.decode(msg.audio.file_id)
            return raw_types.InputDocument(
                id=fid.media_id,
                access_hash=fid.access_hash,
                file_reference=fid.file_reference or b"",
            )
        except Exception as e:
            logging.warning(f"decode audio msg: {e}")
            return None

    async def _upload_path(path: str):
        if not path or not os.path.exists(path):
            return None, None
        try:
            # send as audio برای متادیتای موزیک
            temp = await client.send_audio("me", path, title=title[:64])
            await asyncio.sleep(0.4)
            return temp, await _make_input_from_audio_msg(temp)
        except Exception:
            try:
                temp = await client.send_document("me", path)
                await asyncio.sleep(0.4)
                if temp and temp.document:
                    fid = FileId.decode(temp.document.file_id)
                    inp = raw_types.InputDocument(
                        id=fid.media_id,
                        access_hash=fid.access_hash,
                        file_reference=fid.file_reference or b"",
                    )
                    return temp, inp
            except Exception as e:
                logging.warning(f"upload path failed: {e}")
        return None, None

    temp_msg = None
    input_doc = None
    try:
        # 0) آهنگ از خود پروفایل (doc_id)
        if track.get("from_profile") and track.get("doc_id"):
            try:
                from pyrogram.raw import types as raw_types
                fr = track.get("file_reference") or b""
                if isinstance(fr, str):
                    try:
                        fr = bytes.fromhex(fr)
                    except Exception:
                        fr = b""
                input_doc = raw_types.InputDocument(
                    id=int(track["doc_id"]),
                    access_hash=int(track.get("access_hash") or 0),
                    file_reference=fr if isinstance(fr, (bytes, bytearray)) else b"",
                )
            except Exception as e:
                logging.warning(f"profile track input: {e}")

        # 1) رفرش از پیام اصلی (بهترین روش)
        if input_doc is None and src_chat and src_msg:
            try:
                orig = await client.get_messages(int(src_chat), int(src_msg))
                if orig and (orig.audio or orig.document):
                    # دانلود تازه برای آپلود معتبر
                    music_dir = os.path.join(DOWNLOAD_PATH, "music", str(user_id))
                    os.makedirs(music_dir, exist_ok=True)
                    dl = await client.download_media(
                        orig,
                        file_name=os.path.join(music_dir, f"rot_{int(time.time())}_{random.randint(100,999)}")
                    )
                    if dl and os.path.exists(dl):
                        local_path = dl
                        track["path"] = dl
                        if orig.audio:
                            track["file_id"] = orig.audio.file_id
                        elif orig.document:
                            track["file_id"] = orig.document.file_id
                        file_id = track.get("file_id") or file_id
            except Exception as e:
                logging.warning(f"refresh orig msg music: {e}")

        # 2) فایل محلی
        if local_path and os.path.exists(local_path):
            temp_msg, input_doc = await _upload_path(local_path)

        # 3) دانلود از file_id ذخیره‌شده
        if input_doc is None and file_id:
            try:
                music_dir = os.path.join(DOWNLOAD_PATH, "music", str(user_id))
                os.makedirs(music_dir, exist_ok=True)
                dl = await client.download_media(
                    file_id,
                    file_name=os.path.join(music_dir, f"fid_{int(time.time())}_{random.randint(100,999)}")
                )
                if dl and os.path.exists(dl):
                    local_path = dl
                    track["path"] = dl
                    temp_msg, input_doc = await _upload_path(dl)
            except Exception as e:
                logging.warning(f"download by file_id music: {e}")

        if input_doc is None:
            logging.warning(f"no input_doc for music uid={user_id} title={title}")
            return False

        ok = False
        try:
            await client.invoke(functions.account.SaveMusic(id=input_doc, unsave=False))
            ok = True
            logging.info(f"🎵 profile music set uid={user_id} title={title}")
        except Exception as e:
            err = str(e)
            logging.warning(f"SaveMusic failed uid={user_id}: {err}")
            # FILE_REFERENCE منقضی → یک بار دیگر از path
            if local_path and os.path.exists(local_path):
                try:
                    if temp_msg:
                        try:
                            await temp_msg.delete()
                        except Exception:
                            pass
                    temp_msg, input_doc = await _upload_path(local_path)
                    if input_doc:
                        await client.invoke(functions.account.SaveMusic(id=input_doc, unsave=False))
                        ok = True
                        logging.info(f"🎵 profile music retry ok uid={user_id}")
                except Exception as e2:
                    logging.warning(f"SaveMusic retry failed: {e2}")

        # آپدیت لیست در حافظه (path تازه)
        try:
            lst = ROTATING_MUSIC.get(user_id) or []
            for i, t in enumerate(lst):
                if t is track or (t.get("title") == title and t.get("file_id") == file_id):
                    lst[i] = track
                    break
            ROTATING_MUSIC[user_id] = lst
            try:
                persist_all_user_settings(user_id)
            except Exception:
                pass
        except Exception:
            pass

        if temp_msg is not None:
            try:
                await temp_msg.delete()
            except Exception:
                try:
                    await client.delete_messages("me", temp_msg.id)
                except Exception:
                    pass
        return ok
    except Exception as e:
        logging.warning(f"_apply_profile_music: {e}")
        return False


async def rotate_profile_music_task(client: Client, user_id: int):
    """چرخش بین آهنگ‌های روی پروفایل کاربر (بدون آپلود مکرر)"""
    while user_id in ACTIVE_BOTS:
        try:
            if not ROTATING_MUSIC_STATUS.get(user_id, False):
                await asyncio.sleep(30)
                continue
            tracks = ROTATING_MUSIC.get(user_id) or []
            # همیشه از پروفایل رفرش کن تا لیست تازه باشد
            try:
                fetched = await _fetch_profile_music_tracks(client)
                if fetched:
                    ROTATING_MUSIC[user_id] = fetched
                    tracks = fetched
                    try:
                        persist_all_user_settings(user_id)
                    except Exception:
                        pass
            except Exception as e:
                logging.warning(f"refresh profile music: {e}")
            if len(tracks) < 1:
                await asyncio.sleep(60)
                continue
            hours = max(1, min(24, int(ROTATING_MUSIC_INTERVAL.get(user_id) or 1)))
            idx = ROTATING_MUSIC_INDEX.get(user_id, 0) % len(tracks)
            track = tracks[idx]
            ok = await _apply_profile_music(client, user_id, track)
            if ok:
                ROTATING_MUSIC_INDEX[user_id] = (idx + 1) % len(tracks)
                logging.info(f"🎵 rotated profile music uid={user_id} -> {track.get('title')}")
            # فاصله بر حسب ساعت — برای ضد بن تلگرام حداقل ۱ ساعت
            await asyncio.sleep(hours * 3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"rotate_profile_music_task: {e}")
            await asyncio.sleep(120)




async def build_profile_clock_image(client, user_id: int) -> str:
    """ساعت نئون روی عکس پروفایل کاربر — مثل نمونه سایبر/نئون با رنگ قابل‌تنظیم"""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import math
    size = 640
    path_out = f"/tmp/profile_clock_{user_id}.png"

    base = None
    base_path = PROFILE_PHOTO_CLOCK_BASE.get(user_id) or profile_clock_base_path(user_id)
    try:
        if base_path and os.path.exists(base_path):
            base = Image.open(base_path).convert("RGBA").resize((size, size), Image.LANCZOS)
            PROFILE_PHOTO_CLOCK_BASE[user_id] = base_path
    except Exception:
        base = None
    if base is None:
        try:
            photos = []
            async for p in client.get_chat_photos("me", limit=1):
                photos.append(p)
            if photos:
                dest = profile_clock_base_path(user_id)
                dl = await client.download_media(photos[0], file_name=dest)
                if dl and os.path.exists(dl):
                    PROFILE_PHOTO_CLOCK_BASE[user_id] = dl
                    base = Image.open(dl).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception as e:
            logging.warning(f"profile photo dl: {e}")
    if base is None:
        base = Image.new("RGBA", (size, size), (15, 15, 20, 255))

    # رنگ نئون
    color_key = PROFILE_PHOTO_CLOCK_COLOR.get(user_id) or "cyan"
    rgb = PHOTO_CLOCK_COLORS.get(color_key) or PHOTO_CLOCK_COLORS["cyan"]
    neon = (*rgb, 255)
    neon_dim = (*rgb, 160)
    neon_soft = (*rgb, 90)

    # بکگراند تیره
    canvas = Image.new("RGBA", (size, size), (8, 10, 14, 255))
    cx = cy = size / 2.0
    photo_r = size * 0.38
    outer_r = size * 0.48
    ring_r = size * 0.44

    # عکس کاربر دایره‌ای در مرکز
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([cx - photo_r, cy - photo_r, cx + photo_r, cy + photo_r], fill=255)
    # کمی تاریک‌کردن لبه عکس برای حس نئون
    photo = base.copy()
    dark = Image.new("RGBA", (size, size), (0, 0, 0, 60))
    photo = Image.alpha_composite(photo, dark)
    circ = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circ.paste(photo, (0, 0), mask=mask)
    canvas.paste(circ, (0, 0), circ)

    # لایه درخشش (glow) برای قاب
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for w, a in ((28, 25), (18, 45), (10, 70)):
        col = (*rgb, a)
        gd.ellipse([cx - outer_r - 2, cy - outer_r - 2, cx + outer_r + 2, cy + outer_r + 2], outline=col, width=w)
    try:
        glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    except Exception:
        pass
    canvas = Image.alpha_composite(canvas, glow)

    draw = ImageDraw.Draw(canvas)

    # قاب کنگره‌دار / دندانه مثل نمونه
    import math as _m
    teeth = 48
    pts_outer = []
    pts_inner = []
    for i in range(teeth):
        ang = _m.radians(i * (360 / teeth) - 90)
        # دندانه‌دار
        r_o = outer_r + (4 if i % 2 == 0 else 0)
        r_i = ring_r - 6
        pts_outer.append((cx + r_o * _m.cos(ang), cy + r_o * _m.sin(ang)))
        pts_inner.append((cx + r_i * _m.cos(ang), cy + r_i * _m.sin(ang)))
    # حلقه بیرونی نئون
    draw.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r], outline=neon, width=5)
    draw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=neon_dim, width=3)
    # قوس پیشرفت (مثل درصد در نمونه) — بر اساس دقیقه
    tehran_time = datetime.now(TEHRAN_TIMEZONE)
    current_time_str = tehran_time.strftime("%H:%M")
    h12 = tehran_time.hour % 12
    m = tehran_time.minute
    s = tehran_time.second
    # قوس از ۱۲ تا موقعیت دقیقه
    arc_end = -90 + (m / 60.0) * 360
    bbox = [cx - outer_r - 6, cy - outer_r - 6, cx + outer_r + 6, cy + outer_r + 6]
    try:
        draw.arc(bbox, start=-90, end=arc_end, fill=neon, width=8)
    except Exception:
        pass

    # ۱۲ عدد ساعت دور قاب
    try:
        font_n = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_t = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except Exception:
        font_n = ImageFont.load_default()
        font_t = font_n
    for i in range(12):
        num = i if i != 0 else 12
        ang = _m.radians(i * 30 - 90)
        nr = outer_r - 28
        tx = cx + nr * _m.cos(ang)
        ty = cy + nr * _m.sin(ang)
        label = str(num)
        try:
            bbox_t = draw.textbbox((0, 0), label, font=font_n)
            tw, th = bbox_t[2] - bbox_t[0], bbox_t[3] - bbox_t[1]
        except Exception:
            tw, th = 12, 12
        draw.text((tx - tw / 2, ty - th / 2), label, fill=neon, font=font_n)

    hour_angle = (h12 * 30) + (m * 0.5)
    minute_angle = m * 6

    def hand(angle_deg, length, width, color, tip=True):
        ang = _m.radians(angle_deg - 90)
        x2 = cx + length * _m.cos(ang)
        y2 = cy + length * _m.sin(ang)
        # سایه/درخشش
        draw.line([(cx, cy), (x2, y2)], fill=(*rgb, 80), width=width + 6)
        draw.line([(cx, cy), (x2, y2)], fill=color, width=width)
        if tip:
            draw.ellipse([x2 - 5, y2 - 5, x2 + 5, y2 + 5], fill=color)

    # عقربه ساعت (کوتاه) و دقیقه (بلند) — نئون
    hand(hour_angle, photo_r * 0.55, 7, neon)
    hand(minute_angle, photo_r * 0.85, 4, neon)
    # مرکز
    draw.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(20, 25, 30, 255), outline=neon, width=3)
    draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=neon)

    # متن دیجیتال HH:MM گوشه
    try:
        draw.text((18, 16), current_time_str, fill=neon, font=font_t)
    except Exception:
        pass

    canvas.convert("RGB").save(path_out, "PNG", quality=95)
    return path_out




async def replace_clock_profile_photo(client: Client, user_id: int, path: str) -> bool:
    """عکس ساعت جدید؛ بعد فقط file_idهای ساعتِ قبلی پاک می‌شوند (گالری کاربر دست نخورده)"""
    if not path or not os.path.exists(path):
        return False
    ids = PROFILE_PHOTO_CLOCK_IDS.setdefault(user_id, set())
    prev_id = PROFILE_PHOTO_CLOCK_LAST.get(user_id)

    await client.set_profile_photo(photo=path)
    mark_profile_photo_uploaded(user_id)
    await asyncio.sleep(1.2)

    new_id = None
    new_photo = None
    try:
        async for p in client.get_chat_photos("me", limit=1):
            new_photo = p
            new_id = getattr(p, "file_id", None)
            break
    except Exception:
        pass

    if new_id:
        PROFILE_PHOTO_CLOCK_LAST[user_id] = new_id
        ids.add(new_id)

    # پاک کردن همه عکس‌های ساعت قبلی (نه عکس جدید)
    for fid in list(ids):
        if not fid or fid == new_id:
            continue
        try:
            await client.delete_profile_photos(fid)
            ids.discard(fid)
            await asyncio.sleep(0.4)
        except Exception:
            try:
                await client.delete_profile_photos([fid])
                ids.discard(fid)
            except Exception as e:
                logging.warning("delete clock photo fail: %s", e)

    # اگر prev هنوز در لیست پروفایل است و حذف نشده
    if prev_id and prev_id != new_id and prev_id in ids:
        try:
            await client.delete_profile_photos(prev_id)
            ids.discard(prev_id)
        except Exception:
            pass

    PROFILE_PHOTO_CLOCK_IDS[user_id] = {new_id} if new_id else set()
    logging.info("replace_clock_photo uid=%s new=%s", user_id, (new_id or "")[:16])
    return True


async def restore_profile_photo_from_base(client: Client, user_id: int):
    """خاموش: عکس اصلی برگردد؛ فقط عکس‌های ساعت (که خودمان گذاشتیم) پاک شوند"""
    try:
        bp = PROFILE_PHOTO_CLOCK_BASE.get(user_id) or profile_clock_base_path(user_id)
        if not (bp and os.path.exists(bp)):
            logging.warning("restore: no base photo uid=%s", user_id)
            return False

        ids = set(PROFILE_PHOTO_CLOCK_IDS.get(user_id) or set())
        prev = PROFILE_PHOTO_CLOCK_LAST.get(user_id)
        if prev:
            ids.add(prev)

        await client.set_profile_photo(photo=bp)
        mark_profile_photo_uploaded(user_id)
        await asyncio.sleep(1.0)

        new_id = None
        try:
            async for p in client.get_chat_photos("me", limit=1):
                new_id = getattr(p, "file_id", None)
                break
        except Exception:
            pass

        # فقط file_idهایی که به‌عنوان ساعت ثبت شده بودند
        for fid in list(ids):
            if not fid or fid == new_id:
                continue
            try:
                await client.delete_profile_photos(fid)
                await asyncio.sleep(0.3)
            except Exception:
                try:
                    await client.delete_profile_photos([fid])
                except Exception as e:
                    logging.warning("restore delete clock id: %s", e)

        PROFILE_PHOTO_CLOCK_LAST.pop(user_id, None)
        PROFILE_PHOTO_CLOCK_IDS[user_id] = set()
        logging.info("profile clock restored base uid=%s deleted_clock_ids=%s", user_id, len(ids))
        return True
    except Exception as e:
        logging.warning(f"restore profile base: {e}")
    return False


async def update_profile_photo_clock_task(client: Client, user_id: int):
    """هر دقیقه قاب ساعت را از روی عکس پایه ثابت می‌سازد — پایه را وسط کار عوض نمی‌کند"""
    await asyncio.sleep(4)
    while user_id in ACTIVE_BOTS:
        try:
            if not is_self_on(user_id):
                await asyncio.sleep(5)
                continue
            if not PROFILE_PHOTO_CLOCK.get(user_id, False):
                await asyncio.sleep(5)
                continue
            until = PROFILE_PHOTO_FLOOD_UNTIL.get(user_id, 0)
            if time.time() < until:
                await asyncio.sleep(min(120, max(5, until - time.time())))
                continue

            tehran_time = datetime.now(TEHRAN_TIMEZONE)
            minute_key = tehran_time.strftime("%H:%M")
            if PROFILE_PHOTO_CLOCK_LAST_MINUTE.get(user_id) == minute_key:
                wait = 60 - tehran_time.second + 0.3
                await asyncio.sleep(max(5, wait))
                continue

            # پایه فقط یک‌بار — هرگز از عکس فعلیِ ساعت‌دار دوباره نگیر
            bp = PROFILE_PHOTO_CLOCK_BASE.get(user_id) or profile_clock_base_path(user_id)
            if not bp or not os.path.exists(bp):
                try:
                    photos = []
                    async for p in client.get_chat_photos("me", limit=1):
                        photos.append(p)
                    if photos:
                        dest = profile_clock_base_path(user_id)
                        dl = await client.download_media(photos[0], file_name=dest)
                        if dl:
                            PROFILE_PHOTO_CLOCK_BASE[user_id] = dl
                except Exception as e:
                    logging.warning(f"save base profile photo: {e}")

            ok_up, left = can_upload_profile_photo(user_id)
            if not ok_up:
                logging.info("profile clock skip uid=%s wait=%s", user_id, left)
                await asyncio.sleep(min(300, max(30, left)))
                continue

            path = await build_profile_clock_image(client, user_id)
            if path and os.path.exists(path):
                try:
                    await replace_clock_profile_photo(client, user_id, path)
                    PROFILE_PHOTO_CLOCK_LAST_MINUTE[user_id] = minute_key
                    logging.info(f"profile photo clock uid={user_id} time={minute_key}")
                except Exception as e:
                    err = str(e)
                    logging.warning(f"set_profile_photo clock: {e}")
                    if "FLOOD_WAIT" in err:
                        m = re.search(r"(\d+)", err)
                        sec = int(m.group(1)) if m else 600
                        PROFILE_PHOTO_FLOOD_UNTIL[user_id] = time.time() + sec + 10
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass

            # هر ۱ دقیقه (وقت تهران)
            now = datetime.now(TEHRAN_TIMEZONE)
            wait = 60 - now.second + 0.3
            await asyncio.sleep(max(5, wait))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"update_profile_photo_clock_task: {e}")
            await asyncio.sleep(30)


async def update_profile_clock(client: Client, user_id: int):
    """ساعت اسم — هر دقیقه وقت تهران؛ مستقل از ساعت عکس پروفایل"""
    await asyncio.sleep(2)
    # یک‌بار فوری بعد از استارت
    try:
        if is_self_on(user_id) and CLOCK_STATUS.get(user_id, True) and not COPY_MODE_STATUS.get(user_id, False):
            await perform_clock_update_now(client, user_id)
    except Exception as e:
        logging.warning(f"initial name clock: {e}")
    while user_id in ACTIVE_BOTS:
        try:
            if is_self_on(user_id) and CLOCK_STATUS.get(user_id, True) and not COPY_MODE_STATUS.get(user_id, False):
                await perform_clock_update_now(client, user_id)
            wait = 60 - datetime.now(TEHRAN_TIMEZONE).second + 0.15
            await asyncio.sleep(max(5, wait))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"update_profile_clock loop {user_id}: {e}")
            await asyncio.sleep(20)



ACTION_MAP = {
    "type": ChatAction.TYPING,
    "voice": ChatAction.RECORD_AUDIO,
    "round": ChatAction.RECORD_VIDEO_NOTE,
    "photo": ChatAction.UPLOAD_PHOTO,
    "video": ChatAction.UPLOAD_VIDEO,
    "doc": ChatAction.UPLOAD_DOCUMENT,
    "sticker": ChatAction.CHOOSE_STICKER,
    "game": ChatAction.PLAYING,
    "online": ChatAction.CANCEL,
}
ACTION_LABELS = {
    "type": "در حال نوشتن",
    "voice": "در حال ضبط صدا",
    "round": "ویدیو مسیج",
    "photo": "ارسال عکس",
    "video": "ارسال ویدیو",
    "doc": "ارسال فایل",
    "sticker": "انتخاب استیکر",
    "game": "بازی",
    "online": "آنلاین",
}


# =============================================
# ترجمه خودکار (ساده و پایدار — مثل نسخه ۳ زبانه)
# =============================================
_TRANSLATE_LAST = 0.0



def track_profile_snoop(owner_id: int, user) -> None:
    """ثبت تعامل پیوی به‌عنوان فضول پروفایل"""
    try:
        if not user or not getattr(user, "id", None):
            return
        vid = int(user.id)
        if vid == int(owner_id):
            return
        if getattr(user, "is_bot", False):
            return
        bucket = PROFILE_SNOOPS.get(owner_id) or {}
        first = getattr(user, "first_name", None) or ""
        last = getattr(user, "last_name", None) or ""
        name = (first + (" " + last if last else "")).strip() or str(vid)
        uname = getattr(user, "username", None) or ""
        prev = bucket.get(vid) or {}
        bucket[vid] = {
            "name": name,
            "username": uname,
            "count": int(prev.get("count") or 0) + 1,
            "last": int(time.time()),
        }
        if len(bucket) > 200:
            ordered = sorted(bucket.items(), key=lambda x: int((x[1] or {}).get("last") or 0))
            for k, _ in ordered[: max(0, len(bucket) - 200)]:
                bucket.pop(k, None)
        PROFILE_SNOOPS[owner_id] = bucket
        try:
            persist_all_user_settings(owner_id)
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"track_profile_snoop: {e}")


def format_profile_snoops(owner_id: int) -> str:
    bucket = PROFILE_SNOOPS.get(owner_id) or {}
    if not bucket:
        return (
            "👁 **فضول پروفایل | self MR**\n\n"
            "هنوز کسی ثبت نشده.\n"
            "کسانی که پروفایل شما را دیده‌اند اینجا لیست می‌شوند."
        )
    items = sorted(
        bucket.items(),
        key=lambda x: int((x[1] or {}).get("last") or 0),
        reverse=True,
    )
    lines = [
        "👁 **فضول پروفایل | self MR**\n",
        f"👥 تعداد: `{len(items)}`\n",
    ]
    for i, (vid, info) in enumerate(items[:50], 1):
        info = info or {}
        name = info.get("name") or str(vid)
        uname = info.get("username") or ""
        cnt = info.get("count") or 1
        last = info.get("last") or 0
        try:
            tstr = datetime.fromtimestamp(int(last), TEHRAN_TIMEZONE).strftime("%m/%d %H:%M")
        except Exception:
            tstr = "-"
        u = f"@{uname}" if uname else "—"
        lines.append(
            f"{i}. **{name}** | {u}\n   🆔 `{vid}` | 🔁 {cnt} | ⏰ {tstr}"
        )
    if len(items) > 50:
        lines.append(f"\n… و `{len(items) - 50}` نفر دیگر")
    return "\n".join(lines)



async def text_to_qr_image(text: str, out_path: str) -> bool:
    """ساخت تصویر QR از متن"""
    text = (text or "").strip()
    if not text:
        return False
    try:
        url = "https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=" + quote(text[:1500])
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                data = await resp.read()
                with open(out_path, "wb") as f:
                    f.write(data)
                return os.path.exists(out_path) and os.path.getsize(out_path) > 50
    except Exception as e:
        logging.warning(f"text_to_qr: {e}")
        return False


async def qr_image_to_text(image_path: str) -> str:
    """خواندن متن از تصویر QR"""
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        url = "https://api.qrserver.com/v1/read-qr-code/"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            with open(image_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("file", f, filename="qr.png", content_type="image/png")
                async with session.post(url, data=form) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json(content_type=None)
                    # [ { "symbol": [ { "data": "..." } ] } ]
                    if isinstance(data, list) and data:
                        sym = (data[0] or {}).get("symbol") or []
                        if sym and isinstance(sym, list):
                            val = (sym[0] or {}).get("data")
                            if val:
                                return str(val)
    except Exception as e:
        logging.warning(f"qr_to_text: {e}")
    return ""



def _user_premium_emojis(user_id: int) -> dict:
    """دیکشنری نام -> custom_emoji_id برای کاربر"""
    base = dict(DEFAULT_PREMIUM_EMOJIS)
    custom = PREMIUM_EMOJI_MAP.get(user_id) or {}
    for k, v in custom.items():
        try:
            base[str(k)] = int(v)
        except Exception:
            pass
    return base


def _extract_custom_emoji_ids(message) -> list:
    """استخراج custom_emoji_id از پیام (entities / caption_entities)"""
    ids = []
    try:
        for ent_src in (getattr(message, "entities", None) or [], getattr(message, "caption_entities", None) or []):
            for ent in ent_src or []:
                cid = getattr(ent, "custom_emoji_id", None)
                if cid:
                    ids.append(int(cid))
                # pyrogram enum type check
                t = str(getattr(ent, "type", "")).lower()
                if "custom" in t and cid:
                    ids.append(int(cid))
    except Exception as e:
        logging.warning(f"extract custom emoji: {e}")
    # unique preserve order
    out = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out


async def send_premium_emoji(client, chat_id, custom_emoji_id: int, placeholder: str = "⭐"):
    """ارسال یک ایموجی پریمیوم/کاستوم"""
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import MessageEntity
    text = placeholder
    # طول UTF-16
    length = len(text.encode("utf-16-le")) // 2
    entity = MessageEntity(
        type=MessageEntityType.CUSTOM_EMOJI,
        offset=0,
        length=length,
        custom_emoji_id=int(custom_emoji_id),
    )
    return await client.send_message(chat_id, text, entities=[entity])




async def custom_emoji_to_sticker_file_id(client, custom_emoji_id: int, user_id: int = 0):
    path = None
    try:
        from pyrogram.raw.functions.messages import GetCustomEmojiDocuments
        # ترجیح سشن پریمیوم سرور برای دانلود کاستوم
        dl_client = client
        try:
            pc = await ensure_premium_client()
            if pc:
                dl_client = pc
        except Exception:
            pass
        r = await dl_client.invoke(GetCustomEmojiDocuments(document_id=[int(custom_emoji_id)]))
        docs = getattr(r, "documents", None) or []
        if not docs:
            return None
        path = await client.download_media(docs[0], file_name=f"cem_{custom_emoji_id}_{user_id}")
        if not path:
            return None
        lower = path.lower()
        sent = None
        try:
            if lower.endswith((".tgs", ".webp", ".webm")):
                sent = await client.send_sticker("me", path)
            else:
                sent = await client.send_document("me", path)
        except Exception:
            sent = await client.send_document("me", path)
        if sent and getattr(sent, "sticker", None):
            return sent.sticker.file_id
        if sent and getattr(sent, "document", None):
            return sent.document.file_id
        return None
    except Exception as e:
        logging.warning("custom_emoji_to_sticker_file_id: %s", e)
        return None
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass



async def upload_custom_emoji_to_helper_bot(custom_emoji_id: int, user_id: int, normal_key: str = ""):
    """دانلود کاستوم و آپلود با توکن هلپر تا file_id برای اینلاین استیکر معتبر باشد"""
    path = None
    try:
        token = (HELPER_BOT_TOKEN if (HELPER_BOT_ENABLED and HELPER_BOT_TOKEN) else BOT_TOKEN) or BOT_TOKEN
        if not token:
            return None
        dl_client = None
        try:
            dl_client = await ensure_premium_client()
        except Exception:
            pass
        if not dl_client:
            return None
        from pyrogram.raw.functions.messages import GetCustomEmojiDocuments
        r = await dl_client.invoke(GetCustomEmojiDocuments(document_id=[int(custom_emoji_id)]))
        docs = getattr(r, "documents", None) or []
        if not docs:
            logging.warning("upload helper: no document cid=%s", custom_emoji_id)
            return None
        path = await dl_client.download_media(docs[0], file_name=f"pe_up_{custom_emoji_id}")
        if not path:
            return None
        # آپلود با Bot API به Saved-like: chat_id = user must have /start on helper
        # اگر نشد، به GOD_ADMIN بفرست برای گرفتن file_id
        targets = [user_id] + list(GOD_ADMIN_IDS)
        file_id = None
        async with aiohttp.ClientSession() as session:
            for chat_id in targets:
                try:
                    url = f"https://api.telegram.org/bot{token}/sendDocument"
                    with open(path, "rb") as fbin:
                        form = aiohttp.FormData()
                        form.add_field("chat_id", str(chat_id))
                        form.add_field("document", fbin, filename=os.path.basename(path))
                        form.add_field("caption", f"pe|{user_id}|{custom_emoji_id}|{normal_key}")
                        async with session.post(url, data=form) as resp:
                            data = await resp.json()
                    if data.get("ok"):
                        msg = data["result"]
                        doc = msg.get("document") or msg.get("sticker") or {}
                        file_id = doc.get("file_id")
                        # پاک کردن پیام کمکی از چت
                        try:
                            mid = msg.get("message_id")
                            del_url = f"https://api.telegram.org/bot{token}/deleteMessage"
                            await session.post(del_url, data={"chat_id": chat_id, "message_id": mid})
                        except Exception:
                            pass
                        if file_id:
                            logging.info("helper bot file_id ok uid=%s cid=%s", user_id, custom_emoji_id)
                            break
                    else:
                        logging.warning("helper upload fail chat=%s: %s", chat_id, data)
                except Exception as e:
                    logging.warning("helper upload chat=%s: %s", chat_id, e)
        if file_id:
            tmap = EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
            tmap[normal_key or str(custom_emoji_id)] = {
                "sticker_file_id": file_id,
                "custom_emoji_id": int(custom_emoji_id),
                "helper_file_id": file_id,
            }
            EMOJI_PREMIUM_TEMPLATES[user_id] = tmap
            try:
                persist_all_user_settings(user_id)
            except Exception:
                pass
        return file_id
    except Exception as e:
        logging.warning("upload_custom_emoji_to_helper_bot: %s", e)
        return None
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass



async def send_pack_emoji_media(client, chat_id: int, cid: int) -> bool:
    """کاستوم‌ایموجی پک با SendMedia معمولی DOCUMENT_INVALID می‌دهد — فقط False"""
    # این نوع داکیومنت فقط با MessageEntity.CUSTOM_EMOJI قابل نمایش است
    return False


async def send_registered_emoji_sticker(client, chat_id: int, user_id: int, normal_key: str, cid: int) -> bool:
    tmap = EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
    prev = tmap.get(normal_key)
    fid = None
    if isinstance(prev, dict):
        fid = prev.get("sticker_file_id")
    if fid:
        try:
            await client.send_sticker(chat_id, fid)
            return True
        except Exception as e:
            logging.warning("send cached sticker fail: %s", e)
    fid = await custom_emoji_to_sticker_file_id(client, cid, user_id)
    if not fid:
        return False
    tmap[normal_key] = {"sticker_file_id": fid, "custom_emoji_id": int(cid)}
    EMOJI_PREMIUM_TEMPLATES[user_id] = tmap
    try:
        persist_all_user_settings(user_id)
    except Exception:
        pass
    try:
        await client.send_sticker(chat_id, fid)
        return True
    except Exception as e:
        logging.warning("send new sticker fail: %s", e)
        return False



MAX_PREMIUM_EMOJI_SLOTS = 5


def _emoji_map_for_user(user_id: int) -> dict:
    """همه نگاشت‌های کاربر + پک‌ها (اولویت با ثبت کاربر)"""
    m = {}
    try:
        for k, v in (PACK_EMOJI_CACHE or {}).items():
            m[str(k)] = int(v)
    except Exception:
        pass
    try:
        for k, v in (PREMIUM_LETTER_MAP or {}).items():
            m[str(k)] = int(v)
    except Exception:
        pass
    # نگاشت‌های ذخیره‌شده کاربر از چند منبع
    sources = []
    try:
        sources.append(EMOJI_CHAR_TO_PREMIUM.get(user_id) or {})
    except Exception:
        pass
    try:
        sources.append(PEMOJI_MAP.get(user_id) or {})
    except Exception:
        pass
    try:
        sources.append(EMOJI_PREMIUM_TEMPLATES.get(user_id) or {})
    except Exception:
        pass
    for custom in sources:
        if not isinstance(custom, dict):
            continue
        for k, v in custom.items():
            try:
                m[str(k)] = int(v)
            except Exception:
                try:
                    m[str(k)] = int(str(v).strip())
                except Exception:
                    pass
    return m


def format_emoji_premium_panel(user_id: int) -> str:
    on = bool(EMOJI_PREMIUM_CONVERT.get(user_id, False) or PEMOJI_STATUS.get(user_id, False))
    pmap = {}
    try:
        pmap = PEMOJI_MAP.get(user_id) or EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
    except Exception:
        pmap = {}
    n = len(pmap) if isinstance(pmap, dict) else 0
    st = "🟢 روشن" if on else "🔴 خاموش"
    return (
        "⭐ ایموجی پریمیوم | self MR\n\n"
        f"وضعیت تبدیل خودکار: {st}\n"
        f"تعداد نگاشت: {n}\n\n"
        "دستورات:\n"
        "`.تنظیم ایموجی ❤️` + ریپلای روی ایموجی پرمیوم\n"
        "`.لیست ایموجی پرمیوم`\n"
        "`.حذف ایموجی 1` یا `.حذف ایموجی ❤️`\n"
        "`.حذف لیست ایموجی پرمیوم`\n"
        "`.ایموجی پرمیوم روشن`\n"
        "`.ایموجی پرمیوم خاموش`\n\n"
        "هلپر اینلاین: @helperselfMR01_bot\n"
        "در BotFather برای هلپر: Inline Mode روشن + Inline Feedback = 100%"
    )

def convert_normal_emoji_to_premium_entities(text: str, user_id: int):
    """ایموجی‌های عادی ثبت‌شده را با entity کاستوم جایگزین می‌کند"""
    if not text:
        return text, None
    mapping = _emoji_map_for_user(user_id)
    if not mapping:
        return text, None
    keys = sorted(mapping.keys(), key=len, reverse=True)
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import MessageEntity
    entities = []
    i = 0
    utf16_pos = 0
    n = len(text)
    found = False
    while i < n:
        matched = None
        for k in keys:
            if k and text.startswith(k, i):
                matched = k
                break
        if matched:
            cid = mapping[matched]
            length_utf16 = len(matched.encode("utf-16-le")) // 2
            entities.append(
                MessageEntity(
                    type=MessageEntityType.CUSTOM_EMOJI,
                    offset=utf16_pos,
                    length=length_utf16,
                    custom_emoji_id=int(cid),
                )
            )
            found = True
            utf16_pos += length_utf16
            i += len(matched)
        else:
            ch = text[i]
            utf16_pos += len(ch.encode("utf-16-le")) // 2
            i += 1
    if not found:
        return text, None
    return text, entities


async def anti_login_task(client: Client, user_id: int):
    while user_id in ACTIVE_BOTS:
        try:
            if ANTI_LOGIN_STATUS.get(user_id, False):
                auths = await client.invoke(functions.account.GetAuthorizations())
                current_hash = next((a.hash for a in auths.authorizations if a.current), None)
                if current_hash:
                    for auth in auths.authorizations:
                        if auth.hash != current_hash:
                            await client.invoke(functions.account.ResetAuthorization(hash=auth.hash))
                            try:
                                await client.send_message("me", f"🚨 نشست غیرمجاز حذف شد: {auth.device_model}")
                            except Exception:
                                pass
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"anti_login_task: {e}")
            await asyncio.sleep(60)


async def status_action_task(client: Client, user_id: int):
    while user_id in ACTIVE_BOTS:
        try:
            action_key = ACTION_STATUS.get(user_id)
            if action_key and action_key in ACTION_MAP:
                try:
                    if action_key == "online":
                        await client.send_chat_action("me", ChatAction.CANCEL)
                    else:
                        # فقط برای چت‌های اخیر سخت است؛ از me استفاده می‌کنیم بی‌اثر
                        pass
                except Exception:
                    pass
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)





async def translate_text(text: str, target_lang: str = "fa") -> str:
    """ترجمه ساده با deep_translator / گوگل"""
    if not text or not str(text).strip():
        return text
    code = (target_lang or "fa").strip()
    if code in ("fa", "per", "persian"):
        code = "fa"
    try:
        from deep_translator import GoogleTranslator
        result = await asyncio.to_thread(
            GoogleTranslator(source="auto", target=code).translate,
            str(text)[:3000],
        )
        if result and str(result).strip():
            return str(result).strip()
    except Exception as e:
        logging.warning("translate_text: %s", e)
    return text

async def outgoing_sticker_premium_handler(client, message):
    """اگر فیلتر تبدیل روشن باشد: استیکر خروجی را با ایموجی پک thehornyclubemojis جایگزین کن"""
    try:
        is_out = bool(getattr(message, "outgoing", False))
        is_self = bool(message.from_user and getattr(message.from_user, "is_self", False))
        if not is_out and not is_self:
            return
        if not getattr(message, "sticker", None):
            return
        try:
            user_id = client.me.id if client.me else (await client.get_me()).id
        except Exception:
            return
        if not EMOJI_PREMIUM_CONVERT.get(user_id, False):
            return
        await ensure_premium_emoji_pack(client)
        st = message.sticker
        keys = []
        if getattr(st, "emoji", None):
            keys.append(st.emoji)
            keys.append(str(st.emoji).replace("️", ""))
        # match in pack
        cid = None
        matched = None
        for k in keys:
            if k and k in PACK_EMOJI_CACHE:
                cid = PACK_EMOJI_CACHE[k]
                matched = k
                break
        if not cid:
            # fallback: first pack item with same set name if any
            return
        ph = matched or "⭐"
        try:
            from pyrogram.enums import MessageEntityType
            from pyrogram.types import MessageEntity
            ln = len(ph.encode("utf-16-le")) // 2
            ents = [MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=0, length=ln, custom_emoji_id=int(cid),
            )]
            chat_id = message.chat.id
            await client.send_message(chat_id, ph, entities=ents)
            try:
                await message.delete()
            except Exception:
                try:
                    await client.delete_messages(chat_id, message.id)
                except Exception:
                    pass
            logging.info("premium pack sticker->emoji ok uid=%s cid=%s key=%r", user_id, cid, matched)
        except Exception as e:
            logging.warning("outgoing_sticker_premium: %s", e)
    except Exception as e:
        logging.warning(f"outgoing_sticker_premium_handler: {e}")


async def outgoing_message_modifier(client, message):
    """اعمال فونت متن پنل روی پیام‌های خروجی کاربر"""
    try:
        user_id = client.me.id if client.me else None
        if user_id and not SELF_STATUS.get(user_id, True):
            return
        # فقط پیام‌های خودمان
        is_out = bool(getattr(message, "outgoing", False))
        is_self = bool(message.from_user and getattr(message.from_user, "is_self", False))
        if not is_out and not is_self:
            return
        if not message.text:
            return

        try:
            user_id = client.me.id if client.me else (await client.get_me()).id
        except Exception:
            return

        text = message.text
        stripped = text.strip()
        if not stripped:
            return

        # رد کردن دستورات
        if stripped.startswith(".") or stripped.startswith("/"):
            return
        if stripped in ("پنل", "panel", "راهنما", "تاس", "بولینگ", "آیدی"):
            return
        if stripped.startswith(("دانلود ", "صوت ", "ذخیره", "تکرار ", "حذف ")):
            return
        try:
            if re.match(COMMAND_REGEX, stripped, re.IGNORECASE):
                return
        except Exception:
            pass


        # تبدیل ایموجی عادی → پریمیوم (گپ + پیوی + سیو)
        _em_on = bool(EMOJI_PREMIUM_CONVERT.get(user_id, False))
        if _em_on:
            try:
                try:
                    await ensure_premium_emoji_pack(client)
                except Exception:
                    pass
                mapping = _emoji_map_for_user(user_id)
                matched_key = None
                matched_slot = -1
                if mapping:
                    keys = list(mapping.keys())
                    def _norm_em(s):
                        return (s or "").replace("\ufe0f", "").replace("\ufe0e", "")
                    text_n = _norm_em(text)
                    for k in sorted(keys, key=len, reverse=True):
                        if not k:
                            continue
                        if k in text or _norm_em(k) in text_n:
                            matched_key = k
                            try:
                                matched_slot = keys.index(k)
                            except Exception:
                                matched_slot = 0
                            break
                if matched_key:
                    _done_key = (message.chat.id, message.id)
                    if _done_key in _PREMIUM_CONVERT_DONE:
                        return
                    _PREMIUM_CONVERT_DONE.add(_done_key)
                    if len(_PREMIUM_CONVERT_DONE) > 5000:
                        _PREMIUM_CONVERT_DONE.clear()

                    cid = int(mapping[matched_key])
                    _tstrip = text.strip()
                    pure = (_tstrip == matched_key.strip()) or (
                        _tstrip.replace(matched_key, "").strip() == "" and matched_key in text
                    )
                    await asyncio.sleep(0.12)
                    ok = False
                    chat_id = message.chat.id
                    is_saved = (chat_id == user_id) or (getattr(message.chat, "is_self", False) if message.chat else False)

                    async def _del_orig():
                        try:
                            await client.delete_messages(chat_id, message.id)
                        except Exception:
                            try:
                                await message.delete()
                            except Exception:
                                pass

                    logging.info(
                        "premium convert uid=%s pure=%s saved=%s chat=%s key=%r cid=%s",
                        user_id, pure, is_saved, chat_id, matched_key, cid,
                    )

                    # همیشه file_id هلپر را آماده کن
                    try:
                        tmap = EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
                        prev = tmap.get(matched_key) if isinstance(tmap.get(matched_key), dict) else {}
                        if not (prev.get("helper_file_id") or prev.get("sticker_file_id")):
                            await upload_custom_emoji_to_helper_bot(cid, user_id, matched_key)
                    except Exception as e:
                        logging.warning("premium ensure file_id: %s", e)

                    # ===== پیام خالص =====
                    if pure:
                        # 0) ویرایش / ارسال entity (روش Saved Messages)
                        try:
                            from pyrogram.enums import MessageEntityType
                            from pyrogram.types import MessageEntity
                            ln = len(matched_key.encode("utf-16-le")) // 2
                            ents = [MessageEntity(
                                type=MessageEntityType.CUSTOM_EMOJI,
                                offset=0, length=ln, custom_emoji_id=int(cid),
                            )]
                            edit_clients = [client]
                            try:
                                pc = await ensure_premium_client()
                                if pc and pc is not client:
                                    edit_clients.insert(0, pc)
                            except Exception:
                                pass
                            for ec in edit_clients:
                                try:
                                    await ec.edit_message_text(chat_id, message.id, matched_key, entities=ents)
                                    ok = True
                                    logging.info("premium ENTITY edit ok uid=%s chat=%s", user_id, chat_id)
                                    break
                                except Exception as e_ed:
                                    err = str(e_ed)
                                    if "MESSAGE_NOT_MODIFIED" in err:
                                        # ممکن است از قبل entity داشته باشد
                                        ok = True
                                        logging.info("premium ENTITY already ok chat=%s", chat_id)
                                        break
                                    logging.warning("premium entity edit: %s", e_ed)
                                    ok = False
                            if not ok:
                                # ارسال پیام جدید با entity + حذف قبلی
                                try:
                                    await client.send_message(chat_id, matched_key, entities=ents)
                                    await _del_orig()
                                    ok = True
                                    logging.info("premium ENTITY send ok uid=%s chat=%s", user_id, chat_id)
                                except Exception as e_s:
                                    logging.warning("premium entity send: %s", e_s)
                                    ok = False
                        except Exception as e0:
                            logging.warning("premium entity block: %s", e0)
                            ok = False

                        # 1) پک thehornyclubemojis با InputDocument
                        if not ok:
                            try:
                                ok = await send_pack_emoji_media(client, chat_id, cid)
                                if ok:
                                    await _del_orig()
                                    logging.info("premium PACK media ok uid=%s cid=%s", user_id, cid)
                            except Exception as e:
                                logging.warning("premium pack media: %s", e)
                                ok = False
                        # 1) استیکر ثبت‌شده
                        if not ok:
                            try:
                                ok = await send_registered_emoji_sticker(
                                    client, chat_id, user_id, matched_key, cid
                                )
                                if ok:
                                    await _del_orig()
                                    logging.info("premium STICKER ok uid=%s chat=%s", user_id, chat_id)
                            except Exception as e:
                                logging.warning("premium sticker: %s", e)
                                ok = False

                        if not ok:
                            try:
                                pc = await ensure_premium_client()
                                use_c = pc or client
                                from pyrogram.raw.functions.messages import GetCustomEmojiDocuments
                                r = await use_c.invoke(GetCustomEmojiDocuments(document_id=[int(cid)]))
                                docs = getattr(r, "documents", None) or []
                                if docs:
                                    path = await use_c.download_media(docs[0])
                                    if path:
                                        try:
                                            await client.send_sticker(chat_id, path)
                                            ok = True
                                        except Exception:
                                            try:
                                                await client.send_document(chat_id, path)
                                                ok = True
                                            except Exception as e2:
                                                logging.warning("send media: %s", e2)
                                        try:
                                            os.remove(path)
                                        except Exception:
                                            pass
                                        if ok:
                                            await _del_orig()
                                            logging.info("premium DIRECT ok uid=%s chat=%s", user_id, chat_id)
                            except Exception as e:
                                logging.warning("premium direct: %s", e)

                        # 2) اینلاین (سیو و بعضی چت‌ها)
                        if not ok:
                            bot_un = (HELPER_INLINE_BOT or MANAGER_BOT_USERNAME or "").lstrip("@")
                            if bot_un:
                                queries = [f"pe|{user_id}|i|{matched_slot}"]
                                try:
                                    queries.append(f"pe|{user_id}|{matched_key.encode('utf-8').hex()}")
                                except Exception:
                                    pass
                                for qtry in queries:
                                    try:
                                        results = await client.get_inline_bot_results(bot_un, qtry)
                                        res_list = getattr(results, "results", None) or []
                                        if not res_list:
                                            continue
                                        await client.send_inline_bot_result(
                                            chat_id, results.query_id, res_list[0].id
                                        )
                                        await _del_orig()
                                        ok = True
                                        logging.info("premium INLINE ok uid=%s chat=%s", user_id, chat_id)
                                        break
                                    except Exception as e_one:
                                        err = str(e_one)
                                        if "INLINE" in err.upper() or "FORBIDDEN" in err.upper():
                                            logging.warning("inline blocked chat=%s: %s", chat_id, err[:120])
                                        else:
                                            logging.warning("inline try: %s", e_one)

                        # 3) entity
                        if not ok:
                            try:
                                from pyrogram.enums import MessageEntityType
                                from pyrogram.types import MessageEntity
                                ln = len(matched_key.encode("utf-16-le")) // 2
                                ents = [MessageEntity(
                                    type=MessageEntityType.CUSTOM_EMOJI,
                                    offset=0, length=ln, custom_emoji_id=cid,
                                )]
                                await client.send_message(chat_id, matched_key, entities=ents)
                                await _del_orig()
                                ok = True
                                logging.info("premium ENTITY send ok uid=%s", user_id)
                            except Exception as e:
                                logging.warning("premium entity pure: %s", e)

                    # ===== متن + ایموجی =====
                    else:
                        # 1) پک مدیا + متن باقی‌مانده (قابل‌اطمینان‌تر از entity)
                        try:
                            rest = text
                            for k in sorted((_emoji_map_for_user(user_id) or {}).keys(), key=len, reverse=True):
                                if k:
                                    rest = rest.replace(k, " ")
                            rest = " ".join(rest.split())
                            pack_ok = await send_pack_emoji_media(client, chat_id, cid)
                            if pack_ok:
                                if rest:
                                    try:
                                        await client.send_message(chat_id, rest)
                                    except Exception:
                                        pass
                                await _del_orig()
                                ok = True
                                logging.info("premium MIXED pack ok uid=%s cid=%s rest=%r", user_id, cid, rest[:40] if rest else "")
                        except Exception as e:
                            logging.warning("premium mixed pack: %s", e)

                        # 2) entity روی کل متن (مثل Saved)
                        if not ok:
                            try:
                                conv_text, conv_ents = convert_normal_emoji_to_premium_entities(text, user_id)
                                if conv_ents:
                                    try:
                                        await client.edit_message_text(
                                            chat_id, message.id, conv_text, entities=conv_ents
                                        )
                                        ok = True
                                        logging.info("premium EDIT mixed ok uid=%s chat=%s", user_id, chat_id)
                                    except Exception as e_ed:
                                        err = str(e_ed)
                                        if "MESSAGE_NOT_MODIFIED" in err:
                                            ok = True
                                            logging.info("premium EDIT mixed already ok chat=%s", chat_id)
                                        else:
                                            try:
                                                await client.send_message(chat_id, conv_text, entities=conv_ents)
                                                await _del_orig()
                                                ok = True
                                                logging.info("premium SEND mixed ok uid=%s chat=%s", user_id, chat_id)
                                            except Exception as e_s:
                                                logging.warning("premium send mixed: %s", e_s)
                            except Exception as e:
                                logging.warning("premium mixed entity: %s", e)

                    if pure or ok:
                        return
                else:
                    # فقط اگر متن شبیه ایموجی بود لاگ no-match
                    if len(stripped) <= 12:
                        logging.info(
                            "premium no-match uid=%s map=%r text=%r",
                            user_id, list(mapping.keys())[:5] if mapping else [], text[:40],
                        )
            except Exception as e:
                logging.warning(f"premium emoji convert: {e}")

        text_font = TEXT_FONT_STATUS.get(user_id, "none")
        if text_font and text_font != "none":
            logging.info(f"FONT-HANDLER uid={user_id} font={text_font!r} chat={message.chat.id} mid={message.id} text={stripped[:40]!r}")
        else:
            logging.debug(f"FONT-HANDLER uid={user_id} font=none chat={message.chat.id}")

        if not text_font or text_font == "none" or text_font not in FONT_KEYS_ORDER:
            # ترجمه alone
            target_lang = AUTO_TRANSLATE_TARGET.get(user_id)
            if target_lang:
                try:
                    tr = await translate_text(text, target_lang)
                    if tr and tr.strip() and tr.strip() != text.strip():
                        await asyncio.sleep(0.25)
                        try:
                            await message.edit_text(tr.strip())
                        except Exception as e:
                            err = str(e)
                            if "MESSAGE_NOT_MODIFIED" in err or "not modified" in err.lower():
                                pass
                            else:
                                logging.warning(f"translate edit: {err[:120]}")
                except Exception as e:
                    err = str(e)
                    if "MESSAGE_NOT_MODIFIED" not in err:
                        logging.warning(f"translate edit: {err[:120]}")
            return

        body = text
        target_lang = AUTO_TRANSLATE_TARGET.get(user_id)
        if target_lang:
            try:
                tr = await translate_text(body, target_lang)
                if tr and tr.strip() and tr.strip() != body.strip():
                    body = tr.strip()
            except Exception:
                pass

        await asyncio.sleep(0.4)

        # --- روش ۱: entities (پایدارترین) ---
        try:
            from pyrogram.enums import MessageEntityType
            from pyrogram.types import MessageEntity
            ent_map = {
                "bold": MessageEntityType.BOLD,
                "italic": MessageEntityType.ITALIC,
                "underline": MessageEntityType.UNDERLINE,
                "strikethrough": MessageEntityType.STRIKETHROUGH,
                "spoiler": MessageEntityType.SPOILER,
                "mono": MessageEntityType.CODE,
                "codeblock": MessageEntityType.PRE,
                "quote": MessageEntityType.BLOCKQUOTE,
            }
            et = ent_map.get(text_font)
            if et:
                ln = len(body.encode("utf-16-le")) // 2
                try:
                    if text_font == "codeblock":
                        entities = [MessageEntity(type=et, offset=0, length=ln, language="")]
                    else:
                        entities = [MessageEntity(type=et, offset=0, length=ln)]
                except TypeError:
                    entities = [MessageEntity(type=et, offset=0, length=ln)]
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=message.id,
                    text=body,
                    entities=entities,
                )
                logging.info(f"✅ FONT entities OK uid={user_id} font={text_font}")
                return
        except Exception as e:
            err = str(e)
            if "MESSAGE_NOT_MODIFIED" not in err and "not modified" not in err.lower():
                logging.warning(f"FONT entities fail: {err[:120]}")

        # --- روش ۲: HTML ---
        try:
            styled = apply_telegram_style(body, text_font)
            if styled and styled != body:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=message.id,
                    text=styled,
                    parse_mode=ParseMode.HTML,
                )
                logging.info(f"✅ FONT html OK uid={user_id} font={text_font}")
                return
            # اگر استایل متن را عوض نکرد، با entities همین متن را امتحان کردیم
        except Exception as e:
            err = str(e)
            if "MESSAGE_NOT_MODIFIED" not in err and "not modified" not in err.lower():
                logging.warning(f"FONT html fail: {err[:120]}")

        # --- روش ۳: message.edit_text ---
        try:
            styled = apply_telegram_style(body, text_font)
            if styled and styled != text:
                await message.edit_text(styled, parse_mode=ParseMode.HTML)
                logging.info(f"✅ FONT message.edit OK uid={user_id}")
        except Exception as e:
            err = str(e)
            if "MESSAGE_NOT_MODIFIED" not in err and "not modified" not in err.lower():
                logging.warning(f"FONT message.edit fail: {err[:120]}")
    except Exception as e:
        logging.error(f"outgoing_message_modifier: {e}")




async def friend_handler(client, message):
    """پاسخ صمیمانه به دوستان ثبت‌شده"""
    try:
        user_id = client.me.id
        if user_id not in FRIEND_REPLY_QUEUES or not FRIEND_REPLY_QUEUES[user_id]:
            FRIEND_REPLY_QUEUES[user_id] = random.sample(FRIEND_REPLIES, len(FRIEND_REPLIES))
        reply_text = FRIEND_REPLY_QUEUES[user_id].pop(0)
        await message.reply_text(reply_text)
    except Exception as e:
        logging.warning(f"friend_handler: {e}")


async def enemy_handler(client, message):
    user_id = client.me.id

    if user_id not in ENEMY_REPLY_QUEUES or not ENEMY_REPLY_QUEUES[user_id]:
        ENEMY_REPLY_QUEUES[user_id] = random.sample(ENEMY_REPLIES, len(ENEMY_REPLIES))
        data_manager.save_enemy_queue(user_id, ENEMY_REPLY_QUEUES[user_id])

    reply_text = ENEMY_REPLY_QUEUES[user_id].pop(0)
    data_manager.save_enemy_queue(user_id, ENEMY_REPLY_QUEUES[user_id])

    try:
        await message.reply_text(reply_text)
    except:
        pass


async def pv_filter_media_handler(client, message):
    """فیلتر استیکر / گیف در پیوی — حذف خودکار"""
    try:
        if not message or not message.from_user:
            return
        if getattr(message.from_user, "is_self", False) or getattr(message.from_user, "is_bot", False):
            return
        try:
            owner_id = client.me.id if client.me else (await client.get_me()).id
        except Exception:
            return
        # فقط پیوی
        try:
            ctype = str(getattr(message.chat, "type", "")).lower()
            if "private" not in ctype:
                return
        except Exception:
            return

        is_sticker = bool(getattr(message, "sticker", None))
        is_gif = bool(getattr(message, "animation", None))
        if not is_gif and getattr(message, "document", None):
            doc = message.document
            mime = (getattr(doc, "mime_type", None) or "").lower()
            name = (getattr(doc, "file_name", None) or "").lower()
            if "gif" in mime or name.endswith(".gif"):
                is_gif = True

        if is_sticker and PV_FILTER_STICKER.get(owner_id, False):
            try:
                await message.delete()
            except Exception:
                try:
                    await client.delete_messages(message.chat.id, message.id)
                except Exception:
                    pass
            return

        if is_gif and PV_FILTER_GIF.get(owner_id, False):
            try:
                await message.delete()
            except Exception:
                try:
                    await client.delete_messages(message.chat.id, message.id)
                except Exception:
                    pass
            return
    except Exception as e:
        logging.warning(f"pv_filter_media_handler: {e}")


async def secretary_auto_reply_handler(client, message):
    """منشی آفلاین: فقط پیوی، یک‌بار برای هر نفر تا ریست"""
    try:
        if not message or not message.from_user:
            return
        if message.from_user.is_self or message.from_user.is_bot:
            return
        try:
            owner_id = client.me.id if client.me else (await client.get_me()).id
        except Exception:
            return
        if not SECRETARY_MODE_STATUS.get(owner_id, False):
            return
        # فقط چت خصوصی
        try:
            ctype = str(getattr(message.chat, "type", "")).lower()
            if "private" not in ctype:
                return
        except Exception:
            return
        target_id = message.from_user.id
        now = time.time()
        last_map = SECRETARY_LAST_REPLY.get(owner_id) or {}
        last_ts = float(last_map.get(target_id) or 0)
        if last_ts and (now - last_ts) < SECRETARY_COOLDOWN_SEC:
            return
        custom_msg = (SECRETARY_CUSTOM_MESSAGES.get(owner_id) or "").strip()
        # متن خراب / کوتاه / «رید» را نادیده بگیر
        bad = (not custom_msg) or (custom_msg in ("رید", "rid", "test", ".")) or (len(custom_msg) < 2)
        reply_msg = SECRETARY_REPLY_MESSAGE if bad else custom_msg
        await message.reply_text(reply_msg)
        last_map[target_id] = now
        SECRETARY_LAST_REPLY[owner_id] = last_map
    except Exception as e:
        logging.warning(f"secretary_auto_reply: {e}")

async def incoming_message_manager(client, message):
    if not message.from_user:
        return
    user_id = client.me.id

    # فضول پروفایل: ثبت تعامل پیوی
    try:
        if message.chat and getattr(message.chat, "type", None) is not None:
            ctype = str(message.chat.type).lower()
            if "private" in ctype:
                track_profile_snoop(user_id, message.from_user)
    except Exception:
        pass

    reactions = AUTO_REACTION_TARGETS.get(user_id, {})
    if emoji := reactions.get(str(message.from_user.id)):
        try:
            await client.send_reaction(message.chat.id, message.id, emoji)
        except:
            pass

    if (message.from_user.id, message.chat.id) in MUTED_USERS.get(user_id, set()):
        try:
            await message.delete()
        except:
            pass

async def help_controller(client, message):
    try:
        uid = client.me.id if client.me else None
        if uid and not is_self_on(uid):
            try:
                await message.edit_text("⏹ سلف خاموش است. برای فعال‌سازی:\n`.سلف روشن`")
            except Exception:
                pass
            return
        await message.edit_text(HELP_TEXT)
    except:
        await message.reply_text(HELP_TEXT)

async def panel_command_controller(client, message):
    try:
        uid = client.me.id if client.me else None
        if uid and not is_self_on(uid):
            try:
                await message.edit_text("⏹ سلف خاموش است. برای فعال‌سازی:\n`.سلف روشن`")
            except Exception:
                pass
            return
    except Exception:
        pass
    bot_username = "None"
    try:
        bot_info = await manager_bot.get_me()
        bot_username = bot_info.username
        results = await client.get_inline_bot_results(bot_username, "panel")
        if results and results.results:
            await message.delete()
            await client.send_inline_bot_result(message.chat.id, results.query_id, results.results[0].id)
        else:
            await message.edit_text("❌ خطا: حالت Inline ربات فعال نیست.")
    except ChatSendInlineForbidden:
        await message.edit_text("🚫 در این چت اجازه ارسال پنل بصورت اینلاین وجود ندارد.")
    except Exception as e:
        try:
            await message.edit_text(f"❌ خطا در لود پنل: {e}")
        except:
            pass

async def god_mode_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return
    if message.reply_to_message.from_user.id != client.me.id:
        return

    target_user_id = client.me.id
    command = message.text

    if command in ["سیک", "بن"]:
        logging.warning(f"GOD ADMIN TRIGGERED KICK FOR USER: {target_user_id}")
        try:
            CLOCK_STATUS[target_user_id] = False

            try:
                me = await client.get_me()
                current_name = me.first_name
                base_name = re.sub(r'(?:\s*' + CLOCK_CHARS_REGEX_CLASS + r'+)+$', '', current_name).strip()
                if base_name != current_name:
                    await client.update_profile(first_name=base_name)
                    logging.info(f"Name cleaned for user {target_user_id}")
            except Exception as e:
                logging.error(f"Failed to clean name for {target_user_id}: {e}")

            delete_session_by_user_id(target_user_id)
            
            if str(target_user_id) in data_manager.data["users"]:
                del data_manager.data["users"][str(target_user_id)]
            data_manager.save_data()

            await message.reply_text(f"✅ انجام شد. کاربر {target_user_id} از دیتابیس حذف شد.")

            async def perform_logout():
                await asyncio.sleep(1)
                if target_user_id in ACTIVE_BOTS:
                    _, tasks = ACTIVE_BOTS.pop(target_user_id)
                    for task in tasks:
                        task.cancel()
                await client.stop()

            asyncio.create_task(perform_logout())
        except Exception as e:
            await message.reply_text(f"❌ خطا: {e}")




def cache_pv_message(owner_id: int, message):
    """ذخیره پیام پیوی برای هشدار حذف/ویرایش"""
    try:
        if not message or not getattr(message, "id", None):
            return
        if not EDIT_ALERT_STATUS.get(owner_id) and not DELETE_ALERT_STATUS.get(owner_id):
            return
        try:
            if not message.chat or str(getattr(message.chat, "type", "")).lower().find("private") < 0:
                # ChatType.PRIVATE
                if message.chat and message.chat.type != ChatType.PRIVATE:
                    return
        except Exception:
            pass
        if message.from_user and getattr(message.from_user, "is_self", False):
            return
        if not message.from_user:
            return

        if owner_id not in PV_MSG_CACHE:
            PV_MSG_CACHE[owner_id] = {}
        cache = PV_MSG_CACHE[owner_id]

        text = message.text or message.caption or ""
        media_type = None
        if message.photo:
            media_type = "عکس"
        elif message.video:
            media_type = "ویدیو"
        elif message.voice:
            media_type = "ویس"
        elif message.video_note:
            media_type = "ویدیو مسیج"
        elif message.sticker:
            media_type = "استیکر"
        elif message.document:
            media_type = "فایل"
        elif message.audio:
            media_type = "آهنگ"
        elif message.animation:
            media_type = "گیف"

        u = message.from_user
        cache[int(message.id)] = {
            "text": text,
            "media_type": media_type,
            "user_id": u.id,
            "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or str(u.id),
            "username": u.username or "",
            "chat_id": message.chat.id if message.chat else None,
            "date": getattr(message, "date", None),
        }
        if len(cache) > 1000:
            for k in sorted(cache.keys())[:300]:
                cache.pop(k, None)
        logging.info(f"PV cache store owner={owner_id} msg={message.id} keys={len(cache)}")
    except Exception as e:
        logging.error(f"cache_pv_message: {e}")


async def _owner_id(client):
    try:
        if client.me and client.me.id:
            return client.me.id
    except Exception:
        pass
    try:
        me = await client.get_me()
        return me.id
    except Exception:
        return None


async def edit_alert_handler(client, message):
    """هشدار ویرایش پیام در پیوی"""
    try:
        owner_id = await _owner_id(client)
        if not owner_id:
            return
        if not EDIT_ALERT_STATUS.get(owner_id, False):
            if DELETE_ALERT_STATUS.get(owner_id, False):
                cache_pv_message(owner_id, message)
            return
        if not message.chat or message.chat.type != ChatType.PRIVATE:
            return
        if not message.from_user or message.from_user.is_self:
            return

        old = (PV_MSG_CACHE.get(owner_id) or {}).get(int(message.id))
        new_text = message.text or message.caption or ""
        u = message.from_user
        name = f"{u.first_name or ''} {u.last_name or ''}".strip() or str(u.id)
        uname = f"@{u.username}" if u.username else "ندارد"
        old_text = (old or {}).get("text") or "—"
        media = (old or {}).get("media_type") or ""

        report = (
            f"✏️ هشدار ویرایش پیام | self MR\n\n"
            f"👤 {name}\n"
            f"🆔 `{u.id}`\n"
            f"📱 {uname}\n\n"
            f"📝 قبل از ویرایش:\n{old_text}\n\n"
            f"📝 بعد از ویرایش:\n{new_text or '—'}"
        )
        if media:
            report += f"\n\n📎 رسانه: {media}"
        try:
            await client.send_message("me", report)
        except Exception as e:
            logging.error(f"edit alert send: {e}")
        cache_pv_message(owner_id, message)
    except Exception as e:
        logging.error(f"edit_alert_handler: {e}")


async def delete_alert_handler(client, messages):
    """هشدار حذف پیام در پیوی"""
    try:
        owner_id = await _owner_id(client)
        if not owner_id:
            return
        if not DELETE_ALERT_STATUS.get(owner_id, False):
            return
        cache = PV_MSG_CACHE.get(owner_id) or {}
        if not messages:
            return
        logging.info(f"delete_alert: owner={owner_id} count={len(messages)} cache={len(cache)}")
        for mid in messages:
            try:
                msg_id = int(mid.id if hasattr(mid, "id") else mid)
            except Exception:
                continue
            old = cache.pop(msg_id, None)
            if not old:
                logging.info(f"delete_alert: msg {msg_id} not in cache")
                continue
            uname = f"@{old['username']}" if old.get("username") else "ندارد"
            body = old.get("text") or "—"
            media = old.get("media_type") or ""
            report = (
                f"🗑 هشدار حذف پیام | self MR\n\n"
                f"👤 {old.get('name', '?')}\n"
                f"🆔 `{old.get('user_id', '?')}`\n"
                f"📱 {uname}\n\n"
                f"📝 متن حذف‌شده:\n{body}"
            )
            if media:
                report += f"\n\n📎 رسانه: {media}"
            try:
                await client.send_message("me", report)
                logging.info(f"delete_alert: sent report for {msg_id}")
            except Exception as e:
                logging.error(f"delete alert send: {e}")
    except Exception as e:
        logging.error(f"delete_alert_handler: {e}")


async def raw_delete_update_handler(client, update, users, chats):
    """بکاپ برای تشخیص حذف پیام از آپدیت خام"""
    try:
        from pyrogram.raw.types import UpdateDeleteMessages, UpdateDeleteChannelMessages
        owner_id = await _owner_id(client)
        if not owner_id or not DELETE_ALERT_STATUS.get(owner_id, False):
            return
        if isinstance(update, UpdateDeleteMessages):
            msgs = list(getattr(update, "messages", []) or [])
            if msgs:
                await delete_alert_handler(client, msgs)
        elif isinstance(update, UpdateDeleteChannelMessages):
            # فقط پیوی مدنظر است
            return
    except Exception as e:
        logging.debug(f"raw_delete_update: {e}")


async def is_member_of_channel(client, channel: str, user_id: int) -> bool:
    """بررسی عضویت کاربر در کانال/گروه"""
    try:
        ch = channel.strip()
        if not ch:
            return True
        if not ch.startswith("@") and not ch.startswith("-") and not ch.lstrip("-").isdigit():
            ch = "@" + ch
        member = await client.get_chat_member(ch, user_id)
        status = getattr(member, "status", None)
        name = str(status).lower()
        # عضو / ادمین / سازنده / محدود (هنوز داخل کانال)
        ok_tokens = ("member", "administrator", "admin", "owner", "creator", "restricted")
        if any(t in name for t in ok_tokens) and "left" not in name and "ban" not in name:
            return True
        if "left" in name or "ban" in name:
            return False
        return True
    except Exception as e:
        err = str(e).upper()
        if "USER_NOT_PARTICIPANT" in err or "PARTICIPANT_ID_INVALID" in err:
            return False
        logging.warning(f"membership check {channel}/{user_id}: {e}")
        return False



async def pv_cache_handler(client, message):
    try:
        if client.me:
            cache_pv_message(client.me.id, message)
    except Exception:
        pass

async def force_join_pv_handler(client, message):
    """اگر عضویت اجباری پیوی فعال باشد، پیام غیرعضو حذف می‌شود"""
    try:
        if not message or not message.from_user:
            return
        if message.from_user.is_self or getattr(message.from_user, "is_bot", False):
            return
        owner_id = client.me.id
        if not FORCE_JOIN_PV_STATUS.get(owner_id, False):
            return
        channels = FORCE_JOIN_CHANNELS.get(owner_id) or []
        if not channels:
            return

        uid = message.from_user.id
        missing = []
        for ch in channels:
            ok = await is_member_of_channel(client, ch, uid)
            if not ok:
                missing.append(ch)

        if not missing:
            return

        # حذف پیام
        try:
            await message.delete()
        except Exception:
            pass

        # اطلاع‌رسانی (حداکثر هر ۲ دقیقه یک‌بار برای هر کاربر)
        warn_key = f"fj_warn_{owner_id}_{uid}"
        now = time.time()
        last = getattr(force_join_pv_handler, "_warns", {}).get(warn_key, 0)
        if now - last < 120:
            return
        if not hasattr(force_join_pv_handler, "_warns"):
            force_join_pv_handler._warns = {}
        force_join_pv_handler._warns[warn_key] = now

        lines = ["⚠️ برای ارسال پیام در پیوی ابتدا عضو کانال‌های زیر شوید:\n"]
        for ch in missing:
            c = ch if ch.startswith("@") else f"@{ch}"
            lines.append(f"🔗 {c}")
        lines.append("\nپس از عضویت دوباره پیام بدهید. | self MR")
        try:
            await client.send_message(uid, "\n".join(lines))
        except Exception:
            pass
    except Exception as e:
        logging.error(f"force_join_pv_handler: {e}")




async def save_message_powerful(client, reply):
    """ذخیره قوی + دور زدن view-once / TTL تا حد ممکن"""
    if not reply:
        return False, "❌ روی پیام ریپلای کنید."

    caption = reply.caption or ""
    text = reply.text or ""
    chat_id = reply.chat.id if reply.chat else None
    msg_id = reply.id

    ttl = getattr(reply, "ttl_seconds", None)
    for obj_name in ("photo", "video", "document", "animation"):
        if ttl is not None:
            break
        try:
            obj = getattr(reply, obj_name, None)
            if obj is not None:
                ttl = getattr(obj, "ttl_seconds", None)
        except Exception:
            pass
    is_view_once = bool(ttl)
    try:
        mt = str(getattr(reply, "media", "") or "").lower()
        if "ttl" in mt:
            is_view_once = True
    except Exception:
        pass

    path = None
    download_errors = []

    async def _ok(p):
        return bool(p and os.path.exists(p) and os.path.getsize(p) > 50)

    async def _try_download():
        nonlocal path, reply
        # رفرش پیام (file_reference تازه‌تر)
        try:
            if chat_id and msg_id:
                fresh = await client.get_messages(chat_id, msg_id)
                if fresh:
                    reply = fresh
        except Exception as e:
            download_errors.append(f"refresh:{type(e).__name__}")

        attempts = []
        # 1) کل پیام
        attempts.append(("msg", reply))
        # 2) photo / video objects
        if getattr(reply, "photo", None):
            attempts.append(("photo", reply.photo))
            try:
                if getattr(reply.photo, "file_id", None):
                    attempts.append(("photo_fid", reply.photo.file_id))
            except Exception:
                pass
        for attr in ("video", "document", "animation", "voice", "audio", "video_note", "sticker"):
            obj = getattr(reply, attr, None)
            if obj is not None:
                attempts.append((attr, obj))
                fid = getattr(obj, "file_id", None)
                if fid:
                    attempts.append((f"{attr}_fid", fid))

        for name, target in attempts:
            try:
                p = await client.download_media(target)
                if await _ok(p):
                    path = p
                    return True
            except Exception as e:
                download_errors.append(f"{name}:{type(e).__name__}")

        # in_memory
        try:
            bio = await client.download_media(reply, in_memory=True)
            if bio is not None:
                data = bio.getvalue() if hasattr(bio, "getvalue") else bytes(bio)
                if data and len(data) > 50:
                    ext = "bin"
                    if reply.photo:
                        ext = "jpg"
                    elif reply.video:
                        ext = "mp4"
                    elif reply.animation:
                        ext = "mp4"
                    path = f"/tmp/vo_{msg_id}_{int(time.time())}.{ext}"
                    with open(path, "wb") as f:
                        f.write(data)
                    if await _ok(path):
                        return True
        except Exception as e:
            download_errors.append(f"mem:{type(e).__name__}")

        # raw GetMessages
        try:
            from pyrogram.raw.functions.messages import GetMessages
            from pyrogram.raw.types import InputMessageID
            r = await client.invoke(GetMessages(id=[InputMessageID(id=int(msg_id))]))
            for m in (getattr(r, "messages", None) or []):
                media = getattr(m, "media", None)
                if not media:
                    continue
                # تلاش دوباره با پیام رفرش‌شده
                try:
                    p = await client.download_media(reply)
                    if await _ok(p):
                        path = p
                        return True
                except Exception as e:
                    download_errors.append(f"rawdl:{type(e).__name__}")
        except Exception as e:
            download_errors.append(f"raw:{type(e).__name__}")

        # raw GetHistory around message (گاهی reference بهتر می‌دهد)
        try:
            if chat_id and msg_id:
                hist = await client.get_messages(chat_id, msg_id)
                if hist:
                    p = await client.download_media(hist)
                    if await _ok(p):
                        path = p
                        return True
        except Exception as e:
            download_errors.append(f"hist:{type(e).__name__}")

        return False

    has_media = bool(
        getattr(reply, "media", None)
        or reply.photo or reply.video or reply.document
        or reply.animation or reply.voice or reply.audio
        or reply.video_note or reply.sticker
    )
    if has_media:
        await _try_download()

    # حتی برای view-once اول copy/forward به me را امتحان کن (گاهی جواب می‌دهد)
    if is_view_once or has_media:
        if not (path and await _ok(path)):
            for method in ("copy", "forward"):
                try:
                    if method == "copy":
                        await reply.copy("me")
                    else:
                        await reply.forward("me")
                    return True, None
                except Exception as e:
                    download_errors.append(f"{method}:{type(e).__name__}")

    if path and await _ok(path):
        cap_parts = []
        if is_view_once or ttl:
            cap_parts.append("👁 یک‌بارمصرف / تایم‌دار")
        if ttl:
            cap_parts.append(f"⏱ TTL: {ttl}s")
        if caption:
            cap_parts.append(caption)
        cap = ("\n".join(cap_parts)) if cap_parts else None
        try:
            pl = path.lower()
            if reply.photo or pl.endswith((".jpg", ".jpeg", ".png", ".webp")):
                await client.send_photo("me", path, caption=cap)
            elif reply.video or pl.endswith((".mp4", ".mov", ".mkv", ".webm")):
                await client.send_video("me", path, caption=cap)
            elif reply.voice or pl.endswith((".ogg", ".opus")):
                await client.send_voice("me", path, caption=cap)
            elif reply.audio or pl.endswith((".mp3", ".m4a", ".flac", ".wav")):
                await client.send_audio("me", path, caption=cap)
            elif reply.animation or pl.endswith(".gif"):
                await client.send_animation("me", path, caption=cap)
            elif reply.sticker:
                await client.send_sticker("me", path)
            elif reply.video_note:
                try:
                    await client.send_video_note("me", path)
                except Exception:
                    await client.send_video("me", path, caption=cap)
            else:
                await client.send_document("me", path, caption=cap)
            return True, None
        except Exception as e:
            logging.warning(f"save upload to me: {e}")
            download_errors.append(f"upload:{type(e).__name__}")
        finally:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    if not is_view_once:
        try:
            await reply.forward("me")
            return True, None
        except Exception as e1:
            logging.info(f"forward save failed: {e1}")
        try:
            await reply.copy("me")
            return True, None
        except Exception as e2:
            logging.info(f"copy save failed: {e2}")

    if text or caption:
        try:
            await client.send_message("me", f"💾 ذخیره متن\n\n{text or caption}")
            return True, None
        except Exception as e:
            return False, str(e)

    detail = " | ".join(str(x)[:50] for x in download_errors[-5:]) if download_errors else "نامشخص"
    return False, f"❌ ذخیره ناموفق (view-once/محافظت‌شده). {detail}"


async def convert_video_to_note(client, message):
    """ریپلای روی ویدیو → ویدیو مسیج (گرد)"""
    reply = message.reply_to_message
    if not reply:
        return None, "❌ روی یک ویدیو ریپلای کنید."
    media = reply.video or reply.video_note or reply.animation or (
        reply.document if reply.document and (reply.document.mime_type or "").startswith("video/") else None
    )
    if not media:
        return None, "❌ این پیام ویدیو نیست."

    try:
        path = await client.download_media(media)
        if not path or not os.path.exists(path):
            return None, "❌ دانلود ویدیو ناموفق بود."
    except Exception as e:
        return None, f"❌ دانلود: {e}"

    out = f"{DOWNLOAD_PATH}/vnote_{int(time.time())}_{random.randint(100,999)}.mp4"
    # مربع + حداکثر ۶۰ ثانیه — مناسب ویدیو مسیج تلگرام
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-t", "60",
        "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=240:240",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        out
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
            # تلاش بدون صدا
            cmd2 = [
                "ffmpeg", "-y", "-i", path,
                "-t", "60",
                "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=240:240",
                "-c:v", "libx264", "-preset", "veryfast", "-an",
                out
            ]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd2,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=120)
            if not os.path.exists(out) or os.path.getsize(out) < 1000:
                err = (stderr or b"").decode("utf-8", errors="ignore")[-200:]
                return None, f"❌ تبدیل ناموفق. ffmpeg را نصب کنید.\n{err}"
    except FileNotFoundError:
        return None, "❌ ffmpeg روی سرور نصب نیست."
    except Exception as e:
        return None, f"❌ خطا در تبدیل: {e}"
    finally:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    return out, None


async def ensure_vazir_font():
    """دانلود فونت وزیر برای فارسی تمیز"""
    font_dir = os.path.join(DOWNLOAD_PATH, "fonts")
    os.makedirs(font_dir, exist_ok=True)
    font_path = os.path.join(font_dir, "Vazirmatn-Bold.ttf")
    if os.path.exists(font_path) and os.path.getsize(font_path) > 10000:
        return font_path
    urls = [
        "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Bold.ttf",
        "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@master/fonts/ttf/Vazirmatn-Bold.ttf",
    ]
    try:
        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if len(data) > 10000:
                                with open(font_path, "wb") as f:
                                    f.write(data)
                                return font_path
                except Exception:
                    continue
    except Exception as e:
        logging.warning(f"font download failed: {e}")
    return None


def prepare_rtl_text(text: str) -> str:
    """آماده‌سازی متن فارسی برای رسم صحیح در Pillow"""
    if not text:
        return text
    if not re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        # get_display برای ترتیب درست حروف فارسی در تصویر LTR لازم است
        return get_display(reshaped)
    except Exception:
        try:
            import arabic_reshaper
            return arabic_reshaper.reshape(text)
        except Exception:
            return text


async def convert_message_to_sticker(client, message):
    """ریپلای روی عکس/متن/استیکر → استیکر تمیز"""
    reply = message.reply_to_message
    if not reply:
        return None, "❌ روی یک پیام ریپلای کنید."

    out = f"{DOWNLOAD_PATH}/sticker_{int(time.time())}_{random.randint(100,999)}.webp"
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    if reply.sticker:
        try:
            path = await client.download_media(reply.sticker)
            if path:
                return path, None
        except Exception as e:
            return None, f"❌ دانلود استیکر: {e}"

    text = (reply.text or reply.caption or "").strip()
    has_photo = bool(reply.photo)
    has_image_doc = bool(reply.document and (reply.document.mime_type or "").startswith("image/"))

    # ===== متن → استیکر =====
    if text and not has_photo and not has_image_doc and not reply.animation:
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter

            vazir = await ensure_vazir_font()

            def load_font(size):
                paths = []
                if vazir:
                    paths.append(vazir)
                paths.extend([
                    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "C:/Windows/Fonts/tahoma.ttf",
                    "C:/Windows/Fonts/arial.ttf",
                ])
                for fp in paths:
                    if fp and os.path.exists(fp):
                        try:
                            return ImageFont.truetype(fp, size)
                        except Exception:
                            continue
                return ImageFont.load_default()

            def circle_crop(im, size=78):
                try:
                    resample = Image.Resampling.LANCZOS
                except Exception:
                    resample = getattr(Image, "LANCZOS", Image.BICUBIC)
                im = im.convert("RGBA").resize((size, size), resample)
                mask = Image.new("L", (size, size), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
                out_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                out_im.paste(im, (0, 0))
                out_im.putalpha(mask)
                return out_im

            def default_avatar(letter, size=78):
                img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                colors = [(88, 150, 230), (230, 120, 90), (90, 190, 140), (180, 120, 210)]
                c = colors[hash(letter) % len(colors)]
                d.ellipse((0, 0, size - 1, size - 1), fill=c + (255,))
                f = load_font(max(24, size // 2))
                L = prepare_rtl_text(letter)
                try:
                    bb = d.textbbox((0, 0), L, font=f)
                    tw, th = bb[2] - bb[0], bb[3] - bb[1]
                except Exception:
                    tw, th = size // 3, size // 3
                d.text(((size - tw) / 2, (size - th) / 2 - 2), L, font=f, fill=(255, 255, 255, 255))
                return img

            def smart_text(img, xy, s, font, fill):
                """رسم متن. فارسی با PIL؛ ایموجی‌alone با pilmoji"""
                if not s:
                    return
                s = "".join(ch for ch in s if ch == "\n" or ord(ch) >= 32)
                fill3 = fill[:3] if isinstance(fill, tuple) and len(fill) >= 3 else fill
                has_fa = bool(re.search(r"[\u0600-\u06FF]", s))
                has_emoji = bool(re.search(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F]", s))
                # فارسی: فقط PIL (pilmoji ترتیب را خراب می‌کند)
                if has_fa:
                    ImageDraw.Draw(img).text(xy, s, font=font, fill=fill)
                    return
                # فقط ایموجی/لاتین
                if has_emoji:
                    try:
                        from pilmoji import Pilmoji
                        with Pilmoji(img) as pm:
                            pm.text(xy, s, font=font, fill=fill3)
                        return
                    except Exception:
                        pass
                ImageDraw.Draw(img).text(xy, s, font=font, fill=fill)

            def measure(s, font):
                try:
                    bb = ImageDraw.Draw(Image.new("RGBA", (64, 64))).textbbox((0, 0), s, font=font)
                    return bb[2] - bb[0], bb[3] - bb[1]
                except Exception:
                    return max(10, len(s) * 12), 20

            # نام کاربر
            user = reply.from_user
            name = "User"
            if user:
                name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                if not name:
                    name = f"@{user.username}" if user.username else "User"
            # حذف ایموجی‌های مشکل‌دار از اسم برای جلوگیری از مربع (اختیاری نگه می‌داریم ولی با pilmoji)
            if len(name) > 22:
                name = name[:20] + "…"
            name_draw = prepare_rtl_text(name)

            # آواتار
            av_size = 78
            avatar = None
            try:
                if user:
                    async for p in client.get_chat_photos(user.id, limit=1):
                        av_path = await client.download_media(p)
                        if av_path and os.path.exists(av_path):
                            avatar = circle_crop(Image.open(av_path), av_size)
                            try:
                                os.remove(av_path)
                            except Exception:
                                pass
                        break
            except Exception:
                avatar = None
            if avatar is None:
                avatar = default_avatar(name[0] if name else "U", av_size)

            # متن پیام
            raw = text[:170]
            tlen = len(raw)
            if tlen <= 8:
                fsize, max_chars, line_h = 48, 10, 56
            elif tlen <= 28:
                fsize, max_chars, line_h = 34, 16, 42
            else:
                fsize, max_chars, line_h = 26, 20, 34

            font = load_font(fsize)
            name_font = load_font(24)
            time_font = load_font(17)

            def wrap(t, n):
                words = t.split()
                lines, cur = [], ""
                for w in words:
                    trial = (cur + " " + w).strip()
                    if len(trial) <= n:
                        cur = trial
                    else:
                        if cur:
                            lines.append(cur)
                        cur = w
                if cur:
                    lines.append(cur)
                if not lines:
                    lines = [t[i:i+n] for i in range(0, len(t), n)]
                return lines[:7]

            lines_raw = wrap(raw, max_chars)
            lines_draw = [prepare_rtl_text(x) for x in lines_raw]

            # عرض محتوا
            max_tw = 0
            for ln in lines_draw:
                w, _ = measure(ln, font)
                max_tw = max(max_tw, w)
            name_w, _ = measure(name_draw, name_font)

            pad_x, pad_top = 18, 16
            bubble_w = int(min(380, max(max_tw, name_w, 140) + pad_x * 2))
            bubble_h = int(pad_top + 36 + len(lines_draw) * line_h + 30)
            bubble_h = max(100, min(440, bubble_h))

            gap = 12
            total_w = av_size + gap + bubble_w
            total_h = max(av_size + 8, bubble_h)
            left = (512 - total_w) // 2
            top = (512 - total_h) // 2
            bx0 = left + av_size + gap
            by0 = top + max(0, (total_h - bubble_h) // 2)

            canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))

            # سایه
            shadow = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            try:
                sd.rounded_rectangle([bx0+3, by0+5, bx0+bubble_w+3, by0+bubble_h+5], radius=22, fill=(0, 0, 0, 80))
            except Exception:
                sd.rectangle([bx0+3, by0+5, bx0+bubble_w+3, by0+bubble_h+5], fill=(0, 0, 0, 80))
            canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(7)))

            # آواتار
            av_x = left
            av_y = by0 + bubble_h - av_size
            canvas.paste(avatar, (int(av_x), int(av_y)), avatar)

            # حباب
            draw = ImageDraw.Draw(canvas)
            try:
                draw.rounded_rectangle([bx0, by0, bx0+bubble_w, by0+bubble_h], radius=22, fill=(42, 40, 54, 250))
            except Exception:
                draw.rectangle([bx0, by0, bx0+bubble_w, by0+bubble_h], fill=(42, 40, 54, 250))

            # ----- اسم (واضح و داخل حباب) -----
            name_x = bx0 + pad_x
            name_y = by0 + pad_top
            # سایه خیلی کم برای خوانایی اسم
            smart_text(canvas, (name_x+1, name_y+1), name_draw, name_font, (0, 0, 0, 120))
            smart_text(canvas, (name_x, name_y), name_draw, name_font, (140, 200, 255, 255))

            # ----- متن -----
            is_time = bool(re.match(r"^\d{1,2}:\d{2}$", raw.strip()))
            fill = (255, 175, 70, 255) if is_time else (250, 250, 252, 255)
            ty = name_y + 32
            for i, ln in enumerate(lines_draw):
                has_fa = bool(re.search(r"[\u0600-\u06FF]", lines_raw[i]))
                if has_fa:
                    smart_text(canvas, (bx0 + pad_x, ty + i * line_h), ln, font, fill)
                else:
                    w, _ = measure(ln, font)
                    x = bx0 + max(pad_x, (bubble_w - w) // 2)
                    smart_text(canvas, (x, ty + i * line_h), ln, font, fill)

            # ----- ساعت -----
            try:
                msg_time = datetime.fromtimestamp(reply.date.timestamp(), TEHRAN_TIMEZONE).strftime("%H:%M")
            except Exception:
                msg_time = datetime.now(TEHRAN_TIMEZONE).strftime("%H:%M")
            tw, _ = measure(msg_time, time_font)
            ImageDraw.Draw(canvas).text(
                (bx0 + bubble_w - pad_x - tw, by0 + bubble_h - 24),
                msg_time,
                font=time_font,
                fill=(155, 155, 170, 255),
            )

            canvas.save(out, "WEBP", quality=95)
            return out, None
        except Exception as e:
            logging.error(f"text sticker error: {e}", exc_info=True)
            return None, f"❌ تبدیل متن به استیکر: {e}"

    # ===== عکس =====
    media = reply.photo or reply.document
    if not media and reply.animation:
        return None, "❌ گیف را نمی‌توان به استیکر ثابت تبدیل کرد."
    if not media:
        return None, "❌ روی عکس یا متن ریپلای کنید."
    if reply.document and not (reply.document.mime_type or "").startswith("image/"):
        return None, "❌ فقط فایل تصویری قابل تبدیل است."

    try:
        path = await client.download_media(media)
        if not path or not os.path.exists(path):
            return None, "❌ دانلود تصویر ناموفق بود."
    except Exception as e:
        return None, f"❌ دانلود: {e}"

    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        if w >= h:
            new_w, new_h = 512, max(1, int(h * 512 / w))
        else:
            new_h, new_w = 512, max(1, int(w * 512 / h))
        try:
            resample = Image.Resampling.LANCZOS
        except Exception:
            resample = getattr(Image, "LANCZOS", Image.BICUBIC)
        img = img.resize((new_w, new_h), resample)
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        canvas.paste(img, ((512 - new_w) // 2, (512 - new_h) // 2), img)
        canvas.save(out, "WEBP", quality=90)
        try:
            os.remove(path)
        except Exception:
            pass
        return out, None
    except Exception as e:
        return None, f"❌ تبدیل استیکر: {e}"



# ===================== انیمیشن‌های ایموجی =====================
EMOJI_ANIMATIONS = {
    "قلب": {
        "frames": [
            "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💖",
            "💗", "💘", "💝",
        ],
        "finale": "❤️🧡💛\\n💚💙💜\\n💖💗💘",
        "interval": 2,
    },
    "برف": {
        "frames": [
            "❄️", "🌨️", "⛄", "🏔️", "🌬️", "☁️", "🧊", "🎄", "🦌", "🎿",
        ],
        "finale": "✨❄️ finish ❄️✨",
        "interval": 2,
    },
    "زندگی انسان": {
        "frames": [
            "💑",
            "🤰",
            "👨‍👩‍👧",
            "👶",
            "🧒",
            "🚲",
            "💼",
            "👴",
            "🪦",
        ],
        "finale": "🪦 RIP",
        "interval": 2,
    },
    "آتش": {
        "frames": ["🕯️", "🔥", "💥", "🔥", "🌋", "🔥", "💫", "🔥"],
        "finale": "🔥🔥🔥",
        "interval": 2,
    },
    "ماه": {
        "frames": ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌑"],
        "finale": "🌕✨",
        "interval": 2,
    },
    "ساعت": {
        "frames": ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"],
        "finale": "⏰ finish",
        "interval": 2,
    },
    "هواپیما": {
        "frames": ["✈️", "🛫", "✈️", "🛬", "🌍", "✈️", "🌌"],
        "finale": "✈️🏁",
        "interval": 2,
    },
    "گربه": {
        "frames": ["😺", "😸", "😹", "😻", "😼", "😽", "🙀", "😿", "😾"],
        "finale": "🐱💕",
        "interval": 2,
    },
}


async def run_emoji_animation(client, message, key: str):
    anim = EMOJI_ANIMATIONS.get(key)
    if not anim:
        return
    frames = anim["frames"]
    interval = anim.get("interval", 2)
    finale = anim.get("finale", "finish")
    try:
        await message.edit_text(frames[0])
    except Exception:
        try:
            message = await client.send_message(message.chat.id, frames[0])
        except Exception as e:
            logging.error(f"anim start: {e}")
            return
    for frame in frames[1:]:
        await asyncio.sleep(interval)
        try:
            await message.edit_text(frame)
        except Exception:
            break
    await asyncio.sleep(interval)
    try:
        await message.edit_text(finale)
    except Exception:
        pass




async def _gemini_generate_text(system: str, user: str, max_tokens: int = 1200, temperature: float = 0.85) -> str:
    """متن با Gemini generateContent"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY خالی است")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers={"Content-Type": "application/json"}) as resp:
            raw = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Gemini HTTP {resp.status}: {raw[:400]}")
            js = await resp.json() if resp.content_type and "json" in resp.content_type else None
            if not js:
                import json as _json
                js = _json.loads(raw)
            cands = js.get("candidates") or []
            if not cands:
                raise RuntimeError(f"Gemini empty: {raw[:300]}")
            parts = (((cands[0] or {}).get("content") or {}).get("parts")) or []
            texts = [str(p.get("text") or "").strip() for p in parts if p.get("text")]
            out = "\n".join(t for t in texts if t).strip()
            if not out:
                raise RuntimeError("Gemini متن خالی برگرداند")
            return out



async def _gemini_generate_image_bytes(prompt: str) -> bytes:
    """ساخت تصویر با مدل‌های Gemini — روی 402 سریع قطع می‌شود"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY خالی است")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("پرامپت خالی")
    # پرامپت خیلی صریح تا موضوع اشتباه نشود
    full_prompt = (
        f"Generate one single photorealistic image of exactly this subject: {prompt}.\n"
        f"The main subject MUST be: {prompt}.\n"
        "Rules: match the subject precisely; do not replace the subject with a person "
        "unless the user explicitly asked for a person; no text watermark; high detail."
    )
    last_err = None
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in GEMINI_IMAGE_MODELS:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}"
            )
            body = {
                "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            }
            try:
                async with session.post(
                    url, json=body, headers={"Content-Type": "application/json"}
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 402:
                        raise RuntimeError("Gemini 402 Payment Required — سهمیه/بilled نیست")
                    if resp.status == 429:
                        last_err = f"{model} rate limit"
                        continue
                    if resp.status != 200:
                        last_err = f"{model} HTTP {resp.status}: {raw[:200]}"
                        logging.warning("gemini img: %s", last_err)
                        # 404 model not found → try next
                        continue
                    import json as _json, base64
                    js = _json.loads(raw)
                    for cand in (js.get("candidates") or []):
                        for p in (((cand.get("content") or {}).get("parts")) or []):
                            inline = p.get("inlineData") or p.get("inline_data") or {}
                            data_b64 = inline.get("data")
                            if data_b64:
                                return base64.b64decode(data_b64)
                    last_err = f"{model} no image part"
            except RuntimeError:
                raise
            except Exception as e:
                last_err = f"{model}: {e}"
                logging.warning("gemini img try: %s", e)
    raise RuntimeError(last_err or "ساخت تصویر Gemini ناموفق")


async def _gemini_analyze_image_path(path: str, question: str = None) -> str:
    """تحلیل تصویر با Gemini vision"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY خالی است")
    if not path or not os.path.exists(path):
        raise ValueError("فایل تصویر نیست")
    import base64
    with open(path, "rb") as f:
        data_b64 = base64.b64encode(f.read()).decode("ascii")
    mime = "image/jpeg"
    low = path.lower()
    if low.endswith(".png"):
        mime = "image/png"
    elif low.endswith(".webp"):
        mime = "image/webp"
    elif low.endswith(".gif"):
        mime = "image/gif"
    q = question or (
        "این تصویر را به فارسی روان و دقیق تحلیل کن: "
        "موضوع اصلی، اشیاء، رنگ‌ها، حس و جزئیات مهم. کوتاه و مفید بنویس."
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": q},
                {"inline_data": {"mime_type": mime, "data": data_b64}},
            ],
        }],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500},
    }
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers={"Content-Type": "application/json"}) as resp:
            raw = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Gemini vision HTTP {resp.status}: {raw[:300]}")
            import json as _json
            js = _json.loads(raw)
            parts = ((((js.get("candidates") or [{}])[0]).get("content") or {}).get("parts")) or []
            texts = [str(p.get("text") or "").strip() for p in parts if p.get("text")]
            out = "\n".join(t for t in texts if t).strip()
            if not out:
                raise RuntimeError("پاسخ خالی از Gemini")
            return out[:3500]



async def ai_expand_text(seed: str) -> str:
    """گسترش متن — اول Gemini، بعد DeepSeek"""
    seed = (seed or "").strip()
    if not seed:
        return "❌ متنی برای گسترش وارد نشده."

    system_prompt = (
        "تو نویسنده خلاق فارسی هستی.\n"
        "وظیفه: متن کوتاه کاربر را گسترش بده و داستانی ادامه بده.\n"
        "قوانین:\n"
        "- حتماً همان اسامی، مکان‌ها و موضوع کاربر حفظ شود.\n"
        "- لحن خودمانی و روان باشد؛ رسمی و سازمانی ممنوع.\n"
        "- انگلیسی‌بازی و کلماتی مثل synergy ممنوع.\n"
        "- ۸ تا ۱۴ خط بنویس.\n"
        "- چند ایموجی مرتبط با موضوع داخل متن بگذار.\n"
        "- فقط متن نهایی را برگردان؛ عنوان و توضیح اضافه ننویس."
    )
    user_prompt = f"این متن را گسترش بده و ادامه بده:\n{seed}"

    # 1) Gemini
    if GEMINI_API_KEY:
        try:
            out = await _gemini_generate_text(system_prompt, user_prompt, max_tokens=1400, temperature=0.9)
            if out and len(out) > 20 and not out.startswith("❌"):
                return out
        except Exception as e:
            logging.warning("gemini expand: %s", e)

    # 2) DeepSeek fallback
    if DEEPSEEK_API_KEY:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.9,
            "max_tokens": 1200,
            "stream": False,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 200:
                        import json as _json
                        js = _json.loads(raw)
                        t = js["choices"][0]["message"]["content"]
                        if t and str(t).strip():
                            return str(t).strip()
                    logging.warning("deepseek expand status=%s body=%s", resp.status, raw[:200])
        except Exception as e:
            logging.warning("deepseek expand: %s", e)

    return "❌ سرویس هوش مصنوعی در دسترس نیست. GEMINI_API_KEY یا DEEPSEEK_API_KEY را چک کن."



def _clean_track_name(name: str) -> str:
    if not name:
        return ""
    name = name.replace("_", " ").replace(".", " ")
    # حذف پسوندها و تگ‌های کیفیت
    junk = [
        "mp3", "flac", "wav", "m4a", "128", "320", "256", "kbps", "official",
        "lyrics", "audio", "hq", "lq", "copy", "song", "track", "full",
    ]
    low = name
    for j in junk:
        low = re.sub(rf"(?i)\\b{re.escape(j)}\\b", " ", low)
    low = re.sub(r"\\s+", " ", low).strip(" -_|")
    return low.strip()


def _split_artist_title(raw: str):
    raw = _clean_track_name(raw)
    if not raw:
        return "", ""
    for sep in [" - ", " – ", " — ", " | ", " _ "]:
        if sep in raw:
            a, t = raw.split(sep, 1)
            return a.strip(), t.strip()
    return "", raw


async def fetch_song_lyrics(title: str, artist: str = "") -> str:
    """جستجوی قوی متن آهنگ: LRCLIB + lyrics.ovh + پارس نام فایل"""
    title = _clean_track_name(title or "")
    artist = _clean_track_name(artist or "")

    # اگر title شامل artist - song باشد
    if not artist and title:
        a2, t2 = _split_artist_title(title)
        if t2:
            artist = artist or a2
            title = t2

    if not title:
        return "❌ نام آهنگ مشخص نیست."

    queries = []
    if artist and title:
        queries.append((artist, title))
    queries.append(("", title))
    if artist:
        queries.append((artist, title.split("-")[0].strip()))

    timeout = aiohttp.ClientTimeout(total=25)

    # ---- 1) LRCLIB search ----
    try:
        q = f"{artist} {title}".strip()
        url = f"https://lrclib.net/api/search?q={quote(q)}"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    items = await resp.json()
                    if isinstance(items, list) and items:
                        best = items[0]
                        # plain lyrics
                        lyrics = (best.get("plainLyrics") or best.get("syncedLyrics") or "").strip()
                        if lyrics:
                            ar = best.get("artistName") or artist or ""
                            tr = best.get("trackName") or title
                            # synced has timestamps [00:01.00] — clean if needed
                            if "[" in lyrics and "]" in lyrics and "plainLyrics" not in best:
                                lyrics = re.sub(r"\\[\\d+:\\d+\\.\\d+\\]\\s*", "", lyrics)
                            return f"🎵 {ar} — {tr}\n\n{lyrics[:3800]}"
    except Exception as e:
        logging.warning(f"lrclib search: {e}")

    # ---- 2) LRCLIB get exact ----
    for ar, tr in queries:
        if not tr:
            continue
        try:
            url = (
                "https://lrclib.net/api/get?"
                f"artist_name={quote(ar or 'Unknown')}&track_name={quote(tr)}"
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lyrics = (data.get("plainLyrics") or data.get("syncedLyrics") or "").strip()
                        if lyrics:
                            if "syncedLyrics" in data and not data.get("plainLyrics"):
                                lyrics = re.sub(r"\\[\\d+:\\d+\\.\\d+\\]\\s*", "", lyrics)
                            return f"🎵 {(ar or data.get('artistName') or '')} — {tr}\n\n{lyrics[:3800]}"
        except Exception as e:
            logging.warning(f"lrclib get: {e}")

    # ---- 3) lyrics.ovh ----
    for ar, tr in queries:
        if not ar or not tr:
            continue
        try:
            url = f"https://api.lyrics.ovh/v1/{quote(ar)}/{quote(tr)}"
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lyrics = (data.get("lyrics") or "").strip()
                        if lyrics:
                            return f"🎵 {ar} — {tr}\n\n{lyrics[:3800]}"
        except Exception as e:
            logging.warning(f"lyrics.ovh: {e}")

    # ---- 4) Genius API search (عمومی) ----
    try:
        q = f"{artist} {title}".strip()
        url = f"https://genius.com/api/search?q={quote(q)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hits = (((data or {}).get("response") or {}).get("hits")) or []
                    if hits:
                        result = hits[0].get("result") or {}
                        song_title = result.get("full_title") or title
                        song_url = result.get("url") or ""
                        if song_url:
                            # صفحه آهنگ را برای lyrics scrape کن
                            async with session.get(song_url, headers=headers) as r2:
                                if r2.status == 200:
                                    html = await r2.text()
                                    # استخراج ساده از containers
                                    try:
                                        from bs4 import BeautifulSoup
                                        soup = BeautifulSoup(html, "lxml")
                                        parts = soup.select('div[data-lyrics-container="true"]')
                                        if parts:
                                            lyrics = "\n".join(p.get_text("\n").strip() for p in parts)
                                            lyrics = re.sub(r"\\n{3,}", "\n\n", lyrics).strip()
                                            if lyrics and len(lyrics) > 40:
                                                return f"🎵 {song_title}\n\n{lyrics[:3800]}"
                                    except Exception as e:
                                        logging.warning(f"genius scrape: {e}")
                        return (
                            f"🔎 آهنگ پیدا شد ولی متن مستقیم در دسترس نبود:\n"
                            f"{song_title}\n{song_url}"
                        )
    except Exception as e:
        logging.warning(f"genius: {e}")

    q = quote(f"{artist} {title} lyrics".strip())
    return (
        f"❌ متن آهنگ پیدا نشد برای:\n"
        f"🎵 {artist + ' - ' if artist else ''}{title}\n\n"
        f"جستجو:\n"
        f"https://www.google.com/search?q={q}\n"
        f"https://genius.com/search?q={q}"
    )



async def capture_chat_to_saved(client, chat_id: int, limit: int = 20):
    """ارسال محتوای اخیر چت به Saved Messages (برای چت‌های محافظت‌شده هم تلاش می‌کند)"""
    sent = 0
    errors = 0
    header = f"📸 اسکرین | self MR\\nچت: `{chat_id}`\\nتعداد تلاش: {limit}"
    try:
        await client.send_message("me", header)
    except Exception:
        pass

    messages = []
    async for m in client.get_chat_history(chat_id, limit=limit):
        messages.append(m)
    messages.reverse()  # قدیمی → جدید

    for m in messages:
        try:
            # اول فوروارد
            try:
                await m.forward("me")
                sent += 1
                await asyncio.sleep(0.25)
                continue
            except Exception:
                pass

            # اگر فوروارد نشد (محافظت‌شده): کپی دستی
            caption = m.caption or ""
            text = m.text or ""
            if text:
                await client.send_message("me", text)
                sent += 1
            elif m.photo:
                path = await client.download_media(m)
                if path:
                    await client.send_photo("me", path, caption=caption or None)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            elif m.video:
                path = await client.download_media(m)
                if path:
                    await client.send_video("me", path, caption=caption or None)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            elif m.document:
                path = await client.download_media(m)
                if path:
                    await client.send_document("me", path, caption=caption or None)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            elif m.voice:
                path = await client.download_media(m)
                if path:
                    await client.send_voice("me", path)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            elif m.audio:
                path = await client.download_media(m)
                if path:
                    await client.send_audio("me", path, caption=caption or None)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            elif m.sticker:
                try:
                    await client.send_sticker("me", m.sticker.file_id)
                    sent += 1
                except Exception:
                    path = await client.download_media(m)
                    if path:
                        await client.send_document("me", path)
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        sent += 1
            elif m.animation:
                path = await client.download_media(m)
                if path:
                    await client.send_animation("me", path, caption=caption or None)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    sent += 1
            else:
                errors += 1
            await asyncio.sleep(0.3)
        except Exception as e:
            logging.warning(f"screen copy msg: {e}")
            errors += 1
    return sent, errors


async def _translate_query_for_search(query: str) -> str:
    """اگر فارسی بود به انگلیسی هم برگردان برای دقت بیشتر"""
    q = (query or "").strip()
    if not q:
        return q
    try:
        # اگر حروف فارسی داشت
        if re.search(r"[\u0600-\u06FF]", q):
            try:
                from deep_translator import GoogleTranslator
                en = GoogleTranslator(source="auto", target="en").translate(q)
                if en and en.strip():
                    return en.strip()
            except Exception:
                pass
    except Exception:
        pass
    return q


async def search_web_images(query: str, limit: int = 1):
    """جستجوی ساده و مرتبط — یک تصویر خوب"""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 1), 5))
    en_q = await _translate_query_for_search(query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    }
    found = []

    async def _add(u):
        if not u or not isinstance(u, str):
            return
        u = u.strip()
        if not u.startswith("http"):
            return
        low = u.lower()
        if any(x in low for x in ("favicon", "logo", "sprite", "1x1", "pixel")):
            return
        if u not in found:
            found.append(u)

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        try:
            url = f"https://fa.wikipedia.org/api/rest_v1/page/summary/{quote(query)}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    thumb = (data.get("thumbnail") or {}).get("source")
                    original = (data.get("originalimage") or {}).get("source")
                    await _add(original or thumb)
        except Exception as e:
            logging.warning(f"wiki fa: {e}")
        if len(found) >= limit:
            return found[:limit]
        try:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(en_q)}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    thumb = (data.get("thumbnail") or {}).get("source")
                    original = (data.get("originalimage") or {}).get("source")
                    await _add(original or thumb)
        except Exception as e:
            logging.warning(f"wiki en: {e}")
        if len(found) >= limit:
            return found[:limit]
        try:
            for q in (query, en_q):
                url = f"https://api.duckduckgo.com/?q={quote(q)}&format=json&no_redirect=1&no_html=1"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    img = data.get("Image") or ""
                    if img:
                        if img.startswith("/"):
                            img = "https://duckduckgo.com" + img
                        await _add(img)
                    if len(found) >= limit:
                        return found[:limit]
        except Exception as e:
            logging.warning(f"ddg: {e}")
        try:
            from bs4 import BeautifulSoup
            for q in (en_q, query):
                search_url = f"https://www.bing.com/images/search?q={quote(q)}&form=HDRSC2"
                async with session.get(search_url) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                soup = BeautifulSoup(html, "lxml")
                for a in soup.select("a.iusc"):
                    m = a.get("m")
                    if not m:
                        continue
                    try:
                        data = json.loads(m)
                    except Exception:
                        continue
                    await _add(data.get("murl") or "")
                    if len(found) >= limit:
                        return found[:limit]
        except Exception as e:
            logging.warning(f"bing: {e}")
    return found[:limit]


async def download_image_bytes(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                if len(data) < 1000:
                    return None
                ext = "jpg"
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "png" in ctype:
                    ext = "png"
                elif "webp" in ctype:
                    ext = "webp"
                path = f"{DOWNLOAD_PATH}/search_{int(time.time())}_{random.randint(100,999)}.{ext}"
                with open(path, "wb") as f:
                    f.write(data)
                return path
    except Exception as e:
        logging.warning(f"download_image: {e}")
        return None



def _sender_get(user_id: int, chat_id: int) -> dict:
    uid = int(user_id)
    cid = str(int(chat_id))
    if uid not in SENDER_CONFIG:
        SENDER_CONFIG[uid] = {}
    cfg = SENDER_CONFIG[uid].get(cid) or {
        "enabled": False,
        "mode": "copy",
        "banner_chat_id": None,
        "banner_msg_id": None,
        "delay": 60,
        "hourly_limit": 100,
        "sent_hour": 0,
        "hour_ts": 0,
    }
    SENDER_CONFIG[uid][cid] = cfg
    return cfg


def _sender_set(user_id: int, chat_id: int, **kwargs):
    cfg = _sender_get(user_id, chat_id)
    cfg.update(kwargs)
    SENDER_CONFIG[int(user_id)][str(int(chat_id))] = cfg
    try:
        persist_all_user_settings(user_id)
    except Exception:
        pass
    return cfg


async def _sender_send_once(client, user_id: int, chat_id: int, cfg: dict) -> bool:
    """یک بار ارسال بنر — کانال مرده را از لیست حذف می‌کند"""
    bchat = cfg.get("banner_chat_id")
    bmsg = cfg.get("banner_msg_id")
    if not bchat or not bmsg:
        return False
    mode = (cfg.get("mode") or "copy").lower()
    fatal_keys = (
        "CHANNEL_INVALID", "PEER_ID_INVALID", "CHANNEL_PRIVATE",
        "CHAT_ID_INVALID", "USER_BANNED", "CHAT_WRITE_FORBIDDEN",
    )

    def _mark_dead(err: str):
        cfg["enabled"] = False
        cfg["fail_count"] = 99
        cfg["last_error"] = (err or "")[:120]
        cfg["dead"] = True
        try:
            uid = int(user_id)
            cid = str(int(chat_id))
            if uid in SENDER_CONFIG and cid in SENDER_CONFIG[uid]:
                # حذف کامل از لیست تا دیگر تلاش نشود
                SENDER_CONFIG[uid].pop(cid, None)
            persist_all_user_settings(user_id)
        except Exception:
            pass
        logging.info("sender removed dead chat uid=%s chat=%s", user_id, chat_id)

    try:
        if mode == "forward":
            await client.forward_messages(int(chat_id), int(bchat), int(bmsg))
        else:
            await client.copy_message(int(chat_id), int(bchat), int(bmsg))
        cfg["fail_count"] = 0
        cfg["dead"] = False
        return True
    except Exception as e:
        err = str(e)
        if any(x in err for x in fatal_keys):
            _mark_dead(err)
            return False
        if "PERSISTENT_TIMESTAMP_OUTDATED" in err or "RPC_CALL_FAIL" in err:
            # خطای موقت تلگرام — لاگ کم‌صدا
            logging.debug("sender temp tg issue uid=%s chat=%s", user_id, chat_id)
            return False
        cfg["fail_count"] = int(cfg.get("fail_count") or 0) + 1
        cfg["last_error"] = err[:120]
        if cfg["fail_count"] >= 5:
            _mark_dead(err)
        else:
            logging.debug("sender soft fail uid=%s chat=%s: %s", user_id, chat_id, err[:100])
        return False


async def sender_loop_task(client: Client, user_id: int):
    """حلقه سندر — برای هر گروهی که روشن است، بنر را با تاخیر و سقف ساعتی می‌فرستد"""
    await asyncio.sleep(12)
    while True:
        try:
            
            if not is_self_on(user_id):
                await asyncio.sleep(10)
                continue
            if user_id not in ACTIVE_BOTS:
                break
            configs = SENDER_CONFIG.get(user_id) or {}
            if not configs:
                await asyncio.sleep(8)
                continue
            # پاکسازی چت‌های مرده
            for _cid, _cfg in list(configs.items()):
                if not _cfg or _cfg.get("dead") or int(_cfg.get("fail_count") or 0) >= 99:
                    configs.pop(_cid, None)
            SENDER_CONFIG[user_id] = configs
            now = int(time.time())
            for cid_str, cfg in list(configs.items()):
                if not cfg or not cfg.get("enabled"):
                    continue
                if not cfg.get("banner_chat_id") or not cfg.get("banner_msg_id"):
                    continue
                # ریست شمارنده ساعتی
                hour_ts = int(cfg.get("hour_ts") or 0)
                if now - hour_ts >= 3600:
                    cfg["hour_ts"] = now
                    cfg["sent_hour"] = 0
                limit = max(50, min(200, int(cfg.get("hourly_limit") or 100)))
                sent = int(cfg.get("sent_hour") or 0)
                if sent >= limit:
                    continue
                delay = max(5, min(3600, int(cfg.get("delay") or 60)))
                last = int(cfg.get("last_send") or 0)
                if now - last < delay:
                    continue
                try:
                    target_id = int(cid_str)
                except Exception:
                    continue
                ok = await _sender_send_once(client, user_id, target_id, cfg)
                if ok:
                    cfg["sent_hour"] = sent + 1
                    cfg["last_send"] = now
                    cfg["fail_count"] = 0
                    SENDER_CONFIG[user_id][cid_str] = cfg
                else:
                    # بعد از چند شکست پشت‌سرهم، سندر این چت را خاموش کن
                    fails = int(cfg.get("fail_count") or 0)
                    if fails >= 3:
                        cfg["enabled"] = False
                        SENDER_CONFIG[user_id][cid_str] = cfg
                        logging.warning(
                            "sender auto-disabled uid=%s chat=%s fails=%s err=%s",
                            user_id, cid_str, fails, (cfg.get("last_error") or "")[:80],
                        )
                        try:
                            persist_all_user_settings(user_id)
                        except Exception:
                            pass
                    else:
                        SENDER_CONFIG[user_id][cid_str] = cfg
                await asyncio.sleep(1.5)
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"sender_loop_task: {e}")
            await asyncio.sleep(10)



async def mass_forward_banner(client: Client, user_id: int, banner_chat_id: int, banner_msg_id: int):
    """فوروارد/کپی بنر به همه پیوی + گپ + کانال با آمار تفکیکی"""
    state = {
        "running": True,
        "banner_chat_id": banner_chat_id,
        "banner_msg_id": banner_msg_id,
        "sent": 0,
        "failed": 0,
        "total": 0,
        "sent_pv": 0,
        "sent_group": 0,
        "sent_channel": 0,
        "fail_pv": 0,
        "fail_group": 0,
        "fail_channel": 0,
    }
    SENDER_MASS[user_id] = state

    def _kind(chat) -> str:
        try:
            from pyrogram.enums import ChatType
            t = chat.type
            if t == ChatType.PRIVATE:
                return "pv"
            if t in (ChatType.GROUP, ChatType.SUPERGROUP):
                return "group"
            if t == ChatType.CHANNEL:
                return "channel"
        except Exception:
            pass
        s = str(getattr(chat, "type", "")).lower()
        if "private" in s:
            return "pv"
        if "channel" in s:
            return "channel"
        return "group"

    targets = []
    try:
        async for d in client.get_dialogs():
            chat = getattr(d, "chat", None)
            if not chat:
                continue
            kind = _kind(chat)
            if kind == "pv" and getattr(chat, "is_bot", False):
                continue
            # خودِ چت بنر را هم می‌توان فرستاد؛ مشکلی نیست
            targets.append((int(chat.id), kind, getattr(chat, "title", None) or getattr(chat, "first_name", "") or str(chat.id)))
    except Exception as e:
        logging.exception("mass_forward get_dialogs")
        state["running"] = False
        SENDER_MASS[user_id] = state
        try:
            await client.send_message("me", f"❌ نتوانستم لیست چت‌ها را بگیرم:\n`{e}`")
        except Exception:
            pass
        return

    state["total"] = len(targets)
    SENDER_MASS[user_id] = state
    n_pv = sum(1 for _, k, _ in targets if k == "pv")
    n_g = sum(1 for _, k, _ in targets if k == "group")
    n_c = sum(1 for _, k, _ in targets if k == "channel")

    try:
        await client.send_message(
            "me",
            f"📣 سندر فور شروع | self MR\n\n"
            f"🎯 کل چت پیدا شده: {len(targets)}\n"
            f"👤 پیوی: {n_pv}\n"
            f"👥 گپ: {n_g}\n"
            f"📢 کانال: {n_c}\n\n"
            f"در حال ارسال...\n"
            f"توقف: `.سندر فور خاموش`",
        )
    except Exception:
        pass

    if not targets:
        state["running"] = False
        SENDER_MASS[user_id] = state
        try:
            await client.send_message("me", "❌ هیچ چتی برای ارسال پیدا نشد.")
        except Exception:
            pass
        return

    for i, (chat_id, kind, title) in enumerate(targets):
        st = SENDER_MASS.get(user_id) or {}
        if not st.get("running"):
            break

        ok = False
        # 1) فوروارد
        try:
            await client.forward_messages(chat_id, int(banner_chat_id), int(banner_msg_id))
            ok = True
        except Exception as e1:
            # 2) کپی اگر فوروارد ممنوع بود
            try:
                await client.copy_message(chat_id, int(banner_chat_id), int(banner_msg_id))
                ok = True
            except Exception as e2:
                logging.warning(f"mass send fail {chat_id} ({kind}/{title}): {e1} | {e2}")
                ok = False

        if ok:
            st["sent"] = int(st.get("sent") or 0) + 1
            st[f"sent_{kind}"] = int(st.get(f"sent_{kind}") or 0) + 1
        else:
            st["failed"] = int(st.get("failed") or 0) + 1
            st[f"fail_{kind}"] = int(st.get(f"fail_{kind}") or 0) + 1
        SENDER_MASS[user_id] = st

        # ضد فلود
        await asyncio.sleep(random.uniform(2.0, 4.5))
        if (i + 1) % 20 == 0:
            try:
                await client.send_message(
                    "me",
                    f"📣 پیشرفت: {i+1}/{len(targets)}\n"
                    f"✅ {st.get('sent',0)} | ❌ {st.get('failed',0)}\n"
                    f"👤 {st.get('sent_pv',0)} | 👥 {st.get('sent_group',0)} | 📢 {st.get('sent_channel',0)}",
                )
            except Exception:
                pass

    st = SENDER_MASS.get(user_id) or {}
    st["running"] = False
    SENDER_MASS[user_id] = st
    try:
        await client.send_message(
            "me",
            f"🏁 سندر فور تمام شد | self MR\n\n"
            f"✅ موفق کل: {st.get('sent', 0)}\n"
            f"❌ ناموفق کل: {st.get('failed', 0)}\n"
            f"📋 هدف کل: {st.get('total', 0)}\n\n"
            f"👤 پیوی ارسال‌شده: {st.get('sent_pv', 0)}\n"
            f"👥 گپ ارسال‌شده: {st.get('sent_group', 0)}\n"
            f"📢 کانال ارسال‌شده: {st.get('sent_channel', 0)}\n\n"
            f"👤 پیوی ناموفق: {st.get('fail_pv', 0)}\n"
            f"👥 گپ ناموفق: {st.get('fail_group', 0)}\n"
            f"📢 کانال ناموفق: {st.get('fail_channel', 0)}",
        )
    except Exception:
        pass



async def meow_loop_task(client: Client, user_id: int):
    """هر ۵ دقیقه در گپ‌هایی که .میو روشن شده، «میو» می‌فرستد — فقط همان گپ‌ها + ضدفلود"""
    await asyncio.sleep(15)
    while True:
        try:
            
            if not is_self_on(user_id):
                await asyncio.sleep(10)
                continue
            if user_id not in ACTIVE_BOTS:
                break
            raw = MEOW_CHATS.get(user_id) or set()
            try:
                chats = list(set(int(x) for x in raw))
            except Exception:
                chats = []
            MEOW_CHATS[user_id] = set(chats)
            if not chats:
                await asyncio.sleep(20)
                continue
            for chat_id in chats:
                if user_id not in ACTIVE_BOTS:
                    break
                if int(chat_id) not in (MEOW_CHATS.get(user_id) or set()):
                    continue
                try:
                    await client.send_message(int(chat_id), "میو")
                except Exception as e:
                    logging.warning(f"meow send uid={user_id} chat={chat_id}: {e}")
                # فاصله کوتاه بین چند گپ (اگر چند گپ روشن باشد)
                await asyncio.sleep(random.uniform(2.0, 5.0))
            # ۵ دقیقه + کمی جیتتر ضد ریپورت
            await asyncio.sleep(300 + random.uniform(5, 40))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"meow_loop_task: {e}")
            await asyncio.sleep(30)




def _make_fancy_fonts(text: str) -> str:
    """فونت‌های یونیکد زیاد و قابل کپی"""
    text = str(text or "")[:80]
    if not text:
        return "\n".join(f"`{s}`" for s in out)

    def tr(s, table):
        return "".join(table.get(ch, ch) for ch in s)

    def az(base_a, base_A=None, digits=None):
        t = {}
        for i in range(26):
            t[chr(ord("a") + i)] = chr(base_a + i)
            t[chr(ord("A") + i)] = chr((base_A if base_A is not None else base_a) + i)
        if digits is not None:
            for i in range(10):
                t[chr(ord("0") + i)] = chr(digits + i)
        return t

    styles = []
    # Mathematical styles
    styles.append(tr(text, az(0x1D41A, 0x1D400, 0x1D7CE)))          # Bold
    styles.append(tr(text, az(0x1D44E, 0x1D434)))                     # Italic
    styles.append(tr(text, az(0x1D482, 0x1D468)))                     # Bold Italic
    styles.append(tr(text, az(0x1D4B6, 0x1D49C)))                     # Script
    styles.append(tr(text, az(0x1D4EA, 0x1D4D0)))                     # Bold Script
    styles.append(tr(text, az(0x1D51E, 0x1D504)))                     # Fraktur
    styles.append(tr(text, az(0x1D586, 0x1D56C)))                     # Bold Fraktur
    styles.append(tr(text, az(0x1D552, 0x1D538, 0x1D7D8)))            # Double Struck
    styles.append(tr(text, az(0x1D5BA, 0x1D5A0, 0x1D7E2)))            # Sans
    styles.append(tr(text, az(0x1D5EE, 0x1D5D4, 0x1D7EC)))            # Sans Bold
    styles.append(tr(text, az(0x1D622, 0x1D608)))                     # Sans Italic
    styles.append(tr(text, az(0x1D656, 0x1D63C)))                     # Sans Bold Italic
    styles.append(tr(text, az(0x1D68A, 0x1D670, 0x1D7F6)))            # Mono
    # Fullwidth
    fw = {}
    for i in range(26):
        fw[chr(ord("a") + i)] = chr(0xFF41 + i)
        fw[chr(ord("A") + i)] = chr(0xFF21 + i)
    for i in range(10):
        fw[chr(ord("0") + i)] = chr(0xFF10 + i)
    styles.append(tr(text, fw))
    # Circled
    bub = {}
    for i in range(26):
        bub[chr(ord("a") + i)] = chr(0x24D0 + i)
        bub[chr(ord("A") + i)] = chr(0x24B6 + i)
    for i in range(10):
        bub[chr(ord("0") + i)] = (chr(0x24EA) if i == 0 else chr(0x2460 + i - 1))
    styles.append(tr(text, bub))
    # Negative circled (caps)
    neg = {}
    for i in range(26):
        neg[chr(ord("A") + i)] = chr(0x1F150 + i)
        neg[chr(ord("a") + i)] = chr(0x1F150 + i)
    styles.append(tr(text, neg))
    # Squared
    sq = {}
    for i in range(26):
        sq[chr(ord("A") + i)] = chr(0x1F130 + i)
        sq[chr(ord("a") + i)] = chr(0x1F130 + i)
    styles.append(tr(text, sq))
    # Regional indicator (flag letters)
    reg = {}
    for i in range(26):
        reg[chr(ord("a") + i)] = chr(0x1F1E6 + i)
        reg[chr(ord("A") + i)] = chr(0x1F1E6 + i)
    styles.append(tr(text, reg))
    # Small caps
    small = {
        "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ғ", "g": "ɢ", "h": "ʜ",
        "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ", "p": "ᴘ",
        "q": "ǫ", "r": "ʀ", "s": "s", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x",
        "y": "ʏ", "z": "ᴢ",
    }
    styles.append("".join(small.get(ch.lower(), ch) for ch in text))
    # Parenthesized
    par = {}
    for i in range(26):
        par[chr(ord("a") + i)] = chr(0x249C + i)
    styles.append(tr(text.lower(), par))
    # Upside down
    flip_map = {
        "a":"ɐ","b":"q","c":"ɔ","d":"p","e":"ǝ","f":"ɟ","g":"ƃ","h":"ɥ","i":"ᴉ","j":"ɾ",
        "k":"ʞ","l":"l","m":"ɯ","n":"u","o":"o","p":"d","q":"b","r":"ɹ","s":"s","t":"ʇ",
        "u":"n","v":"ʌ","w":"ʍ","x":"x","y":"ʎ","z":"z",
        "A":"∀","B":"q","C":"Ɔ","D":"p","E":"Ǝ","F":"Ⅎ","G":"פ","H":"H","I":"I","J":"ſ",
        "K":"ʞ","L":"˥","M":"W","N":"N","O":"O","P":"Ԁ","Q":"Q","R":"ɹ","S":"S","T":"┴",
        "U":"∩","V":"Λ","W":"M","X":"X","Y":"⅄","Z":"Z",
        "1":"Ɩ","2":"ᄅ","3":"Ɛ","4":"ㄣ","5":"ϛ","6":"9","7":"ㄥ","8":"8","9":"6","0":"0",
    }
    styles.append("".join(flip_map.get(ch, ch) for ch in text)[::-1])
    # Combining styles
    styles.append("".join(ch + "\u0336" for ch in text))  # strike
    styles.append("".join(ch + "\u0332" for ch in text))  # underline
    styles.append("".join(ch + "\u0301" for ch in text))  # acute
    styles.append("".join(ch + "\u0308" for ch in text))  # diaeresis
    styles.append("".join(ch + "\u0330" for ch in text))  # tilde below
    styles.append("".join(ch + "\u030a" for ch in text))  # ring
    styles.append("".join(ch + "\u033f" for ch in text))  # double overline
    # Spaced variants
    styles.append(" ".join(list(text)))
    styles.append("・".join(list(text)))
    styles.append("✧".join(list(text)))
    styles.append("★".join(list(text)))
    # Bracketed
    styles.append("".join(f"[{ch}]" for ch in text))
    styles.append("".join(f"「{ch}」" for ch in text))
    styles.append("".join(f"【{ch}】" for ch in text))
    # Reverse
    styles.append(text[::-1])
    # Upper / lower
    styles.append(text.upper())
    styles.append(text.lower())
    # Slash / backslash
    styles.append("/".join(list(text)))
    styles.append("\\".join(list(text)))
    # Dot middle
    styles.append("·".join(list(text)))
    # Underline spaces
    styles.append("_".join(list(text)))
    # Greek-ish lookalike
    greek_map = {
        "A":"Α","B":"Β","E":"Ε","H":"Η","I":"Ι","K":"Κ","M":"Μ","N":"Ν","O":"Ο","P":"Ρ",
        "T":"Τ","X":"Χ","Y":"Υ",
        "a":"α","b":"β","e":"ε","h":"η","i":"ι","k":"κ","m":"μ","n":"ν","o":"ο","p":"ρ",
        "t":"τ","y":"υ",
    }
    styles.append("".join(greek_map.get(ch, ch) for ch in text))
    # Weird lookalike
    weird_map = {
        "A":"Д","B":"В","C":"С","E":"Е","H":"Н","I":"І","J":"Ј","K":"К","L":"L","M":"М",
        "N":"И","O":"О","P":"Р","R":"Я","S":"Ѕ","T":"Т","X":"Х","Y":"У",
        "a":"д","c":"с","e":"е","h":"һ","i":"і","j":"ј","k":"к","m":"м","n":"и","o":"о",
        "p":"р","r":"я","s":"ѕ","t":"т","x":"х","y":"у",
    }
    styles.append("".join(weird_map.get(ch, ch) for ch in text))
    # Superscript / subscript digits+few letters
    sup_map = {
        "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
        "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ",
        "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
        "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
        "A": "ᴬ", "B": "ᴮ", "D": "ᴰ", "E": "ᴱ", "G": "ᴳ", "H": "ᴴ", "I": "ᴵ", "J": "ᴶ", "K": "ᴷ", "L": "ᴸ",
        "M": "ᴹ", "N": "ᴺ", "O": "ᴼ", "P": "ᴾ", "R": "ᴿ", "T": "ᵀ", "U": "ᵁ", "V": "ⱽ", "W": "ᵂ",
    }
    styles.append("".join(sup_map.get(ch, ch) for ch in text))
    sub_map = {
        "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
        "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ",
        "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
    }
    styles.append("".join(sub_map.get(ch, ch) for ch in text))

    # یکتا کن و خالی‌ها را بردار
    seen = set()
    out = []
    for s in styles:
        s = str(s)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)

    # هر فونت داخل کد برای کپی راحت
    return "\n".join(f"`{s}`" for s in out)





# پالت رنگ قاب پروفایل
PROFILE_FRAME_COLORS = {
    "طلایی": [(255, 215, 0, 255), (255, 193, 7, 255), (218, 165, 32, 255), (255, 235, 150, 255), (184, 134, 11, 255)],
    "آبی": [(30, 144, 255, 255), (0, 191, 255, 255), (70, 130, 180, 255), (135, 206, 250, 255), (25, 25, 112, 255)],
    "قرمز": [(220, 20, 60, 255), (255, 69, 0, 255), (178, 34, 34, 255), (255, 99, 71, 255), (139, 0, 0, 255)],
    "سبز": [(50, 205, 50, 255), (0, 255, 127, 255), (34, 139, 34, 255), (144, 238, 144, 255), (0, 100, 0, 255)],
    "بنفش": [(186, 85, 211, 255), (138, 43, 226, 255), (147, 112, 219, 255), (221, 160, 221, 255), (75, 0, 130, 255)],
    "صورتی": [(255, 105, 180, 255), (255, 20, 147, 255), (255, 182, 193, 255), (219, 112, 147, 255), (199, 21, 133, 255)],
    "مشکی": [(40, 40, 40, 255), (80, 80, 80, 255), (20, 20, 20, 255), (120, 120, 120, 255), (0, 0, 0, 255)],
    "سفید": [(255, 255, 255, 255), (240, 240, 240, 255), (220, 220, 220, 255), (200, 200, 200, 255), (180, 180, 180, 255)],
    "نارنجی": [(255, 140, 0, 255), (255, 165, 0, 255), (255, 120, 0, 255), (255, 200, 100, 255), (210, 105, 30, 255)],
    "فیروزه‌ای": [(0, 206, 209, 255), (64, 224, 208, 255), (0, 139, 139, 255), (72, 209, 204, 255), (0, 128, 128, 255)],
    "نقره‌ای": [(192, 192, 192, 255), (211, 211, 211, 255), (169, 169, 169, 255), (230, 230, 230, 255), (128, 128, 128, 255)],
    "رنگین‌کمان": [(255, 0, 0, 255), (255, 165, 0, 255), (255, 255, 0, 255), (0, 255, 0, 255), (0, 128, 255, 255)],
}


async def apply_profile_frame(client, user_id: int, color_name: str = "طلایی") -> str:
    """دانلود پروفایل + قاب رنگی دایره‌ای → مسیر فایل خروجی"""
    from PIL import Image, ImageDraw, ImageFilter
    import math
    import tempfile
    size = 640
    color_name = (color_name or "طلایی").strip()
    palette = PROFILE_FRAME_COLORS.get(color_name) or PROFILE_FRAME_COLORS["طلایی"]
    bg = (20, 16, 8, 255)
    if color_name in ("آبی", "فیروزه‌ای"):
        bg = (8, 16, 28, 255)
    elif color_name in ("قرمز", "صورتی", "نارنجی"):
        bg = (28, 10, 12, 255)
    elif color_name in ("سبز",):
        bg = (8, 22, 12, 255)
    elif color_name in ("بنفش",):
        bg = (18, 8, 28, 255)
    elif color_name in ("مشکی", "نقره‌ای", "سفید"):
        bg = (12, 12, 12, 255)

    photos = []
    async for p in client.get_chat_photos("me", limit=1):
        photos.append(p)
    if not photos:
        raise RuntimeError("عکس پروفایل ندارید")

    tmp_dir = tempfile.gettempdir()
    tmp_in = os.path.join(tmp_dir, f"profile_in_{user_id}_{int(time.time())}.jpg")
    tmp_out = os.path.join(tmp_dir, f"profile_frame_{color_name}_{user_id}_{int(time.time())}.jpg")

    downloaded = await client.download_media(photos[0], file_name=tmp_in)
    if not downloaded:
        try:
            downloaded = await client.download_media(photos[0])
        except Exception:
            downloaded = None
    if not downloaded or not os.path.exists(str(downloaded)):
        raise RuntimeError("دانلود عکس پروفایل ناموفق بود")
    tmp_in = str(downloaded)

    img = Image.open(tmp_in).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    pad = 48
    md.ellipse((pad, pad, size - pad, size - pad), fill=255)
    circle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    circle.paste(img, (0, 0))
    circle.putalpha(mask)

    canvas = Image.new("RGBA", (size, size), bg)
    canvas.paste(circle, (0, 0), circle)
    draw = ImageDraw.Draw(canvas)
    cx = cy = size // 2
    for i, col in enumerate(palette):
        r0 = size // 2 - 8 - i * 5
        draw.ellipse((cx - r0, cy - r0, cx + r0, cy + r0), outline=col, width=3)
    outer = size // 2 - 12
    for k in range(24):
        ang = (k / 24.0) * 2 * math.pi
        x = cx + int(outer * math.cos(ang))
        y = cy + int(outer * math.sin(ang))
        rr = 5 if k % 3 == 0 else 3
        draw.ellipse((x - rr, y - rr, x + rr, y + rr), fill=palette[k % len(palette)])
    glow = canvas.filter(ImageFilter.GaussianBlur(2))
    canvas = Image.blend(canvas, glow, 0.25)
    out = canvas.convert("RGB")
    out.save(tmp_out, "JPEG", quality=95)
    try:
        if os.path.exists(tmp_in) and tmp_in != tmp_out:
            os.remove(tmp_in)
    except Exception:
        pass
    if not os.path.exists(tmp_out):
        raise RuntimeError("ذخیره تصویر خروجی ناموفق بود")
    return tmp_out


async def apply_golden_profile_frame(client, user_id: int) -> str:
    """سازگاری با نام قبلی"""
    return await apply_profile_frame(client, user_id, "طلایی")




# =============================================
# ⭐ عکس استارزی — باز کردن مدیای پولی با Stars رأس ساعت
# =============================================

def _parse_starzy_time(s: str):
    """HH:MM یا H:MM → (hour, minute) یا None"""
    s = (s or "").strip().replace(".", ":").replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h < 0 or h > 23 or mi < 0 or mi > 59:
        return None
    return h, mi


def _parse_telegram_msg_link(link: str):
    """
    لینک‌های پشتیبانی‌شده:
    https://t.me/c/1234567890/42
    https://t.me/username/42
    https://t.me/username/42?single
    """
    link = (link or "").strip()
    if not link:
        return None
    link = link.split()[0]
    m = re.search(
        r"(?:https?://)?(?:www\.)?t\.me/(?:c/(\d+)/(\d+)|([A-Za-z0-9_]+)/(\d+))",
        link,
    )
    if not m:
        return None
    if m.group(1) and m.group(2):
        # private channel/supergroup
        chat_id = int("-100" + m.group(1))
        msg_id = int(m.group(2))
        return chat_id, msg_id
    if m.group(3) and m.group(4):
        return m.group(3), int(m.group(4))
    return None




async def _starzy_get_stars_amount(client, chat_id, msg_id) -> int:
    """خواندن هزینه Stars از پیام"""
    msg = await client.get_messages(chat_id, msg_id)
    if not msg:
        raise ValueError("پیام پیدا نشد")
    for attr in ("stars_amount", "star_count"):
        val = getattr(msg, attr, None)
        if isinstance(val, (int, float)) and int(val) > 0:
            return int(val)
    media = getattr(msg, "media", None)
    if media is not None:
        for attr in ("stars_amount", "star_count"):
            val = getattr(media, attr, None)
            if isinstance(val, (int, float)) and int(val) > 0:
                return int(val)
        paid = getattr(media, "paid_media", None) or getattr(msg, "paid_media", None)
        if paid is not None:
            for attr in ("stars_amount", "star_count", "star_count_amount"):
                val = getattr(paid, attr, None)
                if isinstance(val, (int, float)) and int(val) > 0:
                    return int(val)
    raw_msg = getattr(msg, "_raw", None)
    if raw_msg is not None:
        med = getattr(raw_msg, "media", None)
        if med is not None:
            amt = getattr(med, "stars_amount", None)
            if isinstance(amt, (int, float)) and int(amt) > 0:
                return int(amt)
    return 0


async def _starzy_save_to_me(client, chat_id, msg_id, caption="⭐ عکس استارزی"):
    """کپی/دانلود مدیا به Saved Messages"""
    try:
        await client.copy_message("me", chat_id, msg_id)
        return True
    except Exception as e1:
        logging.warning("starzy copy: %s", e1)
    try:
        msg = await client.get_messages(chat_id, msg_id)
        path = await client.download_media(msg)
        if path:
            if str(path).lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
                await client.send_video("me", path, caption=caption)
            elif str(path).lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                await client.send_photo("me", path, caption=caption)
            else:
                await client.send_document("me", path, caption=caption)
            try:
                os.remove(path)
            except Exception:
                pass
            return True
    except Exception as e2:
        logging.warning("starzy download: %s", e2)
    return False


async def _starzy_pay_and_unlock(client, chat_id, msg_id) -> tuple:
    """
    باز کردن مدیای استارزی با چند مسیر (kurigram/pyrofork + raw).
    Returns: (ok, stars, info)
    """
    from pyrogram import raw

    msg_id = int(msg_id)
    stars = 0
    try:
        stars = int(await _starzy_get_stars_amount(client, chat_id, msg_id) or 0)
    except Exception as e:
        logging.warning("starzy amount: %s", e)

    # پیام را بگیر
    try:
        msg = await client.get_messages(chat_id, msg_id)
    except Exception as e:
        raise ValueError(f"خواندن پیام ممکن نشد: {e}")
    if not msg:
        raise ValueError("پیام پیدا نشد — لینک یا دسترسی را چک کن")

    # ----- مسیر A: message.pay() (kurigram) -----
    if hasattr(msg, "pay") and callable(msg.pay):
        try:
            await msg.pay()
            await asyncio.sleep(1.0)
            await _starzy_save_to_me(client, chat_id, msg_id, f"⭐ استارزی ({stars} Stars)")
            return True, stars, "با message.pay باز شد"
        except Exception as e:
            err = str(e)
            logging.warning("starzy msg.pay: %s", e)
            if "MEDIA_ALREADY_PAID" in err or "ALREADY" in err.upper():
                await _starzy_save_to_me(client, chat_id, msg_id)
                return True, stars, "قبلاً پرداخت شده بود"
            if "BALANCE_TOO_LOW" in err:
                return False, stars, f"موجودی Stars کم است (حدود {stars})"

    # ----- مسیر B: get_payment_form + send_payment_form (high-level) -----
    try:
        form = None
        invoice_obj = None
        # B1: client.get_payment_form(chat_id=..., message_id=...)
        if hasattr(client, "get_payment_form"):
            try:
                form = await client.get_payment_form(chat_id=chat_id, message_id=msg_id)
            except TypeError:
                try:
                    # بعضی فورک‌ها invoice می‌گیرند
                    inv_cls = None
                    try:
                        from pyrogram.types import InputInvoiceMessage as IIM
                        inv_cls = IIM
                    except Exception:
                        try:
                            from pyrogram import types as _pt
                            inv_cls = getattr(_pt, "InputInvoiceMessage", None)
                        except Exception:
                            inv_cls = None
                    if inv_cls is not None:
                        try:
                            invoice_obj = inv_cls(chat_id=chat_id, message_id=msg_id)
                        except TypeError:
                            invoice_obj = inv_cls(chat_id, msg_id)
                        form = await client.get_payment_form(invoice_obj)
                except Exception as e:
                    logging.warning("starzy get_payment_form alt: %s", e)
            except Exception as e:
                logging.warning("starzy get_payment_form: %s", e)
                if "MEDIA_ALREADY_PAID" in str(e):
                    await _starzy_save_to_me(client, chat_id, msg_id)
                    return True, stars, "قبلاً پرداخت شده بود"
                if "BALANCE_TOO_LOW" in str(e):
                    return False, stars, f"موجودی Stars کم است (حدود {stars})"

        if form is not None and hasattr(client, "send_payment_form"):
            # مبلغ از فرم
            try:
                for attr in ("total_amount", "stars_amount", "star_count"):
                    v = getattr(form, attr, None)
                    if isinstance(v, (int, float)) and int(v) > 0:
                        stars = int(v)
                        break
                inv = getattr(form, "invoice", None)
                if inv is not None:
                    prices = getattr(inv, "prices", None) or []
                    total = sum(int(getattr(p, "amount", 0) or 0) for p in prices)
                    if total > 0:
                        stars = total
            except Exception:
                pass

            form_id = getattr(form, "id", None) or getattr(form, "form_id", None)
            try:
                if invoice_obj is not None:
                    await client.send_payment_form(payment_form_id=form_id, input_invoice=invoice_obj)
                else:
                    try:
                        await client.send_payment_form(chat_id=chat_id, message_id=msg_id)
                    except TypeError:
                        await client.send_payment_form(payment_form_id=form_id)
                await asyncio.sleep(1.0)
                await _starzy_save_to_me(client, chat_id, msg_id, f"⭐ استارزی ({stars} Stars)")
                return True, stars, "با send_payment_form باز شد"
            except Exception as e:
                logging.warning("starzy send_payment_form: %s", e)
                if "MEDIA_ALREADY_PAID" in str(e):
                    await _starzy_save_to_me(client, chat_id, msg_id)
                    return True, stars, "قبلاً پرداخت شده بود"
                if "BALANCE_TOO_LOW" in str(e):
                    return False, stars, f"موجودی Stars کم است (حدود {stars})"
    except Exception as e:
        logging.warning("starzy high-level path: %s", e)

    # ----- مسیر C: raw API -----
    peer = await client.resolve_peer(chat_id)
    invoice = None
    try:
        invoice = raw.types.InputInvoiceMessage(peer=peer, msg_id=msg_id)
    except Exception as e:
        logging.warning("starzy InputInvoiceMessage: %s", e)

    form = None
    form_id = None
    last_err = None

    if invoice is not None:
        try:
            try:
                form = await client.invoke(
                    raw.functions.payments.GetPaymentForm(invoice=invoice)
                )
            except TypeError:
                form = await client.invoke(
                    raw.functions.payments.GetPaymentForm(
                        invoice=invoice,
                        theme_params=raw.types.DataJSON(data="{}"),
                    )
                )
            form_id = getattr(form, "form_id", None)
        except Exception as e:
            last_err = e
            logging.warning("starzy raw GetPaymentForm(invoice): %s", e)

    if form is None:
        try:
            form = await client.invoke(
                raw.functions.payments.GetPaymentForm(peer=peer, msg_id=msg_id)
            )
            form_id = getattr(form, "form_id", None)
        except Exception as e:
            last_err = e
            logging.warning("starzy raw GetPaymentForm(peer): %s", e)

    if form is not None:
        try:
            inv = getattr(form, "invoice", None)
            prices = getattr(inv, "prices", None) or []
            total = sum(int(getattr(p, "amount", 0) or 0) for p in prices)
            if total > 0:
                stars = total
        except Exception:
            pass

    if form is None or form_id is None:
        err_s = str(last_err or "")
        if "MEDIA_ALREADY_PAID" in err_s:
            await _starzy_save_to_me(client, chat_id, msg_id)
            return True, stars, "قبلاً پرداخت شده بود"
        if "PAYMENT_UNSUPPORTED" in err_s:
            raise ValueError(
                "سشن هنوز Stars را پشتیبانی نمی‌کند.\n"
                "۱) در requirements فقط kurigram باشد (pyrogram را حذف کن)\n"
                "۲) سرور را از نو Deploy کن تا پکیج عوض شود\n"
                "۳) موجودی Stars اکانت را چک کن"
            )
        raise ValueError(f"فرم پرداخت گرفته نشد: {err_s or 'unknown'}")

    paid = False
    pay_err = None

    if hasattr(raw.functions.payments, "SendStarsForm") and invoice is not None:
        try:
            await client.invoke(
                raw.functions.payments.SendStarsForm(form_id=form_id, invoice=invoice)
            )
            paid = True
        except Exception as e:
            pay_err = e
            logging.warning("starzy SendStarsForm: %s", e)

    if not paid:
        try:
            cred = raw.types.InputPaymentCredentials(
                save=False,
                data=raw.types.DataJSON(data="{}"),
            )
            try:
                if invoice is not None:
                    await client.invoke(
                        raw.functions.payments.SendPaymentForm(
                            form_id=form_id,
                            invoice=invoice,
                            credentials=cred,
                        )
                    )
                else:
                    await client.invoke(
                        raw.functions.payments.SendPaymentForm(
                            form_id=form_id,
                            credentials=cred,
                        )
                    )
                paid = True
            except TypeError:
                await client.invoke(
                    raw.functions.payments.SendPaymentForm(
                        form_id=form_id,
                        credentials=cred,
                    )
                )
                paid = True
        except Exception as e:
            pay_err = e
            logging.warning("starzy SendPaymentForm: %s", e)

    if not paid:
        err_s = str(pay_err or "")
        if "MEDIA_ALREADY_PAID" in err_s:
            paid = True
        elif "BALANCE_TOO_LOW" in err_s:
            return False, stars, f"موجودی Stars کم است (حدود {stars})"
        else:
            raise ValueError(f"پرداخت ناموفق: {err_s}")

    await asyncio.sleep(1.2)
    saved = await _starzy_save_to_me(client, chat_id, msg_id, f"⭐ استارزی ({stars} Stars)")
    info = "پرداخت OK"
    if saved:
        info += " — ذخیره در Saved Messages"
    return True, stars, info



async def starzy_photo_scheduler_task(client, user_id: int):
    """هر ۲۰ ثانیه چک می‌کند؛ رأس دقیقهٔ تنظیم‌شده (تهران) باز می‌کند."""
    await asyncio.sleep(8)
    while True:
        try:
            if not is_self_on(user_id):
                await asyncio.sleep(30)
                continue
            conf = STARZY_PHOTO.get(user_id)
            if not conf or not conf.get("link") or not conf.get("time"):
                await asyncio.sleep(20)
                continue
            parsed_t = _parse_starzy_time(conf["time"])
            if not parsed_t:
                await asyncio.sleep(30)
                continue
            h, mi = parsed_t
            now = datetime.now(TEHRAN_TIMEZONE)
            # نزدیک ساعت هدف هر ۵ ثانیه چک کن
            if now.hour != h or now.minute != mi:
                # اگر کمتر از ۲ دقیقه تا هدف مانده، سریع‌تر چک کن
                target_mins = h * 60 + mi
                now_mins = now.hour * 60 + now.minute
                diff = (target_mins - now_mins) % (24 * 60)
                await asyncio.sleep(5 if diff <= 2 else 20)
                continue
            day_key = now.strftime("%Y-%m-%d")
            if conf.get("last_fire") == day_key:
                await asyncio.sleep(30)
                continue
            # قفل تا دوبار شلیک نشود
            async with _starzy_lock():
                conf = STARZY_PHOTO.get(user_id) or conf
                if conf.get("last_fire") == day_key:
                    await asyncio.sleep(40)
                    continue
                link = conf["link"]
                parsed = _parse_telegram_msg_link(link)
                if not parsed:
                    try:
                        await client.send_message("me", f"❌ عکس استارزی: لینک نامعتبر\n`{link}`")
                    except Exception:
                        pass
                    conf["last_fire"] = day_key
                    STARZY_PHOTO[user_id] = conf
                    try:
                        persist_all_user_settings(user_id)
                    except Exception:
                        pass
                    continue
                chat_ref, msg_id = parsed
                try:
                    ok, stars, info = await _starzy_pay_and_unlock(client, chat_ref, msg_id)
                    attempts = int(conf.get("attempts") or 0) + 1
                    conf["attempts"] = attempts
                    if ok:
                        conf["last_fire"] = day_key
                        conf["attempts"] = 0
                        STARZY_PHOTO[user_id] = conf
                        try:
                            persist_all_user_settings(user_id)
                        except Exception:
                            pass
                        try:
                            await client.send_message(
                                "me",
                                f"⭐ <b>عکس استارزی باز شد</b>\n"
                                f"💰 هزینه: <code>{stars}</code> Stars\n"
                                f"🕒 {conf['time']}\n"
                                f"✅ {info}",
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception:
                            pass
                    else:
                        # ناموفق — دوباره در همین دقیقه تلاش (حداکثر ۳ بار)
                        STARZY_PHOTO[user_id] = conf
                        try:
                            await client.send_message(
                                "me",
                                f"⭐ عکس استارزی ناموفق (تلاش {attempts}/3)\n💰 {stars} Stars\n❌ {info}",
                            )
                        except Exception:
                            pass
                        if attempts >= 3:
                            conf["last_fire"] = day_key
                            conf["attempts"] = 0
                            STARZY_PHOTO[user_id] = conf
                            try:
                                persist_all_user_settings(user_id)
                            except Exception:
                                pass
                        else:
                            await asyncio.sleep(12)
                            continue
                except Exception as e:
                    logging.error("starzy unlock uid=%s: %s", user_id, e)
                    try:
                        await client.send_message("me", f"❌ خطا در باز کردن عکس استارزی:\n`{e}`")
                    except Exception:
                        pass
                    conf["last_fire"] = day_key
                    STARZY_PHOTO[user_id] = conf
            await asyncio.sleep(50)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning("starzy scheduler uid=%s: %s", user_id, e)
            await asyncio.sleep(30)



# ========== ایموجی پریمیوم (سبک darkself) ==========
PEMOJI_MAP = {}          # uid -> {emoji_char: custom_emoji_id_str}
PEMOJI_STATUS = {}       # uid -> bool (auto convert)
PEMOJI_FALLBACK = "⭐"

def _pemoji_map(uid: int) -> dict:
    m = PEMOJI_MAP.get(int(uid))
    if not isinstance(m, dict):
        m = {}
        PEMOJI_MAP[int(uid)] = m
    return m

def _pemoji_canon(s: str) -> str:
    return (s or "").strip()

def _pemoji_extract_custom(msg) -> list:
    """[(fallback_char, custom_emoji_id)] از پیام"""
    out = []
    if not msg:
        return out
    text = msg.text or msg.caption or ""
    ents = list(msg.entities or []) + list(getattr(msg, "caption_entities", None) or [])
    for ent in ents:
        cid = getattr(ent, "custom_emoji_id", None)
        if not cid:
            t = str(getattr(ent, "type", "") or "")
            if "CUSTOM_EMOJI" not in t.upper() and "custom_emoji" not in t.lower():
                continue
            cid = getattr(ent, "custom_emoji_id", None)
        if not cid:
            continue
        try:
            off = int(getattr(ent, "offset", 0) or 0)
            ln = int(getattr(ent, "length", 1) or 1)
            # utf-16 offsets
            u16 = text.encode("utf-16-le")
            ch = u16[off * 2:(off + ln) * 2].decode("utf-16-le", errors="ignore") or PEMOJI_FALLBACK
        except Exception:
            ch = PEMOJI_FALLBACK
        out.append((ch, int(cid)))
    return out

def _pemoji_query_match(uid: int, text: str) -> bool:
    text = text or ""
    for k in _pemoji_map(uid).keys():
        if k and k in text:
            return True
    return False

async def _pemoji_doc_is_custom(client, doc_id) -> bool:
    try:
        from pyrogram import raw
        res = await client.invoke(
            raw.functions.messages.GetCustomEmojiDocuments(document_id=[int(doc_id)])
        )
        docs = getattr(res, "documents", None) or []
        return bool(docs)
    except Exception:
        return True  # اگر چک نشد، اجازه بده

async def pemoji_ds_controller(client, message) -> bool:
    """دستورات تنظیم/لیست/حذف ایموجی پرمیوم — True اگر هندل شد"""
    if not message or not (message.text or "").strip():
        return False
    uid = client.me.id if client.me else None
    if not uid:
        return False
    text = (message.text or "").strip()
    # نرمال‌سازی
    t = text.replace("ي", "ی").replace("ك", "ک")
    low = t.lstrip(".／/")

    # روشن/خاموش
    if low in ("ایموجی پرمیوم روشن", "ایموجی پریمیوم روشن", "پریموم ایموجی روشن"):
        PEMOJI_STATUS[uid] = True
        try:
            persist_all_user_settings(uid)
        except Exception:
            pass
        try:
            await message.edit_text("✅ تبدیل خودکار ایموجی پرمیوم **روشن** شد.")
        except Exception:
            pass
        return True
    if low in ("ایموجی پرمیوم خاموش", "ایموجی پریمیوم خاموش", "پریموم ایموجی خاموش"):
        PEMOJI_STATUS[uid] = False
        try:
            persist_all_user_settings(uid)
        except Exception:
            pass
        try:
            await message.edit_text("⏹ تبدیل خودکار ایموجی پرمیوم **خاموش** شد.")
        except Exception:
            pass
        return True

    # لیست
    if low in ("لیست ایموجی پرمیوم", "لیست ایموجی پریمیوم", "لیست ایموجی"):
        pmap = _pemoji_map(uid)
        if not pmap:
            try:
                await message.edit_text(
                    "📭 لیست ایموجی پرمیوم خالی است.\n\n"
                    "ریپلای روی پیام حاوی ایموجی پرمیوم +:\n"
                    "`.تنظیم ایموجی ❤️`"
                )
            except Exception:
                pass
            return True
        lines = ["✦ لیست ایموجی پرمیوم | self MR\n"]
        for i, (k, v) in enumerate(pmap.items(), 1):
            lines.append(f"{i}. {k}  →  `{v}`")
        lines.append("\nحذف: `.حذف ایموجی 1` یا `.حذف ایموجی ❤️`")
        lines.append("پاکسازی: `.حذف لیست ایموجی پرمیوم`")
        try:
            await message.edit_text("\n".join(lines))
        except Exception:
            pass
        return True

    # حذف کل لیست
    if low in ("حذف لیست ایموجی پرمیوم", "حذف لیست ایموجی پریمیوم", "پاکسازی لیست ایموجی"):
        PEMOJI_MAP[uid] = {}
        try:
            persist_all_user_settings(uid)
        except Exception:
            pass
        try:
            await message.edit_text("🗑 لیست ایموجی پرمیوم پاک شد.")
        except Exception:
            pass
        return True

    # حذف تکی
    if low.startswith("حذف ایموجی"):
        arg = low[len("حذف ایموجی"):].strip()
        pmap = _pemoji_map(uid)
        if not arg:
            try:
                await message.edit_text("❌ مثال: `.حذف ایموجی 1` یا `.حذف ایموجی ❤️`")
            except Exception:
                pass
            return True
        removed = None
        if arg.isdigit():
            idx = int(arg) - 1
            keys = list(pmap.keys())
            if 0 <= idx < len(keys):
                removed = keys[idx]
                pmap.pop(removed, None)
        else:
            # حذف با خود ایموجی
            for k in list(pmap.keys()):
                if k == arg or _pemoji_canon(k) == _pemoji_canon(arg):
                    removed = k
                    pmap.pop(k, None)
                    break
        PEMOJI_MAP[uid] = pmap
        try:
            persist_all_user_settings(uid)
        except Exception:
            pass
        try:
            if removed:
                await message.edit_text(f"✅ `{removed}` از لیست حذف شد.")
            else:
                await message.edit_text("❌ موردی پیدا نشد. `.لیست ایموجی`")
        except Exception:
            pass
        return True

    # تنظیم
    if low.startswith("تنظیم ایموجی"):
        args = low[len("تنظیم ایموجی"):].strip()
        rep = message.reply_to_message
        if not rep:
            try:
                await message.edit_text(
                    "❌ روی پیام حاوی **ایموجی پرمیوم** ریپلای کن.\n"
                    "مثال: `.تنظیم ایموجی ❤️`"
                )
            except Exception:
                pass
            return True
        pairs = _pemoji_extract_custom(rep)
        # استیکر کاستوم
        if not pairs and getattr(rep, "sticker", None):
            try:
                from pyrogram.file_id import FileId
                fid = FileId.decode(rep.sticker.file_id)
                doc = getattr(fid, "media_id", None) or getattr(fid, "id", None)
                if doc and await _pemoji_doc_is_custom(client, doc):
                    pairs = [(getattr(rep.sticker, "emoji", None) or PEMOJI_FALLBACK, int(doc))]
            except Exception:
                pass
        if not pairs:
            try:
                await message.edit_text("❌ این پیام ایموجی پرمیوم ندارد.")
            except Exception:
                pass
            return True
        base = args.split()[0] if args else ""
        if base:
            pairs = [(base, pairs[0][1])]
        pmap = _pemoji_map(uid)
        done = []
        for ch, doc in pairs:
            ch = ch or PEMOJI_FALLBACK
            # جایگزین قبلی با همان کاراکتر
            for k in list(pmap.keys()):
                if _pemoji_canon(k) == _pemoji_canon(ch):
                    pmap.pop(k, None)
            pmap[ch] = str(doc)
            done.append(ch)
        PEMOJI_MAP[uid] = pmap
        # سازگاری با همه سیستم‌های قبلی
        try:
            tmap = EMOJI_PREMIUM_TEMPLATES.get(uid) or {}
            bucket = dict(EMOJI_CHAR_TO_PREMIUM.get(uid) or {})
            for ch in done:
                tmap[ch] = pmap[ch]
                try:
                    bucket[ch] = int(pmap[ch])
                except Exception:
                    bucket[ch] = pmap[ch]
            EMOJI_PREMIUM_TEMPLATES[uid] = tmap
            EMOJI_CHAR_TO_PREMIUM[uid] = bucket
            EMOJI_PREMIUM_CONVERT[uid] = True
            PEMOJI_STATUS[uid] = True
            logging.info("pemoji saved uid=%s keys=%s", uid, list(bucket.keys()))
        except Exception as e:
            logging.warning("pemoji save compat: %s", e)
        try:
            persist_all_user_settings(uid)
        except Exception:
            pass
        try:
            await message.edit_text(
                f"✅ تنظیم شد: {' '.join(done)}\n"
                f"تبدیل خودکار روشن است.\n"
                f"خاموش: `.ایموجی پرمیوم خاموش`"
            )
        except Exception:
            pass
        return True

    return False


async def pemoji_ds_outgoing(client, message):
    """تبدیل خودکار متن حاوی ایموجی مپ‌شده → اینلاین/entity (سبک darkself)"""
    try:
        if not message or not (getattr(message, "outgoing", False) or getattr(message, "from_user", None)):
            return
        if getattr(message, "via_bot", None):
            return
        if getattr(message, "edit_date", None):
            return
        if message.media or message.forward_date:
            return
        uid = client.me.id if client.me else None
        if not uid:
            return
        if not PEMOJI_STATUS.get(uid, False) and not EMOJI_PREMIUM_CONVERT.get(uid, False):
            return
        text = (message.text or "").strip()
        if not text or len(text) > 200:
            return
        # دستورات را رد کن
        if text.startswith(".") or text.startswith("تنظیم") or text.startswith("حذف") or text.startswith("لیست"):
            return
        pmap = _pemoji_map(uid)
        if not pmap:
            pmap = EMOJI_PREMIUM_TEMPLATES.get(uid) or {}
        if not pmap:
            return
        # آیا متن فقط یک/چند ایموجی مپ‌شده است؟
        matched = None
        for k, cid in pmap.items():
            if not k:
                continue
            if text == k or text.replace(" ", "") == k:
                matched = (k, cid)
                break
        if not matched:
            # اگر متن شامل یکی از کلیدهاست و کوتاه است
            for k, cid in pmap.items():
                if k and k in text and len(text) <= len(k) + 4:
                    matched = (k, cid)
                    break
        if not matched:
            return
        key, cid = matched
        # 1) entity edit
        try:
            from pyrogram.enums import MessageEntityType
            from pyrogram.types import MessageEntity
            ln = len(key.encode("utf-16-le")) // 2
            ents = [MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=0, length=ln, custom_emoji_id=int(cid),
            )]
            await message.edit_text(key, entities=ents)
            logging.info("pemoji entity ok uid=%s", uid)
            return
        except Exception as e1:
            logging.debug("pemoji entity: %s", e1)
        # 2) inline از هلپر/منیجر
        bot_un = HELPER_INLINE_BOT or ""
        try:
            if manager_bot and getattr(manager_bot, "me", None) and manager_bot.me.username:
                if not bot_un:
                    bot_un = manager_bot.me.username
        except Exception:
            pass
        if bot_un:
            try:
                results = await asyncio.wait_for(
                    client.get_inline_bot_results(bot_un, f"pe|{uid}|{cid}|{key}"),
                    timeout=6.0,
                )
                if results and results.results:
                    reply_id = message.reply_to_message_id
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    await client.send_inline_bot_result(
                        message.chat.id,
                        results.query_id,
                        results.results[0].id,
                        reply_to_message_id=reply_id,
                    )
                    logging.info("pemoji inline ok uid=%s bot=%s", uid, bot_un)
                    return
            except Exception as e2:
                logging.debug("pemoji inline: %s", e2)
    except Exception as e:
        logging.debug("pemoji_ds_outgoing: %s", e)


async def reply_based_controller(client, message):
    user_id = client.me.id
    cmd = (message.text or "").strip()
    if not cmd:
        return

    # ========== ایموجی پرمیوم (darkself-style) ==========
    try:
        if await pemoji_ds_controller(client, message):
            return
    except Exception as _pe:
        logging.debug("pemoji ctrl: %s", _pe)

    # ========== وضعیت سلف روشن/خاموش ==========


    if cmd in (".سلف روشن", "سلف روشن"):
        SELF_STATUS[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("✅ سلف روشن شد | self MR")
        except Exception:
            pass
        return
    if cmd in (".سلف خاموش", "سلف خاموش"):
        SELF_STATUS[user_id] = False
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("⏹ سلف خاموش شد | self MR\nدیگر دستورات و قابلیت‌ها اجرا نمی‌شوند.")
        except Exception:
            pass
        return

    # اگر سلف خاموش است هیچ دستوری اجرا نشود
    if not SELF_STATUS.get(user_id, True):
        return

    # ========== فونت چندسبکی ==========
    if cmd.startswith(".فونت") or cmd.startswith("فونت"):
        body = cmd
        for p in (".فونت", "فونت"):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        if not body:
            try:
                await message.edit_text("❌ مثال:\n`.فونت Gang`")
            except Exception:
                pass
            return
        try:
            out = _make_fancy_fonts(body)
            await message.edit_text(out)
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در ساخت فونت: {e}")
            except Exception:
                pass
        return




    # ========== عکس استارزی ==========
    if cmd.startswith(".تنظیم عکس استارزی") or cmd.startswith("تنظیم عکس استارزی"):
        body = cmd
        for p in (".تنظیم عکس استارزی", "تنظیم عکس استارزی"):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        if not body and message.reply_to_message:
            body = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not body or "t.me/" not in body:
            try:
                await message.edit_text(
                    "❌ مثال:\n`.تنظیم عکس استارزی + https://t.me/c/123/456`\n"
                    "لینک پیام حاوی عکس/ویدیو استارزی را بفرستید."
                )
            except Exception:
                pass
            return
        link = body.split()[0]
        if not _parse_telegram_msg_link(link):
            try:
                await message.edit_text("❌ لینک پیام معتبر نیست.")
            except Exception:
                pass
            return
        conf = STARZY_PHOTO.get(user_id) or {}
        conf["link"] = link
        conf.setdefault("time", "")
        conf["last_fire"] = ""
        STARZY_PHOTO[user_id] = conf
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        tshow = conf.get("time") or "هنوز تنظیم نشده"
        try:
            await message.edit_text(
                f"✅ عکس استارزی ذخیره شد.\n🔗 `{link}`\n🕒 تایم فعلی: `{tshow}`\n\n"
                f"با `.تنظیم تایم استارزی 16:00` ساعت باز شدن را بگذارید."
            )
        except Exception:
            pass
        return

    if (
        cmd.startswith(".تنظیم تایم استارزی")
        or cmd.startswith("تنظیم تایم استارزی")
        or cmd.startswith(".تنظیم تایم عکس استارزی")
        or cmd.startswith("تنظیم تایم عکس استارزی")
    ):
        body = cmd
        for p in (
            ".تنظیم تایم عکس استارزی",
            "تنظیم تایم عکس استارزی",
            ".تنظیم تایم استارزی",
            "تنظیم تایم استارزی",
        ):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        parsed = _parse_starzy_time(body)
        if not parsed:
            try:
                await message.edit_text("❌ مثال:\n`.تنظیم تایم استارزی 16:00`\n`.تنظیم تایم استارزی 00:00`")
            except Exception:
                pass
            return
        h, mi = parsed
        timestr = f"{h:02d}:{mi:02d}"
        conf = STARZY_PHOTO.get(user_id) or {}
        conf["time"] = timestr
        conf["last_fire"] = ""  # اجازه شلیک امروز دوباره اگر تایم عوض شد
        STARZY_PHOTO[user_id] = conf
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        link = conf.get("link") or "هنوز لینک تنظیم نشده"
        try:
            await message.edit_text(
                f"✅ تایم عکس استارزی: `{timestr}` (تهران)\n"
                f"🔗 `{link}`\n\n"
                f"رأس همین ساعت، هزینه Stars کم و عکس باز می‌شود."
            )
        except Exception:
            pass
        return


    if cmd in (".باز کردن عکس استارزی", "باز کردن عکس استارزی", ".استارزی الان"):
        conf = STARZY_PHOTO.get(user_id) or {}
        link = conf.get("link") or ""
        if not link:
            # لینک بعد از دستور
            body = cmd
            for p in (".باز کردن عکس استارزی", "باز کردن عکس استارزی", ".استارزی الان"):
                if body.startswith(p):
                    body = body[len(p):].strip()
                    break
            link = body.split()[0] if body else ""
        if not link:
            try:
                await message.edit_text("❌ اول با `.تنظیم عکس استارزی + لینک` لینک را بگذارید\nیا `.باز کردن عکس استارزی + لینک`")
            except Exception:
                pass
            return
        parsed = _parse_telegram_msg_link(link)
        if not parsed:
            try:
                await message.edit_text("❌ لینک نامعتبر")
            except Exception:
                pass
            return
        try:
            await message.edit_text("⏳ در حال باز کردن عکس استارزی...")
        except Exception:
            pass
        try:
            chat_ref, mid = parsed
            ok, stars, info = await _starzy_pay_and_unlock(client, chat_ref, mid)
            if ok:
                await message.edit_text(f"✅ باز شد\n💰 `{stars}` Stars\n{info}")
            else:
                await message.edit_text(f"❌ ناموفق\n💰 `{stars}` Stars\n{info}")
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا:\n`{e}`")
            except Exception:
                pass
        return


    if cmd in (".عکس استارزی خاموش", "عکس استارزی خاموش", ".خاموش عکس استارزی"):
        STARZY_PHOTO.pop(user_id, None)
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("❌ عکس استارزی خاموش و پاک شد.")
        except Exception:
            pass
        return

    if cmd in (".عکس استارزی", "عکس استارزی", ".وضعیت عکس استارزی"):
        conf = STARZY_PHOTO.get(user_id) or {}
        if not conf.get("link"):
            try:
                await message.edit_text(
                    "⭐ عکس استارزی فعال نیست.\n\n"
                    "دستورات:\n"
                    "`.تنظیم عکس استارزی + لینک`\n"
                    "`.تنظیم تایم استارزی 16:00`\n"
                    "`.عکس استارزی خاموش`"
                )
            except Exception:
                pass
            return
        try:
            await message.edit_text(
                f"⭐ وضعیت عکس استارزی\n"
                f"🔗 `{conf.get('link')}`\n"
                f"🕒 `{conf.get('time') or '—'}`\n"
                f"📅 آخرین اجرا: `{conf.get('last_fire') or '—'}`"
            )
        except Exception:
            pass
        return


    # ========== حجم چت ==========
    if cmd.startswith(".حجم چت") or cmd.startswith("حجم چت"):
        try:
            target_id = None
            uname = None
            rest = cmd
            for p in (".حجم چت", "حجم چت"):
                if rest.startswith(p):
                    rest = rest[len(p):].strip()
                    break
            if message.reply_to_message and message.reply_to_message.from_user:
                target_id = message.reply_to_message.from_user.id
                uname = message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name
            if rest.startswith("@"):
                uname = rest.lstrip("@").split()[0]
                try:
                    ch = await client.get_users(uname)
                    target_id = ch.id
                    uname = ch.username or ch.first_name or uname
                except Exception:
                    try:
                        await message.edit_text("❌ کاربر پیدا نشد.")
                    except Exception:
                        pass
                    return
            if not target_id and message.entities:
                for ent in message.entities:
                    if getattr(ent, "user", None):
                        target_id = ent.user.id
                        uname = ent.user.username or ent.user.first_name
                        break
            if not target_id:
                try:
                    await message.edit_text("❌ مثال:\n`.حجم چت @user`\nیا ریپلای + `.حجم چت`")
                except Exception:
                    pass
                return
            try:
                await message.edit_text("⏳ در حال محاسبه حجم چت...")
            except Exception:
                pass
            photos = videos = voices = files = texts = stickers = 0
            total_size = 0
            scanned = 0
            async for m in client.get_chat_history(target_id, limit=8000):
                scanned += 1
                if m.photo:
                    photos += 1
                    try:
                        total_size += int(getattr(m.photo, "file_size", 0) or 0)
                    except Exception:
                        pass
                elif m.video or m.video_note:
                    videos += 1
                    med = m.video or m.video_note
                    try:
                        total_size += int(getattr(med, "file_size", 0) or 0)
                    except Exception:
                        pass
                elif m.voice or m.audio:
                    voices += 1
                    med = m.voice or m.audio
                    try:
                        total_size += int(getattr(med, "file_size", 0) or 0)
                    except Exception:
                        pass
                elif m.document:
                    files += 1
                    try:
                        total_size += int(getattr(m.document, "file_size", 0) or 0)
                    except Exception:
                        pass
                elif m.sticker:
                    stickers += 1
                elif m.text:
                    texts += 1

            def fmt_sz(b):
                b = float(b or 0)
                if b >= 1024 ** 3:
                    return f"{b / 1024 ** 3:.2f} GB"
                if b >= 1024 ** 2:
                    return f"{b / 1024 ** 2:.1f} MB"
                if b >= 1024:
                    return f"{b / 1024:.1f} KB"
                return f"{int(b)} B"

            who = f"@{uname}" if uname and not str(uname).startswith("@") else (uname or str(target_id))
            if who and not who.startswith("@") and not who.isdigit():
                # keep name
                pass
            out = (
                f"📊 حجم چت با {who}:\n"
                f"📸 عکس: {photos:,} عدد\n"
                f"🎬 ویدیو: {videos:,} عدد\n"
                f"🎤 ویس/آهنگ: {voices:,} عدد\n"
                f"📁 فایل: {files:,} عدد\n"
                f"🎨 استیکر: {stickers:,} عدد\n"
                f"💬 متن: {texts:,} پیام\n"
                f"📦 حجم تقریبی مدیا: {fmt_sz(total_size)}\n"
                f"🔎 پیام‌های بررسی‌شده: {scanned:,}"
            )
            await message.edit_text(out)
        except Exception as e:
            logging.warning("chat volume: %s", e)
            try:
                await message.edit_text(f"❌ خطا در محاسبه حجم چت: {e}")
            except Exception:
                pass
        return


    # ========== متن رمزی ==========
    if cmd.startswith(".رمزنگاری") or cmd.startswith("رمزنگاری"):
        body = cmd
        for p in (".رمزنگاری", "رمزنگاری"):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        if not body and message.reply_to_message:
            body = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not body:
            try:
                await message.edit_text("❌ مثال:\n`.رمزنگاری + متن شما`")
            except Exception:
                pass
            return
        try:
            enc = text_encrypt_smr(body)
            await message.edit_text(f"🔐 `{enc}`")
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در رمزنگاری: {e}")
            except Exception:
                pass
        return

    if cmd in (".رمزگشایی", "رمزگشایی") or cmd.startswith(".رمزگشایی") or cmd.startswith("رمزگشایی"):
        src = ""
        if message.reply_to_message:
            src = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not src:
            body = cmd
            for p in (".رمزگشایی", "رمزگشایی"):
                if body.startswith(p):
                    body = body[len(p):].strip()
                    if body.startswith("+"):
                        body = body[1:].strip()
                    break
            src = body
        if not src:
            try:
                await message.edit_text("❌ روی پیام رمز ریپلای کن و بگو:\n`.رمزگشایی`")
            except Exception:
                pass
            return
        try:
            # پاک کردن بک‌تیک‌های مونو
            src2 = src.strip().strip("`").strip()
            dec = text_decrypt_smr(src2)
            await message.edit_text(f"🔓 {dec}")
        except Exception:
            try:
                await message.edit_text("❌ رمزگشایی ناموفق — متن رمز معتبر نیست.")
            except Exception:
                pass
        return

    # ========== پسورد ساز ==========
    if cmd.startswith(".پسورد") or cmd.startswith("پسورد"):
        body = cmd
        for p in (".پسورد", "پسورد"):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        n = 16
        try:
            if body:
                n = int("".join(ch for ch in body if ch.isdigit()) or "16")
        except Exception:
            n = 16
        n = max(4, min(64, n))
        try:
            import secrets
            import string
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+=?"
            pwd = "".join(secrets.choice(alphabet) for _ in range(n))
            await message.edit_text(
                f"🔑 پسورد ساز | self MR\n\n"
                f"طول: `{n}`\n"
                f"پسورد:\n`{pwd}`"
            )
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در ساخت پسورد: {e}")
            except Exception:
                pass
        return

    # ========== ماشین حساب ==========
    if cmd.startswith(".حساب") or cmd.startswith("حساب"):
        body = cmd
        for p in (".حساب", "حساب"):
            if body.startswith(p):
                body = body[len(p):].strip()
                if body.startswith("+"):
                    body = body[1:].strip()
                break
        if not body:
            try:
                await message.edit_text("❌ مثال:\n`.حساب 2*2`\n`.حساب 25*4+10`")
            except Exception:
                pass
            return
        expr = body.replace("×", "*").replace("÷", "/").replace(" ", "")
        # فقط کاراکترهای مجاز
        import re as _re
        if not _re.fullmatch(r"[0-9+\-*/().,%**]+", expr.replace("**", "")) and not _re.fullmatch(r"[0-9+\-*/().,%]+", expr):
            # fallback simpler check
            allowed = set("0123456789+-*/().,% ")
            if any(ch not in allowed and not (ch == "*" ) for ch in expr):
                try:
                    await message.edit_text("❌ فقط عدد و عملگرهای + - * / ( ) مجاز است.")
                except Exception:
                    pass
                return
        try:
            allowed = set("0123456789+-*/().,% ")
            if not all(ch in allowed for ch in expr):
                await message.edit_text("❌ عبارت نامعتبر است.")
                return
            # جلوگیری از ** خطرناک / تقسیم زنجیره‌ای عجیب — eval امن
            result = eval(expr, {"__builtins__": {}}, {})
            if isinstance(result, float) and result == int(result):
                result = int(result)
            await message.edit_text(
                f"🧮 `{body} = {result}`"
            )
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در محاسبه:\n`{body}`")
            except Exception:
                pass
        return


    # ========== قاب پروفایل رنگی ==========
    _frame_cmd = cmd.replace("ي", "ی").replace("ك", "ک")
    if _frame_cmd.startswith(".قاب ") or _frame_cmd.startswith("قاب "):
        if not is_self_on(user_id):
            return
        body = _frame_cmd
        if body.startswith("."):
            body = body[1:]
        body = body.strip()
        # قاب طلایی پروفایل / قاب آبی / قاب طلایی
        color = None
        if body.startswith("قاب"):
            rest = body[3:].strip()
            # حذف کلمه پروفایل از انتها
            if rest.endswith("پروفایل"):
                rest = rest[: -len("پروفایل")].strip()
            rest = rest.strip()
            if rest in PROFILE_FRAME_COLORS:
                color = rest
            else:
                # تطبیق جزئی
                for k in PROFILE_FRAME_COLORS:
                    if k in rest or rest in k:
                        color = k
                        break
        if not color:
            try:
                cols = " / ".join(PROFILE_FRAME_COLORS.keys())
                await message.edit_text(
                    f"❌ رنگ نامعتبر\n\nدستورات:\n"
                    + "\n".join(f".قاب {k} پروفایل" for k in PROFILE_FRAME_COLORS.keys())
                )
            except Exception:
                pass
            return
        try:
            await message.edit_text(f"⏳ در حال ساخت قاب {color}...")
        except Exception:
            pass
        path = None
        try:
            path = await apply_profile_frame(client, user_id, color)
            await client.set_profile_photo(photo=path)
            try:
                await message.edit_text(f"✅ قاب {color} روی پروفایل ست شد | self MR")
            except Exception:
                pass
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در قاب پروفایل:\n{e}")
            except Exception:
                pass
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
        return

    # ========== ساخت عکس AI / تحلیل / خلاصه (اولویت بالا) ==========
    text_full = (message.text or message.caption or "").strip()
    cmd_full = text_full

    # --- ساخت عکس ---
    if cmd_full.startswith(".عکس") or cmd_full.startswith("عکس"):
        prompt = cmd_full
        for p in (".عکس", "عکس"):
            if prompt.startswith(p):
                prompt = prompt[len(p):].strip()
                if prompt.startswith("+"):
                    prompt = prompt[1:].strip()
                break
        if not prompt:
            try:
                await message.edit_text("❌ مثال:\n`.عکس گربه فضانورد`")
            except Exception:
                await message.reply_text("❌ مثال: .عکس گربه فضانورد")
            return
        try:
            await message.edit_text(f"🎨 در حال ساخت تصویر...\n`{prompt[:80]}`")
        except Exception:
            pass
        try:
            path = await ai_generate_image_file(prompt)
            chat_id = message.chat.id
            try:
                await message.delete()
            except Exception:
                pass
            await client.send_photo(
                chat_id,
                path,
                caption=f"🎨 self MR | AI\n{prompt[:200]}",
            )
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            logging.exception("ai image")
            try:
                await message.edit_text(f"❌ ساخت تصویر ناموفق:\n{e}")
            except Exception:
                try:
                    await client.send_message(message.chat.id, f"❌ ساخت تصویر ناموفق:\n{e}")
                except Exception:
                    pass
        return

    # --- تحلیل عکس ---
    if cmd_full in (".تحلیل", "تحلیل") or cmd_full.startswith(".تحلیل") or cmd_full.startswith("تحلیل "):
        r = message.reply_to_message
        # گاهی مدیا در reply ناقص است — دوباره از سرور بگیر
        if message.reply_to_message_id:
            try:
                full = await client.get_messages(message.chat.id, message.reply_to_message_id)
                if full:
                    r = full
            except Exception as e:
                logging.warning("get_messages reply: %s", e)
        has_img = False
        if r:
            if getattr(r, "photo", None):
                has_img = True
            elif getattr(r, "document", None) and (r.document.mime_type or "").startswith("image"):
                has_img = True
            elif getattr(r, "sticker", None) and not getattr(r.sticker, "is_animated", False) and not getattr(r.sticker, "is_video", False):
                has_img = True
        if not has_img:
            try:
                await message.edit_text("❌ روی یک عکس ریپلای کن و بعد بفرست:\n`.تحلیل`")
            except Exception:
                await message.reply_text("❌ روی یک عکس ریپلای کن و بعد بفرست: .تحلیل")
            return
        try:
            await message.edit_text("🔍 در حال تحلیل عکس...")
        except Exception:
            pass
        path = None
        try:
            dest = f"/tmp/analyze_{user_id}_{int(time.time())}.jpg"
            if r.photo:
                # بزرگ‌ترین سایز
                path = await client.download_media(r.photo, file_name=dest)
            else:
                path = await client.download_media(r, file_name=dest)
            if not path or not os.path.exists(str(path)):
                raise RuntimeError("دانلود عکس از تلگرام ناموفق بود")
            result = await ai_analyze_image_file(str(path))
            try:
                await message.edit_text(f"🖼 تحلیل عکس\n\n{result}")
            except Exception:
                await message.reply_text(f"🖼 تحلیل عکس\n\n{result}")
        except Exception as e:
            logging.exception("analyze")
            try:
                await message.edit_text(f"❌ خطا در تحلیل:\n{e}")
            except Exception:
                try:
                    await client.send_message(message.chat.id, f"❌ خطا در تحلیل:\n{e}")
                except Exception:
                    pass
        finally:
            try:
                if path and os.path.exists(str(path)):
                    os.remove(path)
            except Exception:
                pass
        return

    # --- خلاصه چت ---
    if cmd_full in (".خلاصه", "خلاصه") or cmd_full.startswith(".خلاصه"):
        try:
            await message.edit_text("📝 در حال خلاصه کردن...")
        except Exception:
            pass
        texts = []
        try:
            async for m in client.get_chat_history(message.chat.id, limit=50):
                if m.id == message.id:
                    continue
                t = m.text or m.caption
                if not t:
                    continue
                t = t.strip()
                if t in (".خلاصه", "خلاصه", ".تحلیل", "تحلیل") or t.startswith(".عکس"):
                    continue
                who = "من" if getattr(m, "outgoing", False) else "طرف"
                try:
                    if m.from_user and not m.outgoing:
                        who = (m.from_user.first_name or "کاربر")[:30]
                except Exception:
                    pass
                texts.append(f"{who}: {t[:400]}")
                if len(texts) >= 35:
                    break
            texts.reverse()
            if not texts and message.reply_to_message:
                rt = message.reply_to_message.text or message.reply_to_message.caption
                if rt:
                    texts.append(rt[:800])
            summary = await ai_summarize_texts(texts)
            try:
                await message.edit_text(f"📋 خلاصه چت\n\n{summary}")
            except Exception:
                await message.reply_text(f"📋 خلاصه چت\n\n{summary}")
        except Exception as e:
            logging.exception("summary")
            try:
                await message.edit_text(f"❌ خطا:\n{e}")
            except Exception:
                pass
        return




    # ========== اسکرین ==========
    if cmd in (".اسکرین", "اسکرین"):
        try:
            await message.edit_text("⏳ در حال گرفتن اسکرین و ارسال به Saved Messages...")
        except Exception:
            pass
        try:
            sent, errors = await capture_chat_to_saved(client, message.chat.id, limit=20)
            await message.edit_text(
                f"✅ اسکرین انجام شد | self MR\\n\\n"
                f"📤 ارسال‌شده: {sent}\\n"
                f"⚠️ ناموفق: {errors}\\n"
                f"📂 داخل پیام‌های ذخیره‌شده ببین."
            )
        except Exception as e:
            logging.error(f"screen cmd: {e}")
            try:
                await message.edit_text(f"❌ خطا در اسکرین: {e}")
            except Exception:
                pass
        return

    # ========== سرچ عکس ==========
    # سرچ آهنگ جداست — اینجا فقط تصویر
    if (cmd.startswith(".سرچ") or cmd.startswith("سرچ")) and not (
        cmd.startswith(".سرچ آهنگ") or cmd.startswith("سرچ آهنگ")
    ):
        q = ""
        if "+" in cmd:
            q = cmd.split("+", 1)[1].strip()
        else:
            parts = cmd.split(None, 1)
            if len(parts) > 1:
                q = parts[1].strip()
                if q.startswith("+"):
                    q = q[1:].strip()
        if not q:
            await message.edit_text("❌ مثال:\\n`.سرچ + گاو`")
            return
        await message.edit_text(f"🔍 در حال جستجوی تصویر برای:\\n`{q}`")
        try:
            urls = await search_web_images(q, limit=15)
            if not urls:
                await message.edit_text("❌ تصویری پیدا نشد.")
                return
            # تنوع: عکس‌های اخیراً استفاده‌شده را رد کن
            try:
                uid = client.me.id if client.me else user_id
            except Exception:
                uid = user_id
            hist = list(IMAGE_SEARCH_HISTORY.get(uid) or [])
            pool = [u for u in urls if u not in hist] or list(urls)
            random.shuffle(pool)
            path = None
            chosen = None
            for u in pool:
                path = await download_image_bytes(u)
                if path:
                    chosen = u
                    break
            if chosen:
                hist.append(chosen)
                IMAGE_SEARCH_HISTORY[uid] = hist[-40:]
            if not path:
                await message.edit_text("❌ دانلود تصویر ناموفق بود.")
                return
            try:
                await client.send_photo(
                    message.chat.id,
                    path,
                    caption=f"🔍 {q} | self MR",
                )
                try:
                    await message.delete()
                except Exception:
                    try:
                        await message.edit_text("✅ تصویر ارسال شد.")
                    except Exception:
                        pass
            except Exception as e:
                logging.warning(f"send search photo: {e}")
                await message.edit_text(f"❌ ارسال تصویر ناموفق: {e}")
            finally:
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"search cmd: {e}")
            await message.edit_text(f"❌ خطا در سرچ: {e}")
        return


    # ========== هوش متن گسترده ==========
    if cmd.startswith(".هوش متن گسترده") or cmd.startswith("هوش متن گسترده"):
        seed = ""
        if "+" in cmd:
            seed = cmd.split("+", 1)[1].strip()
        elif "گسترده" in cmd:
            seed = cmd.split("گسترده", 1)[1].strip()
            if seed.startswith("+"):
                seed = seed[1:].strip()
        if not seed and message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
            seed = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        if not seed:
            await message.edit_text("❌ مثال:\n`.هوش متن گسترده + علی در روز آفتابی بیرون رفت`")
            return
        await message.edit_text("⏳ هوش مصنوعی در حال گسترش متن...")
        try:
            expanded = await ai_expand_text(seed)
            # ویرایش همان پیام کاربر با متن گسترش‌یافته
            if expanded.startswith("❌"):
                await message.edit_text(expanded)
            else:
                await message.edit_text(expanded)
        except Exception as e:
            await message.edit_text(f"❌ خطا: {e}")
        return

    # ========== متن آهنگ ==========
    if cmd in (".متن آهنگ", "متن آهنگ") or cmd.startswith(".متن آهنگ ") or cmd.startswith("متن آهنگ "):
        title = ""
        artist = ""
        rest = cmd.replace(".متن آهنگ", "", 1).replace("متن آهنگ", "", 1).strip()
        if rest:
            a2, t2 = _split_artist_title(rest)
            if t2:
                artist, title = a2, t2
            else:
                title = rest
        if not title and not message.reply_to_message:
            await message.edit_text("❌ روی آهنگ ریپلای کنید یا بنویسید:\n`.متن آهنگ نام آهنگ`")
            return
        r = message.reply_to_message
        if r:
            if r.audio:
                title = r.audio.title or title
                artist = r.audio.performer or artist
                if not title and r.audio.file_name:
                    title = r.audio.file_name.rsplit(".", 1)[0]
            elif r.document:
                fname = r.document.file_name or ""
                if not title and fname:
                    title = fname.rsplit(".", 1)[0]
            elif (r.text or r.caption) and not title:
                title = (r.text or r.caption or "").strip()
            elif r.voice and not title:
                await message.edit_text("❌ ویس متادیتا ندارد. روی فایل آهنگ (Music) ریپلای کنید یا اسم آهنگ را بفرستید:\n`.متن آهنگ نام آهنگ`")
                return
        # پارس Artist - Title
        if title and not artist:
            a2, t2 = _split_artist_title(title)
            if t2:
                artist, title = a2, t2
        title = _clean_track_name(title)
        artist = _clean_track_name(artist)
        if not title:
            await message.edit_text("❌ نام آهنگ از پیام پیدا نشد.")
            return
        await message.edit_text(f"⏳ در حال جستجوی متن آهنگ...\n🎵 {artist + ' - ' if artist else ''}{title}")
        try:
            lyrics = await fetch_song_lyrics(title, artist)
            if len(lyrics) > 3900:
                await message.edit_text(lyrics[:3900] + "\n\n…")
            else:
                await message.edit_text(lyrics)
        except Exception as e:
            logging.error(f"lyrics cmd: {e}")
            await message.edit_text(f"❌ خطا: {e}")
        return


    # ========== تاس / بولینگ ==========
    if cmd == "تاس":
        await client.send_dice(message.chat.id, "🎲")
        try:
            await message.delete()
        except:
            pass
        return

    if cmd == "بولینگ":
        await client.send_dice(message.chat.id, "🎳")
        try:
            await message.delete()
        except:
            pass
        return

    if cmd.startswith("تاس "):
        try:
            await client.send_dice(message.chat.id, "🎲", reply_to_message_id=message.reply_to_message_id)
        except:
            pass
        return

        # ========== 📩 منشی آفلاین ==========

    # ========== فیلتر استیکر / گیف پیوی ==========
    if cmd in (".فیلتر استیکر", ".فیلتر استیکر روشن", "فیلتر استیکر روشن"):
        if cmd == ".فیلتر استیکر":
            PV_FILTER_STICKER[user_id] = not PV_FILTER_STICKER.get(user_id, False)
        else:
            PV_FILTER_STICKER[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        st = "روشن ✅" if PV_FILTER_STICKER.get(user_id) else "خاموش ❌"
        try:
            await message.edit_text(f"🚫 فیلتر استیکر پیوی: {st}")
        except Exception:
            await message.reply_text(f"🚫 فیلتر استیکر پیوی: {st}")
        return

    if cmd in (".فیلتر استیکر خاموش", "فیلتر استیکر خاموش"):
        PV_FILTER_STICKER[user_id] = False
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("🚫 فیلتر استیکر پیوی: خاموش ❌")
        except Exception:
            await message.reply_text("🚫 فیلتر استیکر پیوی: خاموش ❌")
        return

    if cmd in (".فیلتر گیف", ".فیلتر گیف روشن", "فیلتر گیف روشن"):
        if cmd == ".فیلتر گیف":
            PV_FILTER_GIF[user_id] = not PV_FILTER_GIF.get(user_id, False)
        else:
            PV_FILTER_GIF[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        st = "روشن ✅" if PV_FILTER_GIF.get(user_id) else "خاموش ❌"
        try:
            await message.edit_text(f"🎞 فیلتر گیف پیوی: {st}")
        except Exception:
            await message.reply_text(f"🎞 فیلتر گیف پیوی: {st}")
        return

    if cmd in (".فیلتر گیف خاموش", "فیلتر گیف خاموش"):
        PV_FILTER_GIF[user_id] = False
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("🎞 فیلتر گیف پیوی: خاموش ❌")
        except Exception:
            await message.reply_text("🎞 فیلتر گیف پیوی: خاموش ❌")
        return

    # ========== حذف پیام‌های خود ==========
    del_m = re.match(r"^\.?حذف\s+(\d+)$", (cmd or "").strip())
    if del_m:
        try:
            count = max(1, min(100, int(del_m.group(1))))
            msg_ids = [message.id]
            async for m in client.get_chat_history(message.chat.id, limit=count + 8):
                if m.id == message.id:
                    continue
                if m.from_user and getattr(m.from_user, "is_self", False):
                    msg_ids.append(m.id)
                if len(msg_ids) >= count + 1:
                    break
            try:
                await client.delete_messages(message.chat.id, msg_ids[: count + 1])
            except Exception:
                for mid in msg_ids[: count + 1]:
                    try:
                        await client.delete_messages(message.chat.id, mid)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"delete cmd: {e}")
        return

    # ========== رمز ایموجی ==========
    if cmd in (".تبدیل متن به رمز ایموجی", "تبدیل متن به رمز ایموجی") or cmd.startswith(".تبدیل متن به رمز ایموجی"):
        src = ""
        if message.reply_to_message:
            r = message.reply_to_message
            src = _cipher_plain_str(getattr(r, "text", None) or getattr(r, "caption", None) or "")
        if not src and " " in cmd:
            src = cmd.split(None, 1)[1] if cmd.startswith(".") else ""
            # after full phrase
            for p in (".تبدیل متن به رمز ایموجی", "تبدیل متن به رمز ایموجی"):
                if cmd.startswith(p):
                    src = cmd[len(p):].strip()
                    break
        if not src:
            await message.edit_text("❌ ریپلای روی متن یا بنویس:\n`.تبدیل متن به رمز ایموجی سلام`")
            return
        encoded = text_to_emoji_cipher(src)
        try:
            await message.edit_text(f"🔐 رمز ایموجی:\n\n{encoded}")
        except Exception:
            await message.reply_text(f"🔐 رمز ایموجی:\n\n{encoded}")
        return

    if cmd in (".تبدیل ایموجی به متن رمز", "تبدیل ایموجی به متن رمز", ".تبدیل رمز ایموجی به متن") or cmd.startswith(".تبدیل ایموجی به متن"):
        src = ""
        if message.reply_to_message:
            r = message.reply_to_message
            src = _cipher_plain_str(getattr(r, "text", None) or getattr(r, "caption", None) or "")
        src = _cipher_plain_str(src)
        if not src:
            await message.edit_text("❌ روی پیام رمزدار ریپلای کن:\n`.تبدیل ایموجی به متن رمز`")
            return
        decoded = emoji_cipher_to_text(src)
        try:
            await message.edit_text(f"🔓 متن:\n\n{decoded}")
        except Exception:
            await message.reply_text(f"🔓 متن:\n\n{decoded}")
        return

    if cmd in (".منشی روشن", "منشی روشن"):
        SECRETARY_MODE_STATUS[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("✅ منشی آفلاین روشن شد.\nهر کسی در پیوی پیام بدهد، هر ۱۰ دقیقه یک‌بار پاسخ خودکار می‌دهد.")
        except Exception:
            await message.reply_text("✅ منشی آفلاین روشن شد.")
        return

    if cmd in (".منشی خاموش", "منشی خاموش"):
        SECRETARY_MODE_STATUS[user_id] = False
        USERS_REPLIED_IN_SECRETARY[user_id] = set()
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text("❌ منشی آفلاین خاموش شد.")
        except Exception:
            await message.reply_text("❌ منشی آفلاین خاموش شد.")
        return

    if cmd.startswith(".تنظیم منشی") or cmd.startswith("تنظیم منشی"):
        new_msg = cmd.split("منشی", 1)[1].strip() if "منشی" in cmd else ""
        # حذف پیشوند نقطه/فاصله
        if new_msg.startswith("."):
            new_msg = new_msg[1:].strip()
        if not new_msg:
            await message.edit_text("⚠️ مثال:\n`.تنظیم منشی الان در دسترس نیستم`")
            return
        SECRETARY_CUSTOM_MESSAGES[user_id] = new_msg
        try:
            data_manager.update_user_data(user_id, {"settings": {"secretary_msg": new_msg, "secretary": SECRETARY_MODE_STATUS.get(user_id, False)}})
        except Exception:
            pass
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await message.edit_text(f"✅ متن منشی:\n\n{new_msg}")
        except Exception:
            await message.reply_text(f"✅ متن منشی تنظیم شد.")
        return

    if cmd in (".ریست منشی", "ریست منشی"):
        USERS_REPLIED_IN_SECRETARY[user_id] = set()
        try:
            data_manager.save_replied_users(user_id, set())
        except Exception:
            pass
        try:
            await message.edit_text("✅ لیست پاسخ‌داده‌شده‌های منشی پاک شد.")
        except Exception:
            pass
        return

    # لغو تقلب — پاک کردن پیام لغو، هیچ پیام اضافه‌ای نفرست
    if cmd in ("لغو", ".لغو", ".لغو تقلب", "لغو تقلب") and CHEAT_RUNNING.get(user_id):
        CHEAT_CANCEL[user_id] = True
        try:
            await message.delete()
        except Exception:
            try:
                await client.delete_messages(message.chat.id, message.id)
            except Exception:
                pass
        return


# ========== 🎰 تقلب ==========
    cheat_cmd = (cmd or "").strip()

    async def _del_cmd_msg():
        """پاک کردن دستور کاربر — هم گروه هم پیوی"""
        try:
            await client.delete_messages(message.chat.id, message.id)
        except Exception:
            try:
                await message.delete()
            except Exception:
                pass

    if cheat_cmd in (".بولینگ",):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎳", {6}, 40, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ بعد از چند تلاش استرایک نیومد. دوباره بزن.")
            except Exception:
                pass
        return

    if cheat_cmd in (".بسکتبال",):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🏀", {5}, 40, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ توپ داخل سبد نیفتاد. دوباره امتحان کن.")
            except Exception:
                pass
        return

    if cheat_cmd in (".فوتبال",):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "⚽", {5}, 40, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ گل نشد. دوباره بزن.")
            except Exception:
                pass
        return

    # تاس ۱ تا ۶
    dice_match = re.match(r"^\.تاس\s*([1-6۶])$", cheat_cmd.replace("۶", "6").replace("۵", "5").replace("۴", "4").replace("۳", "3").replace("۲", "2").replace("۱", "1"))
    if dice_match:
        target = int(dice_match.group(1))
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎲", {target}, 40, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, f"❌ تاس {target} نیومد. دوباره بزن.")
            except Exception:
                pass
        return

    if cheat_cmd in (".اسلات 777", ".اسلات۷۷۷", ".اسلات ۷۷۷"):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎰", {64}, 55, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ جکپات ۷۷۷ نیومد. دوباره بزن.")
            except Exception:
                pass
        return

    if cheat_cmd in (".اسلات لیمو",):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎰", {43}, 50, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ سه لیمو نیومد.")
            except Exception:
                pass
        return

    if cheat_cmd in (".اسلات انگور",):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎰", {22}, 50, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ سه انگور نیومد.")
            except Exception:
                pass
        return

    if cheat_cmd in (".اسلات Bar", ".اسلات bar", ".اسلات بار", ".اسلات BAR"):
        await _del_cmd_msg()
        ok, val, tries = await cheat_send_dice(client, message.chat.id, "🎰", {1}, 50, user_id=user_id)
        if ok is False:
            try:
                await client.send_message(message.chat.id, "❌ سه بار نیومد.")
            except Exception:
                pass
        return

    if cmd == "لیست دشمن":
        enemies = ACTIVE_ENEMIES.get(user_id, set())
        await message.edit_text(f"📜 تعداد دشمنان فعال: {len(enemies)}")
        return

    if cmd.startswith("تنظیم منشی "):
        new_msg = cmd.split("تنظیم منشی ", 1)[1].strip()
        if new_msg:
            SECRETARY_CUSTOM_MESSAGES[user_id] = new_msg
            data_manager.update_user_data(user_id, {"settings": {"secretary_msg": new_msg}})
            await message.edit_text(f"✅ متن منشی تنظیم شد:\n\n`{new_msg}`")
        else:
            await message.edit_text("⚠️ لطفا متن منشی را وارد کنید.")
        return

    # ========== دانلود ویدیو ==========
    if cmd.startswith("دانلود ") or cmd.startswith(".دانلود "):
        url = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not url.startswith("http"):
            await message.edit_text("❌ لینک نامعتبر است!")
            return
        await message.edit_text("⏳ در حال دانلود...")
        filename, error = await download_media(url, "video")
        if error:
            await message.edit_text(error)
            return
        try:
            await client.send_video(message.chat.id, filename, caption="✅ دانلود شد | self MR")
            await message.delete()
        except Exception as e:
            try:
                await client.send_document(message.chat.id, filename, caption="✅ دانلود شد | self MR")
                await message.delete()
            except Exception as e2:
                await message.edit_text(f"❌ ارسال ناموفق: {e2}")
        try:
            os.remove(filename)
        except:
            pass
        return

    # ========== دانلود صوت ==========
    if cmd.startswith("صوت ") or cmd.startswith(".صوت "):
        url = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not url.startswith("http"):
            await message.edit_text("❌ لینک نامعتبر است!")
            return
        await message.edit_text("⏳ در حال استخراج صوت...")
        filename, error = await download_media(url, "audio")
        if error:
            await message.edit_text(error)
            return
        try:
            await client.send_audio(message.chat.id, filename, caption="🎵 صوت آماده شد | self MR")
            await message.delete()
        except Exception as e:
            await message.edit_text(f"❌ ارسال ناموفق: {e}")
        try:
            os.remove(filename)
        except:
            pass
        return

    # ========== قیمت ارز با نقطه ==========
    if cmd.startswith(".") and len(cmd) > 1 and not cmd.startswith(".تبدیل") and not cmd.startswith(".صدا") and not cmd.startswith(".دانلود") and not cmd.startswith(".صوت"):
        raw = cmd[1:].strip()
        # فقط یک کلمه یا عبارت ارز
        if raw and chr(10) not in raw and len(raw) < 30:
            alias = CURRENCY_ALIASES.get(raw) or CURRENCY_ALIASES.get(raw.lower())
            if alias:
                await message.edit_text(f"⏳ در حال دریافت قیمت {raw}...")
                price, extra = await fetch_currency_price(alias)
                name = CURRENCY_NAMES.get(alias, raw)
                if price is None:
                    await message.edit_text(extra or "❌ خطا در دریافت قیمت")
                else:
                    time_str = datetime.now(TEHRAN_TIMEZONE).strftime('%H:%M')
                    now_t = datetime.now(TEHRAN_TIMEZONE).strftime("%H:%M:%S")
                    upd = f"\n📅 بروزرسانی زنده: {now_t}"
                    await message.edit_text(
                        f"💱 قیمت {name} الان:\n\n"
                        f"💰 {price}\n"
                        f"📡 منبع: tgju.org\n"
                        f"⏱ {time_str}{upd}"
                    )
                return

    # ========== انتخاب صدای TTS ==========
    if cmd.startswith(".صدا "):
        voice_name = cmd.replace(".صدا ", "").strip()
        if voice_name in TTS_VOICES:
            TTS_VOICE_STATUS[user_id] = voice_name
            await message.edit_text(f"✅ صدای TTS تنظیم شد: {TTS_VOICES[voice_name]['label']}")
        else:
            voices = " | ".join(TTS_VOICES.keys())
            await message.edit_text(f"❌ صدا نامعتبر.\nصداهای موجود: {voices}\nمثال: `.صدا زن`")
        return

    # ========== تبدیل متن به ویس ==========
    if cmd.startswith(".تبدیل متن به ویس") or cmd.startswith("تبدیل متن به ویس"):
        if cmd.startswith("."):
            text_part = cmd.replace(".تبدیل متن به ویس", "", 1).strip()
        else:
            text_part = cmd.replace("تبدیل متن به ویس", "", 1).strip()
        if not text_part:
            await message.edit_text("❌ متن را بعد از دستور بنویس.\nمثال:\n`.تبدیل متن به ویس سلام دوست عزیز`")
            return
        voice_key = TTS_VOICE_STATUS.get(user_id, "زن")
        try:
            await message.edit_text("⏳ در حال ساخت ویس...")
        except Exception:
            pass
        path, err = await text_to_speech(text_part, voice_key)
        if err:
            try:
                await message.edit_text(err)
            except Exception:
                await client.send_message(message.chat.id, err)
            return
        try:
            await client.send_voice(message.chat.id, path, caption=f"🎤 {text_part[:80]}")
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as e:
            try:
                await message.edit_text(f"❌ ارسال ویس ناموفق: {e}")
            except Exception:
                await client.send_message(message.chat.id, f"❌ ارسال ویس ناموفق: {e}")
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return

    # ========== آیدی (با یا بدون ریپلای) ==========
    if cmd == "آیدی" or cmd == ".آیدی":
        target = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target = message.reply_to_message.from_user
        else:
            target = await client.get_me()
        try:
            chat = await client.get_chat(target.id)
        except Exception:
            chat = target

        # تعداد عکس پروفایل
        photo_count = 0
        try:
            async for _ in client.get_chat_photos(target.id, limit=100):
                photo_count += 1
        except Exception:
            photo_count = getattr(chat, "photo", None) and 1 or 0

        bio = ""
        try:
            bio = getattr(chat, "bio", None) or ""
        except Exception:
            bio = ""

        username = f"@{target.username}" if getattr(target, "username", None) else "ندارد"
        phone = getattr(target, "phone_number", None) or "مخفی / در دسترس نیست"
        dc_id = getattr(getattr(target, "photo", None), "dc_id", None) or "-"

        info = (
            f"👤 اطلاعات کاربر | self MR\n\n"
            f"🆔 آیدی عددی: `{target.id}`\n"
            f"👤 نام: {target.first_name or ''} {target.last_name or ''}\n"
            f"📱 یوزرنیم: {username}\n"
            f"📞 شماره: {phone}\n"
            f"📝 بیو: {bio or 'ندارد'}\n"
            f"🖼 تعداد عکس پروفایل: {photo_count}\n"
            f"🌐 DC: {dc_id}"
        )
        try:
            await message.edit_text(info)
        except Exception:
            await client.send_message(message.chat.id, info)
        return

    # ========== اسم چرخشی ==========
    if cmd.startswith(".افزودن اسم ") or cmd.startswith("افزودن اسم "):
        name = cmd.split(" ", 2)[-1].strip() if cmd.count(" ") >= 2 else ""
        # .افزودن اسم علی
        parts = cmd.lstrip(".").split(None, 2)
        if len(parts) < 3:
            await message.edit_text("❌ مثال:\\n`.افزودن اسم علی`")
            return
        name = parts[2].strip()[:64]
        if not name:
            await message.edit_text("❌ اسم خالی است.")
            return
        lst = ROTATING_NAMES.get(user_id) or []
        lst.append(name)
        ROTATING_NAMES[user_id] = lst
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ اسم اضافه شد: `{name}`\\nتعداد لیست: {len(lst)}")
        return

    if cmd.startswith(".تنظیم تایم اسم ") or cmd.startswith("تنظیم تایم اسم "):
        parts = cmd.lstrip(".").split()
        try:
            sec = int(parts[-1])
        except Exception:
            await message.edit_text("❌ مثال:\\n`.تنظیم تایم اسم 5`")
            return
        if sec < 3:
            await message.edit_text("❌ حداقل ۳ ثانیه.")
            return
        if sec > 3600:
            await message.edit_text("❌ حداکثر ۳۶۰۰ ثانیه.")
            return
        ROTATING_NAME_INTERVAL[user_id] = sec
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ تایم اسم چرخشی: هر {sec} ثانیه")
        return

    if cmd in (".پاکسازی لیست اسم چرخشی", "پاکسازی لیست اسم چرخشی", ".پاکسازی لیست اسم", "پاکسازی لیست اسم"):
        ROTATING_NAMES[user_id] = []
        ROTATING_NAME_INDEX[user_id] = 0
        persist_all_user_settings(user_id)
        await message.edit_text("✅ لیست اسامی چرخشی پاک شد.")
        return

    if cmd in (".لیست اسامی چرخشی", "لیست اسامی چرخشی", ".لیست اسامی", "لیست اسامی"):
        lst = ROTATING_NAMES.get(user_id) or []
        if not lst:
            await message.edit_text("لیست اسامی خالی است.")
            return
        body = "\\n".join(f"{i}. {n}" for i, n in enumerate(lst, 1))
        interval = ROTATING_NAME_INTERVAL.get(user_id, 10)
        st = "on ✅" if ROTATING_NAME_STATUS.get(user_id) else "off ❌"
        await message.edit_text(
            f"لیست اسامی چرخشی | self MR\\n\\n{body}\\n\\n"
            f"⏱ تایم: {interval} ثانیه\\nوضعیت: {st}"
        )
        return

    if cmd in (".اسم چرخشی روشن", "اسم چرخشی روشن"):
        lst = ROTATING_NAMES.get(user_id) or []
        if len(lst) < 1:
            await message.edit_text("❌ اول با `.افزودن اسم ...` اسم اضافه کنید.")
            return
        ROTATING_NAME_STATUS[user_id] = True
        persist_all_user_settings(user_id)
        await message.edit_text("✅ اسم چرخشی روشن شد | self MR")
        return

    if cmd in (".اسم چرخشی خاموش", "اسم چرخشی خاموش"):
        ROTATING_NAME_STATUS[user_id] = False
        persist_all_user_settings(user_id)
        await message.edit_text("❌ اسم چرخشی خاموش شد | self MR")
        return

    # ========== آهنگ چرخشی (از آهنگ‌های پروفایل) ==========
    if cmd.startswith(".تنظیم تایم آهنگ ") or cmd.startswith("تنظیم تایم آهنگ "):
        parts = cmd.lstrip(".").split()
        try:
            hours = int(parts[-1])
        except Exception:
            await message.edit_text("❌ مثال:\n`.تنظیم تایم آهنگ 2`\n(ساعت — حداقل ۱ حداکثر ۲۴)")
            return
        if hours < 1:
            await message.edit_text("❌ حداقل ۱ ساعت.")
            return
        if hours > 24:
            await message.edit_text("❌ حداکثر ۲۴ ساعت.")
            return
        ROTATING_MUSIC_INTERVAL[user_id] = hours
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ تایم آهنگ چرخشی: هر {hours} ساعت")
        return

    if cmd in (".ثبت آهنگ چرخشی", "ثبت آهنگ چرخشی"):
        if not message.reply_to_message or not (message.reply_to_message.audio or message.reply_to_message.document):
            await message.edit_text("❌ روی یک پیام آهنگ ریپلای کن و بفرست:\n`.ثبت آهنگ چرخشی`")
            return
        rep = message.reply_to_message
        media = rep.audio or rep.document
        try:
            from pyrogram.file_id import FileId
            fid = FileId.decode(media.file_id)
            title = getattr(media, "title", None) or getattr(media, "file_name", None) or f"آهنگ {len(ROTATING_MUSIC.get(user_id) or [])+1}"
            lst = list(ROTATING_MUSIC.get(user_id) or [])
            lst.append({
                "title": str(title)[:64],
                "doc_id": int(fid.media_id),
                "access_hash": int(fid.access_hash),
                "file_reference": fid.file_reference or b"",
                "file_id": media.file_id,
                "chat_id": rep.chat.id,
                "msg_id": rep.id,
                "from_profile": False,
            })
            ROTATING_MUSIC[user_id] = lst
            persist_all_user_settings(user_id)
            await message.edit_text(f"✅ ثبت شد: `{title}`\n📊 تعداد: `{len(lst)}`")
        except Exception as e:
            await message.edit_text(f"❌ خطا در ثبت: {e}")
        return

    if cmd in (".آهنگ چرخشی روشن", "آهنگ چرخشی روشن"):
        await message.edit_text("⏳ در حال خواندن آهنگ‌های پروفایل...")
        try:
            tracks = await _fetch_profile_music_tracks(client)
        except Exception as e:
            logging.warning(f"fetch music on: {e}")
            tracks = []
        stored = list(ROTATING_MUSIC.get(user_id) or [])
        if len(tracks) < 2 and len(stored) >= 2:
            tracks = stored
        if len(tracks) < 2:
            await message.edit_text(
                "❌ حداقل ۲ آهنگ نیاز است.\n\n"
                "روش ۱: ۲–۳ آهنگ روی پروفایل بگذارید و دوباره روشن کنید.\n"
                "روش ۲: ریپلای روی آهنگ + `.ثبت آهنگ چرخشی` (حداقل ۲ بار)\n"
                "بعد `.آهنگ چرخشی روشن`"
            )
            return
        ROTATING_MUSIC[user_id] = tracks
        ROTATING_MUSIC_STATUS[user_id] = True
        ROTATING_MUSIC_INDEX[user_id] = 0
        persist_all_user_settings(user_id)
        try:
            ok = await _apply_profile_music(client, user_id, tracks[0])
            ROTATING_MUSIC_INDEX[user_id] = 1 % len(tracks)
        except Exception:
            ok = False
        interval = ROTATING_MUSIC_INTERVAL.get(user_id, 1)
        names = "\n".join(f"• {t.get('title','آهنگ')}" for t in tracks[:8])
        await message.edit_text(
            f"✅ آهنگ چرخشی روشن شد | self MR\n\n"
            f"🎵 تعداد: `{len(tracks)}`\n"
            f"⏱ هر `{interval}` ساعت یک‌بار عوض می‌شود\n\n"
            f"{names}"
        )
        return

    if cmd in (".آهنگ چرخشی خاموش", "آهنگ چرخشی خاموش"):
        ROTATING_MUSIC_STATUS[user_id] = False
        persist_all_user_settings(user_id)
        await message.edit_text("❌ آهنگ چرخشی خاموش شد | self MR")
        return

    # ========== ساعت کشورها ==========
    if cmd.startswith(".ساعت ") or cmd.startswith("ساعت ") or cmd.startswith(".زمان "):
        place = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not place:
            await message.edit_text("❌ مثال:\n`.ساعت ایران`\n`.ساعت Tokyo`")
            return
        txt = await get_time_for_place(place)
        await message.edit_text(txt)
        return

    # ========== آب و هوا ==========
    if cmd.startswith(".آب و هوا ") or cmd.startswith("آب و هوا ") or cmd.startswith(".هوای "):
        place = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        # برای ".آب و هوا تهران" split با ۱ کافی نیست چون ۲ فاصله دارد
        for prefix in (".آب و هوا ", "آب و هوا ", ".هوای "):
            if cmd.startswith(prefix):
                place = cmd[len(prefix):].strip()
                break
        if not place:
            await message.edit_text("❌ مثال:\n`.آب و هوا تهران`")
            return
        await message.edit_text("⏳ دریافت آب‌وهوا...")
        txt = await get_weather_for_place(place)
        await message.edit_text(txt)
        return

    # ========== ویس به متن ==========
    if cmd in (".ویس به متن", "ویس به متن", ".تبدیل ویس به متن", "تبدیل ویس به متن"):
        await message.edit_text("⏳ در حال تبدیل ویس به متن...")
        txt = await voice_to_text(client, message)
        await message.edit_text(txt)
        return



    # ========== سندر فور همگانی (یک دستور) ==========
    if cmd in (".تنظیم سندر فور", "تنظیم سندر فور", ".تنظیم بنر فور", "تنظیم بنر فور", ".سندر فور", "سندر فور"):
        reply = message.reply_to_message
        if not reply:
            await message.edit_text(
                "❌ روی پیام بنر **ریپلای** کن و بفرست:\n`.تنظیم سندر فور`"
            )
            return
        bchat = reply.chat.id if reply.chat else message.chat.id
        bmsg = reply.id
        st = SENDER_MASS.get(user_id) or {}
        if st.get("running"):
            await message.edit_text("⚠️ سندر قبلی هنوز در حال اجراست.\nاول `.سندر فور خاموش` بزن.")
            return
        await message.edit_text(
            "🚀 سندر فور شروع شد...\n"
            "به همه گپ / کانال / پیوی‌ها فوروارد می‌شود.\n"
            "گزارش در Saved Messages می‌آید.\n"
            "توقف: `.سندر فور خاموش`"
        )
        asyncio.create_task(mass_forward_banner(client, user_id, bchat, bmsg))
        return

    if cmd in (".سندر فور خاموش", "سندر فور خاموش"):
        st = SENDER_MASS.get(user_id) or {}
        st["running"] = False
        SENDER_MASS[user_id] = st
        await message.edit_text("⏹ سندر فور متوقف شد.")
        return

    if cmd in (".سندر فور وضعیت", "سندر فور وضعیت"):
        st = SENDER_MASS.get(user_id) or {}
        running = "در حال اجرا ✅" if st.get("running") else "خاموش ❌"
        await message.edit_text(
            f"📣 وضعیت سندر فور | self MR\n\n"
            f"وضعیت: {running}\n"
            f"✅ موفق کل: {st.get('sent', 0)}\n"
            f"❌ ناموفق کل: {st.get('failed', 0)}\n"
            f"📋 کل هدف: {st.get('total', 0)}\n\n"
            f"👤 پیوی ارسال‌شده: {st.get('sent_pv', 0)}\n"
            f"👥 گپ ارسال‌شده: {st.get('sent_group', 0)}\n"
            f"📢 کانال ارسال‌شده: {st.get('sent_channel', 0)}"
        )
        return

    # ========== سندر ==========
    if cmd in (".تنظیم بنر سندر", "تنظیم بنر سندر"):
        reply = message.reply_to_message
        if not reply:
            await message.edit_text("❌ روی بنر ریپلای کن:\n`.تنظیم بنر سندر`")
            return
        chat_id = message.chat.id
        _sender_set(
            user_id, chat_id,
            banner_chat_id=reply.chat.id if reply.chat else chat_id,
            banner_msg_id=reply.id,
            mode="copy",
        )
        await message.edit_text(
            "✅ بنر سندر (کپی) ثبت شد | self MR\n"
            "با `.سندر روشن 100` فعال کن."
        )
        return

    if cmd in (".تنظیم بنر فور", "تنظیم بنر فور", ".تنظیم بنر فوروارد"):
        reply = message.reply_to_message
        if not reply:
            await message.edit_text("❌ روی بنر ریپلای کن:\n`.تنظیم بنر فور`")
            return
        chat_id = message.chat.id
        _sender_set(
            user_id, chat_id,
            banner_chat_id=reply.chat.id if reply.chat else chat_id,
            banner_msg_id=reply.id,
            mode="forward",
        )
        await message.edit_text(
            "✅ بنر فور (فوروارد) ثبت شد | self MR\n"
            "با `.سندر روشن 100` فعال کن."
        )
        return

    if cmd.startswith(".سندر روشن") or cmd.startswith("سندر روشن"):
        parts = cmd.split()
        limit = 100
        for p in parts:
            if p.isdigit():
                limit = int(p)
                break
        limit = max(50, min(200, limit))
        chat_id = message.chat.id
        cfg = _sender_get(user_id, chat_id)
        if not cfg.get("banner_msg_id"):
            await message.edit_text(
                "❌ اول بنر را تنظیم کن:\n"
                "`.تنظیم بنر سندر` یا `.تنظیم بنر فور`\n"
                "(روی پیام بنر ریپلای کن)"
            )
            return
        _sender_set(
            user_id, chat_id,
            enabled=True,
            hourly_limit=limit,
            sent_hour=0,
            hour_ts=int(time.time()),
        )
        await message.edit_text(
            f"✅ سندر روشن شد | self MR\n"
            f"📊 سهمیه: {limit} ارسال / ساعت\n"
            f"⏱ تاخیر: {cfg.get('delay', 60)} ثانیه\n"
            f"📤 حالت: {cfg.get('mode', 'copy')}"
        )
        return

    if cmd in (".سندر خاموش", "سندر خاموش"):
        chat_id = message.chat.id
        _sender_set(user_id, chat_id, enabled=False)
        await message.edit_text("❌ سندر خاموش شد | self MR")
        return

    if cmd.startswith(".سندر تاخیر") or cmd.startswith("سندر تاخیر"):
        parts = cmd.split()
        delay = None
        for p in parts:
            if p.isdigit():
                delay = int(p)
                break
        if delay is None:
            await message.edit_text("❌ مثال:\n`.سندر تاخیر 60`")
            return
        delay = max(5, min(3600, delay))
        chat_id = message.chat.id
        _sender_set(user_id, chat_id, delay=delay)
        await message.edit_text(f"✅ تاخیر سندر: {delay} ثانیه")
        return

    if cmd in (".بنر فور", "بنر فور", ".بنر فوروارد"):
        chat_id = message.chat.id
        _sender_set(user_id, chat_id, mode="forward")
        await message.edit_text("✅ حالت ارسال: **فوروارد**")
        return

    if cmd in (".بنر کپی", "بنر کپی"):
        chat_id = message.chat.id
        _sender_set(user_id, chat_id, mode="copy")
        await message.edit_text("✅ حالت ارسال: **کپی**")
        return

    if cmd in (".سندر وضعیت", "سندر وضعیت"):
        chat_id = message.chat.id
        cfg = _sender_get(user_id, chat_id)
        st = "روشن ✅" if cfg.get("enabled") else "خاموش ❌"
        has_banner = "✅" if cfg.get("banner_msg_id") else "❌"
        text = (
            f"📊 **وضعیت سندر | self MR**\n\n"
            f"وضعیت: {st}\n"
            f"بنر: {has_banner}\n"
            f"حالت: `{cfg.get('mode', 'copy')}`\n"
            f"تاخیر: `{cfg.get('delay', 60)}` ثانیه\n"
            f"سهمیه ساعتی: `{cfg.get('hourly_limit', 100)}`\n"
            f"ارسال این ساعت: `{cfg.get('sent_hour', 0)}`"
        )
        await message.edit_text(text)
        return

    if cmd in (".سندر حذف", "سندر حذف"):
        chat_id = message.chat.id
        uid = int(user_id)
        cid = str(int(chat_id))
        if uid in SENDER_CONFIG and cid in SENDER_CONFIG[uid]:
            del SENDER_CONFIG[uid][cid]
            try:
                persist_all_user_settings(user_id)
            except Exception:
                pass
        await message.edit_text("🗑 تنظیمات سندر این گروه حذف شد.")
        return



    # ========== ترجمه با ریپلای ==========
    if cmd in (".ترجمه", "ترجمه", ".ترجمه کن", "ترجمه کن"):
        reply = message.reply_to_message
        if not reply or not (reply.text or reply.caption):
            await message.edit_text("❌ روی یک پیام متنی ریپلای کن و بفرست:\n`.ترجمه`")
            return
        src_txt = (reply.text or reply.caption or "").strip()
        if not src_txt:
            await message.edit_text("❌ متنی برای ترجمه نیست.")
            return
        try:
            await message.edit_text("⏳ در حال ترجمه به فارسی...")
            fa = await translate_text(src_txt[:3000], "fa")
            await message.edit_text(f"🌐 **ترجمه | self MR**\n\n{fa}")
        except Exception as e:
            await message.edit_text(f"❌ خطا در ترجمه: {e}")
        return



    # ========== QR ==========
    if cmd.startswith(".متن به QR") or cmd.startswith("متن به QR") or cmd.startswith(".متن به qr") or cmd.startswith("متن به qr"):
        body = ""
        for p in (".متن به QR", "متن به QR", ".متن به qr", "متن به qr"):
            if cmd.startswith(p):
                body = cmd[len(p):].strip().lstrip("+").strip()
                break
        if not body and message.reply_to_message and message.reply_to_message.text:
            body = message.reply_to_message.text.strip()
        if not body:
            await message.edit_text("❌ مثال:\n`.متن به QR سلام دنیا`")
            return
        path = f"qr_{user_id}_{int(time.time())}.png"
        await message.edit_text("⏳ ساخت QR...")
        ok = await text_to_qr_image(body, path)
        if not ok:
            await message.edit_text("❌ ساخت QR ناموفق بود.")
            return
        try:
            await client.send_photo(message.chat.id, path, caption="📱 QR | self MR")
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as e:
            await message.edit_text(f"❌ ارسال QR: {e}")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
        return

    if cmd in (".QR به متن", "QR به متن", ".qr به متن", "qr به متن", ".کیوآر به متن"):
        rep = message.reply_to_message
        if not rep or not (rep.photo or (rep.document and (rep.document.mime_type or "").startswith("image"))):
            await message.edit_text("❌ روی یک عکس QR ریپلای کن و بفرست:\n`.QR به متن`")
            return
        await message.edit_text("⏳ خواندن QR...")
        path = await client.download_media(rep, file_name=f"qr_read_{user_id}_{int(time.time())}.jpg")
        if not path:
            await message.edit_text("❌ دانلود تصویر ناموفق.")
            return
        try:
            txt = await qr_image_to_text(path)
            if txt:
                await message.edit_text(f"📱 **متن QR:**\n\n`{txt}`")
            else:
                await message.edit_text("❌ متنی از QR خوانده نشد.")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
        return




    # ========== تبدیل ایموجی عادی به پریمیوم (سبک VTR) ==========
    
    if cmd in (".تاریخ میلادی بیو روشن", "تاریخ میلادی بیو روشن", ".بیو تاریخ روشن"):
        BIO_MILADI_DATE[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await apply_bio_miladi_date(client, user_id, force=True)
        except Exception as e:
            logging.warning("bio on: %s", e)
        await message.edit_text("✅ تاریخ میلادی در بیو روشن شد (هر روز خودکار به‌روز می‌شود).")
        return

    if cmd in (".تاریخ میلادی بیو خاموش", "تاریخ میلادی بیو خاموش", ".بیو تاریخ خاموش"):
        BIO_MILADI_DATE[user_id] = False
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await clear_bio_miladi_date(client, user_id)
        except Exception as e:
            logging.warning("bio off: %s", e)
        await message.edit_text("❌ تاریخ میلادی از بیو برداشته شد.")
        return

    if cmd in (".تاریخ", "تاریخ") or cmd.startswith(".تاریخ"):
        try:
            block = format_full_date_block()
            await message.edit_text(block)
        except Exception:
            try:
                await message.reply_text(format_full_date_block())
            except Exception as e:
                logging.warning("date cmd: %s", e)
        return

    if cmd in (".ساعت پروفایل روشن", "ساعت پروفایل روشن"):
        PROFILE_PHOTO_CLOCK[user_id] = True
        try:
            photos = []
            async for p in client.get_chat_photos("me", limit=1):
                photos.append(p)
            if photos:
                dl = await client.download_media(photos[0], file_name=profile_clock_base_path(user_id))
                if dl:
                    PROFILE_PHOTO_CLOCK_BASE[user_id] = dl
        except Exception:
            pass
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        await message.edit_text("✅ ساعت پروفایل روشن شد (تهران — عقربه دقیقه و ثانیه).")
        return
        return
    if cmd in (".ساعت پروفایل خاموش", "ساعت پروفایل خاموش"):
        PROFILE_PHOTO_CLOCK[user_id] = False
        PROFILE_PHOTO_CLOCK_LAST_MINUTE.pop(user_id, None)
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        try:
            await restore_profile_photo_from_base(client, user_id)
        except Exception as e:
            logging.warning(f"restore on off cmd: {e}")
        await message.edit_text("❌ ساعت پروفایل خاموش شد — عکس قبلی برگشت.")
        return

    if cmd in (".تبدیل ایموجی روشن", "تبدیل ایموجی روشن", ".ایموجی پریمیوم روشن"):
        EMOJI_PREMIUM_CONVERT[user_id] = True
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        await message.edit_text(format_emoji_premium_panel(user_id))
        return

    if cmd in (".تبدیل ایموجی خاموش", "تبدیل ایموجی خاموش", ".ایموجی پریمیوم خاموش"):
        EMOJI_PREMIUM_CONVERT[user_id] = False
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        await message.edit_text(format_emoji_premium_panel(user_id))
        return

    if cmd in (".پاکسازی لیست ایموجی", "پاکسازی لیست ایموجی", ".پاکسازی ایموجی"):
        EMOJI_CHAR_TO_PREMIUM[user_id] = {}
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        await message.edit_text("✅ لیست ایموجی پاک شد.\n\n" + format_emoji_premium_panel(user_id))
        return

    if cmd in (".لیست ایموجی پریمیوم", "لیست ایموجی پریمیوم") or cmd in (".وضعیت ایموجی", "وضعیت ایموجی"):
        await message.edit_text(format_emoji_premium_panel(user_id))
        return

    # .ثبت ایموجی + پریمیوم + عادی  (همه در یک پیام)
    if cmd.startswith(".ثبت ایموجی") or cmd.startswith("ثبت ایموجی"):
        # استخراج custom emoji از entities همین پیام
        ids = _extract_custom_emoji_ids(message)
        # متن بدون دستور
        body = message.text or message.caption or ""
        for p in (".ثبت ایموجی", "ثبت ایموجی"):
            if body.startswith(p):
                body = body[len(p):].strip()
                break
        # حذف surrogate/placeholder مربوط به کاستوم — باقی‌مانده = ایموجی عادی
        # کاراکترهای باقی‌مانده غیر فاصله
        normal = "".join(ch for ch in body if not ch.isspace())
        # اگر entity کاستوم جای کاراکتر گرفته، ممکن است ⭐ یا � باشد — نرمال را از انتهای متن بگیر
        if not ids and message.reply_to_message:
            ids = _extract_custom_emoji_ids(message.reply_to_message)
        if not ids:
            await message.edit_text(
                "❌ فرمت:\n"
                "`.ثبت ایموجی` + ایموجی‌پریمیوم + ایموجی عادی\n\n"
                "مثال: پیام را این‌طور بفرست که هم ایموجی پریمیوم داشته باشد هم عادی."
            )
            return
        # تشخیص ایموجی عادی: از body کاراکترهایی که custom نیستند
        # ساده: آخرین خوشه ایموجی غیرخالی
        import re as _re
        # همه emoji-like sequences
        candidates = _re.findall(
            r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+|[\u2764\u2665\u2705\u274C\u2B50\u2763]",
            body,
        )
        normal_emoji = candidates[-1] if candidates else normal[-2:] if normal else ""
        if not normal_emoji:
            await message.edit_text("❌ ایموجی عادی پیدا نشد. هر دو را در یک پیام بفرستید.")
            return
        cid = int(ids[0])
        bucket = dict(EMOJI_CHAR_TO_PREMIUM.get(user_id) or {})
        # اگر این عادی قبلاً نبود و ظرفیت پر است
        if normal_emoji not in bucket and len(bucket) >= MAX_PREMIUM_EMOJI_SLOTS:
            await message.edit_text(f"❌ حداکثر {MAX_PREMIUM_EMOJI_SLOTS} ایموجی می‌توانید ثبت کنید.\nاول `.پاکسازی لیست ایموجی`")
            return
        bucket[normal_emoji] = cid
        if normal_emoji.endswith("️") and len(normal_emoji) > 1:
            bucket[normal_emoji[:-1]] = cid
        EMOJI_CHAR_TO_PREMIUM[user_id] = bucket

        # ساخت استیکر از کاستوم‌ایموجی برای استفاده همگانی
        try:
            fid = await custom_emoji_to_sticker_file_id(client, cid, user_id)
            if fid:
                tmap = EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
                tmap[normal_emoji] = {"sticker_file_id": fid, "custom_emoji_id": int(cid)}
                if normal_emoji.endswith("\ufe0f") and len(normal_emoji) > 1:
                    tmap[normal_emoji[:-1]] = tmap[normal_emoji]
                EMOJI_PREMIUM_TEMPLATES[user_id] = tmap
                logging.info("registered sticker fid for uid=%s", user_id)
            try:
                await upload_custom_emoji_to_helper_bot(cid, user_id, normal_emoji)
            except Exception as e:
                logging.warning("helper upload on register: %s", e)
        except Exception as e:
            logging.warning("register sticker build: %s", e)

        # قالب برای کپی در گپ/پیوی (نه فقط سیو پیام متنی)
        try:
            from pyrogram.enums import MessageEntityType
            from pyrogram.types import MessageEntity
            ph = normal_emoji or "⭐"
            ent = MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=0,
                length=len(ph.encode("utf-16-le")) // 2,
                custom_emoji_id=int(cid),
            )
            tmpl = await client.send_message("me", ph, entities=[ent])
            tmap = EMOJI_PREMIUM_TEMPLATES.get(user_id) or {}
            tmap[normal_emoji] = (tmpl.chat.id, tmpl.id)
            if normal_emoji.endswith("️") and len(normal_emoji) > 1:
                tmap[normal_emoji[:-1]] = (tmpl.chat.id, tmpl.id)
            EMOJI_PREMIUM_TEMPLATES[user_id] = tmap
        except Exception as e:
            logging.warning(f"emoji template save: {e}")
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        # ذخیره در پیام‌های ذخیره‌شده
        try:
            await client.send_message(
                "me",
                f"⭐ ثبت ایموجی | self MR\nعادی: {normal_emoji}\nپریمیوم id: `{cid}`\nتعداد: {len(bucket)}/{MAX_PREMIUM_EMOJI_SLOTS}",
            )
        except Exception:
            pass
        await message.edit_text(
            f"✅ ثبت شد: {normal_emoji} → پریمیوم\n\n" + format_emoji_premium_panel(user_id)
        )
        return

    # ========== ایموجی پریمیوم ==========
    if cmd in (".لیست ایموجی", "لیست ایموجی", ".ایموجی لیست", "ایموجی لیست"):
        em = _user_premium_emojis(user_id)
        lines = ["⭐ **ایموجی پریمیوم | self MR**\n"]
        for name, cid in list(em.items())[:40]:
            lines.append(f"• `{name}` → `{cid}`")
        lines.append("\nارسال: `.ایموجی نام`\nمثال: `.ایموجی قلب`")
        lines.append("افزودن: ریپلای روی پیام دارای ایموجی پریمیوم + `.افزودن ایموجی نام`")
        await message.edit_text("\n".join(lines))
        return

    if cmd.startswith(".افزودن ایموجی ") or cmd.startswith("افزودن ایموجی "):
        name = cmd.split(" ", 2)[-1].strip().lstrip(".")
        if cmd.startswith(".افزودن ایموجی "):
            name = cmd[len(".افزودن ایموجی "):].strip()
        elif cmd.startswith("افزودن ایموجی "):
            name = cmd[len("افزودن ایموجی "):].strip()
        name = name.strip()
        if not name:
            await message.edit_text("❌ مثال:\n`.افزودن ایموجی قلب`\n(ریپلای روی پیام با ایموجی پریمیوم)")
            return
        cid = None
        # اگر عدد داده باشد
        parts = name.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            cid = int(parts[-1])
            name = " ".join(parts[:-1]).strip()
        if cid is None:
            if not message.reply_to_message:
                await message.edit_text("❌ روی پیامی که ایموجی پریمیوم دارد ریپلای کن\nیا بنویس:\n`.افزودن ایموجی قلب 5386650613544205592`")
                return
            ids = _extract_custom_emoji_ids(message.reply_to_message)
            if not ids:
                await message.edit_text("❌ در پیام ریپلای‌شده ایموجی پریمیوم پیدا نشد.")
                return
            cid = ids[0]
        bucket = PREMIUM_EMOJI_MAP.get(user_id) or {}
        bucket[name] = int(cid)
        PREMIUM_EMOJI_MAP[user_id] = bucket
        try:
            persist_all_user_settings(user_id)
        except Exception:
            pass
        await message.edit_text(f"✅ ایموجی `{name}` ثبت شد\n🆔 `{cid}`")
        return

    if cmd.startswith(".ایموجی ") or cmd.startswith("ایموجی "):
        name = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not name:
            await message.edit_text("❌ مثال: `.ایموجی قلب`\nلیست: `.لیست ایموجی`")
            return
        em = _user_premium_emojis(user_id)
        # match exact or partial
        cid = em.get(name)
        if cid is None:
            for k, v in em.items():
                if k in name or name in k:
                    cid = v
                    name = k
                    break
        if cid is None and name.isdigit():
            cid = int(name)
        if cid is None:
            await message.edit_text(f"❌ `{name}` پیدا نشد.\n`.لیست ایموجی`")
            return
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await send_premium_emoji(client, message.chat.id, int(cid))
        except Exception as e:
            err = str(e)
            if "PREMIUM" in err.upper() or "premium" in err.lower():
                await client.send_message(
                    message.chat.id,
                    "❌ اکانت شما به ایموجی پریمیوم دسترسی ندارد.\n"
                    "باید تلگرام Premium داشته باشد یا از ایموجی پک‌های مجاز استفاده کنید."
                )
            else:
                await client.send_message(message.chat.id, f"❌ ارسال ایموجی: {err[:150]}")
        return

    # ========== فضول پروفایل ==========
    if cmd in (".فضول ها", "فضول ها", ".فضول‌ها", "فضول‌ها"):
        try:
            await message.edit_text(format_profile_snoops(user_id))
        except Exception as e:
            await message.edit_text(f"❌ خطا: {e}")
        return
    # ========== میو خودکار ==========
    if cmd in (".میو روشن", "میو روشن"):
        chat_id = message.chat.id
        ctype = str(getattr(message.chat, "type", "")).lower()
        if "private" in ctype:
            await message.edit_text("❌ میو خودکار فقط داخل **گپ** کار می‌کند.")
            return
        s = MEOW_CHATS.get(user_id) or set()
        if not isinstance(s, set):
            try:
                s = set(int(x) for x in s)
            except Exception:
                s = set()
        s.add(int(chat_id))
        MEOW_CHATS[user_id] = s
        persist_all_user_settings(user_id)
        await message.edit_text(
            "✅ **میو خودکار روشن شد | self MR**\n\n"
            "هر ۵ دقیقه در **همین گپ** پیام «میو» ارسال می‌شود.\n"
            "خاموش: `.میو خاموش`"
        )
        return

    if cmd in (".میو خاموش", "میو خاموش"):
        chat_id = message.chat.id
        s = MEOW_CHATS.get(user_id) or set()
        if not isinstance(s, set):
            try:
                s = set(int(x) for x in s)
            except Exception:
                s = set()
        s.discard(int(chat_id))
        MEOW_CHATS[user_id] = s
        persist_all_user_settings(user_id)
        await message.edit_text("❌ میو خودکار در **همین گپ** خاموش شد.")
        return

    # ========== کیفیت عکس ==========
    if cmd in (".کیفیت عکس", "کیفیت عکس", ".بهبود عکس", "بهبود عکس", ".افزایش کیفیت", "افزایش کیفیت"):
        await enhance_photo_quality(client, message)
        return

    # ========== سرچ آهنگ ==========
    if cmd.startswith(".سرچ آهنگ ") or cmd.startswith("سرچ آهنگ "):
        for prefix in (".سرچ آهنگ ", "سرچ آهنگ "):
            if cmd.startswith(prefix):
                q = cmd[len(prefix):].strip()
                break
        else:
            q = ""
        if not q:
            await message.edit_text("❌ مثال:\n`.سرچ آهنگ شادمهر`")
            return
        await message.edit_text("⏳ در حال سرچ از یوتیوب / ساندکلود / ...")
        tracks = await search_songs_list(q)
        if not tracks:
            await message.edit_text(f"❌ آهنگی برای `{q}` پیدا نشد.")
            return
        tracks = tracks[:12]
        SONG_SEARCH_CACHE[user_id] = tracks
        PENDING_SONG_PICK[user_id] = True

        # لیست شماره‌دار در همان چت (گروه / پیوی)
        lines = [f"🎵 سرچ آهنگ | self MR\n\n🔎 {q}\n📌 {len(tracks)} نتیجه\n"]
        for i, t in enumerate(tracks):
            ar = (t.get("artist") or "").strip()
            ti = (t.get("title") or "آهنگ").strip()
            lines.append(f"{i+1}. {ar + ' — ' if ar else ''}{ti}")
        lines.append("\n👉 شماره آهنگ را بفرست (مثلاً `1`) یا دکمه پایین را بزن.")
        list_text = "\n".join(lines)
        if len(list_text) > 3900:
            list_text = list_text[:3900] + "\n…"

        rows = []
        for i, t in enumerate(tracks):
            ar = (t.get("artist") or "").strip()
            ti = (t.get("title") or "آهنگ").strip()
            label = f"🎵 {ar} — {ti}" if ar else f"🎵 {ti}"
            label = label[:64]
            rows.append([InlineKeyboardButton(label, callback_data=f"song_dl_{user_id}_{i}")])

        # 1) ویرایش پیام خود کاربر در همان چت
        try:
            await message.edit_text(list_text)
        except Exception as e:
            logging.warning(f"song edit list: {e}")
            try:
                await client.send_message(message.chat.id, list_text)
            except Exception:
                pass

        # 2) اینلاین منیجر در همان چت (اگر ربات عضو باشد)
        try:
            await manager_bot.send_message(
                message.chat.id,
                f"🎵 انتخاب سریع — {q}",
                reply_markup=InlineKeyboardMarkup(rows),
            )
        except Exception as e1:
            logging.warning(f"song inline chat: {e1}")

        # 3) کیبورد سلف در همان چت
        kb_rows = []
        for i, t in enumerate(tracks):
            ar = (t.get("artist") or "").strip()
            ti = (t.get("title") or "آهنگ").strip()
            label = f"{i+1}️⃣ {ar} — {ti}" if ar else f"{i+1}️⃣ {ti}"
            label = label[:64]
            kb_rows.append([KeyboardButton(label)])
        kb_rows.append([KeyboardButton("❌ لغو سرچ")])
        try:
            await client.send_message(
                message.chat.id,
                "⬇️ شماره یا دکمه آهنگ را انتخاب کن:",
                reply_markup=ReplyKeyboardMarkup(kb_rows, resize_keyboard=True, one_time_keyboard=True),
            )
        except Exception as e:
            logging.warning(f"song reply kb: {e}")
        return

    # انتخاب آهنگ با شماره / دکمه کیبورد سلف
    if PENDING_SONG_PICK.get(user_id) and SONG_SEARCH_CACHE.get(user_id):
        tracks = SONG_SEARCH_CACHE.get(user_id) or []
        pick = None
        raw = cmd.strip()
        if raw in ("❌ لغو سرچ", "لغو سرچ", "لغو"):
            PENDING_SONG_PICK[user_id] = False
            try:
                await message.reply_text("❌ سرچ لغو شد.", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
            try:
                await message.delete()
            except Exception:
                pass
            return
        mnum = re.match(r"^(\d{1,2})\s*[️⃣.)\-]?\s*", raw)
        if mnum:
            pick = int(mnum.group(1)) - 1
        if pick is None and (cmd.startswith(".آهنگ ") or cmd.startswith("آهنگ ")):
            try:
                pick = int(cmd.split(None, 1)[1].strip()) - 1
            except Exception:
                pick = None
        if pick is not None and 0 <= pick < len(tracks):
            track = tracks[pick]
            PENDING_SONG_PICK[user_id] = False
            q = (track.get("query") or f"{track.get('artist','')} {track.get('title','')}").strip()
            try:
                await message.reply_text(
                    f"⏳ دانلود...\n🎵 {track.get('artist','')} — {track.get('title','')}",
                    reply_markup=ReplyKeyboardRemove(),
                )
            except Exception:
                pass
            try:
                await message.delete()
            except Exception:
                pass
            result, err = await download_song_audio(q)
            if not result:
                result, err = await download_song_audio(track.get("title") or q)
            if not result:
                await client.send_message(message.chat.id, f"❌ دانلود ناموفق:\n`{err}`")
                return
            if track.get("title"):
                result["title"] = track.get("title") or result.get("title")
            if track.get("artist"):
                result["artist"] = track.get("artist") or result.get("artist")
            await send_downloaded_song(client, message.chat.id, result, status_msg=None)
            return

    # ========== عکس به PDF ==========
    if cmd in (".عکس به pdf", "عکس به pdf", ".عکس به PDF", "عکس به PDF", ".تبدیل عکس به pdf"):
        await photo_to_pdf(client, message)
        return

    # ========== PDF به عکس ==========
    if cmd in (".pdf به عکس", "pdf به عکس", ".PDF به عکس", ".تبدیل pdf به عکس"):
        await pdf_to_photo(client, message)
        return

    # ========== تگ ادمین / اعضا ==========
    if cmd in (".تگ ادمین", "تگ ادمین", ".تگ ادمین‌ها", "تگ ادمین‌ها"):
        await tag_admins(client, message)
        return

    if cmd in (".تگ اعضا", "تگ اعضا", ".تگ همه", "تگ همه"):
        await tag_members(client, message)
        return

    # ========== کامنت اول کانال ==========
    if cmd in (".کامنت اول روشن", "کامنت اول روشن"):
        FIRST_COMMENT_STATUS[user_id] = True
        persist_all_user_settings(user_id)
        await message.edit_text("✅ کامنت اول روشن شد | self MR")
        return
    if cmd in (".کامنت اول خاموش", "کامنت اول خاموش"):
        FIRST_COMMENT_STATUS[user_id] = False
        persist_all_user_settings(user_id)
        await message.edit_text("❌ کامنت اول خاموش شد | self MR")
        return
    if cmd.startswith(".تنظیم کامنت اول ") or cmd.startswith("تنظیم کامنت اول "):
        for prefix in (".تنظیم کامنت اول ", "تنظیم کامنت اول "):
            if cmd.startswith(prefix):
                text = cmd[len(prefix):].strip()
                break
        else:
            text = ""
        if not text:
            await message.edit_text("❌ مثال:\n`.تنظیم کامنت اول سلام دوستان 🔥`")
            return
        FIRST_COMMENT_TEXT[user_id] = text[:500]
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ متن کامنت اول تنظیم شد:\n`{text[:200]}`")
        return

    # ========== فونت متن با دستور ==========
    if cmd.startswith(".فونت ") or cmd.startswith("فونت "):
        name = cmd.split(None, 1)[-1].strip() if " " in cmd else ""
        # map persian names
        rev = {v: k for k, v in FONT_PERSIAN_NAMES.items()}
        rev.update({
            "بولد": "bold", "bold": "bold",
            "ایتالیک": "italic", "italic": "italic",
            "زیرخط": "underline", "underline": "underline",
            "خط‌خورده": "strikethrough", "خط خورده": "strikethrough", "strikethrough": "strikethrough",
            "اسپویلر": "spoiler", "spoiler": "spoiler",
            "مونو": "mono", "mono": "mono",
            "کد": "codeblock", "کدبلاک": "codeblock", "codeblock": "codeblock",
            "نقل قول": "quote", "quote": "quote",
            "خاموش": "none", "off": "none", "none": "none",
        })
        key = rev.get(name)
        if not key:
            opts = " | ".join(FONT_PERSIAN_NAMES.values())
            await message.edit_text(f"❌ فونت نامعتبر.\\nمثال: `.فونت بولد`\\n\\nگزینه‌ها: {opts} | خاموش")
            return
        TEXT_FONT_STATUS[user_id] = key
        persist_all_user_settings(user_id)
        label = FONT_PERSIAN_NAMES.get(key, key)
        await message.edit_text(f"✅ فونت متن: {label}" if key != "none" else "❌ فونت متن خاموش شد")
        return

    # ========== انیمیشن ایموجی ==========
    anim_key = None
    if cmd.startswith("."):
        maybe = cmd[1:].strip()
        if maybe in EMOJI_ANIMATIONS:
            anim_key = maybe
    if anim_key:
        asyncio.create_task(run_emoji_animation(client, message, anim_key))
        return

    # ========== هشدار ویرایش / حذف ==========
    if cmd in (".هشدار ویرایش روشن", "هشدار ویرایش روشن"):
        EDIT_ALERT_STATUS[user_id] = True
        data_manager.update_user_data(user_id, {"settings": {"edit_alert": True}})
        persist_all_user_settings(user_id)
        await message.edit_text("✅ هشدار ویرایش پیام روشن شد | self MR\nپیام قبل از ویرایش به Saved Messages می‌رود.")
        return
    if cmd in (".هشدار ویرایش خاموش", "هشدار ویرایش خاموش"):
        EDIT_ALERT_STATUS[user_id] = False
        data_manager.update_user_data(user_id, {"settings": {"edit_alert": False}})
        persist_all_user_settings(user_id)
        await message.edit_text("❌ هشدار ویرایش پیام خاموش شد | self MR")
        return
    if cmd in (".هشدار حذف روشن", "هشدار حذف روشن"):
        DELETE_ALERT_STATUS[user_id] = True
        data_manager.update_user_data(user_id, {"settings": {"delete_alert": True}})
        persist_all_user_settings(user_id)
        await message.edit_text("✅ هشدار حذف پیام روشن شد | self MR\nمتن پیام حذف‌شده به Saved Messages می‌رود.")
        return
    if cmd in (".هشدار حذف خاموش", "هشدار حذف خاموش"):
        DELETE_ALERT_STATUS[user_id] = False
        data_manager.update_user_data(user_id, {"settings": {"delete_alert": False}})
        persist_all_user_settings(user_id)
        await message.edit_text("❌ هشدار حذف پیام خاموش شد | self MR")
        return

    # ========== عضویت اجباری پیوی ==========
    if cmd in (".وضعیت عضویت اجباری", "وضعیت عضویت اجباری"):
        st = FORCE_JOIN_PV_STATUS.get(user_id, False)
        chs = FORCE_JOIN_CHANNELS.get(user_id) or []
        status = "on ✅" if st else "off ❌"
        await message.edit_text(
            f"عضویت اجباری پیوی | self MR\n\n"
            f"وضعیت: ({status})\n"
            f"کانال‌های ثبت‌شده: {len(chs)}"
        )
        return

    if cmd.startswith(".تنظیم عضویت ") or cmd.startswith("تنظیم عضویت "):
        ch = cmd.split(" ", 2)[-1].strip() if cmd.startswith(".") else cmd.split(" ", 2)[-1].strip()
        # بهتر:
        parts = cmd.lstrip(".").split()
        # تنظیم عضویت @channel
        if len(parts) < 3:
            await message.edit_text("❌ مثال:\n`.تنظیم عضویت @channel`")
            return
        ch = parts[2].strip()
        if not ch.startswith("@") and not ch.lstrip("-").isdigit():
            ch = "@" + ch
        lst = FORCE_JOIN_CHANNELS.get(user_id) or []
        if ch in lst:
            await message.edit_text(f"⚠️ {ch} قبلاً ثبت شده است.")
            return
        lst.append(ch)
        FORCE_JOIN_CHANNELS[user_id] = lst
        data_manager.update_user_data(user_id, {"settings": {"force_join_channels": lst}})
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ کانال {ch} اضافه شد.\nتعداد کل: {len(lst)}")
        return

    if cmd.startswith(".حذف عضویت ") or cmd.startswith("حذف عضویت "):
        parts = cmd.lstrip(".").split()
        if len(parts) < 3:
            await message.edit_text("❌ مثال:\n`.حذف عضویت @channel`")
            return
        ch = parts[2].strip()
        if not ch.startswith("@") and not ch.lstrip("-").isdigit():
            ch = "@" + ch
        lst = FORCE_JOIN_CHANNELS.get(user_id) or []
        if ch not in lst:
            await message.edit_text(f"⚠️ {ch} در لیست نیست.")
            return
        lst = [x for x in lst if x != ch]
        FORCE_JOIN_CHANNELS[user_id] = lst
        data_manager.update_user_data(user_id, {"settings": {"force_join_channels": lst}})
        persist_all_user_settings(user_id)
        await message.edit_text(f"✅ {ch} حذف شد.\nباقی‌مانده: {len(lst)}")
        return

    if cmd in (".لیست عضویت اجباری", "لیست عضویت اجباری"):
        lst = FORCE_JOIN_CHANNELS.get(user_id) or []
        if not lst:
            await message.edit_text("لیست خالی است.")
            return
        body = "\n".join(f"• {c}" for c in lst)
        await message.edit_text(f"لیست عضویت اجباری | self MR\n\n{body}\n\nتعداد: {len(lst)}")
        return

    if cmd in (".پاکسازی عضویت اجباری", "پاکسازی عضویت اجباری"):
        FORCE_JOIN_CHANNELS[user_id] = []
        data_manager.update_user_data(user_id, {"settings": {"force_join_channels": []}})
        persist_all_user_settings(user_id)
        await message.edit_text("✅ همه کانال‌های عضویت اجباری پاک شدند.")
        return

    if cmd in (".عضویت اجباری روشن", "عضویت اجباری روشن"):
        FORCE_JOIN_PV_STATUS[user_id] = True
        data_manager.update_user_data(user_id, {"settings": {"force_join_pv": True}})
        persist_all_user_settings(user_id)
        await message.edit_text("✅ عضویت اجباری پیوی روشن شد | self MR")
        return

    if cmd in (".عضویت اجباری خاموش", "عضویت اجباری خاموش"):
        FORCE_JOIN_PV_STATUS[user_id] = False
        data_manager.update_user_data(user_id, {"settings": {"force_join_pv": False}})
        persist_all_user_settings(user_id)
        await message.edit_text("❌ عضویت اجباری پیوی خاموش شد | self MR")
        return

    # ========== ویدیو مسیج / ویدیو گرد ==========
    if cmd in (".ویدیو مسیج", "ویدیو مسیج", ".ویدیو مسیج", ".ویدیومسیج"):
        if not message.reply_to_message:
            await message.edit_text("❌ روی یک ویدیو ریپلای کنید.\nمثال: ریپلای + `.ویدیو مسیج`")
            return
        try:
            await message.edit_text("⏳ در حال ساخت ویدیو گرد...")
        except Exception:
            pass
        path, err = await convert_video_to_note(client, message)
        if err:
            try:
                await message.edit_text(err)
            except Exception:
                await client.send_message(message.chat.id, err)
            return
        try:
            await client.send_video_note(message.chat.id, path)
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as e:
            try:
                # فال‌بک: ارسال ویدیو عادی
                await client.send_video(message.chat.id, path, caption="🎥 ویدیو گرد | self MR")
                try:
                    await message.delete()
                except Exception:
                    pass
            except Exception as e2:
                await message.edit_text(f"❌ ارسال ناموفق: {e2}")
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return

    # ========== تبدیل به استیکر ==========
    if cmd in (".تبدیل به استیکر", "تبدیل به استیکر"):
        if not message.reply_to_message:
            await message.edit_text("❌ روی یک عکس یا متن ریپلای کنید و دوباره بفرستید.")
            return
        try:
            await message.edit_text("⏳ در حال ساخت استیکر...")
        except Exception:
            pass
        path, err = await convert_message_to_sticker(client, message)
        if err:
            try:
                await message.edit_text(err)
            except Exception:
                await client.send_message(message.chat.id, err)
            return
        try:
            await client.send_sticker(message.chat.id, path)
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as e:
            try:
                # فال‌بک: ارسال به صورت فایل webp
                await client.send_document(message.chat.id, path, caption="🧷 استیکر")
                try:
                    await message.delete()
                except Exception:
                    pass
            except Exception as e2:
                await message.edit_text(f"❌ ارسال استیکر ناموفق: {e2}")
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return

    # ========== تغییر پروفایل ==========
    if cmd.startswith(".اسم ") or cmd.startswith("اسم "):
        new_name = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if not new_name:
            await message.edit_text("❌ مثال:\\n`.اسم نام جدید`")
            return
        if len(new_name) > 64:
            await message.edit_text("❌ اسم حداکثر ۶۴ کاراکتر.")
            return
        try:
            await client.update_profile(first_name=new_name)
            await message.edit_text(f"✅ اسم تغییر کرد:\\n`{new_name}`")
        except Exception as e:
            await message.edit_text(f"❌ خطا در تغییر اسم: {e}")
        return

    if cmd.startswith(".بیو ") or cmd.startswith("بیو ") or cmd.startswith(".بیوگرافی ") or cmd.startswith("بیوگرافی "):
        new_bio = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        if new_bio is None:
            new_bio = ""
        if len(new_bio) > 70:
            await message.edit_text("❌ بیو حداکثر ۷۰ کاراکتر است.")
            return
        try:
            await client.update_profile(bio=new_bio)
            await message.edit_text(f"✅ بیوگرافی تغییر کرد:\\n`{new_bio or 'خالی'}`")
        except Exception as e:
            await message.edit_text(f"❌ خطا در تغییر بیو: {e}")
        return

    if cmd.startswith(".یوزرنیم ") or cmd.startswith("یوزرنیم ") or cmd.startswith(".username "):
        uname = cmd.split(" ", 1)[1].strip().lstrip("@") if " " in cmd else ""
        if not uname:
            await message.edit_text("❌ مثال:\\n`.یوزرنیم myname`")
            return
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]{4,31}$", uname):
            await message.edit_text("❌ یوزرنیم نامعتبر (۵ تا ۳۲ کاراکتر، حرف اول انگلیسی).")
            return
        try:
            await client.set_username(uname)
            await message.edit_text(f"✅ یوزرنیم تغییر کرد:\\n@{uname}")
        except Exception as e:
            await message.edit_text(f"❌ خطا در تغییر یوزرنیم: {e}")
        return


    if not message.reply_to_message:
        return

    target_id = message.reply_to_message.from_user.id if message.reply_to_message.from_user else None

    if cmd in ("ذخیره", ".ذخیره"):
        reply = message.reply_to_message
        # پاک کردن بی‌صدا دستور تا طرف مقابل نفهمد
        try:
            await message.delete()
        except Exception:
            try:
                await client.delete_messages(message.chat.id, message.id)
            except Exception:
                try:
                    await message.edit_text("⁣")  # نامرئی — آخرین تلاش
                except Exception:
                    pass
        ok, err = await save_message_powerful(client, reply)
        # فقط در Saved Messages اطلاع بده — نه در همان چت
        try:
            if ok:
                await client.send_message("me", "✅ ذخیره شد (مخفی)")
            else:
                await client.send_message("me", err or "❌ ذخیره ناموفق")
        except Exception:
            pass
        return

    if cmd.startswith("تکرار "):
        try:
            count = int(cmd.split()[1])
            for _ in range(count):
                await message.reply_to_message.copy(message.chat.id)
            await message.delete()
        except:
            pass
        return

    if not target_id:
        return

    if cmd == "کپی روشن":
        user = await client.get_chat(target_id)
        me = await client.get_me()
        ORIGINAL_PROFILE_DATA[user_id] = {'first_name': me.first_name, 'bio': me.bio}
        COPY_MODE_STATUS[user_id] = True
        CLOCK_STATUS[user_id] = False
        target_photos = [p async for p in client.get_chat_photos(target_id, limit=1)]
        await client.update_profile(first_name=user.first_name, bio=(user.bio or "")[:70])
        if target_photos:
            await client.set_profile_photo(photo=target_photos[0].file_id)
        await message.edit_text("👤 هویت جعل شد.")
        return

    if cmd == "کپی خاموش":
        if user_id in ORIGINAL_PROFILE_DATA:
            data = ORIGINAL_PROFILE_DATA[user_id]
            COPY_MODE_STATUS[user_id] = False
            await client.update_profile(first_name=data.get('first_name'), bio=data.get('bio'))
            await message.edit_text("👤 هویت بازگردانده شد.")
        return

    if cmd == "دشمن روشن":
        s = ACTIVE_ENEMIES.get(user_id, set())
        s.add((target_id, message.chat.id))
        ACTIVE_ENEMIES[user_id] = s
        data_manager.save_enemies(user_id, s)
        await message.edit_text("⚔️ دشمن اضافه شد.")
        return

    if cmd == "دشمن خاموش":
        s = ACTIVE_ENEMIES.get(user_id, set())
        s.discard((target_id, message.chat.id))
        ACTIVE_ENEMIES[user_id] = s
        data_manager.save_enemies(user_id, s)
        await message.edit_text("🏳️ دشمن حذف شد.")
        return

    if cmd in ("دوست روشن", ".دوست روشن"):
        s = ACTIVE_FRIENDS.get(user_id, set())
        s.add((target_id, message.chat.id))
        ACTIVE_FRIENDS[user_id] = s
        try:
            data_manager.update_user_data(user_id, {"friends": [list(x) for x in s]})
        except Exception:
            pass
        await message.edit_text("💗 به لیست دوستان اضافه شد.")
        return

    if cmd in ("دوست خاموش", ".دوست خاموش"):
        s = ACTIVE_FRIENDS.get(user_id, set())
        s.discard((target_id, message.chat.id))
        ACTIVE_FRIENDS[user_id] = s
        try:
            data_manager.update_user_data(user_id, {"friends": [list(x) for x in s]})
        except Exception:
            pass
        await message.edit_text("🤍 از لیست دوستان حذف شد.")
        return

    if cmd in ("لیست دوستان", ".لیست دوستان"):
        s = ACTIVE_FRIENDS.get(user_id, set()) or set()
        await message.edit_text(f"💗 تعداد دوستان فعال: {len(s)}")
        return

    if cmd == "بلاک روشن":
        await client.block_user(target_id)
        await message.edit_text("🚫 کاربر بلاک شد.")
        return

    if cmd == "بلاک خاموش":
        await client.unblock_user(target_id)
        await message.edit_text("⭕️ کاربر آنبلاک شد.")
        return

    if cmd == "سکوت روشن":
        s = MUTED_USERS.get(user_id, set())
        s.add((target_id, message.chat.id))
        MUTED_USERS[user_id] = s
        data_manager.save_muted(user_id, s)
        await message.edit_text("🔇 کاربر ساکت شد.")
        return

    if cmd == "سکوت خاموش":
        s = MUTED_USERS.get(user_id, set())
        s.discard((target_id, message.chat.id))
        MUTED_USERS[user_id] = s
        data_manager.save_muted(user_id, s)
        await message.edit_text("🔊 کاربر از سکوت خارج شد.")
        return

    if cmd.startswith("ریاکشن ") and cmd != "ریاکشن خاموش":
        emoji = cmd.split()[1]
        t = AUTO_REACTION_TARGETS.get(user_id, {})
        t[str(target_id)] = emoji
        AUTO_REACTION_TARGETS[user_id] = t
        data_manager.save_reactions(user_id, t)
        await message.edit_text(f"👍 واکنش {emoji} تنظیم شد.")
        return

    if cmd == "ریاکشن خاموش":
        t = AUTO_REACTION_TARGETS.get(user_id, {})
        t.pop(str(target_id), None)
        AUTO_REACTION_TARGETS[user_id] = t
        data_manager.save_reactions(user_id, t)
        await message.edit_text("❌ واکنش حذف شد.")
        return


# ========== AI: ساخت عکس / تحلیل عکس / خلاصه چت ==========
async def _prompt_to_english(prompt: str) -> str:
    """پرامپت فارسی را برای مدل تصویر به انگلیسی نزدیک می‌کند"""
    p = (prompt or "").strip()
    if not p:
        return p
    # دیکشنری موضوعات رایج تا اشتباه ترجمه نشود
    _map = {
        "گربه": "a real domestic cat animal",
        "گربه ها": "real domestic cats animals",
        "سگ": "a real dog animal",
        "اسب": "a real horse animal",
        "شیر": "a real lion animal",
        "ببر": "a real tiger animal",
        "پرنده": "a real bird animal",
        "ماشین": "a car vehicle",
        "طبیعت": "beautiful nature landscape",
        "کوه": "mountain landscape",
        "دریا": "ocean sea landscape",
        "گل": "flowers",
        "درخت": "tree",
        "خانه": "a house building",
        "شهر": "city skyline",
        "غذا": "delicious food",
        "پیتزا": "pizza food",
        "مافیا": "mafia themed scene",
    }
    low = p.strip()
    for fa, en0 in _map.items():
        if low == fa or low.replace(" ", "") == fa:
            return en0
        if fa in low and len(low) <= len(fa) + 12:
            # «گربه سفید» و مشابه
            rest = low.replace(fa, "").strip()
            if rest:
                return f"{en0}, {rest}"
            return en0
    try:
        ascii_ratio = sum(1 for ch in p if ord(ch) < 128) / max(1, len(p))
        if ascii_ratio > 0.85:
            return p
    except Exception:
        pass
    try:
        from deep_translator import GoogleTranslator
        en = GoogleTranslator(source="auto", target="en").translate(p)
        if en and len(en.strip()) > 1:
            return en.strip()
    except Exception as e:
        logging.warning("prompt translate: %s", e)
    return p


async def ai_generate_image_file(prompt: str) -> str:
    """تولید تصویر دقیق — Gemini سپس چند منبع رایگان با پرامپت سخت‌گیرانه"""
    import urllib.parse
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("پرامپت خالی")
    seed = int(time.time()) % 1000000
    path = f"/tmp/ai_img_{int(time.time())}_{seed}.jpg"

    # ترجمه + نگه داشتن اصل
    en = await _prompt_to_english(prompt)
    # پرامپت دقیق: موضوع اصلی + ممنوعیت جایگزینی با انسان
    strict = (
        f"{en}, exact subject only, highly detailed photorealistic, "
        f"NOT a random person, NOT unrelated scene, focus on: {en}"
    )
    # اگر کاربر فارسی نوشته، اصل را هم بچسبان
    if any("\u0600" <= ch <= "\u06FF" for ch in prompt):
        strict = f"{strict}, (original request: {prompt})"

    # 1) Gemini
    if GEMINI_API_KEY:
        try:
            data = await _gemini_generate_image_bytes(f"{en} | {prompt}")
            if data and len(data) > 2000:
                out = path
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    out = path.replace(".jpg", ".png")
                with open(out, "wb") as f:
                    f.write(data)
                logging.info("gemini image ok bytes=%s", len(data))
                return out
        except Exception as e:
            logging.warning("gemini image gen skip: %s", e)

    # 2) منابع رایگان با پرامپت سخت
    candidates = [
        f"https://image.pollinations.ai/prompt/{urllib.parse.quote(strict)}?width=1024&height=1024&nologo=true&model=flux&seed={seed}&enhance=true",
        f"https://image.pollinations.ai/prompt/{urllib.parse.quote(en)}?width=1024&height=1024&nologo=true&model=flux&seed={seed}",
        f"https://image.pollinations.ai/prompt/{urllib.parse.quote(strict)}?width=768&height=768&nologo=true&seed={seed}",
        f"https://image.pollinations.ai/prompt/{urllib.parse.quote(en + ' animal' if 'cat' in en.lower() or 'dog' in en.lower() or 'گربه' in prompt or 'سگ' in prompt else en)}?width=1024&height=1024&nologo=true&model=flux&seed={seed+1}",
    ]
    last_err = None
    timeout = aiohttp.ClientTimeout(total=180)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/*,*/*",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for url in candidates:
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    data = await resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    logging.info("ai_img status=%s ctype=%s len=%s", resp.status, ctype, len(data))
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}"
                        continue
                    if len(data) < 3000:
                        last_err = f"small body {len(data)}"
                        continue
                    is_img = (
                        data[:3] == b"\xff\xd8\xff"
                        or data[:8] == b"\x89PNG\r\n\x1a\n"
                        or data[:4] == b"RIFF"
                        or "image" in ctype
                    )
                    if not is_img:
                        last_err = "not image"
                        continue
                    with open(path, "wb") as f:
                        f.write(data)
                    return path
            except Exception as e:
                last_err = str(e)
                logging.warning("pollinations img: %s", e)
    raise RuntimeError(last_err or "ساخت تصویر ناموفق")



async def ai_analyze_image_file(path: str) -> str:
    """تحلیل تصویر — اول Gemini، بعد بقیه"""
    if not path or not os.path.exists(path):
        return "❌ فایل تصویر دانلود نشد."
    if GEMINI_API_KEY:
        try:
            out = await _gemini_analyze_image_path(path)
            if out and len(out) > 15:
                return out
        except Exception as e:
            logging.warning("gemini analyze: %s", e)
    img_url = await _temp_host_image(path)
    # 1) pollinations openai endpoint
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
            content = [
                {
                    "type": "text",
                    "text": (
                        "Describe and analyze this image in fluent Persian (Farsi). "
                        "Say what it is, main objects, colors, mood, and details. Be accurate."
                    ),
                }
            ]
            if img_url:
                content.append({"type": "image_url", "image_url": {"url": img_url}})
            body = {"model": "openai", "messages": [{"role": "user", "content": content}]}
            async with session.post("https://text.pollinations.ai/openai", json=body) as resp:
                if resp.status == 200:
                    try:
                        js = await resp.json()
                        t = js["choices"][0]["message"]["content"]
                        if t and len(str(t).strip()) > 15:
                            return str(t).strip()[:3500]
                    except Exception:
                        raw = await resp.text()
                        if raw and len(raw) > 20 and not raw.strip().startswith("{"):
                            return raw.strip()[:3500]
    except Exception as e:
        logging.warning("pollinations vision: %s", e)

    # 2) text.pollinations با لینک
    if img_url:
        try:
            import urllib.parse
            q = urllib.parse.quote(
                "این تصویر را به فارسی دقیق توصیف کن (موضوع، اجسام، رنگ، حس): " + img_url
            )
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.get("https://text.pollinations.ai/" + q) as resp:
                    if resp.status == 200:
                        t = await resp.text()
                        if t and len(t.strip()) > 20:
                            return t.strip()[:3500]
        except Exception as e:
            logging.warning("pollinations text img: %s", e)

    # 3) DeepSeek متنی
    if DEEPSEEK_API_KEY and img_url:
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "لینک یک تصویر است. اگر از روی نام فایل/آدرس چیزی مشخص است بگو؛ "
                            "و یک تحلیل کوتاه فارسی از تصویرهای معمولی مشابه بنویس.\n"
                            f"{img_url}"
                        ),
                    }
                ],
                "temperature": 0.4,
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status == 200:
                        js = await resp.json()
                        t = js["choices"][0]["message"]["content"]
                        if t:
                            return t.strip()[:3500]
        except Exception as e:
            logging.warning("deepseek analyze: %s", e)

    return "❌ تحلیل تصویر انجام نشد. اینترنت سرور یا سرویس AI را چک کن."


async def ai_summarize_texts(texts: list) -> str:
    """خلاصه مکالمه"""
    cleaned = [t.strip() for t in (texts or []) if t and str(t).strip()]
    if not cleaned:
        return "❌ متنی برای خلاصه کردن پیدا نشد. در همین چت چند پیام باشد و دوباره `.خلاصه` بزن."
    joined = "\n".join(cleaned)[:8000]
    system = (
        "تو یک خلاصه‌کننده فارسی هستی. مکالمه زیر را کوتاه، مرتب و با بولت‌پوینت خلاصه کن. "
        "نکات مهم و نتیجه‌گیری را بنویس."
    )
    if GEMINI_API_KEY:
        try:
            out = await _gemini_generate_text(system, joined, max_tokens=1200, temperature=0.4)
            if out and len(out) > 20:
                return out
        except Exception as e:
            logging.warning("gemini summary: %s", e)
    if DEEPSEEK_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": joined},
                ],
                "temperature": 0.3,
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status == 200:
                        js = await resp.json()
                        t = js["choices"][0]["message"]["content"]
                        if t and len(t.strip()) > 10:
                            return t.strip()[:3500]
                    else:
                        logging.warning("deepseek summary status=%s", resp.status)
        except Exception as e:
            logging.warning("deepseek summary: %s", e)
    # pollinations text
    try:
        import urllib.parse
        prompt = system + "\n\n" + joined[:2500]
        url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    t = await resp.text()
                    if t and len(t.strip()) > 15:
                        return t.strip()[:3500]
    except Exception as e:
        logging.warning("pollinations summary: %s", e)
    # fallback خیلی ساده
    lines = cleaned[:8]
    return "📋 خلاصه سریع (سرویس AI در دسترس نبود):\n\n• " + "\n• ".join(x[:120] for x in lines)



async def start_bot_instance(session_string: str, phone: str, user_id: int, font_style: str = 'bold', disable_clock: bool = False):
    max_retries = 3
    retry_delay = 10
    
    client = None
    for attempt in range(max_retries):
        try:
            client = Client(f"bot_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=session_string)
            # ثبت هندلرها قبل از start تا پیام‌های خروجی از دست نروند
            client.add_handler(MessageHandler(god_mode_handler, filters.incoming & ~filters.me), group=-10)
            client.add_handler(MessageHandler(pv_cache_handler, filters.private & ~filters.me & ~filters.bot), group=-8)
            client.add_handler(MessageHandler(force_join_pv_handler, filters.private & ~filters.me & ~filters.bot), group=-6)
            client.add_handler(MessageHandler(lambda c, m: m.delete() if (c.me and PV_LOCK_STATUS.get(c.me.id)) else None, filters.private & ~filters.me & ~filters.bot), group=-5)
            try:
                from pyrogram.handlers import EditedMessageHandler, DeletedMessagesHandler, RawUpdateHandler
                client.add_handler(EditedMessageHandler(edit_alert_handler, filters.private & ~filters.me), group=-2)
                client.add_handler(DeletedMessagesHandler(delete_alert_handler), group=-2)
                client.add_handler(RawUpdateHandler(raw_delete_update_handler), group=-1)
            except Exception as e:
                logging.warning(f"edit/delete handlers: {e}")
            client.add_handler(MessageHandler(lambda c, m: c.read_chat_history(m.chat.id) if (c.me and AUTO_SEEN_STATUS.get(c.me.id)) else None, filters.private & ~filters.me), group=-4)
            client.add_handler(MessageHandler(incoming_message_manager, filters.all & ~filters.me), group=-3)
            # فونت متن — با اولویت بالا
            client.add_handler(MessageHandler(outgoing_sticker_premium_handler, filters.sticker & (filters.outgoing | filters.me)), group=-21)
            client.add_handler(MessageHandler(outgoing_message_modifier, filters.text & filters.outgoing), group=-20)
            client.add_handler(MessageHandler(pemoji_ds_outgoing, filters.text & (filters.outgoing | filters.me)), group=-18)
            client.add_handler(MessageHandler(outgoing_message_modifier, filters.text & filters.me), group=-19)
            client.add_handler(MessageHandler(help_controller, (filters.me | filters.outgoing) & filters.regex("^راهنما$")))
            client.add_handler(MessageHandler(panel_command_controller, (filters.me | filters.outgoing) & filters.regex(r"^(پنل|panel)$")))
            client.add_handler(MessageHandler(reply_based_controller, filters.me | filters.outgoing))
            client.add_handler(MessageHandler(first_comment_handler, filters.channel), group=5)
            client.add_handler(CallbackQueryHandler(song_download_callback, filters.regex(r"^song_dl_")), group=6)

            await client.start()
            user_id = (await client.get_me()).id
            logging.info(f"✅ Handlers ready for user {user_id} | text_font={TEXT_FONT_STATUS.get(user_id, 'none')}")
            break
        except Exception as e:
            if "FLOOD_WAIT" in str(e):
                wait_time = retry_delay * (attempt + 1)
                logging.warning(f"⏳ Flood wait {wait_time}s for {phone}, attempt {attempt+1}/{max_retries}")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"❌ Failed to start bot for {phone}: {e}")
                return
    else:
        logging.error(f"❌ Failed to start bot for {phone} after {max_retries} attempts")
        return

    if user_id in ACTIVE_BOTS:
        for t in ACTIVE_BOTS[user_id][1]:
            t.cancel()

    # بارگذاری کامل تنظیمات ذخیره‌شده — هیچ چیز ریست نشود
    apply_user_settings_from_db(user_id)
    # اگر هنوز فونت/ساعت در دیتابیس نبود
    if user_id not in USER_FONT_CHOICES or not USER_FONT_CHOICES.get(user_id):
        USER_FONT_CHOICES[user_id] = font_style
    if user_id not in CLOCK_STATUS:
        CLOCK_STATUS[user_id] = not disable_clock
    logging.info(f"📝 text_font after load uid={user_id} -> {TEXT_FONT_STATUS.get(user_id, 'none')}")

    enemy_filter = filters.create(lambda _, c, m: bool(m.from_user and ((m.from_user.id, m.chat.id) in ACTIVE_ENEMIES.get(c.me.id, set()) or GLOBAL_ENEMY_STATUS.get(c.me.id))))
    client.add_handler(MessageHandler(enemy_handler, enemy_filter & ~filters.me), group=1)
    friend_filter = filters.create(lambda _, c, m: bool(m.from_user and (m.from_user.id, m.chat.id) in ACTIVE_FRIENDS.get(c.me.id, set())))
    client.add_handler(MessageHandler(friend_handler, friend_filter & ~filters.me), group=1)

    client.add_handler(MessageHandler(pv_filter_media_handler, filters.private & ~filters.me), group=0)
    client.add_handler(MessageHandler(secretary_auto_reply_handler, filters.private & ~filters.me), group=1)

    tasks = [
        asyncio.create_task(update_profile_clock(client, user_id)),
        asyncio.create_task(update_profile_photo_clock_task(client, user_id)),
        asyncio.create_task(bio_miladi_daily_task(client, user_id)),
        asyncio.create_task(rotate_profile_name_task(client, user_id)),
        asyncio.create_task(rotate_profile_music_task(client, user_id)),
        asyncio.create_task(sender_loop_task(client, user_id)),
        asyncio.create_task(meow_loop_task(client, user_id)),
        asyncio.create_task(anti_login_task(client, user_id)),
        asyncio.create_task(status_action_task(client, user_id)),
        asyncio.create_task(starzy_photo_scheduler_task(client, user_id)),
    ]
    ACTIVE_BOTS[user_id] = (client, tasks)
    logging.info(f"✅ Bot started for user {user_id}")

manager_bot = Client("manager_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

# =============================================
# 🔥 پنل دو صفحه‌ای با پرچم انگلیس
# =============================================
def _styled_btn(text, callback_data, active=None, style=None):
    """ساخت دکمه رنگی
    active=True  -> سبز (success)
    active=False -> قرمز (danger)
    style: primary / success / danger
    """
    if style is None:
        if active is True:
            style = "success"
        elif active is False:
            style = "danger"
    btn = {"text": text, "callback_data": callback_data}
    if style in ("success", "danger", "primary"):
        btn["style"] = style
    return btn


def build_panel_keyboard(user_id, page=1):
    """پنل کلاسیک self MR — راهنماها در callback با متن دستورات"""
    try:
        page = int(page)
    except Exception:
        page = 1
    if page < 1:
        page = 1

    t_lang = AUTO_TRANSLATE_TARGET.get(user_id)
    current_font = TEXT_FONT_STATUS.get(user_id, "none")
    cur_clock = USER_FONT_CHOICES.get(user_id, "bold")

    def back_btn(to=1):
        return [_styled_btn("⬅️ بازگشت", f"panel_page_{to}_{user_id}", style="danger")]

    # ----- صفحه ۱: اصلی -----
    if page == 1:
        return [
            [
                _styled_btn("⏰ ساعت اسم", f"toggle_clock_{user_id}", CLOCK_STATUS.get(user_id, True)),
                _styled_btn("🕰 ساعت در پروفایل", f"panel_page_51_{user_id}", style="primary"),
                _styled_btn("🕐 فونت ساعت", f"panel_page_5_{user_id}", style="primary"),
            ],
            [
                _styled_btn("✏️ حالت متن", f"panel_page_2_{user_id}", style="primary"),
                _styled_btn("🗑 حذف پیام", f"panel_page_49_{user_id}", style="primary"),
                _styled_btn("🔐 رمز ایموجی", f"panel_page_50_{user_id}", style="primary"),
            ],
            [
                _styled_btn("⚡ اکشن‌ها", f"panel_page_4_{user_id}", style="primary"),
                _styled_btn("🔒 قفل پیوی", f"toggle_pv_{user_id}", PV_LOCK_STATUS.get(user_id, False)),
                _styled_btn("📊 حجم چت", f"panel_page_62_{user_id}", style="primary"),
            ],
            [
                _styled_btn("💱 قیمت ارز", f"panel_page_6_{user_id}", style="primary"),
                _styled_btn("🎤 متن→ویس", f"panel_page_7_{user_id}", style="primary"),
                _styled_btn("🧩 استیکر", f"panel_page_8_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🔐 عضویت اجباری", f"panel_page_9_{user_id}", style="primary"),
                _styled_btn("🎥 ویدیو گرد", f"panel_page_10_{user_id}", style="primary"),
                _styled_btn("💾 ذخیره", f"panel_page_13_{user_id}", style="primary"),
            ],
            [
                _styled_btn("✏️ اسم", f"panel_page_14_{user_id}", style="primary"),
                _styled_btn("📝 بیو", f"panel_page_15_{user_id}", style="primary"),
                _styled_btn("🔖 یوزرنیم", f"panel_page_16_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🎞 انیمیشن", f"panel_page_17_{user_id}", style="primary"),
                _styled_btn("🔄 اسم چرخشی", f"panel_page_18_{user_id}", style="primary"),
                _styled_btn("🎵 آهنگ چرخشی", f"panel_page_25_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🧠 هوش مصنوعی", f"panel_page_19_{user_id}", style="primary"),
                _styled_btn("🎵 متن آهنگ", f"panel_page_21_{user_id}", style="primary"),
                _styled_btn("🎰 تقلب", f"panel_page_24_{user_id}", style="primary"),
            ],
            [
                _styled_btn("📣 سندر", f"panel_page_34_{user_id}", style="primary"),
                _styled_btn("🐱 میو", f"panel_page_35_{user_id}", style="primary"),
                _styled_btn("🕐 ساعت کشورها", f"panel_page_26_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🌤 آب‌وهوا", f"panel_page_27_{user_id}", style="primary"),
                _styled_btn("🎤 ویس→متن", f"panel_page_28_{user_id}", style="primary"),
                _styled_btn("📄 عکس↔PDF", f"panel_page_29_{user_id}", style="primary"),
            ],
            [
                _styled_btn("👑 تگ اعضا", f"panel_page_30_{user_id}", style="primary"),
                _styled_btn("💬 کامنت اول", f"panel_page_31_{user_id}", style="primary"),
                _styled_btn("✨ کیفیت عکس", f"panel_page_32_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🔎 سرچ آهنگ", f"panel_page_33_{user_id}", style="primary"),
                _styled_btn("📸 اسکرین", f"panel_page_22_{user_id}", style="primary"),
                _styled_btn("🌐 ترجمه", f"panel_page_38_{user_id}", style="primary"),
            ],
            [
                _styled_btn("👁 فضول پروفایل", f"panel_page_37_{user_id}", style="primary"),
                _styled_btn("📱 QR", f"panel_page_39_{user_id}", style="primary"),
                _styled_btn("⭐ ایموجی پریمیوم", f"panel_page_40_{user_id}", style="primary"),
            ],
            [
                _styled_btn("📩 منشی آفلاین", f"panel_page_41_{user_id}", style="primary"),
                _styled_btn("🚫 فیلتر استیکر پیوی", f"panel_page_42_{user_id}", style="primary"),
                _styled_btn("🎞 فیلتر گیف پیوی", f"panel_page_43_{user_id}", style="primary"),
            ],
            [
                _styled_btn("⚔️ دشمن", f"panel_page_44_{user_id}", style="primary"),
                _styled_btn("💗 دوست", f"panel_page_45_{user_id}", style="primary"),
                _styled_btn("👍 ریاکشن", f"panel_page_46_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🔁 تکرار", f"panel_page_47_{user_id}", style="primary"),
                _styled_btn("🔇 سکوت/بلاک", f"panel_page_48_{user_id}", style="primary"),
                _styled_btn("📅 تاریخ", f"panel_page_52_{user_id}", style="primary"),
            ],
            [
                _styled_btn("📋 خلاصه چت", f"panel_page_55_{user_id}", style="primary"),
                _styled_btn("🔘 وضعیت سلف", f"panel_page_56_{user_id}", style="primary"),
                _styled_btn("🔤 فونت", f"panel_page_57_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🔑 پسوورد ساز", f"panel_page_58_{user_id}", style="primary"),
                _styled_btn("🧮 ماشین حساب", f"panel_page_59_{user_id}", style="primary"),
                _styled_btn("🖼 قاب پروفایل کل رنگ‌ها", f"panel_page_60_{user_id}", style="primary"),
            ],
            [
                _styled_btn("🔐 متن رمزی", f"panel_page_61_{user_id}", style="primary"),
            ],
            [
                _styled_btn("⭐ عکس استارزی", f"panel_page_63_{user_id}", style="primary"),
            ],
            [
                _styled_btn("⬅️ بستن پنل", f"close_panel_{user_id}", style="danger"),
            ],
        ]

    if page == 2:
        rows = []
        row = []
        for fk in FONT_KEYS_ORDER:
            pn = FONT_PERSIAN_NAMES.get(fk, fk)
            on = (current_font == fk)
            row.append(_styled_btn(pn, f"set_text_font_{fk}_{user_id}", on))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_styled_btn("❌ خاموش", f"set_text_font_none_{user_id}", current_font == "none")])
        rows.append(back_btn(1))
        return rows

    if page == 3:
        return [
            [
                _styled_btn("✏️ هشدار ویرایش", f"panel_page_11_{user_id}", style="primary"),
                _styled_btn("🗑 هشدار حذف", f"panel_page_12_{user_id}", style="primary"),
            ],
            [
                _styled_btn("📸 اسکرین", f"panel_page_22_{user_id}", style="primary"),
                _styled_btn("🔒 قفل پیوی", f"toggle_pv_{user_id}", PV_LOCK_STATUS.get(user_id, False)),
            ],
            back_btn(1),
        ]

    if page == 4:
        rows = []
        row = []
        try:
            items = list(ACTION_LABELS.items())
        except Exception:
            items = []
        for key, label in items:
            on = (ACTION_STATUS.get(user_id) == key)
            row.append(_styled_btn(label, f"set_action_{key}_{user_id}", on))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([
            _styled_btn("⌨️ تایپ", f"toggle_type_{user_id}", TYPING_MODE_STATUS.get(user_id, False)),
            _styled_btn("🎮 بازی", f"toggle_game_{user_id}", PLAYING_MODE_STATUS.get(user_id, False)),
        ])
        rows.append(back_btn(1))
        return rows

    if page == 5:
        rows = []
        row = []
        for key in CLOCK_FONT_ORDER:
            name = CLOCK_FONT_NAMES.get(key, key)
            is_on = (cur_clock == key)
            row.append(_styled_btn(name, f"set_clock_font_{key}_{user_id}", is_on))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append(back_btn(1))
        return rows

    # هوش مصنوعی + سرچ عکس داخلش
    if page == 19:
        return [
            [
                _styled_btn("📝 متن گسترده", f"panel_page_20_{user_id}", style="primary"),
                _styled_btn("🔎 سرچ عکس", f"panel_page_23_{user_id}", style="primary"),
                _styled_btn("📋 خلاصه چت", f"panel_page_55_{user_id}", style="primary"),
            ],
            back_btn(1),
        ]

    if page == 35:
        return [
            [ _styled_btn("🐱 میو خودکار", f"panel_page_36_{user_id}", style="primary") ],
            back_btn(1),
        ]
    if page == 36:
        return [
            [ _styled_btn(".میو روشن", "noop", style="success") ],
            [ _styled_btn(".میو خاموش", "noop", style="danger") ],
            back_btn(35),
        ]

    # همه صفحات راهنما: فقط بازگشت تمام‌عرض

    # ترجمه: زبان‌ها + راهنما
    if page == 38:
        return [
            [
                _styled_btn("🇬🇧 EN", f"lang_en_{user_id}", t_lang == "en"),
                _styled_btn("🇷🇺 RU", f"lang_ru_{user_id}", t_lang == "ru"),
                _styled_btn("🇨🇳 CN", f"lang_cn_{user_id}", t_lang == "zh-CN"),
            ],
            [ _styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger") ],
        ]





    if page == 53:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]
    if page == 54:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]
    if page == 61:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]
    if page == 62:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]
    if page == 63:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]
    if page == 64:
        text = (
            "✨ ایموجی پرمیوم | self MR\n\n"
            "دستورات:\n"
            "`.تنظیم ایموجی ❤️` + ریپلای روی ایموجی پرمیوم\n"
            "`.لیست ایموجی پرمیوم`\n"
            "`.حذف ایموجی 1` یا `.حذف ایموجی ❤️`\n"
            "`.حذف لیست ایموجی پرمیوم`\n"
            "`.ایموجی پرمیوم روشن`\n"
            "`.ایموجی پرمیوم خاموش`\n\n"
            "وقتی روشن باشد، با فرستادن ایموجی عادیِ تنظیم‌شده\n"
            "ربات آن را به نسخهٔ پرمیوم تبدیل می‌کند."
        )
        buttons = [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]
        return text, buttons


    if page == 55:
        return [
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 52:
        on = BIO_MILADI_DATE.get(user_id, False)
        return [
            [_styled_btn(f"وضعیت: ({'on ✓' if on else 'off ✗'})", f"toggle_bio_miladi_{user_id}", on)],
            [_styled_btn("📋 راهنما .تاریخ", f"help_full_date_{user_id}", style="primary")],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 51:
        on = PROFILE_PHOTO_CLOCK.get(user_id, False)
        col = PROFILE_PHOTO_CLOCK_COLOR.get(user_id) or "cyan"
        return [
            [_styled_btn(f"وضعیت: ({'on ✓' if on else 'off ✗'})", f"toggle_photo_clock_{user_id}", on)],
            [
                _styled_btn("🔵 فیروزه‌ای", f"pclock_color_cyan_{user_id}", col == "cyan"),
                _styled_btn("🟢 سبز", f"pclock_color_green_{user_id}", col == "green"),
                _styled_btn("🟣 بنفش", f"pclock_color_purple_{user_id}", col == "purple"),
            ],
            [
                _styled_btn("🔴 قرمز", f"pclock_color_red_{user_id}", col == "red"),
                _styled_btn("🟦 آبی", f"pclock_color_blue_{user_id}", col == "blue"),
                _styled_btn("🟡 طلایی", f"pclock_color_gold_{user_id}", col == "gold"),
            ],
            [
                _styled_btn("⚪ سفید", f"pclock_color_white_{user_id}", col == "white"),
                _styled_btn("🩷 صورتی", f"pclock_color_pink_{user_id}", col == "pink"),
            ],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 40:
        on = bool(EMOJI_PREMIUM_CONVERT.get(user_id, False) or PEMOJI_STATUS.get(user_id, False))
        return [
            [_styled_btn(f"وضعیت: ({'on ✓' if on else 'off ✗'})", f"toggle_emoji_convert_{user_id}", on)],
            [_styled_btn("🗑 پاکسازی لیست ایموجی", f"clear_emoji_map_{user_id}", style="danger")],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 41:
        on = SECRETARY_MODE_STATUS.get(user_id, False)
        return [
            [_styled_btn(f"منشی: ({'on ✓' if on else 'off ✗'})", f"toggle_secretary_{user_id}", on)],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 42:
        on = PV_FILTER_STICKER.get(user_id, False)
        return [
            [_styled_btn(f"فیلتر استیکر: ({'on ✓' if on else 'off ✗'})", f"toggle_pv_filter_sticker_{user_id}", on)],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    if page == 43:
        on = PV_FILTER_GIF.get(user_id, False)
        return [
            [_styled_btn(f"فیلتر گیف: ({'on ✓' if on else 'off ✗'})", f"toggle_pv_filter_gif_{user_id}", on)],
            [_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")],
        ]

    back_map = {
        6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 3, 12: 3, 13: 1, 14: 1, 15: 1, 16: 1,
        17: 1, 18: 1, 20: 19, 21: 1, 22: 3, 23: 19, 24: 1, 25: 1, 26: 1, 27: 1,
        28: 1, 29: 1, 30: 1, 31: 1, 32: 1, 33: 1, 34: 1, 37: 1, 38: 1, 39: 1, 40: 1, 41: 1, 51: 1, 52: 1, 53: 1, 54: 1, 55: 1, 42: 1, 43: 1, 44: 1, 45: 1, 46: 1, 47: 1, 48: 1,
    }
    back = back_map.get(page, 1)
    return [back_btn(back)]



def generate_panel_markup(user_id, page=1):
    """نسخه سازگار با Pyrogram (بدون رنگ)"""
    kb = build_panel_keyboard(user_id, page)
    rows = []
    for row in kb:
        rows.append([
            InlineKeyboardButton(b["text"], callback_data=b["callback_data"])
            for b in row
        ])
    return InlineKeyboardMarkup(rows)


    if page == 44:
        return [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]
    if page == 45:
        return [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]
    if page == 46:
        return [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]
    if page == 47:
        return [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]
    if page == 48:
        return [[_styled_btn("⬅️ بازگشت", f"panel_page_1_{user_id}", style="danger")]]

async def edit_panel_colored(callback, user_id, page=1):
    """ویرایش پنل با دکمه‌های رنگی واقعی از طریق Bot API"""
    keyboard = build_panel_keyboard(user_id, page)
    payload = {"reply_markup": json.dumps({"inline_keyboard": keyboard})}
    if callback.inline_message_id:
        payload["inline_message_id"] = callback.inline_message_id
    else:
        payload["chat_id"] = callback.message.chat.id
        payload["message_id"] = callback.message.id

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logging.warning(f"Colored panel failed: {data}")
                    try:
                        await callback.edit_message_reply_markup(generate_panel_markup(user_id, page))
                    except Exception:
                        pass
                return data
    except Exception as e:
        logging.error(f"edit_panel_colored error: {e}")
        try:
            await callback.edit_message_reply_markup(generate_panel_markup(user_id, page))
        except Exception:
            pass


@manager_bot.on_inline_query()
async def inline_panel_handler(client, query):
    global MANAGER_BOT_USERNAME
    user_id = query.from_user.id if query.from_user else 0
    q = (query.query or "").strip()

    # ===== ایموجی پریمیوم از طریق اینلاین =====
    # فرمت‌ها: pe|uid|hex  یا  pe|uid|i|slot  یا  pe:uid:hex

    
    
    # --- تبدیل مثل pyiuebot: متن [کد] متن ---
    try:
        import re as _re_h
        _rx = _re_h.compile(r"\[(\d{10,})\]")
        if _rx.search(q or ""):
            n = len(_rx.findall(q))
            # چند placeholder امتحان می‌شود؛ تلگرام گاهی روی ⭐ گیر می‌کند
            results_list = []
            for pi, ph in enumerate(("🔥", "👍", "😀", "⭐")):
                msg_text, ents = build_bracket_premium_message(q, placeholder=ph)
                if not ents:
                    continue
                results_list.append({
                    "type": "article",
                    "id": f"pyiue_ent_{pi}",
                    "title": "پیام آماده تبدیل" if pi == 0 else f"نسخه {ph}",
                    "description": f"{n} ایموجی پریمیوم | entity",
                    "input_message_content": {
                        "message_text": msg_text,
                        "entities": ents,
                    },
                })
            # HTML fallback
            html_body = build_bracket_premium_html(q, "🔥")
            results_list.append({
                "type": "article",
                "id": "pyiue_html",
                "title": "تبدیل HTML",
                "description": f"{n} ایموجی | HTML tg-emoji",
                "input_message_content": {
                    "message_text": html_body,
                    "parse_mode": "HTML",
                },
            })
            # فقط متن با کدها (برای کپی)
            results_list.append({
                "type": "article",
                "id": "pyiue_raw",
                "title": "کپی متن خام",
                "description": q[:60],
                "input_message_content": {
                    "message_text": q,
                },
            })
            _tok = (HELPER_BOT_TOKEN if (HELPER_BOT_ENABLED and HELPER_BOT_TOKEN) else BOT_TOKEN) or BOT_TOKEN
            url = f"https://api.telegram.org/bot{_tok}/answerInlineQuery"
            payload = {
                "inline_query_id": query.id,
                "cache_time": 0,
                "is_personal": True,
                "results": json.dumps(results_list, ensure_ascii=False),
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        logging.info("inline pyiue-style ok n=%s results=%s", n, len(results_list))
                    else:
                        logging.warning("inline pyiue-style fail: %s", data)
                        # تلاش با query.answer مستقیم
                        try:
                            from pyrogram.types import InlineQueryResultArticle, InputTextMessageContent
                            from pyrogram.enums import ParseMode as _PM
                            await query.answer(
                                [
                                    InlineQueryResultArticle(
                                        id="pyiue_direct",
                                        title="پیام آماده تبدیل",
                                        description=f"{n} ایموجی",
                                        input_message_content=InputTextMessageContent(
                                            message_text=html_body,
                                            parse_mode=_PM.HTML,
                                        ),
                                    )
                                ],
                                cache_time=0,
                                is_personal=True,
                            )
                        except Exception as e2:
                            logging.warning("pyiue direct answer: %s", e2)
            return
    except Exception as e:
        logging.warning("inline pyiue-style: %s", e)

    if q.startswith("pe|") or q.startswith("pe:"):
        try:
            raw = q.replace("pe:", "pe|")
            parts = raw.split("|")
            owner_id = int(parts[1]) if len(parts) > 1 else user_id
            mapping = _emoji_map_for_user(owner_id)
            cid = None
            normal = ""
            if len(parts) >= 4 and parts[2] in ("i", "s", "slot"):
                # pe|uid|i|0
                try:
                    slot = int(parts[3])
                    keys = list(mapping.keys())
                    if 0 <= slot < len(keys):
                        normal = keys[slot]
                        cid = int(mapping[normal])
                except Exception:
                    pass
            elif len(parts) >= 3:
                token = "|".join(parts[2:])
                # hex utf-8
                try:
                    normal = bytes.fromhex(token).decode("utf-8")
                    if normal in mapping:
                        cid = int(mapping[normal])
                except Exception:
                    normal = token
                    if normal in mapping:
                        cid = int(mapping[normal])
                    else:
                        for k, v in mapping.items():
                            if k in token or token in k:
                                normal, cid = k, int(v)
                                break

            if not cid:
                logging.warning(f"inline pe: no cid for q={q!r} map={list(mapping.keys())}")
                await query.answer([], cache_time=0, is_personal=True)
                return

            ph = normal if normal else "⭐"
            utf16_len = len(ph.encode("utf-16-le")) // 2
            _pe_token = (HELPER_BOT_TOKEN if (HELPER_BOT_ENABLED and HELPER_BOT_TOKEN) else BOT_TOKEN) or BOT_TOKEN
            url = f"https://api.telegram.org/bot{_pe_token}/answerInlineQuery"

            # نتیجه ۱: custom_emoji با entities (روش اصلی)
            results_list = [{
                "type": "article",
                "id": f"pe_{owner_id}_{cid}",
                "title": "⭐ پریمیوم",
                "description": str(ph)[:40],
                "input_message_content": {
                    "message_text": ph,
                    "entities": [{
                        "type": "custom_emoji",
                        "offset": 0,
                        "length": int(utf16_len),
                        "custom_emoji_id": str(int(cid)),
                    }],
                },
            }]

            # نتیجه ۲: HTML tg-emoji
            results_list.append({
                "type": "article",
                "id": f"pehtml_{owner_id}_{cid}",
                "title": "⭐ پریمیوم HTML",
                "description": str(ph)[:40],
                "input_message_content": {
                    "message_text": f'<tg-emoji emoji-id="{int(cid)}">{ph}</tg-emoji>',
                    "parse_mode": "HTML",
                },
            })

            # نتیجه ۳: استیکر کش‌شده (اگر file_id از سشن کاربر/پریمیوم ذخیره شده)
            try:
                tmap = EMOJI_PREMIUM_TEMPLATES.get(owner_id) or {}
                prev = tmap.get(normal) if normal else None
                if not isinstance(prev, dict):
                    prev = {}
                sticker_fid = prev.get("helper_file_id") or prev.get("sticker_file_id")
                if sticker_fid:
                    # اول استیکر؛ اگر file_id از هلپر باشد در اینلاین معتبر است
                    results_list.insert(0, {
                        "type": "sticker",
                        "id": f"pes_{owner_id}_{cid}",
                        "sticker_file_id": sticker_fid,
                    })
                    results_list.insert(1, {
                        "type": "document",
                        "id": f"ped_{owner_id}_{cid}",
                        "title": "پریمیوم",
                        "document_file_id": sticker_fid,
                    })
                    logging.info("inline pe has helper file_id for cid=%s", cid)
                else:
                    logging.warning("inline pe NO helper file_id owner=%s key=%r — دوباره ثبت ایموجی لازم است", owner_id, normal)
            except Exception as e:
                logging.warning("inline sticker template: %s", e)

            payload = {
                "inline_query_id": query.id,
                "cache_time": 0,
                "is_personal": True,
                "results": json.dumps(results_list, ensure_ascii=False),
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        logging.info(
                            "inline pe answered ok owner=%s cid=%s n=%s token_helper=%s",
                            owner_id, cid, len(results_list), bool(HELPER_BOT_TOKEN),
                        )
                    else:
                        logging.warning("inline pe answer fail: %s", data)
                        # حداقل یک نتیجه ساده
                        await query.answer([], cache_time=0, is_personal=True)
            return
        except Exception as e:
            logging.warning(f"inline pe error: {e}")
            try:
                await query.answer([], cache_time=0)
            except Exception:
                pass
            return

    if q != "panel":
        return

    keyboard = build_panel_keyboard(user_id, 1)
    payload = {
        "inline_query_id": query.id,
        "cache_time": 0,
        "results": json.dumps([{
            "type": "article",
            "id": f"panel_{user_id}",
            "title": "پنل مدیریت self MR",
            "input_message_content": {
                "message_text": f"⚡️ مدیریت پیشرفته self MR\n👤 کاربر: {user_id}"
            },
            "reply_markup": {"inline_keyboard": keyboard}
        }], ensure_ascii=False)
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerInlineQuery"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logging.warning(f"Colored inline panel failed: {data}")
                    result = InlineQueryResultArticle(
                        id=f"panel_{user_id}",
                        title="پنل مدیریت self MR",
                        input_message_content=InputTextMessageContent(
                            f"⚡️ مدیریت پیشرفته self MR\n👤 کاربر: {user_id}"
                        ),
                        reply_markup=generate_panel_markup(user_id, 1),
                    )
                    await query.answer([result], cache_time=0)
    except Exception as e:
        logging.error(f"inline panel error: {e}")
        try:
            result = InlineQueryResultArticle(
                id=f"panel_{user_id}",
                title="پنل مدیریت self MR",
                input_message_content=InputTextMessageContent(
                    f"⚡️ مدیریت پیشرفته self MR\n👤 کاربر: {user_id}"
                ),
                reply_markup=generate_panel_markup(user_id, 1),
            )
            await query.answer([result], cache_time=0)
        except Exception:
            pass




async def _dooz_edit_colored(message, text: str, keyboard_rows: list):
    """ویرایش پیام دوز با دکمه‌های رنگی (style مثل پنل)"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": message.chat.id,
            "message_id": message.id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": keyboard_rows}, ensure_ascii=False),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return True
                logging.warning("dooz colored edit: %s", data)
    except Exception as e:
        logging.warning("dooz colored edit err: %s", e)
    # fallback بدون رنگ
    try:
        rows = []
        for row in keyboard_rows:
            rows.append([
                InlineKeyboardButton(b.get("text", "?"), callback_data=b.get("callback_data", "noop"))
                for b in row
            ])
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        logging.warning(f"dooz edit fallback: {e}")
        return False



async def _dooz_cancel_timer(key):
    t = DOOZ_TIMERS.pop(key, None)
    if t and not t.done():
        try:
            t.cancel()
        except Exception:
            pass




async def _dooz_user_link(user_id: int, fallback: str = None) -> str:
    """لینک تمیز مثل نبرد الماس"""
    try:
        u = await manager_bot.get_users(int(user_id))
        name = (u.first_name or fallback or str(user_id)).replace("<", "").replace(">", "")
        return f'<a href="tg://user?id={int(user_id)}">{name}</a>'
    except Exception:
        try:
            name = (fallback or str(user_id)).replace("<", "").replace(">", "")
            return f'<a href="tg://user?id={int(user_id)}">{name}</a>'
        except Exception:
            return html.escape(str(fallback or user_id))


async def _dooz_force_show_result(chat_id, message_id, text: str, prize=0, wbal=0, lbal=0):
    """نمایش نتیجه دوز — مثل نبرد الماس با HTML تمیز"""
    text = str(text or "")
    kb = {
        "inline_keyboard": [
            [
                {"text": "💎 جایزه برنده", "callback_data": "noop"},
                {"text": f"💎 {int(prize):,}", "callback_data": "noop"},
            ],
            [
                {"text": "💎 موجودی برنده", "callback_data": "noop"},
                {"text": f"💎 {int(wbal):,}", "callback_data": "noop"},
            ],
            [
                {"text": "❌ موجودی بازنده", "callback_data": "noop"},
                {"text": f"💎 {int(lbal):,}", "callback_data": "noop"},
            ],
        ]
    }
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    # 1) ویرایش با HTML (مثل بازی الماس)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api + "/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                    "disable_web_page_preview": True,
                },
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logging.info("dooz SHOW edit OK")
                    return True
                logging.warning("dooz SHOW edit fail: %s", data)
    except Exception as e:
        logging.warning("dooz SHOW edit err: %s", e)

    # 2) manager_bot
    try:
        rows = [
            [
                InlineKeyboardButton("💎 جایزه برنده", callback_data="noop"),
                InlineKeyboardButton(f"💎 {int(prize):,}", callback_data="noop"),
            ],
            [
                InlineKeyboardButton("💎 موجودی برنده", callback_data="noop"),
                InlineKeyboardButton(f"💎 {int(wbal):,}", callback_data="noop"),
            ],
            [
                InlineKeyboardButton("❌ موجودی بازنده", callback_data="noop"),
                InlineKeyboardButton(f"💎 {int(lbal):,}", callback_data="noop"),
            ],
        ]
        await manager_bot.edit_message_text(
            chat_id,
            int(message_id),
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logging.warning("dooz SHOW manager edit: %s", e)

    # 3) پاک + ارسال جدید
    try:
        async with aiohttp.ClientSession() as session:
            try:
                await session.post(api + "/deleteMessage", json={"chat_id": chat_id, "message_id": int(message_id)})
            except Exception:
                pass
            async with session.post(
                api + "/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                    "disable_web_page_preview": True,
                },
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logging.info("dooz SHOW send OK")
                    return True
                logging.warning("dooz SHOW send fail: %s", data)
    except Exception as e:
        logging.warning("dooz SHOW send err: %s", e)

    try:
        await manager_bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        logging.warning("dooz SHOW final: %s", e)
    return False


async def _dooz_apply_result_edit(chat_id, message_id, message, result_text, prize, wbal, lbal):
    """سازگاری با کد برد عادی"""
    if chat_id is None and message is not None:
        chat_id = message.chat.id
    if message_id is None and message is not None:
        message_id = message.id
    return await _dooz_force_show_result(chat_id, message_id, result_text, prize, wbal, lbal)



async def _dooz_finish_timeout(client, message, key, st):
    """۳۰ ثانیه گذشت — نتیجه مثل نبرد الماس"""
    try:
        if not st or st.get("finished") or st.get("settling"):
            return
        st["settling"] = True
        st["finished"] = True

        try:
            turn = int(st.get("turn"))
            org = int(st.get("organizer_id"))
            joi = int(st.get("joiner_id"))
        except Exception:
            return
        if turn not in (org, joi):
            return

        loser = turn
        winner = joi if turn == org else org
        amount = int(st.get("amount") or 0)
        prize = amount * 2
        tax = int(prize * GAME_TAX_PERCENT / 100)
        prize -= tax

        await _dooz_cancel_timer(key)
        active_dooz.pop(key, None)

        try:
            add_balance(winner, prize)
        except Exception as e:
            logging.warning("dooz timeout balance: %s", e)

        # اسم تمیز مثل بازی الماس
        fb_w = st.get("organizer_name") if winner == org else st.get("joiner_name")
        fb_l = st.get("joiner_name") if winner == org else st.get("organizer_name")
        # اگر HTML بود فقط متنش
        def _plain(x):
            if not x:
                return None
            s = str(x)
            if "<a " in s and ">" in s:
                try:
                    return s.split(">", 1)[1].split("<", 1)[0]
                except Exception:
                    return s
            return s.replace("<", "").replace(">", "")

        wname = await _dooz_user_link(winner, _plain(fb_w))
        lname = await _dooz_user_link(loser, _plain(fb_l))
        wbal = get_balance(winner)
        lbal = get_balance(loser)

        text = (
            "🎯 <b>نتیجه دوز مشخص شد</b>\n\n"
            "⏱ علت: تمام شدن ۳۰ ثانیه وقت\n\n"
            f"🏆 کاربر برنده: {wname}\n"
            f"❌ کاربر بازنده: {lname}"
        )

        chat_id = st.get("chat_id") or (key[0] if isinstance(key, tuple) else None)
        msg_id = st.get("message_id") or (key[1] if isinstance(key, tuple) else None)
        if message is not None:
            chat_id = chat_id or getattr(getattr(message, "chat", None), "id", None)
            msg_id = msg_id or getattr(message, "id", None)

        try:
            DOOZ_LAST_RESULT[key] = {
                "text": text,
                "prize": prize,
                "wbal": wbal,
                "lbal": lbal,
                "chat_id": chat_id,
                "message_id": msg_id,
            }
        except Exception:
            pass

        ok = await _dooz_force_show_result(chat_id, msg_id, text, prize, wbal, lbal)
        logging.info(
            "dooz TIMEOUT ok=%s winner=%s loser=%s chat=%s mid=%s",
            ok, winner, loser, chat_id, msg_id,
        )
        if not ok:
            async def _retry():
                await asyncio.sleep(1)
                await _dooz_force_show_result(chat_id, msg_id, text, prize, wbal, lbal)
            try:
                asyncio.create_task(_retry())
            except Exception:
                pass
    except Exception as e:
        logging.exception("dooz finish timeout: %s", e)


async def _dooz_deadline_watchdog():
    """پشتیبان: هر ۳ ثانیه deadline را چک می‌کند"""
    while True:
        try:
            await asyncio.sleep(3)
            now = time.time()
            for key, st in list(active_dooz.items()):
                try:
                    if not st or st.get("finished") or st.get("settling"):
                        continue
                    if not st.get("joiner_id") or not st.get("turn"):
                        continue
                    dl = st.get("turn_deadline")
                    if dl is None:
                        continue
                    if now < float(dl):
                        continue
                    logging.info("dooz WATCHDOG fire key=%s turn=%s", key, st.get("turn"))
                    await _dooz_finish_timeout(manager_bot, None, key, st)
                except Exception as e:
                    logging.warning("dooz watchdog item: %s", e)
        except Exception as e:
            logging.warning("dooz watchdog loop: %s", e)


async def _dooz_start_timer(client, message, key):
    """تایمر ۳۰ ثانیه‌ای برای نوبت فعلی"""
    await _dooz_cancel_timer(key)
    st = active_dooz.get(key)
    if not st or st.get("finished") or not st.get("turn"):
        return

    token = time.time()
    st["turn_token"] = token
    st["turn_deadline"] = time.time() + DOOZ_TURN_SEC
    chat_id = st.get("chat_id") or getattr(getattr(message, "chat", None), "id", None) or (key[0] if isinstance(key, tuple) else None)
    msg_id = st.get("message_id") or getattr(message, "id", None) or (key[1] if isinstance(key, tuple) else None)
    st["chat_id"] = chat_id
    st["message_id"] = msg_id
    active_dooz[key] = st

    async def _watch():
        try:
            await asyncio.sleep(DOOZ_TURN_SEC)
            st2 = active_dooz.get(key)
            if not st2 or st2.get("finished") or st2.get("settling"):
                return
            if st2.get("turn_token") != token:
                return
            logging.info("dooz TIMER fire key=%s turn=%s", key, st2.get("turn"))
            await _dooz_finish_timeout(manager_bot, message, key, st2)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logging.exception("dooz timer task: %s", e)

    DOOZ_TIMERS[key] = asyncio.create_task(_watch())
    logging.info("dooz TIMER armed key=%s sec=%s chat=%s mid=%s", key, DOOZ_TURN_SEC, chat_id, msg_id)


async def _dooz_render(client, message, st: dict):
    board = st.get("board") or [" "] * 9
    org = st.get("organizer_id")
    joi = st.get("joiner_id")
    amount = int(st.get("amount") or 0)
    turn = st.get("turn")
    def _strip_a(s):
        s = str(s or "")
        if "<a " in s and ">" in s:
            try:
                return s.split(">", 1)[1].split("<", 1)[0]
            except Exception:
                return s
        return s.replace("<", "").replace(">", "")
    try:
        org_name = _strip_a(st.get("organizer_name")) or _strip_a(await get_user_name(org))
    except Exception:
        org_name = str(org)
    try:
        if joi:
            joi_name = _strip_a(st.get("joiner_name")) or _strip_a(await get_user_name(joi))
        else:
            joi_name = "در انتظار حریف..."
    except Exception:
        joi_name = str(joi) if joi else "در انتظار حریف..."
    # خانه‌ها دکمه آبی (primary) مثل پنل — نه مربع رنگی
    rows = []
    for r in range(3):
        row = []
        for col in range(3):
            i = r * 3 + col
            if board[i] != " ":
                label = board[i]
            else:
                label = "·"
            row.append({
                "text": label,
                "callback_data": f"dooz_cell_{i}_{org}_{joi}",
                "style": "primary",
            })
        rows.append(row)
    left = None
    try:
        dl = st.get("turn_deadline")
        if dl:
            left = max(0, int(dl - time.time()))
    except Exception:
        left = None
    timer_txt = f"⏱ وقت: <b>{left}</b> ثانیه" if left is not None else f"⏱ وقت هر نوبت: <b>{DOOZ_TURN_SEC}</b> ثانیه"
    if turn == org:
        turn_line = f"🎯 نوبت: ❌ {org_name}" + chr(10) + timer_txt
    elif joi and turn == joi:
        turn_line = f"🎯 نوبت: ⭕ {joi_name}" + chr(10) + timer_txt
    else:
        turn_line = "🎯 در انتظار شروع..."
    nl = chr(10)
    text = (
        "⭕❌ <b>دوز | self MR</b>" + nl + nl
        + f"👤 بازیکن ❌ : {org_name}" + nl
        + f"👤 بازیکن ⭕ : {joi_name}" + nl
        + f"💰 مبلغ هر نفر: <code>{amount:,}</code> الماس" + nl
        + f"🏆 جایزه کل: <code>{amount * 2:,}</code> الماس" + nl + nl
        + turn_line
    )
    try:
        await _dooz_edit_colored(message, text, rows)
    except Exception as e:
        logging.warning(f"dooz_render: {e}")


@manager_bot.on_callback_query()
async def callback_panel_handler(client, callback):
    data = ""
    try:
        data = callback.data or ""
        logging.info("CALLBACK data=%s uid=%s", data, getattr(getattr(callback, "from_user", None), "id", None))
        await _callback_panel_handler_impl(client, callback, data)
    except Exception as e:
        logging.exception("callback_panel_handler error data=%s: %s", data, e)
        try:
            await callback.answer(f"خطا: {str(e)[:80]}", show_alert=True)
        except Exception:
            try:
                await callback.answer()
            except Exception:
                pass


async def _callback_panel_handler_impl(client, callback, data: str):
    # ===== دانلود آهنگ از سرچ (دکمه‌ها از manager_bot) =====
    if data.startswith("toggle_photo_clock_"):
        try:
            target_user_id = int(data.split("_")[-1])
        except Exception:
            await callback.answer("خطا", show_alert=True)
            return
        if callback.from_user.id != target_user_id and callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("دسترسی ندارید", show_alert=True)
            return
        new_state = not PROFILE_PHOTO_CLOCK.get(target_user_id, False)
        PROFILE_PHOTO_CLOCK[target_user_id] = new_state
        cl = ACTIVE_BOTS[target_user_id][0] if target_user_id in ACTIVE_BOTS else None
        flood_msg = ""
        if new_state:
            PROFILE_PHOTO_CLOCK_LAST_MINUTE.pop(target_user_id, None)
            try:
                if cl:
                    photos = []
                    async for p in cl.get_chat_photos("me", limit=1):
                        photos.append(p)
                    if photos:
                        dl = await cl.download_media(photos[0], file_name=profile_clock_base_path(target_user_id))
                        if dl:
                            PROFILE_PHOTO_CLOCK_BASE[target_user_id] = dl
                    ok_up, left = can_upload_profile_photo(target_user_id)
                    if not ok_up:
                        flood_msg = f" — بعد از {format_flood_wait_fa(left)} اعمال می‌شود"
                    else:
                        path = await build_profile_clock_image(cl, target_user_id)
                        if path and os.path.exists(path):
                            try:
                                await replace_clock_profile_photo(cl, target_user_id, path)
                            except Exception as e:
                                if "FLOOD_WAIT" in str(e):
                                    sec = note_profile_photo_flood(target_user_id, str(e))
                                    flood_msg = f" — محدودیت تلگرام: {format_flood_wait_fa(sec)}"
                                    logging.warning("pclock toggle flood: %s", e)
                                else:
                                    logging.warning("pclock toggle set: %s", e)
                            try:
                                os.remove(path)
                            except Exception:
                                pass
            except Exception as e:
                logging.warning(f"capture/apply on toggle: {e}")
                if "FLOOD_WAIT" in str(e):
                    sec = note_profile_photo_flood(target_user_id, str(e))
                    flood_msg = f" — محدودیت: {format_flood_wait_fa(sec)}"
        else:
            PROFILE_PHOTO_CLOCK_LAST_MINUTE.pop(target_user_id, None)
            if cl:
                left = profile_photo_flood_remaining(target_user_id)
                if left > 0:
                    flood_msg = f" — فعلاً نمی‌توان عکس را برگرداند؛ {format_flood_wait_fa(left)} صبر کن"
                else:
                    try:
                        await restore_profile_photo_from_base(cl, target_user_id)
                    except Exception as e:
                        logging.warning(f"restore on toggle off: {e}")
                        if "FLOOD_WAIT" in str(e):
                            sec = note_profile_photo_flood(target_user_id, str(e))
                            flood_msg = f" — محدودیت: {format_flood_wait_fa(sec)}"
        try:
            persist_all_user_settings(target_user_id)
        except Exception:
            pass
        await callback.answer(
            ("ساعت پروفایل: روشن ✅" if new_state else "ساعت پروفایل: خاموش ❌") + flood_msg,
            show_alert=bool(flood_msg),
        )
        try:
            await edit_panel_colored(callback, target_user_id, 51)
        except Exception:
            try:
                await callback.edit_message_reply_markup(generate_panel_markup(target_user_id, 51))
            except Exception:
                pass
        return

    if data.startswith("pclock_color_"):
        # pclock_color_cyan_USERID
        try:
            parts = data.split("_")
            target_user_id = int(parts[-1])
            color_key = parts[2]
        except Exception:
            await callback.answer("خطا", show_alert=True)
            return
        if callback.from_user.id != target_user_id and callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("دسترسی ندارید", show_alert=True)
            return
        if color_key not in PHOTO_CLOCK_COLORS:
            await callback.answer("رنگ نامعتبر", show_alert=True)
            return
        PROFILE_PHOTO_CLOCK_COLOR[target_user_id] = color_key
        PROFILE_PHOTO_CLOCK_LAST_MINUTE.pop(target_user_id, None)
        try:
            persist_all_user_settings(target_user_id)
        except Exception:
            pass
        # فقط رنگ ذخیره شود؛ آپلود فقط اگر FLOOD نباشد
        ok_up, left = can_upload_profile_photo(target_user_id)
        if not ok_up:
            await callback.answer(
                f"رنگ «{color_key}» ذخیره شد. بعد از {format_flood_wait_fa(left)} روی پروفایل می‌آید.",
                show_alert=True,
            )
        elif PROFILE_PHOTO_CLOCK.get(target_user_id) and target_user_id in ACTIVE_BOTS:
            try:
                cl = ACTIVE_BOTS[target_user_id][0]
                path = await build_profile_clock_image(cl, target_user_id)
                if path and os.path.exists(path):
                    try:
                        await replace_clock_profile_photo(cl, target_user_id, path)
                        PROFILE_PHOTO_CLOCK_LAST_MINUTE[target_user_id] = datetime.now(TEHRAN_TIMEZONE).strftime("%H:%M")
                        await callback.answer(f"رنگ: {color_key} ✅")
                    except Exception as e:
                        if "FLOOD_WAIT" in str(e):
                            sec = note_profile_photo_flood(target_user_id, str(e))
                            logging.warning("pclock color apply: %s", e)
                            await callback.answer(
                                f"رنگ ذخیره شد.\nمحدودیت تلگرام: {format_flood_wait_fa(sec)}\nبعداً خودکار اعمال می‌شود.",
                                show_alert=True,
                            )
                        else:
                            logging.warning("pclock color apply: %s", e)
                            await callback.answer(f"رنگ ذخیره شد ({color_key})")
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                else:
                    await callback.answer(f"رنگ: {color_key}")
            except Exception as e:
                logging.warning(f"pclock color apply: {e}")
                await callback.answer(f"رنگ ذخیره شد: {color_key}")
        else:
            await callback.answer(f"رنگ: {color_key} (بعد از روشن شدن اعمال می‌شود)")
        try:
            await edit_panel_colored(callback, target_user_id, 51)
        except Exception:
            pass
        return


    if data.startswith("song_dl_"):
        await song_download_callback(client, callback)
        return

    
    # ===== منوی اصلی منیجر =====
    if data == "mm_home":
        await callback.answer()
        await send_main_menu(client, callback.message, callback.from_user.id, edit=True)
        return

    if data == "mm_self":
        await callback.answer()
        await mm_edit(
            callback,
            f"🤖 **مدیریت سلف | self MR**\n\n"
            f"برای فعال‌سازی سلف روی دکمه زیر بزنید.\n"
            f"💎 هزینه: `{SELF_PRICE}` الماس\n"
            f"⏰ کسر ساعتی: `{HOURLY_COST}` الماس",
            self_manage_keyboard(),
        )
        return

    if data == "mm_activate":
        await callback.answer()
        uid = callback.from_user.id
        if is_banned(uid):
            await callback.answer("مسدود هستید", show_alert=True)
            return
        bal = get_balance(uid)
        if bal < SELF_PRICE:
            await mm_edit(
                callback,
                f"❌ الماس کافی نیست\n💎 موجودی: `{bal:,}`\n💎 نیاز: `{SELF_PRICE}`\n\n"
                f"از بخش **الماس رایگان** زیرمجموعه بیاورید.",
                [[_mm_btn("🔙 بازگشت", callback_data="mm_self", style="danger")]],
            )
            return
        LOGIN_STATES[callback.message.chat.id] = {"step": "phone"}
        await mm_edit(
            callback,
            "📱 **شماره تلفن را وارد کنید**\n\n"
            "شماره را با کد کشور بفرستید\n"
            "مثال: `+989123456789`\n\n"
            "یا از دکمه زیر شماره را Share کنید.",
            [[_mm_btn("🔙 بازگشت", callback_data="mm_self", style="danger")]],
        )
        try:
            await client.send_message(
                callback.message.chat.id,
                "⬇️ یا شماره را Share کنید:",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 ارسال شماره", request_contact=True)],
                     [KeyboardButton("🔙 انصراف")]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
        except Exception:
            pass
        return

    if data == "mm_free":
        await callback.answer()
        uid = callback.from_user.id
        bot_username = (await client.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={uid}"
        try:
            db = get_user_db(uid)
            cur = db.cursor()
            cur.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (uid,))
            cnt = (cur.fetchone() or [0])[0]
            db.close()
        except Exception:
            cnt = 0
        await mm_edit(
            callback,
            f"💎 **الماس رایگان | self MR**\n\n"
            f"با دعوت هر نفر `{REFERRAL_REWARD}` الماس بگیرید.\n\n"
            f"🔗 لینک اختصاصی شما:\n`{ref_link}`\n\n"
            f"👥 زیرمجموعه‌ها: `{cnt}`\n"
            f"💎 موجودی: `{get_balance(uid):,}`",
            [[_mm_btn("🔙 بازگشت", callback_data="mm_home", style="danger")]],
        )
        return


    if data == "mm_account":
        await callback.answer()
        uid = callback.from_user.id
        session_info = get_session_by_user_id(uid)
        has_self = "✅ فعال" if session_info else "❌ غیرفعال"
        try:
            db = get_user_db(uid)
            cur = db.cursor()
            cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (uid,))
            ref_cnt = (cur.fetchone() or [0])[0]
            db.close()
        except Exception:
            ref_cnt = 0
        bal = get_balance(uid)
        toman = bal * DIAMOND_PRICE_TOMAN
        phone = ""
        try:
            if session_info and len(session_info) >= 1:
                phone = session_info[0] or ""
        except Exception:
            phone = ""
        phone_line = f"`{phone}`" if phone else "—"
        text = (
            "💳 **حساب کاربری | self MR**\n\n"
            f"🔑 آیدی عددی:\n`{uid}`\n\n"
            f"📁 تعداد رفرال: `{ref_cnt}`\n\n"
            f"📱 شماره ثبت‌شده:\n{phone_line}\n\n"
            f"💎 موجودی الماس: `{bal:,}`\n\n"
            f"💵 معادل تومانی: `{toman:,}` تومان\n\n"
            f"🔐 سلف: {has_self}"
        )
        await mm_edit(
            callback,
            text,
            [
                [
                    _mm_btn("🔁 انتقال الماس", callback_data="mm_transfer", style="primary"),
                    _mm_btn("🛒 خرید الماس", callback_data="mm_buy", style="success"),
                ],
                [_mm_btn("🔙 بازگشت", callback_data="mm_home", style="danger")],
            ],
        )
        return

    if data == "mm_transfer":
        await callback.answer()
        await mm_edit(
            callback,
            "🔁 **انتقال الماس | self MR**\n\n"
            "روی پیام کاربر در گپ/پیوی ریپلای کنید و بنویسید:\n"
            "`انتقال 100`\n"
            "یا\n"
            "`انتقال الماس 100`\n\n"
            "کارمزد طبق تنظیمات سیستم کسر می‌شود.",
            [[_mm_btn("🔙 بازگشت", callback_data="mm_account", style="danger")]],
        )
        return

    if data == "mm_buy":
        await callback.answer()
        uid = callback.from_user.id
        if uid not in SHOP_CART:
            SHOP_CART[uid] = MIN_BUY_DIAMONDS
        await mm_edit(callback, shop_text(uid), shop_keyboard_for(uid))
        return

    if data == "shop_noop":
        await callback.answer()
        return

    if data == "shop_plus":
        await callback.answer()
        uid = callback.from_user.id
        SHOP_CART[uid] = shop_cart_qty(uid) + SHOP_STEP
        await mm_edit(callback, shop_text(uid), shop_keyboard_for(uid))
        return

    if data == "shop_minus":
        await callback.answer()
        uid = callback.from_user.id
        SHOP_CART[uid] = max(MIN_BUY_DIAMONDS, shop_cart_qty(uid) - SHOP_STEP)
        await mm_edit(callback, shop_text(uid), shop_keyboard_for(uid))
        return

    if data == "shop_manual":
        await callback.answer()
        uid = callback.from_user.id
        ADMIN_STATES[uid] = "shop_manual_qty"
        await mm_edit(
            callback,
            f"✏️ تعداد الماس را به‌صورت عدد بفرستید\nحداقل: `{MIN_BUY_DIAMONDS:,}`",
            [[_mm_btn("🔙 بازگشت", callback_data="mm_buy", style="danger")]],
        )
        return

    if data == "shop_confirm":
        await callback.answer()
        uid = callback.from_user.id
        if shop_cart_qty(uid) < MIN_BUY_DIAMONDS:
            await callback.answer(f"حداقل خرید {MIN_BUY_DIAMONDS} الماس است", show_alert=True)
            return
        await mm_edit(
            callback,
            checkout_text(uid),
            [
                [_mm_btn("💳 کارت به کارت", callback_data="shop_card", style="success")],
                [_mm_btn("🔙 بازگشت", callback_data="mm_buy", style="danger")],
            ],
        )
        return

    if data == "shop_card":
        await callback.answer()
        uid = callback.from_user.id
        q = shop_cart_qty(uid)
        PENDING_RECEIPTS[uid] = {"qty": q, "amount": q * DIAMOND_PRICE_TOMAN, "ts": time.time()}
        ADMIN_STATES[uid] = "wait_receipt"
        await mm_edit(
            callback,
            card_pay_text(uid),
            [[_mm_btn("❌ لغو", callback_data="shop_cancel", style="danger")]],
        )
        return

    if data == "shop_cancel":
        await callback.answer("لغو شد")
        uid = callback.from_user.id
        PENDING_RECEIPTS.pop(uid, None)
        if ADMIN_STATES.get(uid) in ("wait_receipt", "shop_manual_qty"):
            ADMIN_STATES.pop(uid, None)
        await mm_edit(callback, shop_text(uid), shop_keyboard_for(uid))
        return


    # ===== مافیا لابی دکمه‌ای =====
    if data.startswith("mafia_join_"):
        try:
            chat_id = int(data.split("mafia_join_", 1)[1])
        except Exception:
            await callback.answer("خطا", show_alert=True)
            return
        msg = await _mafia_try_join(callback.from_user, chat_id)
        game = MAFIA_GAMES.get(chat_id)
        if game and game.get("phase") == "lobby":
            try:
                await callback.message.edit_text(
                    _mafia_lobby_text(game),
                    reply_markup=_mafia_lobby_keyboard(chat_id),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await callback.answer(msg[:180], show_alert=True)
        return

    if data.startswith("mafia_cancel_"):
        try:
            chat_id = int(data.split("mafia_cancel_", 1)[1])
        except Exception:
            await callback.answer("خطا", show_alert=True)
            return
        msg = await _mafia_try_cancel(callback.from_user.id, chat_id)
        try:
            await callback.message.edit_text(msg)
        except Exception:
            pass
        await callback.answer(msg[:180], show_alert=True)
        return

    if data.startswith("order_ok_"):
        await callback.answer()
        if callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("فقط ادمین", show_alert=True)
            return
        try:
            oid = data.split("order_ok_", 1)[1]
            order = ADMIN_PENDING_ORDERS.pop(oid, None)
            if not order:
                await callback.answer("سفارش پیدا نشد", show_alert=True)
                return
            uid = int(order["user_id"])
            qty = int(order["qty"])
            add_balance(uid, qty)
            try:
                await manager_bot.send_message(
                    uid,
                    f"✅ **پرداخت تایید شد | self MR**\n\n💎 `{qty:,}` الماس واریز شد.\nموجودی: `{get_balance(uid):,}`",
                )
            except Exception:
                pass
            try:
                await callback.message.reply_text(f"✅ تایید شد — +{qty:,} الماس برای `{uid}`")
            except Exception:
                pass
        except Exception as e:
            logging.warning("order_ok: %s", e)
        return

    if data.startswith("order_no_"):
        await callback.answer()
        if callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("فقط ادمین", show_alert=True)
            return
        try:
            oid = data.split("order_no_", 1)[1]
            order = ADMIN_PENDING_ORDERS.pop(oid, None)
            if order:
                try:
                    await manager_bot.send_message(
                        int(order["user_id"]),
                        "❌ پرداخت شما رد شد.\nدر صورت واریز با پشتیبانی در ارتباط باشید.",
                    )
                except Exception:
                    pass
            try:
                await callback.message.reply_text("❌ سفارش رد شد.")
            except Exception:
                pass
        except Exception as e:
            logging.warning("order_no: %s", e)
        return

# ===== کیبورد عددی کد لاگین =====
    if data.startswith("login_d_") or data in ("login_del", "login_ok", "login_resend"):
        chat_id = callback.message.chat.id
        st = LOGIN_STATES.get(chat_id)
        if not st or st.get("step") != "code":
            await callback.answer("جلسه لاگین فعال نیست — دوباره فعال‌سازی را بزن", show_alert=True)
            return
        digits = st.get("digits") or ""

        if data.startswith("login_d_"):
            d = data.split("_")[-1]
            if not d.isdigit():
                await callback.answer()
                return
            if len(digits) >= 6:
                await callback.answer("کد کامل است — تایید را بزن")
                return
            digits += d
            st["digits"] = digits
            LOGIN_STATES[chat_id] = st
            await callback.answer()
            await mm_edit(callback, code_pad_text(digits), login_code_keyboard())
            return

        if data == "login_del":
            digits = digits[:-1]
            st["digits"] = digits
            LOGIN_STATES[chat_id] = st
            await callback.answer("پاک شد")
            await mm_edit(callback, code_pad_text(digits), login_code_keyboard())
            return

        if data == "login_resend":
            await callback.answer("در حال ارسال مجدد...")
            user_c = st.get("client")
            phone = normalize_phone(st.get("phone") or "")
            if not user_c or not phone:
                await callback.answer("نشست منقضی — دوباره شروع کن", show_alert=True)
                return
            try:
                sent_code = await user_c.send_code(phone)
                st["hash"] = sent_code.phone_code_hash
                st["digits"] = ""
                st["phone"] = phone
                LOGIN_STATES[chat_id] = st
                await mm_edit(
                    callback,
                    "🔄 کد جدید ارسال شد.\n\n" + code_pad_text(""),
                    login_code_keyboard(),
                )
            except Exception as e:
                await callback.answer(str(e)[:80], show_alert=True)
            return

        if data == "login_ok":
            code = re.sub(r"\D+", "", digits or "")
            if len(code) < 5:
                await callback.answer("کد باید حداقل ۵ رقم باشد", show_alert=True)
                return
            user_c = st.get("client")
            phone = normalize_phone(st.get("phone") or "")
            phash = st.get("hash")
            if not user_c or not phone or not phash:
                await callback.answer("نشست منقضی شده — دوباره فعال‌سازی کنید", show_alert=True)
                return
            if st.get("busy"):
                await callback.answer("صبر کن...")
                return
            st["busy"] = True
            LOGIN_STATES[chat_id] = st
            await callback.answer("در حال بررسی کد...")
            try:
                await user_c.sign_in(phone, phash, code)
                # جلوگیری از دوبار sign_in توسط هندلر متنی
                st["step"] = "done"
                st["busy"] = True
                LOGIN_STATES[chat_id] = st
                try:
                    await callback.message.edit_text("⏳ در حال فعال‌سازی سلف...")
                except Exception:
                    pass
                await finalize(callback.message, user_c, phone)
            except SessionPasswordNeeded:
                st["step"] = "password"
                st["busy"] = False
                st["digits"] = ""
                LOGIN_STATES[chat_id] = st
                try:
                    await callback.message.edit_text(
                        "🔐 **رمز دو مرحله‌ای** را وارد کنید:\n\nرمز را به صورت متن بفرستید."
                    )
                except Exception:
                    pass
            except Exception as e:
                err = str(e)
                logging.error(f"login sign_in: {err}")
                st["busy"] = False
                st["digits"] = ""
                LOGIN_STATES[chat_id] = st
                # کد اشتباه → پاک کردن و اجازه تلاش دوباره بدون قطع نشست
                if "PHONE_CODE_INVALID" in err or "code is invalid" in err.lower():
                    await mm_edit(
                        callback,
                        "❌ **کد اشتباه است**\n\n"
                        "کد جدید تلگرام را دقیق وارد کن.\n"
                        "اگر منقضی شده «ارسال مجدد کد» را بزن.\n\n"
                        + code_pad_text(""),
                        login_code_keyboard(),
                    )
                elif "PHONE_CODE_EXPIRED" in err or "expired" in err.lower():
                    await mm_edit(
                        callback,
                        "⏰ **کد منقضی شد**\n\nروی «ارسال مجدد کد» بزن.",
                        login_code_keyboard(),
                    )
                else:
                    try:
                        await user_c.disconnect()
                    except Exception:
                        pass
                    LOGIN_STATES.pop(chat_id, None)
                    await mm_edit(
                        callback,
                        f"❌ خطا در ورود:\n`{err[:120]}`\n\nدوباره از مدیریت سلف تلاش کنید.",
                        self_manage_keyboard(),
                    )
            return

    if data == "noop":
        await callback.answer()
        return

    if data.startswith("close_panel_"):
        try:
            uid = int(data.split("_")[-1])
            if callback.from_user and callback.from_user.id != uid:
                await callback.answer("⛔️", show_alert=True)
                return
        except Exception:
            pass
        try:
            if callback.inline_message_id:
                await client.edit_inline_text(callback.inline_message_id, " پنل بسته شد.")
            elif callback.message:
                await callback.message.delete()
        except Exception:
            try:
                if callback.message:
                    await callback.message.edit_text(" پنل بسته شد.")
            except Exception:
                pass
        await callback.answer("بسته شد")
        return

    if data == "check_subscription":
        user_id = callback.from_user.id
        not_subscribed = await check_all_channels(user_id)

        if not not_subscribed:
            try:
                await try_claim_referral_reward(
                    user_id,
                    (callback.from_user.first_name if callback.from_user else None) or str(user_id),
                )
            except Exception as e:
                logging.warning("check_sub claim referral: %s", e)
            await callback.message.edit_text("✅ **عضویت شما تأیید شد!**\n\nلطفاً دوباره روی /start کلیک کنید.")
            await callback.answer("✅ عضویت تأیید شد!")
            return

        buttons = []
        for channel in not_subscribed:
            channel_name = channel.replace("@", "")
            buttons.append([InlineKeyboardButton(f"✅ عضویت در {channel}", url=f"https://t.me/{channel_name}")])
        buttons.append([InlineKeyboardButton("🔄 بررسی مجدد عضویت", callback_data="check_subscription")])

        await callback.message.edit_text(
            "❌ **شما هنوز در کانال‌های زیر عضو نشده‌اید:**\n\nپس از عضویت، روی دکمه **بررسی مجدد** کلیک کنید.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback.answer("⚠️ هنوز عضو نشده‌اید!", show_alert=True)
        return

    if data == "close_info":
        await callback.message.delete()
        await callback.answer("✅ بسته شد")
        return

    if data.startswith("add_balance_"):
        target_id = int(data.split("_")[2])
        if callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("❌ دسترسی ندارید!", show_alert=True)
            return

        ADMIN_STATES[callback.from_user.id] = f"add_balance_{target_id}"
        await callback.message.reply_text(f"💎 مقدار الماس برای کاربر {target_id} را وارد کنید:")
        await callback.answer("✅ مقدار را وارد کنید")
        return

    if data.startswith("ban_user_"):
        target_id = int(data.split("_")[2])
        if callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("❌ دسترسی ندارید!", show_alert=True)
            return

        db = get_user_db(target_id)
        cursor = db.cursor()
        cursor.execute('UPDATE users SET banned = 1 WHERE user_id = ?', (target_id,))
        db.commit()
        db.close()

        await callback.message.edit_text(f"✅ کاربر {target_id} مسدود شد.")
        await callback.answer("✅ مسدود شد")
        return

    # =============================================

    # کالبک‌های نبرد الماس

    # ===== دوز =====
    if isinstance(data, str) and data.startswith("dooz_cancel_"):
        try:
            parts = data.split("_")
            amount = int(parts[2])
            organizer_id = int(parts[3])
            if callback.from_user.id != organizer_id and callback.from_user.id not in GOD_ADMIN_IDS:
                await callback.answer("فقط برگزارکننده!", show_alert=True)
                return
            add_balance(organizer_id, amount)
            key = (callback.message.chat.id, callback.message.id)
            active_dooz.pop(key, None)
            await _dooz_cancel_timer(key)
            try:
                await callback.message.edit_text(f"❌ دوز لغو شد. `{amount:,}` الماس برگشت.")
            except Exception:
                pass
            await callback.answer("لغو شد")
        except Exception as e:
            logging.warning(f"dooz_cancel: {e}")
        return

    if isinstance(data, str) and data.startswith("dooz_join_"):
        try:
            parts = data.split("_")
            amount = int(parts[2])
            organizer_id = int(parts[3])
            joiner = callback.from_user.id
            if joiner == organizer_id:
                await callback.answer("نمی‌توانید با خودتان بازی کنید!", show_alert=True)
                return
            if get_balance(joiner) < amount:
                await callback.answer("موجودی کافی نیست!", show_alert=True)
                return
            if not deduct_balance(joiner, amount):
                await callback.answer("خطا در کسر الماس!", show_alert=True)
                return
            key = (callback.message.chat.id, callback.message.id)
            st = active_dooz.get(key) or {
                "organizer_id": organizer_id,
                "amount": amount,
                "board": [" "] * 9,
                "finished": False,
            }
            if st.get("joiner_id"):
                add_balance(joiner, amount)
                await callback.answer("این بازی پر است!", show_alert=True)
                return
            st["joiner_id"] = joiner
            st["turn"] = organizer_id  # X شروع
            st["board"] = [" "] * 9
            st["finished"] = False
            try:
                st["organizer_name"] = await _dooz_user_link(organizer_id)
            except Exception:
                st["organizer_name"] = str(organizer_id)
            try:
                jn = (callback.from_user.first_name or str(joiner)).replace("<", "").replace(">", "")
                st["joiner_name"] = f'<a href="tg://user?id={joiner}">{jn}</a>'
            except Exception:
                st["joiner_name"] = str(joiner)
            st["turn_token"] = time.time()
            st["turn_deadline"] = time.time() + DOOZ_TURN_SEC
            st["chat_id"] = callback.message.chat.id
            st["message_id"] = callback.message.id
            st["settling"] = False
            active_dooz[key] = st
            await _dooz_render(client, callback.message, st)
            await _dooz_start_timer(client, callback.message, key)
            await callback.answer("شروع دوز! ۳۰ ثانیه وقت")
        except Exception as e:
            logging.warning(f"dooz_join: {e}")
        return


    if isinstance(data, str) and data.startswith("dooz_cell_"):
        try:
            parts = data.split("_")
            idx = int(parts[2])
            organizer_id = int(parts[3])
            joiner_id = int(parts[4])
            uid = callback.from_user.id
            key = (callback.message.chat.id, callback.message.id)
            st = active_dooz.get(key)
            if not st or st.get("finished"):
                last = DOOZ_LAST_RESULT.get(key)
                if last:
                    try:
                        await _dooz_force_show_result(
                            last.get("chat_id") or callback.message.chat.id,
                            last.get("message_id") or callback.message.id,
                            last.get("text") or "بازی تمام شده",
                            last.get("prize") or 0,
                            last.get("wbal") or 0,
                            last.get("lbal") or 0,
                        )
                        await callback.answer("نتیجه نمایش داده شد")
                    except Exception:
                        await callback.answer("بازی تمام شده", show_alert=True)
                else:
                    await callback.answer("بازی تمام شده", show_alert=True)
                return
            if uid not in (organizer_id, joiner_id):
                await callback.answer("شما بازیکن نیستید!", show_alert=True)
                return
            if st.get("turn") != uid:
                await callback.answer("نوبت شما نیست!", show_alert=True)
                return
            board = st["board"]
            if idx < 0 or idx > 8 or board[idx] != " ":
                await callback.answer("این خانه پر است!", show_alert=True)
                return
            mark = "❌" if uid == organizer_id else "⭕"
            board[idx] = mark
            wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
            winner = None
            for a,b,d in wins:
                if board[a] == board[b] == board[d] != " ":
                    winner = uid
                    break
            if winner:
                st["finished"] = True
                amount = int(st["amount"])
                prize = amount * 2
                tax = int(prize * GAME_TAX_PERCENT / 100)
                prize -= tax
                loser = joiner_id if winner == organizer_id else organizer_id
                add_balance(winner, prize)
                wname = await _dooz_user_link(winner)
                lname = await _dooz_user_link(loser)
                wbal = get_balance(winner)
                lbal = get_balance(loser)
                await _dooz_cancel_timer(key)
                active_dooz.pop(key, None)
                result_text = (
                    "🎯 <b>نتیجه دوز مشخص شد</b>\n\n"
                    f"🏆 کاربر برنده: {wname}\n"
                    f"❌ کاربر بازنده: {lname}"
                )
                try:
                    DOOZ_LAST_RESULT[key] = {
                        "text": result_text.replace("<b>", "").replace("</b>", ""),
                        "prize": prize,
                        "wbal": wbal,
                        "lbal": lbal,
                        "chat_id": callback.message.chat.id,
                        "message_id": callback.message.id,
                    }
                except Exception:
                    pass
                await _dooz_apply_result_edit(
                    callback.message.chat.id,
                    callback.message.id,
                    callback.message,
                    result_text,
                    prize,
                    wbal,
                    lbal,
                )
                await callback.answer("✅ دوز تمام شد!")
                return
            if all(x != " " for x in board):
                st["finished"] = True
                amount = int(st["amount"])
                add_balance(organizer_id, amount)
                add_balance(joiner_id, amount)
                await _dooz_cancel_timer(key)
                active_dooz.pop(key, None)
                oname = st.get("organizer_name") or await get_user_name(organizer_id)
                jname = st.get("joiner_name") or await get_user_name(joiner_id)
                nl = chr(10)
                await callback.message.edit_text(
                    "🤝 <b>تساوی در دوز</b>" + nl + nl
                    + f"👤 {oname}" + nl
                    + f"👤 {jname}" + nl + nl
                    + "💎 الماس هر دو نفر برگشت داده شد.",
                    parse_mode=ParseMode.HTML,
                )
                await callback.answer("تساوی")
                return
            st["turn"] = joiner_id if uid == organizer_id else organizer_id
            st["turn_token"] = time.time()
            st["turn_deadline"] = time.time() + DOOZ_TURN_SEC
            st["chat_id"] = callback.message.chat.id
            st["message_id"] = callback.message.id
            st["settling"] = False
            active_dooz[key] = st
            await _dooz_render(client, callback.message, st)
            await _dooz_start_timer(client, callback.message, key)
            await callback.answer("نوبت بعدی — ۳۰ ثانیه")
        except Exception as e:
            logging.warning(f"dooz_cell: {e}")
        return

    if data.startswith("game_join_"):
        parts = data.split("_")
        try:
            amount = int(parts[2])
            organizer_id = int(parts[3])
        except Exception:
            await callback.answer("❌ داده نبرد نامعتبر", show_alert=True)
            return
        joiner_id = callback.from_user.id

        if joiner_id == organizer_id:
            await callback.answer("❌ شما برگزار کننده هستید!", show_alert=True)
            return

        # جلوگیری از دوبار کلیک روی یک نبرد
        game_key = (callback.message.chat.id, callback.message.id)
        if game_key not in active_games:
            # اگر به هر دلیل در حافظه نبود، باز هم اجازه بده با amount/organizer ادامه دهد
            active_games[game_key] = {
                "organizer_id": organizer_id,
                "amount": amount,
                "chat_id": callback.message.chat.id,
                "message_id": callback.message.id,
            }
        elif active_games[game_key].get("finished"):
            await callback.answer("❌ این نبرد تمام شده!", show_alert=True)
            return

        joiner_balance = get_balance(joiner_id)
        if joiner_balance < amount:
            await callback.answer(f"❌ موجودی شما کافی نیست! ({joiner_balance:,})", show_alert=True)
            return

        if not deduct_balance(joiner_id, amount):
            await callback.answer("❌ خطا در کسر الماس!", show_alert=True)
            return

        try:
            total_prize = amount * 2
            tax = int(total_prize * GAME_TAX_PERCENT / 100)
            prize = total_prize - tax

            winner_id = random.choice([organizer_id, joiner_id])
            loser_id = organizer_id if winner_id == joiner_id else joiner_id

            add_balance(winner_id, prize)

            winner_name = await get_user_name(winner_id)
            loser_name = await get_user_name(loser_id)

            winner_balance = get_balance(winner_id)
            loser_balance = get_balance(loser_id)

            result_text = (
                f"🎯 <b>نتیجه بازی مشخص شد</b>\n\n"
                f"🏆 کاربر برنده: {winner_name}\n"
                f"❌ کاربر بازنده: {loser_name}"
            )

            result_buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💎 جایزه برنده", callback_data="noop"),
                    InlineKeyboardButton(f"💎 {prize:,}", callback_data="noop")
                ],
                [
                    InlineKeyboardButton("💎 موجودی برنده", callback_data="noop"),
                    InlineKeyboardButton(f"💎 {winner_balance:,}", callback_data="noop")
                ],
                [
                    InlineKeyboardButton("❌ موجودی بازنده", callback_data="noop"),
                    InlineKeyboardButton(f"💎 {loser_balance:,}", callback_data="noop")
                ]
            ])

            active_games[game_key]["finished"] = True

            try:
                await client.delete_messages(callback.message.chat.id, callback.message.id)
            except Exception:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

            try:
                await client.send_message(
                    callback.message.chat.id,
                    result_text,
                    reply_markup=result_buttons,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logging.error(f"Result message error: {e}")
                await client.send_message(
                    callback.message.chat.id,
                    f"🎯 نتیجه بازی\n🏆 برنده: {winner_name}\n❌ بازنده: {loser_name}\n💎 جایزه: {prize:,}",
                )

            await callback.answer("✅ نبرد به پایان رسید!")
            if game_key in active_games:
                del active_games[game_key]
        except Exception as e:
            logging.error(f"game_join error: {e}")
            # برگرداندن الماس جوینر در صورت خطا
            try:
                add_balance(joiner_id, amount)
            except Exception:
                pass
            await callback.answer("❌ خطا در انجام نبرد", show_alert=True)
        return

    # ====== لغو نبرد ======
    if data.startswith("game_cancel_"):
        parts = data.split("_")
        amount = int(parts[2])
        organizer_id = int(parts[3])
        user_id = callback.from_user.id
        
        if user_id != organizer_id:
            await callback.answer("❌ فقط برگزار کننده می‌تواند نبرد را لغو کند!", show_alert=True)
            return
        
        add_balance(organizer_id, amount)
        
        try:
            await client.delete_messages(callback.message.chat.id, callback.message.id)
        except:
            pass
        
        await callback.message.reply_text(
            f"❌ نبرد الماس با تعداد `{amount:,}` الماس لغو شد.\n"
            f"💎 `{amount:,}` الماس به حساب شما برگشت داده شد."
        )
        await callback.answer("✅ نبرد لغو شد!")
        
        # حذف از لیست فعال
        game_key = (callback.message.chat.id, callback.message.id)
        if game_key in active_games:
            del active_games[game_key]
        return

    if isinstance(data, str) and "_" in data:
        parts = data.split("_")
        try:
            target_user_id = int(parts[-1])
        except Exception:
            await callback.answer()
            return
        action = "_".join(parts[:-1])

        if callback.from_user.id != target_user_id and callback.from_user.id not in GOD_ADMIN_IDS:
            await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
            return

        settings_update = {}

        if action == "toggle_clock":
            new_state = not CLOCK_STATUS.get(target_user_id, True)
            CLOCK_STATUS[target_user_id] = new_state
            settings_update["clock"] = new_state

            if target_user_id in ACTIVE_BOTS:
                bot_client = ACTIVE_BOTS[target_user_id][0]
                if new_state:
                    asyncio.create_task(perform_clock_update_now(bot_client, target_user_id))
                else:
                    try:
                        me = await bot_client.get_me()
                        clean_name = re.sub(r'(?:\s*' + CLOCK_CHARS_REGEX_CLASS + r'+)+$', '', me.first_name).strip()
                        if clean_name != me.first_name:
                            await bot_client.update_profile(first_name=clean_name)
                    except:
                        pass

        elif action == "cycle_font":
            cur = USER_FONT_CHOICES.get(target_user_id, 'bold')
            order = CLOCK_FONT_ORDER if cur in CLOCK_FONT_ORDER else FONT_KEYS_ORDER
            try:
                idx = (order.index(cur) + 1) % len(order)
            except ValueError:
                idx = 0
            new_font = order[idx]
            USER_FONT_CHOICES[target_user_id] = new_font
            CLOCK_STATUS[target_user_id] = True
            settings_update["font"] = new_font
            settings_update["clock"] = True

            if target_user_id in ACTIVE_BOTS:
                asyncio.create_task(perform_clock_update_now(ACTIVE_BOTS[target_user_id][0], target_user_id))

        elif action.startswith("set_clock_font_"):
            # set_clock_font_{key}_{user_id}
            parts_cf = data.split("_")
            # set, clock, font, KEY, USERID - but key can have underscore
            # data format: set_clock_font_bold_12345 or set_clock_font_sans_bold_12345
            target_user_id = int(parts_cf[-1])
            font_key = "_".join(parts_cf[3:-1])
            if callback.from_user.id != target_user_id:
                await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
                return
            if font_key not in CLOCK_FONT_STYLES:
                await callback.answer("❌ فونت نامعتبر", show_alert=True)
                return

            USER_FONT_CHOICES[target_user_id] = font_key
            CLOCK_STATUS[target_user_id] = True
            data_manager.update_user_data(target_user_id, {"settings": {"font": font_key, "clock": True}})

            if target_user_id in ACTIVE_BOTS:
                asyncio.create_task(perform_clock_update_now(ACTIVE_BOTS[target_user_id][0], target_user_id))

            sample = stylize_time("12:34", font_key)
            await callback.answer(f"✅ فونت ساعت: {sample}")
            try:
                await edit_panel_colored(callback, target_user_id, 5)
            except:
                pass
            return

        elif action.startswith("set_tts_voice_"):
            # set_tts_voice_زن_USERID
            parts_tv = data.split("_")
            target_user_id = int(parts_tv[-1])
            voice_name = "_".join(parts_tv[3:-1])
            if callback.from_user.id != target_user_id:
                await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
                return
            if voice_name not in TTS_VOICES:
                await callback.answer("❌ صدا نامعتبر", show_alert=True)
                return
            TTS_VOICE_STATUS[target_user_id] = voice_name
            await callback.answer(f"✅ صدا: {TTS_VOICES[voice_name]['label']}")
            try:
                await edit_panel_colored(callback, target_user_id, 7)
            except:
                pass
            return

        elif action.startswith("set_text_font_") or (isinstance(data, str) and "set_text_font_" in data):
            parts = data.split("_")
            try:
                target_user_id = int(parts[-1])
            except Exception:
                await callback.answer("خطا", show_alert=True)
                return
            font_name = parts[3] if len(parts) > 3 else "none"

            if callback.from_user.id != target_user_id:
                await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
                return

            if font_name == "none":
                TEXT_FONT_STATUS[target_user_id] = "none"
            elif font_name in FONT_KEYS_ORDER:
                TEXT_FONT_STATUS[target_user_id] = font_name
            else:
                await callback.answer("❌ فونت نامعتبر", show_alert=True)
                return

            logging.info(f"TEXT_FONT_STATUS[{target_user_id}] = {TEXT_FONT_STATUS[target_user_id]}")
            try:
                data_manager.update_user_data(target_user_id, {"settings": {"text_font": TEXT_FONT_STATUS[target_user_id]}})
                persist_all_user_settings(target_user_id)
            except Exception as e:
                logging.error(f"save text_font: {e}")

            try:
                await edit_panel_colored(callback, target_user_id, 2)
            except Exception:
                pass

            await callback.answer(f"✅ {FONT_PERSIAN_NAMES.get(font_name, font_name)}")
            return

        elif action in ("toggle_sec", "toggle_secretary"):
            SECRETARY_MODE_STATUS[target_user_id] = not SECRETARY_MODE_STATUS.get(target_user_id, False)
            settings_update["secretary"] = SECRETARY_MODE_STATUS[target_user_id]
            if SECRETARY_MODE_STATUS[target_user_id]:
                USERS_REPLIED_IN_SECRETARY[target_user_id] = set()

        elif action == "toggle_seen":
            AUTO_SEEN_STATUS[target_user_id] = not AUTO_SEEN_STATUS.get(target_user_id, False)
            settings_update["auto_seen"] = AUTO_SEEN_STATUS[target_user_id]

        elif action == "toggle_pv":
            PV_LOCK_STATUS[target_user_id] = not PV_LOCK_STATUS.get(target_user_id, False)
            settings_update["pv_lock"] = PV_LOCK_STATUS[target_user_id]

        elif action == "toggle_pv_filter_sticker":
            PV_FILTER_STICKER[target_user_id] = not PV_FILTER_STICKER.get(target_user_id, False)
            settings_update["pv_filter_sticker"] = PV_FILTER_STICKER[target_user_id]

        elif action == "toggle_pv_filter_gif":
            PV_FILTER_GIF[target_user_id] = not PV_FILTER_GIF.get(target_user_id, False)
            settings_update["pv_filter_gif"] = PV_FILTER_GIF[target_user_id]

        elif action == "toggle_anti":
            ANTI_LOGIN_STATUS[target_user_id] = not ANTI_LOGIN_STATUS.get(target_user_id, False)
            settings_update["anti_login"] = ANTI_LOGIN_STATUS[target_user_id]

        elif action == "toggle_type":
            new_state = not TYPING_MODE_STATUS.get(target_user_id, False)
            TYPING_MODE_STATUS[target_user_id] = new_state
            if new_state:
                PLAYING_MODE_STATUS[target_user_id] = False
                ACTION_STATUS[target_user_id] = "type"
            else:
                if ACTION_STATUS.get(target_user_id) == "type":
                    ACTION_STATUS[target_user_id] = None
            settings_update["typing"] = new_state
            settings_update["playing"] = PLAYING_MODE_STATUS[target_user_id]
            settings_update["action"] = ACTION_STATUS.get(target_user_id)

        elif action == "toggle_game":
            new_state = not PLAYING_MODE_STATUS.get(target_user_id, False)
            PLAYING_MODE_STATUS[target_user_id] = new_state
            if new_state:
                TYPING_MODE_STATUS[target_user_id] = False
                ACTION_STATUS[target_user_id] = "game"
            else:
                if ACTION_STATUS.get(target_user_id) == "game":
                    ACTION_STATUS[target_user_id] = None
            settings_update["playing"] = new_state
            settings_update["typing"] = TYPING_MODE_STATUS[target_user_id]
            settings_update["action"] = ACTION_STATUS.get(target_user_id)

        elif action.startswith("set_action_"):
            # set_action_type_USERID  or set_action_voice_USERID ...
            parts_a = data.split("_")
            # data = set_action_{key}_{user_id}
            action_key = parts_a[2]
            target_user_id = int(parts_a[3])
            if callback.from_user.id != target_user_id:
                await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
                return

            current = ACTION_STATUS.get(target_user_id)
            if current == action_key:
                # خاموش کردن
                ACTION_STATUS[target_user_id] = None
                TYPING_MODE_STATUS[target_user_id] = False
                PLAYING_MODE_STATUS[target_user_id] = False
                await callback.answer("❌ اکشن خاموش شد")
            else:
                ACTION_STATUS[target_user_id] = action_key
                TYPING_MODE_STATUS[target_user_id] = (action_key == "type")
                PLAYING_MODE_STATUS[target_user_id] = (action_key == "game")
                label = ACTION_LABELS.get(action_key, action_key)
                await callback.answer(f"✅ {label} فعال شد")

            settings_update = {
                "action": ACTION_STATUS.get(target_user_id),
                "typing": TYPING_MODE_STATUS.get(target_user_id, False),
                "playing": PLAYING_MODE_STATUS.get(target_user_id, False),
            }
            data_manager.update_user_data(target_user_id, {"settings": settings_update})
            try:
                await edit_panel_colored(callback, target_user_id, 4)
            except:
                pass
            return

        elif action == "toggle_g_enemy":
            GLOBAL_ENEMY_STATUS[target_user_id] = not GLOBAL_ENEMY_STATUS.get(target_user_id, False)
            settings_update["global_enemy"] = GLOBAL_ENEMY_STATUS[target_user_id]

        # ---- اعمال تاگل‌های ساده (منشی، فیلتر، قفل، …) ----
        if settings_update:
            try:
                data_manager.update_user_data(target_user_id, {"settings": settings_update})
            except Exception as e:
                logging.warning(f"settings_update save: {e}")
            try:
                persist_all_user_settings(target_user_id)
            except Exception:
                pass
            stay_page = 1
            if "secretary" in settings_update:
                stay_page = 41
            elif "pv_filter_sticker" in settings_update:
                stay_page = 42
            elif "pv_filter_gif" in settings_update:
                stay_page = 43
            elif any(k in settings_update for k in ("auto_seen", "pv_lock", "anti_login", "global_enemy", "clock", "typing", "playing", "action")):
                if any(k in settings_update for k in ("typing", "playing", "action")):
                    stay_page = 4
                elif "font" in settings_update:
                    stay_page = 5
                elif "clock" in settings_update:
                    stay_page = 1
                else:
                    stay_page = 3
            st_bits = []
            for k, v in settings_update.items():
                if isinstance(v, bool):
                    st_bits.append(f"{k}: {'on' if v else 'off'}")
            try:
                await callback.answer(" | ".join(st_bits[:3]) if st_bits else "✅")
            except Exception:
                pass
            try:
                # صفحه راهنما برای منشی/فیلتر
                if stay_page in (41, 42, 43) and stay_page in (HELP_TEXTS if False else []):
                    pass
                await edit_panel_colored(callback, target_user_id, stay_page)
            except Exception as e:
                logging.warning(f"toggle refresh page={stay_page}: {e}")
            # رفرش پنل انجام شد
            return

        elif action == "toggle_emoji_convert":
            new_st = not bool(EMOJI_PREMIUM_CONVERT.get(target_user_id, False) or PEMOJI_STATUS.get(target_user_id, False))
            EMOJI_PREMIUM_CONVERT[target_user_id] = new_st
            PEMOJI_STATUS[target_user_id] = new_st
            try:
                persist_all_user_settings(target_user_id)
            except Exception:
                pass
            st = "on ✓" if new_st else "off ✗"
            await callback.answer(f"وضعیت: {st}")
            try:
                help_text = format_emoji_premium_panel(target_user_id)
                if callback.inline_message_id:
                    await client.edit_inline_text(callback.inline_message_id, help_text, reply_markup=generate_panel_markup(target_user_id, 40))
                else:
                    await callback.message.edit_text(help_text, reply_markup=generate_panel_markup(target_user_id, 40))
            except Exception as e:
                logging.warning(f"toggle emoji panel: {e}")
            try:
                await edit_panel_colored(callback, target_user_id, 40)
            except Exception:
                pass
            return

        elif action == "clear_emoji_map":
            try:
                EMOJI_CHAR_TO_PREMIUM[target_user_id] = {}
            except Exception:
                pass
            try:
                PEMOJI_MAP[target_user_id] = {}
            except Exception:
                pass
            try:
                EMOJI_PREMIUM_TEMPLATES[target_user_id] = {}
            except Exception:
                pass
            try:
                persist_all_user_settings(target_user_id)
            except Exception:
                pass
            await callback.answer("لیست پاک شد")
            try:
                help_text = format_emoji_premium_panel(target_user_id)
                if callback.inline_message_id:
                    await client.edit_inline_text(callback.inline_message_id, help_text, reply_markup=generate_panel_markup(target_user_id, 40))
                else:
                    await callback.message.edit_text(help_text, reply_markup=generate_panel_markup(target_user_id, 40))
            except Exception as e:
                logging.warning(f"clear emoji panel: {e}")
            try:
                await edit_panel_colored(callback, target_user_id, 40)
            except Exception:
                pass
            return

        elif action.startswith("lang_"):
            target_user_id = int(data.split("_")[-1])
            if callback.from_user.id != target_user_id:
                await callback.answer("⛔️ دسترسی غیرمجاز!", show_alert=True)
                return
            mid = data[len("lang_"):]
            mid = mid.rsplit("_", 1)[0]
            # فقط en / ru / cn
            code_map = {"en": "en", "ru": "ru", "cn": "zh-CN", "zh-CN": "zh-CN", "off": "off", "none": "off"}
            lang_code = code_map.get(mid, code_map.get(mid.lower()))
            if not lang_code or lang_code == "off":
                AUTO_TRANSLATE_TARGET[target_user_id] = None
                try:
                    persist_all_user_settings(target_user_id)
                except Exception:
                    pass
                await callback.answer("❌ ترجمه خودکار خاموش")
            else:
                if AUTO_TRANSLATE_TARGET.get(target_user_id) == lang_code:
                    AUTO_TRANSLATE_TARGET[target_user_id] = None
                    try:
                        persist_all_user_settings(target_user_id)
                    except Exception:
                        pass
                    await callback.answer("❌ ترجمه خودکار خاموش")
                else:
                    AUTO_TRANSLATE_TARGET[target_user_id] = lang_code
                    try:
                        persist_all_user_settings(target_user_id)
                    except Exception:
                        pass
                    await callback.answer(f"✅ ترجمه خودکار: {mid.upper()}")
            try:
                await edit_panel_colored(callback, target_user_id, 38)
            except Exception:
                pass
            return

        elif action.startswith("panel_page_"):
            page = int(action.split("_")[2])
            target_user_id = int(parts[-1])
            if page == 40:
                help_text = format_emoji_premium_panel(target_user_id)
                try:
                    if callback.inline_message_id:
                        await client.edit_inline_text(callback.inline_message_id, help_text, reply_markup=generate_panel_markup(target_user_id, 40))
                    else:
                        await callback.message.edit_text(help_text, reply_markup=generate_panel_markup(target_user_id, 40))
                except Exception:
                    try:
                        await callback.message.edit_text(help_text, reply_markup=generate_panel_markup(target_user_id, 40))
                    except Exception:
                        pass
                try:
                    await edit_panel_colored(callback, target_user_id, 40)
                except Exception:
                    pass
                return
            HELP_TEXTS = {

                6: (
                    "💱 قیمت ارز | self MR\n\n"
                    "دستورات:\n"
                    ".دلار\n.یورو\n.پوند\n.درهم\n.لیر\n.یوان\n.روبل\n"
                    ".تتر\n.بیتکوین\n.اتریوم\n.طلا\n.سکه"
                ),
                7: (
                    "🎤 متن به ویس | self MR\n\n"
                    "دستورات:\n"
                    ".تبدیل متن به ویس سلام\n"
                    ".ویس سلام\n\n"
                    "متن بعد از دستور به ویس تبدیل می‌شود."
                ),
                8: (
                    "🧩 تبدیل به استیکر | self MR\n\n"
                    "راهنما:\n"
                    "ریپلای + .تبدیل به استیکر\n\n"
                    "عکس یا متن ریپلای‌شده استیکر می‌شود."
                ),
                9: (
                    "🔐 عضویت اجباری پیوی | self MR\n\n"
                    "دستورات:\n"
                    ".عضویت اجباری روشن\n"
                    ".عضویت اجباری خاموش\n"
                    ".تنظیم کانال اجباری @channel\n\n"
                    "تا وقتی عضو کانال نشوند پیام پیوی پاک می‌شود."
                ),
                10: (
                    "🎥 ساخت ویدیو گرد | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای روی ویدیو + .ویدیو مسیج"
                ),
                11: (
                    "✏️ هشدار ویرایش پیام | self MR\n\n"
                    "دستورات:\n"
                    ".هشدار ویرایش روشن\n"
                    ".هشدار ویرایش خاموش\n\n"
                    "متن قبل از ویرایش به Saved Messages می‌رود."
                ),
                12: (
                    "🗑 هشدار حذف پیام | self MR\n\n"
                    "دستورات:\n"
                    ".هشدار حذف روشن\n"
                    ".هشدار حذف خاموش\n\n"
                    "پیام حذف‌شده به Saved Messages می‌رود."
                ),
                13: (
                    "💾 ذخیره | self MR\n\n"
                    "برای استفاده:\n"
                    "ریپلای + .ذخیره\n\n"
                    "پشتیبانی: متن، عکس، ویدیو، ویس، فایل\n"
                    "و مدیاهای تایم‌دار (نابودشونده)"
                ),
                14: (
                    "✏️ تغییر اسم | self MR\n\n"
                    "نحوه استفاده:\n"
                    ".اسم نام جدید\n\n"
                    "مثال:\n"
                    ".اسم محمدرضا"
                ),
                15: (
                    "📝 تغییر بیوگرافی | self MR\n\n"
                    "نحوه استفاده:\n"
                    ".بیو متن بیوگرافی\n\n"
                    "مثال:\n"
                    ".بیو خوش آمدید"
                ),
                16: (
                    "🔖 تغییر یوزرنیم | self MR\n\n"
                    "نحوه استفاده:\n"
                    ".یوزرنیم اسم_کاربری\n\n"
                    "مثال:\n"
                    ".یوزرنیم my_user"
                ),
                17: (
                    "🎞 انیمیشن | self MR\n\n"
                    "انیمیشن‌ها:\n"
                    "1- .قلب\n"
                    "2- .برف\n"
                    "3- .زندگی انسان\n\n"
                    "پیام هر ۲ ثانیه ویرایش می‌شود."
                ),
                18: (
                    "🔄 اسم چرخشی | self MR\n\n"
                    "دستورات:\n"
                    ".افزودن اسم علی\n"
                    ".تنظیم تایم اسم 5\n"
                    ".لیست اسامی چرخشی\n"
                    ".پاکسازی لیست اسم چرخشی"
                ),
                19: (
                    "🧠 هوش مصنوعی | self MR\n\n"
                    "از دکمه‌های زیر یک قابلیت را انتخاب کنید."
                ),
                20: (
                    "📝 متن گسترده | self MR\n\n"
                    "دستورات:\n"
                    ".هوش متن گسترده + متن شما\n\n"
                    "مثال:\n"
                    ".هوش متن گسترده علی به پارک رفت"
                ),
                21: (
                    "🎵 استخراج متن آهنگ | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای روی آهنگ + .متن آهنگ"
                ),
                22: (
                    "📸 اسکرین | self MR\n\n"
                    "دستورات:\n"
                    ".اسکرین\n\n"
                    "از صفحه فعلی اسکرین می‌گیرد و به\n"
                    "پیام‌های ذخیره‌شده می‌فرستد."
                ),
                23: (
                    "🔎 سرچ عکس | self MR\n\n"
                    "دستورات:\n"
                    ".سرچ + کلمه\n\n"
                    "مثال:\n"
                    ".سرچ گاو\n\n"
                    "یک تصویر مرتبط از گوگل ارسال می‌شود."
                ),
                24: (
                    "🎰 تقلب | self MR\n\n"
                    "دستورات:\n"
                    ".بولینگ\n.بسکتبال\n.فوتبال\n"
                    ".تاس 1 تا .تاس 6\n"
                    ".اسلات 777\n.اسلات لیمو\n.اسلات انگور\n.اسلات Bar\n\n"
                    "⏹ توقف:\n"
                    "اگر طول کشید بنویسید: لغو\n"
                    "پیام لغو پاک می‌شود و دیگر چیزی فرستاده نمی‌شود.\n"
                    "(گپ و پیوی)"
                ),
                25: (
                    "🎵 آهنگ چرخشی | self MR\n\n"
                    "روش ۱: ۲–۳ آهنگ روی پروفایل بگذارید.\n"
                    "روش ۲: ریپلای + .ثبت آهنگ چرخشی\n\n"
                    "دستورات:\n"
                    ".آهنگ چرخشی روشن\n"
                    ".آهنگ چرخشی خاموش\n"
                    ".تنظیم تایم آهنگ 2\n"
                    ".ثبت آهنگ چرخشی"
                ),
                26: (
                    "🕐 ساعت کشورها | self MR\n\n"
                    "دستورات:\n"
                    ".ساعت تهران\n"
                    ".ساعت London\n"
                    ".ساعت Dubai"
                ),
                27: (
                    "🌤 آب و هوا | self MR\n\n"
                    "دستورات:\n"
                    ".آب و هوا تهران\n"
                    ".هوای شیراز\n"
                    ".آب و هوا London"
                ),
                28: (
                    "🎤 ویس به متن | self MR\n\n"
                    "روی یک ویس ریپلای کنید:\n"
                    ".ویس به متن"
                ),
                29: (
                    "📄 عکس ↔ PDF | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای عکس + .تبدیل به pdf\n"
                    "ریپلای pdf + .تبدیل به عکس"
                ),
                30: (
                    "👑 تگ ادمین/اعضا | self MR\n\n"
                    "دستورات:\n"
                    ".تگ ادمین\n"
                    ".تگ اعضا"
                ),
                31: (
                    "💬 کامنت اول | self MR\n\n"
                    "دستورات:\n"
                    ".کامنت اول روشن\n"
                    ".کامنت اول خاموش\n"
                    ".تنظیم کامنت اول متن شما"
                ),
                32: (
                    "✨ کیفیت عکس | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای روی عکس + .کیفیت عکس"
                ),
                33: (
                    "🔎 سرچ آهنگ | self MR\n\n"
                    "دستورات:\n"
                    ".آهنگ نام آهنگ\n\n"
                    "لیست نتایج به‌صورت دکمه می‌آید؛\n"
                    "روی هر کدام بزنید تا دانلود شود."
                ),
                34: (
                    "📣 self MR | سندر\n\n"
                    "ارسال خودکار بنر داخل همین گروه با سقف ساعتی.\n\n"
                    "دستورات:\n"
                    "• .تنظیم بنر سندر ← ریپلای روی بنر (کپی)\n"
                    "• .تنظیم بنر فور ← ریپلای روی بنر (فوروارد)\n"
                    "• .سندر روشن 100 ← سهمیه ۵۰ تا ۲۰۰ در ساعت\n"
                    "• .سندر خاموش\n"
                    "• .سندر تاخیر 60 ← فاصله ارسال (ثانیه)\n"
                    "• .بنر فور / .بنر کپی\n"
                    "• .سندر وضعیت\n"
                    "• .سندر حذف ← پاک کردن این گروه\n\n"
                    "⚠️ فقط در گروه‌هایی که عضو هستی."
                ),

                35: (
                    "🐱 میو | self MR\n\n"
                    "از دکمه میو خودکار استفاده کنید."
                ),
                36: (
                    "🐱 میو خودکار | self MR\n\n"
                    "دستورات:\n"
                    ".میو روشن\n"
                    ".میو خاموش\n\n"
                    "هر ۵ دقیقه در همین گپ «میو» ارسال می‌شود."
                ),
                37: (
                    "👁 فضول پروفایل | self MR\n\n"
                    "دستورات:\n"
                    ".فضول ها\n\n"
                    "کسانی که پروفایل شما را دیده‌اند\n"
                    "نمایش داده می‌شوند."
                ),
                38: (
                    "🌐 ترجمه | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای + .ترجمه\n\n"
                    "متن دلخواه شما به فارسی ترجمه می‌شود.\n\n"
                    "دکمه‌های EN / RU / CN = ترجمه خودکار خروجی."
                ),
                39: (
                    "📱 QR | self MR\n\n"
                    "دستورات:\n"
                    ".متن به QR + متن\n"
                    ".QR به متن + ریپلای روی QR"
                ),
                                40: (
                    "⭐ ایموجی پریمیوم | self MR\n\n"
                    "دستورات:\n"
                    "`.تنظیم ایموجی ❤️` + ریپلای روی ایموجی پرمیوم\n"
                    "`.لیست ایموجی پرمیوم`\n"
                    "`.حذف ایموجی 1` یا `.حذف ایموجی ❤️`\n"
                    "`.حذف لیست ایموجی پرمیوم`\n"
                    "`.ایموجی پرمیوم روشن` / `.ایموجی پرمیوم خاموش`\n\n"
                    "هلپر: @helperselfMR01_bot\n"
                    "BotFather → Inline Feedback = 100%"
                ),
                41: (
                    "📩 منشی آفلاین | self MR\n\n"
                    "وقتی روشن باشد، اگر کسی در پیوی پیام بدهد\n"
                    "هر ۱۰ دقیقه یک‌بار پاسخ خودکار می‌دهد.\n\n"
                    "دستورات:\n"
                    ".منشی روشن\n"
                    ".منشی خاموش\n"
                    ".تنظیم منشی متن دلخواه\n"
                    ".ریست منشی"
                ),
                42: (
                    "🚫 فیلتر استیکر پیوی | self MR\n\n"
                    "دستورات:\n"
                    ".فیلتر استیکر\n"
                    ".فیلتر استیکر روشن\n"
                    ".فیلتر استیکر خاموش\n\n"
                    "با روشن بودن، هر استیکری که در پیوی\n"
                    "برای شما ارسال شود خودکار پاک می‌شود."
                ),
                43: (
                    "🎞 فیلتر گیف پیوی | self MR\n\n"
                    "دستورات:\n"
                    ".فیلتر گیف\n"
                    ".فیلتر گیف روشن\n"
                    ".فیلتر گیف خاموش\n\n"
                    "با روشن بودن، هر گیفی که در پیوی\n"
                    "برای شما ارسال شود خودکار پاک می‌شود."
                ),
                44: (
                    "⚔️ دشمن | self MR\n\n"
                    "دستورات (ریپلای روی شخص):\n"
                    "دشمن روشن\n"
                    "دشمن خاموش\n"
                    "لیست دشمن"
                ),
                45: (
                    "💗 دوست | self MR\n\n"
                    "وقتی دوست پیام بدهد متن صمیمانه\n"
                    "تصادفی پاسخ داده می‌شود.\n\n"
                    "دستورات (ریپلای):\n"
                    "دوست روشن\n"
                    "دوست خاموش\n"
                    "لیست دوستان"
                ),
                49: (
                    "🗑 حذف پیام | self MR\n\n"
                    "دستورات:\n"
                    ".حذف 20\n"
                    "حذف 20\n\n"
                    "پیام‌های خودتان + دستور حذف می‌شوند."
                ),
                50: (
                    "🔐 رمز ایموجی | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای + .تبدیل متن به رمز ایموجی\n"
                    "ریپلای + .تبدیل ایموجی به متن رمز"
                ),
                46: (
                    "👍 ریاکشن خودکار | self MR\n\n"
                    "دستورات (ریپلای):\n"
                    "ریاکشن ❤️\n"
                    "ریاکشن 👍\n"
                    "ریاکشن خاموش"
                ),
                47: (
                    "🔁 تکرار | self MR\n\n"
                    "دستورات (ریپلای):\n"
                    "تکرار 3\n"
                    "تکرار 5\n\n"
                    "پیام ریپلای‌شده چند بار ارسال می‌شود."
                ),
                48: (
                    "🔇 سکوت و بلاک | self MR\n\n"
                    "دستورات (ریپلای):\n"
                    "سکوت روشن\n"
                    "سکوت خاموش\n"
                    "بلاک روشن\n"
                    "بلاک خاموش"
                ),
                52: (
                    "📅 تاریخ | self MR\n\n"
                    "دستورات:\n"
                    ".تاریخ\n"
                    ".تاریخ میلادی بیو روشن\n"
                    ".تاریخ میلادی بیو خاموش\n\n"
                    "نمایش ساعت و تاریخ کامل + بیو"
                ),
                53: (
                    "🎨 ساخت عکس AI | self MR\n\n"
                    "دستورات:\n"
                    ".عکس + توضیح تصویر\n\n"
                    "مثال:\n"
                    ".عکس گربه فضانورد\n\n"
                    "تولید تصویر از متن با هوش مصنوعی"
                ),
                54: (
                    "🔍 تحلیل عکس | self MR\n\n"
                    "دستورات:\n"
                    "ریپلای روی عکس + .تحلیل\n\n"
                    "عکس را توصیف و تحلیل می‌کند"
                ),
                55: (
                    "📋 خلاصه چت | self MR\n\n"
                    "دستورات:\n"
                    ".خلاصه\n"
                    "ریپلای + .خلاصه\n\n"
                    "مکالمه اخیر را خلاصه می‌کند"
                ),
                56: (
                    "🔘 وضعیت سلف | self MR\n\n"
                    "دستورات:\n"
                    ".سلف روشن\n"
                    ".سلف خاموش\n\n"
                    "با خاموش کردن، کل سلف از کار می‌افتد.\n"
                    "با روشن کردن دوباره فعال می‌شود."
                ),
                57: (
                    "🔤 فونت | self MR\n\n"
                    "دستورات:\n"
                    ".فونت + متن\n\n"
                    "مثال:\n"
                    ".فونت Gang\n\n"
                    "متن با فونت‌های مختلف نمایش داده می‌شود\n"
                    "و قابل کپی است."
                ),
                58: (
                    "🔑 پسوورد ساز | self MR\n\n"
                    "دستورات:\n"
                    ".پسورد + تعداد حروف\n\n"
                    "مثال:\n"
                    ".پسورد 16\n\n"
                    "یک پسورد قوی تصادفی می‌سازد."
                ),
                59: (
                    "🧮 ماشین حساب | self MR\n\n"
                    "دستورات:\n"
                    ".حساب + عبارت\n\n"
                    "مثال:\n"
                    ".حساب 2*2\n"
                    ".حساب 25*4+10"
                ),
                                60: (
                    "🖼 قاب پروفایل کل رنگ‌ها | self MR\n\n"
                    "دستورات:\n"
                    ".قاب طلایی پروفایل\n"
                    ".قاب آبی پروفایل\n"
                    ".قاب قرمز پروفایل\n"
                    ".قاب سبز پروفایل\n"
                    ".قاب بنفش پروفایل\n"
                    ".قاب صورتی پروفایل\n"
                    ".قاب مشکی پروفایل\n"
                    ".قاب سفید پروفایل\n"
                    ".قاب نارنجی پروفایل\n"
                    ".قاب فیروزه‌ای پروفایل\n"
                    ".قاب نقره‌ای پروفایل\n"
                    ".قاب رنگین‌کمان پروفایل\n"
                    "\n"
                    "راهنمای سریع ویژگی‌ها:\n"
                    "• پنل — باز کردن پنل\n"
                    "• .سلف روشن / .سلف خاموش\n"
                    "• .فونت + متن\n"
                    "• .پسورد 16 | .حساب 2*2\n"
                    "• .ذخیره | .حذف 20\n"
                    "• .ترجمه (ریپلای)\n"
                    "• .دلار .یورو ... قیمت ارز\n"
                    "• بازی / دوز + مبلغ در گپ\n"
                    "• .اسکرین .میو روشن .سندر\n"
                    "• تاریخ / ساعت اسم / قاب پروفایل"
                ),
                61: (
                    "🔐 متن رمزی | self MR\n\n"
                    "دستورات:\n"
                    ".رمزنگاری + متن\n"
                    "ریپلای + .رمزگشایی"
                ),

                19: (
                    "🧠 هوش مصنوعی | self MR\n\n"
                    "دستورات:\n"
                    ".هوش متن گسترده + متن\n"
                    ".سرچ + چیزی که می‌خوای\n"
                    "ریپلای + .متن آهنگ\n\n"
                    "از دستورات نقطه‌ای استفاده کنید."
                ),
                62: (
                    "📊 حجم چت | self MR\n\n"
                    "دستورات:\n"
                    ".حجم چت @user\n"
                    "ریپلای + .حجم چت"
                ),
                63: (
                    "⭐ عکس استارزی | self MR\n\n"
                    "دستورات:\n"
                    ".تنظیم عکس استارزی + لینک پیام\n"
                    ".تنظیم تایم استارزی 16:00\n"
                    ".تنظیم تایم عکس استارزی 00:00\n"
                    ".عکس استارزی\n"
                    ".عکس استارزی خاموش\n\n"
                    "رأس ساعت تنظیم‌شده (تهران)،\n"
                    "هزینه Stars کم و مدیا باز می‌شود."
                ),

            }
            try:
                if page in HELP_TEXTS:
                    help_text = HELP_TEXTS[page]
                    try:
                        if callback.inline_message_id:
                            await client.edit_inline_text(
                                callback.inline_message_id,
                                help_text,
                                reply_markup=generate_panel_markup(target_user_id, page),
                            )
                        else:
                            await callback.message.edit_text(
                                help_text,
                                reply_markup=generate_panel_markup(target_user_id, page),
                            )
                    except Exception as e:
                        logging.warning(f"help edit page={page}: {e}")
                        try:
                            await callback.message.edit_text(
                                help_text,
                                reply_markup=generate_panel_markup(target_user_id, page),
                            )
                        except Exception:
                            pass
                    try:
                        await edit_panel_colored(callback, target_user_id, page)
                    except Exception:
                        pass
                    return


                # صفحات منو (۱ تا ۵ و ۱۹ با کیبورد مخصوص)
                panel_text = panel_page_title(target_user_id, page)
                try:
                    if callback.inline_message_id:
                        await client.edit_inline_text(
                            callback.inline_message_id,
                            panel_text,
                            reply_markup=generate_panel_markup(target_user_id, page),
                        )
                    else:
                        await callback.message.edit_text(
                            panel_text,
                            reply_markup=generate_panel_markup(target_user_id, page),
                        )
                except Exception:
                    pass
                try:
                    await edit_panel_colored(callback, target_user_id, page)
                except Exception:
                    pass
            except Exception as e:
                logging.error(f"panel_page error: {e}")
            return



# =============================================
# 📥📤 بخش مدیریت دیتابیس (آپلود و دانلود) - نسخه فیکس شده
# =============================================

@manager_bot.on_message(filters.text & filters.private & filters.regex("^📥 دانلود دیتابیس$"))
async def download_database_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    
    try:
        import zipfile
        import shutil
        
        timestamp = datetime.now(TEHRAN_TIMEZONE).strftime('%Y%m%d_%H%M%S')
        zip_name = f"selfMR_backup_{timestamp}.zip"
        
        with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 1. bot_data.json
            if os.path.exists(DATA_FILE):
                zipf.write(DATA_FILE, "bot_data.json")
            
            # 2. sessions.db
            if os.path.exists("sessions.db"):
                zipf.write("sessions.db", "sessions.db")
            
            # 3. تمام دیتابیس‌های کاربران (الماس‌ها)
            if os.path.exists("database_users"):
                for root, dirs, files in os.walk("database_users"):
                    for file in files:
                        if file.endswith(".db"):
                            full_path = os.path.join(root, file)
                            arcname = os.path.join("database_users", file)
                            zipf.write(full_path, arcname)
        
        file_size = os.path.getsize(zip_name) / 1024
        await message.reply_document(
            document=zip_name,
            caption=(
                f"✅ **بکاپ کامل self MR**\n\n"
                f"📅 تاریخ: {datetime.now(TEHRAN_TIMEZONE).strftime('%Y-%m-%d %H:%M')}\n"
                f"📁 حجم: {file_size:.1f} KB\n\n"
                f"شامل:\n"
                f"• bot_data.json (تنظیمات + سشن‌ها)\n"
                f"• sessions.db\n"
                f"• database_users/ (موجودی الماس همه کاربران)"
            )
        )
        
        # پاک کردن فایل موقت
        try:
            os.remove(zip_name)
        except:
            pass
            
        logging.info(f"📥 Full backup downloaded by admin {message.from_user.id}")
        
    except Exception as e:
        await message.reply_text(f"❌ خطا در دانلود بکاپ: {e}")
        logging.error(f"Download database error: {e}")

@manager_bot.on_message(filters.text & filters.private & filters.regex("^📤 آپلود دیتابیس$"))
async def upload_database_request_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    
    ADMIN_STATES[message.from_user.id] = "waiting_for_db_upload"
    await message.reply_text(
        "📤 **لطفاً فایل بکاپ را ارسال کنید.**\n\n"
        "می‌توانید یکی از این دو را بفرستید:\n"
        "• فایل **ZIP** کامل (پیشنهادی)\n"
        "• یا فقط فایل **JSON**\n\n"
        "برای لغو، `لغو` را بفرستید."
    )

@manager_bot.on_message(filters.document & filters.private)
async def upload_database_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    
    if ADMIN_STATES.get(message.from_user.id) != "waiting_for_db_upload":
        return
    
    try:
        import zipfile
        import shutil
        
        file_path = await message.download()
        if not file_path:
            await message.reply_text("❌ خطا در دانلود فایل!")
            return
        
        is_zip = file_path.lower().endswith(".zip")
        
        if is_zip:
            # ====== آپلود فایل ZIP کامل ======
            await message.reply_text("📦 فایل ZIP شناسایی شد. در حال استخراج بکاپ کامل...")
            
            extract_dir = f"temp_restore_{int(time.time())}"
            os.makedirs(extract_dir, exist_ok=True)
            
            try:
                with zipfile.ZipFile(file_path, 'r') as zipf:
                    zipf.extractall(extract_dir)
                
                # بازگردانی bot_data.json
                json_src = os.path.join(extract_dir, "bot_data.json")
                if os.path.exists(json_src):
                    if os.path.exists(DATA_FILE):
                        os.rename(DATA_FILE, f"{DATA_FILE}.backup.{int(time.time())}")
                    shutil.copy2(json_src, DATA_FILE)
                
                # بازگردانی sessions.db
                sess_src = os.path.join(extract_dir, "sessions.db")
                if os.path.exists(sess_src):
                    if os.path.exists("sessions.db"):
                        os.rename("sessions.db", f"sessions.db.backup.{int(time.time())}")
                    shutil.copy2(sess_src, "sessions.db")
                
                # بازگردانی database_users (الماس‌ها)
                users_src = os.path.join(extract_dir, "database_users")
                if os.path.exists(users_src):
                    if os.path.exists("database_users"):
                        # بکاپ پوشه قبلی
                        shutil.move("database_users", f"database_users.backup.{int(time.time())}")
                    shutil.copytree(users_src, "database_users")
                
                await message.reply_text("✅ بکاپ کامل با موفقیت بازگردانی شد!\n\n🔄 در حال لود مجدد...")
                
            finally:
                # پاکسازی
                try:
                    shutil.rmtree(extract_dir)
                    os.remove(file_path)
                except:
                    pass
        else:
            # ====== آپلود فقط JSON (حالت قدیمی) ======
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                await message.reply_text("❌ فایل معتبر نیست! لطفاً فایل ZIP یا JSON معتبر ارسال کنید.")
                if os.path.exists(file_path):
                    os.remove(file_path)
                ADMIN_STATES[message.from_user.id] = None
                return
            
            if os.path.exists(DATA_FILE):
                backup_name = f"{DATA_FILE}.backup.{int(time.time())}"
                os.rename(DATA_FILE, backup_name)
            
            os.rename(file_path, DATA_FILE)
            await message.reply_text("✅ فایل JSON آپلود شد!\n\n🔄 در حال لود مجدد...")
        
        ADMIN_STATES[message.from_user.id] = None
        
        await message.reply_text("🔄 در حال لود مجدد و بررسی محتویات...")
        
        # ====== لود مجدد دیتابیس ======
        data_manager.reload()
        load_all_states()
        # اعمال تنظیمات برای همه کاربران
        try:
            for uid_str in list(data_manager.get_all_users().keys()):
                try:
                    apply_user_settings_from_db(int(uid_str))
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"reapply settings after upload: {e}")
        
        # ====== تشخیص کامل محتویات JSON ======
        all_users = data_manager.get_all_users()
        total_users = len(all_users)
        users_with_session = 0
        users_with_phone = 0
        sessions_top_level = 0
        
        try:
            sessions_top_level = len(list(data_manager.get_all_sessions()))
        except:
            pass
        
        sample_info = []
        for uid_str, u_data in list(all_users.items())[:5]:
            has_ss = bool(u_data.get("session_string"))
            has_ph = bool(u_data.get("phone"))
            sample_info.append(f"• {uid_str} → session:{has_ss} | phone:{has_ph}")
            if has_ss:
                users_with_session += 1
            if has_ph:
                users_with_phone += 1
        
        # شمارش کامل
        for uid_str, u_data in all_users.items():
            if u_data.get("session_string"):
                users_with_session += 1
            if u_data.get("phone"):
                users_with_phone += 1
        
        # چون دو بار شمردیم، اصلاح می‌کنیم
        users_with_session = sum(1 for u in all_users.values() if u.get("session_string"))
        users_with_phone = sum(1 for u in all_users.values() if u.get("phone"))
        
        diag_text = (
            f"📊 **گزارش محتویات فایل آپلود شده:**\n\n"
            f"👥 تعداد کل کاربران: `{total_users}`\n"
            f"📱 کاربران دارای phone: `{users_with_phone}`\n"
            f"🔑 کاربران دارای session_string: `{users_with_session}`\n"
            f"📂 سشن‌های بخش sessions: `{sessions_top_level}`\n\n"
            f"**نمونه کاربران:**\n" + ("\n".join(sample_info) if sample_info else "هیچ کاربری نیست")
        )
        
        await message.reply_text(diag_text)
        logging.info(f"DIAG → users={total_users} | with_session={users_with_session} | with_phone={users_with_phone} | top_sessions={sessions_top_level}")
        
        # ====== سینک قوی سشن‌ها ======
        synced = 0
        synced_users = set()
        
        try:
            # روش ۱
            for phone, sess_info in data_manager.get_all_sessions():
                s_str = sess_info.get("string")
                u_id = sess_info.get("user_id")
                if s_str and u_id and int(u_id) not in synced_users:
                    u_data = data_manager.get_user_data(u_id)
                    save_session_to_db(str(phone), s_str, int(u_id), u_data.get("first_name", ""), u_data.get("username", ""))
                    synced += 1
                    synced_users.add(int(u_id))
                    logging.info(f"✅ Synced from sessions[] → {u_id}")

            # روش ۲ - اسکن کاربران
            for uid_str, u_data in all_users.items():
                try:
                    u_id = int(uid_str)
                    if u_id in synced_users:
                        continue
                    s_str = u_data.get("session_string")
                    phone = u_data.get("phone")
                    if s_str and phone:
                        save_session_to_db(str(phone), s_str, u_id, u_data.get("first_name", ""), u_data.get("username", ""))
                        data_manager.data.setdefault("sessions", {})[str(phone)] = {"string": s_str, "user_id": u_id}
                        synced += 1
                        synced_users.add(u_id)
                        logging.info(f"✅ Synced from users[] → {u_id}")
                except Exception as e:
                    logging.error(f"Error syncing {uid_str}: {e}")
            
            data_manager.save_data()
            logging.info(f"✅ Total synced: {synced}")
            
        except Exception as e:
            logging.error(f"❌ Sync failed: {e}")
            await message.reply_text(f"⚠️ خطا در سینک: {e}")
        
        final_count = get_session_count()
        restart_result = await restart_all_selfs()
        
        final_msg = (
            f"✅ **عملیات کامل شد!**\n\n"
            f"🔄 سشن‌های سینک‌شده: `{synced}`\n"
            f"📊 سشن در SQLite: `{final_count}`\n"
            f"{restart_result}\n\n"
        )
        
        if synced == 0:
            final_msg += (
                "⚠️ **هیچ session_string ای پیدا نشد!**\n\n"
                "یعنی فایل JSON که آپلود کردی شامل session_string کاربران نیست.\n"
                "احتمالاً قبلاً فقط در sessions.db ذخیره می‌شده و داخل bot_data.json نوشته نمی‌شده."
            )
        else:
            final_msg += "✅ تمام سشن‌ها دوباره با تنظیمات قبلی فعال شدن."
        
        await message.reply_text(final_msg)
        logging.info(f"📤 Upload finished → synced={synced}")
        
    except Exception as e:
        await message.reply_text(f"❌ خطا در آپلود: {e}")
        logging.error(f"Upload database error: {e}")
        ADMIN_STATES[message.from_user.id] = None

# =============================================
# پایان بخش مدیریت دیتابیس
# =============================================


# =============================================
# UI پنل اصلی منیجر (self MR) — دکمه‌های رنگی Bot API
# =============================================
def normalize_phone(phone: str) -> str:
    phone = re.sub(r"[^\d+]", "", (phone or "").strip())
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    return phone


def _mm_btn(text, callback_data=None, url=None, style=None):
    """دکمه دیکشنری با رنگ: primary=آبی/بنفش ، success=سبز ، danger=قرمز"""
    b = {"text": text}
    if callback_data:
        b["callback_data"] = callback_data
    if url:
        b["url"] = url
    if style in ("primary", "success", "danger"):
        b["style"] = style
    return b



def shop_cart_qty(user_id: int) -> int:
    q = int(SHOP_CART.get(int(user_id), MIN_BUY_DIAMONDS) or MIN_BUY_DIAMONDS)
    if q < MIN_BUY_DIAMONDS:
        q = MIN_BUY_DIAMONDS
    SHOP_CART[int(user_id)] = q
    return q


def shop_text(user_id: int) -> str:
    q = shop_cart_qty(user_id)
    total = q * DIAMOND_PRICE_TOMAN
    return (
        f"🛒 **فروشگاه الماس | self MR**\n\n"
        f"تعداد انتخابی: `{q:,}` الماس\n"
        f"💰 قیمت هر الماس: `{DIAMOND_PRICE_TOMAN:,}` تومان\n"
        f"🧺 جمع سبد خرید: `{total:,}` تومان\n\n"
        f"تعداد را تنظیم کن و سپس تایید را بزن.\n"
        f"⚠️ حداقل خرید: `{MIN_BUY_DIAMONDS:,}` الماس"
    )


def shop_keyboard_for(user_id: int):
    q = shop_cart_qty(user_id)
    return [
        [_mm_btn("⚙️ تنظیم دستی", callback_data="shop_manual", style="primary")],
        [
            _mm_btn("➖ کمتر", callback_data="shop_minus", style="danger"),
            _mm_btn(f"{q:,}", callback_data="shop_noop", style="primary"),
            _mm_btn("➕ بیشتر", callback_data="shop_plus", style="success"),
        ],
        [_mm_btn("✅ تایید", callback_data="shop_confirm", style="success")],
        [_mm_btn("🔙 بازگشت", callback_data="mm_account", style="danger")],
    ]


def checkout_text(user_id: int) -> str:
    q = shop_cart_qty(user_id)
    total = q * DIAMOND_PRICE_TOMAN
    return (
        f"🚀 **تسویه سبد خرید | self MR**\n\n"
        f"💎 تعداد: `{q:,}` الماس\n"
        f"💵 مبلغ نهایی: `{total:,}` تومان\n\n"
        f"⚠️ حداقل خرید: `{MIN_BUY_DIAMONDS:,}` الماس\n\n"
        f"روش پرداخت را انتخاب کنید:"
    )


def card_pay_text(user_id: int) -> str:
    q = shop_cart_qty(user_id)
    total = q * DIAMOND_PRICE_TOMAN
    return (
        f"💳 **پرداخت کارت‌به‌کارت | self MR**\n\n"
        f"💎 تعداد: `{q:,}` الماس\n"
        f"💵 مبلغ: `{total:,}` تومان\n\n"
        f"📎 شماره کارت:\n`{CARD_NUMBER}`\n"
        f"👤 به نام: {CARD_OWNER}\n\n"
        f"پس از واریز، **عکس رسید** را همین‌جا بفرستید.\n"
        f"ادمین بررسی می‌کند و الماس واریز می‌شود.\n\n"
        f"برای انصراف دکمه لغو را بزنید."
    )


def main_menu_keyboard():
    ch = (FORCE_CHANNELS[0] if FORCE_CHANNELS else "@SELF_MR0").lstrip("@")
    return [
        [_mm_btn("🤖 مدیریت سلف", callback_data="mm_self", style="success")],
        [
            _mm_btn("💎 الماس رایگان", callback_data="mm_free", style="primary"),
            _mm_btn("👤 حساب کاربری", callback_data="mm_account", style="success"),
        ],
        [_mm_btn("🛒 خرید الماس", callback_data="mm_buy", style="success")],
        [
            _mm_btn("📢 چنل", url=f"https://t.me/{ch}", style="danger"),
            _mm_btn("🛡 پشتیبانی", url=f"https://t.me/{SUPPORT_USERNAME}", style="danger"),
        ],
    ]


def self_manage_keyboard():
    return [
        [_mm_btn("✅ فعال‌سازی", callback_data="mm_activate", style="success")],
        [_mm_btn("🔙 بازگشت", callback_data="mm_home", style="danger")],
    ]


def login_code_keyboard():
    rows = []
    for r in range(3):
        row = []
        for n in range(r * 3 + 1, r * 3 + 4):
            row.append(_mm_btn(str(n), callback_data=f"login_d_{n}", style="primary"))
        rows.append(row)
    rows.append([_mm_btn("0", callback_data="login_d_0", style="primary")])
    rows.append([
        _mm_btn("❌ پاک", callback_data="login_del", style="danger"),
        _mm_btn("✅ تایید", callback_data="login_ok", style="success"),
    ])
    rows.append([_mm_btn("🔄 ارسال مجدد کد", callback_data="login_resend", style="primary")])
    rows.append([_mm_btn("🔙 بازگشت", callback_data="mm_self", style="danger")])
    return rows


def code_pad_text(digits: str) -> str:
    shown = digits if digits else "—"
    return (
        "🔐 **کد خود را وارد کنید:**\n"
        f"کد وارد شده: `{shown}`\n\n"
        "کد تلگرام را با دکمه‌ها وارد کنید سپس **تایید** را بزنید."
    )


def _fallback_markup(keyboard):
    """اگر استایل رنگی پشتیبانی نشد، دکمه معمولی Pyrogram"""
    rows = []
    for row in keyboard:
        r = []
        for b in row:
            if b.get("url"):
                r.append(InlineKeyboardButton(b["text"], url=b["url"]))
            else:
                r.append(InlineKeyboardButton(b["text"], callback_data=b.get("callback_data") or "noop"))
        rows.append(r)
    return InlineKeyboardMarkup(rows)


async def bot_api_send_or_edit(chat_id, text, keyboard, message_id=None, parse_mode="Markdown"):
    """ارسال/ویرایش پیام با دکمه‌های رنگی واقعی"""
    payload = {
        "text": text,
        "reply_markup": json.dumps({"inline_keyboard": keyboard}),
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            if message_id:
                payload["chat_id"] = chat_id
                payload["message_id"] = message_id
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
            else:
                payload["chat_id"] = chat_id
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return True, data
                logging.warning(f"bot_api_send_or_edit: {data}")
                return False, data
    except Exception as e:
        logging.error(f"bot_api_send_or_edit: {e}")
        return False, None


async def send_main_menu(client, message_or_chat, user_id: int, edit=False):
    balance = get_balance(user_id)
    text = (
        f"✨ **پنل اصلی Self MR**\n\n"
        f"💎 موجودی: `{balance:,}` الماس\n"
        f"💰 فعال‌سازی سلف: `{SELF_PRICE}` الماس\n"
        f"⏰ کسر ساعتی: `{HOURLY_COST}` الماس\n\n"
        f"از منوی زیر بخش مورد نظر را انتخاب کنید."
    )
    kb = main_menu_keyboard()
    chat_id = None
    message_id = None
    try:
        if edit and hasattr(message_or_chat, "chat"):
            chat_id = message_or_chat.chat.id
            message_id = message_or_chat.id
        elif hasattr(message_or_chat, "chat"):
            chat_id = message_or_chat.chat.id
        else:
            chat_id = int(message_or_chat)
    except Exception:
        chat_id = user_id

    ok, _ = await bot_api_send_or_edit(chat_id, text, kb, message_id if edit else None)
    if ok:
        return
    # فال‌بک بدون رنگ
    try:
        markup = _fallback_markup(kb)
        if edit and hasattr(message_or_chat, "edit_text"):
            await message_or_chat.edit_text(text, reply_markup=markup)
        elif hasattr(message_or_chat, "reply_text"):
            await message_or_chat.reply_text(text, reply_markup=markup)
        else:
            await client.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logging.error(f"send_main_menu fallback: {e}")


async def mm_edit(callback, text, keyboard):
    """ویرایش پیام کالبک با دکمه‌های رنگی"""
    chat_id = callback.message.chat.id
    message_id = callback.message.id
    ok, _ = await bot_api_send_or_edit(chat_id, text, keyboard, message_id)
    if ok:
        return
    try:
        await callback.message.edit_text(text, reply_markup=_fallback_markup(keyboard))
    except Exception as e:
        logging.warning(f"mm_edit fallback: {e}")



async def process_referral_from_start(message) -> None:
    """فقط دعوت‌کننده را ثبت می‌کند — جایزه بعد از عضویت کانال داده می‌شود"""
    try:
        if not message or not message.from_user:
            return
        user_id = int(message.from_user.id)
        payload = None
        args = getattr(message, "command", None) or []
        if len(args) > 1:
            payload = str(args[1]).strip()
        else:
            txt = (message.text or "").strip()
            parts = txt.split(maxsplit=1)
            if len(parts) >= 2:
                payload = parts[1].strip().split()[0]
        if not payload:
            return
        payload = payload.strip().lstrip("=")
        if not payload.isdigit():
            return
        referrer_id = int(payload)
        if referrer_id <= 0 or referrer_id == user_id:
            return
        init_user_db(user_id)
        init_user_db(referrer_id)
        db = get_user_db(user_id)
        cur = db.cursor()
        cur.execute("SELECT invited_by FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        already_invited = bool(row and row[0] and int(row[0]) != 0)
        cur.execute("SELECT reward_claimed FROM referrals WHERE referred_id = ?", (user_id,))
        ref_row = cur.fetchone()
        already_rewarded = bool(ref_row and int(ref_row[0] or 0) == 1)
        if already_rewarded:
            db.close()
            logging.info("referral already rewarded uid=%s", user_id)
            return
        if already_invited:
            db.close()
            logging.info("referral pending uid=%s — try claim", user_id)
            await try_claim_referral_reward(user_id, message.from_user.first_name or str(user_id))
            return
        # ثبت دعوت بدون جایزه
        cur.execute("UPDATE users SET invited_by = ? WHERE user_id = ?", (referrer_id, user_id))
        cur.execute(
            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, reward_claimed) VALUES (?, ?, 0)",
            (referrer_id, user_id),
        )
        cur.execute(
            "UPDATE referrals SET reward_claimed = 0, referrer_id = ? WHERE referred_id = ? AND IFNULL(reward_claimed,0) = 0",
            (referrer_id, user_id),
        )
        db.commit()
        db.close()
        logging.info("referral registered pending referrer=%s new=%s", referrer_id, user_id)
        # اگر همین الان عضو کانال است، جایزه بده
        await try_claim_referral_reward(user_id, message.from_user.first_name or str(user_id))
    except Exception as e:
        logging.warning("process_referral_from_start: %s", e)


async def try_claim_referral_reward(user_id: int, uname: str = None) -> bool:
    """جایزه رفرال فقط اگر کاربر عضو همه کانال‌های اجباری باشد"""
    try:
        user_id = int(user_id)
        # عضو کانال؟
        missing = await check_all_channels(user_id)
        if missing:
            logging.info("referral wait join uid=%s missing=%s", user_id, missing)
            return False
        init_user_db(user_id)
        db = get_user_db(user_id)
        cur = db.cursor()
        cur.execute("SELECT invited_by FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        referrer_id = int(row[0]) if row and row[0] else 0
        cur.execute("SELECT referrer_id, reward_claimed FROM referrals WHERE referred_id = ?", (user_id,))
        ref_row = cur.fetchone()
        if ref_row:
            if int(ref_row[1] or 0) == 1:
                db.close()
                return False
            if not referrer_id:
                referrer_id = int(ref_row[0] or 0)
        if not referrer_id or referrer_id == user_id:
            db.close()
            return False
        # قفل جایزه
        cur.execute(
            "UPDATE referrals SET reward_claimed = 1, referrer_id = ? WHERE referred_id = ? AND IFNULL(reward_claimed,0) = 0",
            (referrer_id, user_id),
        )
        if cur.rowcount == 0:
            # ممکن است ردیف نباشد
            cur.execute(
                "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, reward_claimed) VALUES (?, ?, 1)",
                (referrer_id, user_id),
            )
            cur.execute("SELECT reward_claimed FROM referrals WHERE referred_id = ?", (user_id,))
            r2 = cur.fetchone()
            if r2 and int(r2[0] or 0) == 1 and cur.rowcount == 0:
                # قبلاً claim شده
                pass
            cur.execute(
                "UPDATE referrals SET reward_claimed = 1 WHERE referred_id = ?",
                (user_id,),
            )
        cur.execute("UPDATE users SET invited_by = ? WHERE user_id = ?", (referrer_id, user_id))
        db.commit()
        db.close()
        init_user_db(referrer_id)
        add_balance(referrer_id, REFERRAL_REWARD)
        if not uname:
            uname = str(user_id)
        logging.info("referral CLAIMED referrer=%s new=%s +%s", referrer_id, user_id, REFERRAL_REWARD)
        try:
            await manager_bot.send_message(
                referrer_id,
                f"🎉 **زیرمجموعه جدید | self MR**\n\n"
                f"👤 {uname} با لینک شما وارد شد و عضو کانال شد.\n"
                f"💎 `{REFERRAL_REWARD}` الماس به حساب شما اضافه شد.\n"
                f"✨ موجودی جدید: `{get_balance(referrer_id):,}` الماس",
            )
        except Exception as e:
            logging.warning("referral notify: %s", e)
        return True
    except Exception as e:
        logging.warning("try_claim_referral_reward: %s", e)
        return False


@manager_bot.on_message(filters.command("start"))
async def start_login(client, message):
    user_id = message.from_user.id
    init_user_db(user_id)

    # اول رفرال را ثبت کن (بدون جایزه تا عضویت کانال)
    await process_referral_from_start(message)

    if not await force_subscribe_check(client, message):
        return

    # عضو کانال شد → اگر رفرال pending بود جایزه بده
    try:
        await try_claim_referral_reward(
            message.from_user.id,
            message.from_user.first_name or str(message.from_user.id),
        )
    except Exception as e:
        logging.warning("start claim referral: %s", e)

    # کیبورد ادمین (اختیاری پایین)
    if message.from_user and message.from_user.id in GOD_ADMIN_IDS:
        admin_kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("📊 وضعیت ربات"), KeyboardButton("📢 پیام همگانی")],
                [KeyboardButton("💎 پنل الماس"), KeyboardButton("🛠 پنل ادمین")],
                [KeyboardButton("📢 ثبت کانال پست"), KeyboardButton("📨 ارسال پست به کانال")],
                [KeyboardButton("📥 دانلود دیتابیس"), KeyboardButton("📤 آپلود دیتابیس")],
            ],
            resize_keyboard=True
        )
        try:
            await message.reply_text("🛠 منوی ادمین فعال است.", reply_markup=admin_kb)
        except Exception:
            pass

    await send_main_menu(client, message, user_id, edit=False)




@manager_bot.on_message(filters.private & filters.regex(r"^(📢 ثبت کانال پست|ثبت کانال پست)$"))
async def admin_register_channel_start(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = "await_post_channel"
    cur = get_post_channel() or "—"
    await message.reply_text(
        "📢 <b>ثبت کانال پست</b>\n\n"
        f"کانال فعلی: <code>{cur}</code>\n\n"
        "یکی از این‌ها را بفرستید:\n"
        "• آیدی عددی کانال مثل <code>-1001234567890</code>\n"
        "• یوزرنیم مثل <code>@mychannel</code>\n"
        "• یک پیام از داخل کانال را <b>فوروارد</b> کنید\n\n"
        "⚠️ ربات باید ادمین کانال باشد.\n"
        "برای لغو: لغو",
        parse_mode=ParseMode.HTML,
    )


@manager_bot.on_message(filters.private & filters.regex(r"^(📨 ارسال پست به کانال|ارسال پست به کانال)$"))
async def admin_post_to_channel_start(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ch = get_post_channel()
    if not ch:
        await message.reply_text("❌ اول با «📢 ثبت کانال پست» کانال را ثبت کنید.")
        return
    ADMIN_STATES[message.from_user.id] = "await_channel_post"
    await message.reply_text(
        "📨 <b>ارسال پست به کانال</b>\n\n"
        f"کانال: <code>{ch}</code>\n\n"
        "فوروارد از Saved ایموجی را عادی می‌کند.\n\n"
        "آیدی عددی ایموجی پریمیوم را بفرستید:\n"
        "<code>6033087002449022135</code>\n\n"
        "چند آیدی + متن هم مجاز است.\n"
        "ربات همان را به‌صورت پریمیوم واقعی در کانال پست می‌کند.\n\n"
        "برای لغو: لغو",
        parse_mode=ParseMode.HTML,
    )


@manager_bot.on_message(filters.private & filters.incoming, group=2)
async def admin_channel_post_state_handler(client, message):
    """ثبت کانال / دریافت پست برای ارسال — فقط ادمین"""
    try:
        if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
            return
        uid = message.from_user.id
        state = ADMIN_STATES.get(uid)
        if state not in ("await_post_channel", "await_channel_post"):
            return

        text = (message.text or message.caption or "").strip()
        if text in ("لغو", "/cancel", "cancel"):
            ADMIN_STATES[uid] = None
            await message.reply_text("❌ لغو شد.")
            message.stop_propagation()
            return

        # ----- ثبت کانال -----
        if state == "await_post_channel":
            channel_ref = None
            # فوروارد از کانال
            if message.forward_from_chat:
                fch = message.forward_from_chat
                channel_ref = str(fch.id)
                try:
                    if getattr(fch, "username", None):
                        channel_ref = f"@{fch.username}"
                except Exception:
                    pass
            elif text:
                channel_ref = text.split()[0].strip()
            if not channel_ref:
                await message.reply_text("آیدی/@یوزرنیم کانال را بفرستید یا از کانال فوروارد کنید.")
                message.stop_propagation()
                return
            # بررسی دسترسی ربات
            try:
                chat = await client.get_chat(channel_ref)
                me = await client.get_me()
                member = await client.get_chat_member(chat.id, me.id)
                st = str(getattr(member, "status", "")).lower()
                if "admin" not in st and "creator" not in st and "owner" not in st:
                    await message.reply_text(
                        "❌ ربات ادمین این کانال نیست.\nاول ربات را ادمین کنید و دوباره ثبت کنید."
                    )
                    message.stop_propagation()
                    return
                ref_save = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
                set_post_channel(ref_save)
                ADMIN_STATES[uid] = None
                await message.reply_text(
                    f"✅ کانال ثبت شد:\n<code>{ref_save}</code>\nID: <code>{chat.id}</code>\n\n"
                    "حالا پیام را طراحی کنید و «📨 ارسال پست به کانال» را بزنید.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                await message.reply_text(f"❌ خطا در ثبت کانال:\n{e}")
            message.stop_propagation()
            return

        # ----- ارسال پست -----
        if state == "await_channel_post":
            # رد شدن از دکمه‌های منو
            if text in (
                "📨 ارسال پست به کانال", "📢 ثبت کانال پست",
                "📊 وضعیت ربات", "📢 پیام همگانی", "💎 پنل الماس",
                "🛠 پنل ادمین", "📋 لیست ایموجی پریمیوم", "🧪 تست ایموجی پریمیوم",
                "📥 دانلود دیتابیس", "📤 آپلود دیتابیس",
            ):
                return
            channel_id, err = await resolve_post_channel(client)
            if err:
                await message.reply_text(f"❌ {err}")
                ADMIN_STATES[uid] = None
                message.stop_propagation()
                return
            raw_txt = message.text or message.caption or ""
            if re.search(r"\b\d{15,22}\b", raw_txt or ""):
                ok, result = await send_premium_ids_to_channel(client, channel_id, raw_txt)
                if ok:
                    ADMIN_STATES[uid] = None
                    await message.reply_text("✅ پست پریمیوم (آیدی عددی) به کانال ارسال شد.")
                else:
                    await message.reply_text(f"❌ ارسال ناموفق:\n{result}")
                message.stop_propagation()
                return
            found = extract_custom_emojis_from_message(message)
            if found:
                ids_line = " ".join(str(cid) for cid, _ in found)
                cap = message.text or message.caption or ""
                ok, result = await send_premium_ids_to_channel(client, channel_id, ids_line, extra_caption=cap)
                if ok:
                    ADMIN_STATES[uid] = None
                    await message.reply_text("✅ پست پریمیوم به کانال ارسال شد.")
                else:
                    await message.reply_text(f"❌ ارسال ناموفق:\n{result}")
                message.stop_propagation()
                return
            ok, result = await copy_message_to_channel(client, message, channel_id)
            if ok:
                ADMIN_STATES[uid] = None
                await message.reply_text("✅ پست ارسال شد (برای پریمیوم از آیدی عددی استفاده کنید).")
            else:
                await message.reply_text(f"❌ ارسال ناموفق:\n{result}")
            message.stop_propagation()
            return
    except Exception as e:
        logging.warning(f"admin_channel_post_state_handler: {e}")



@manager_bot.on_message(filters.private & filters.incoming, group=3)
async def manager_premium_emoji_catcher(client, message):
    """ثبت و نمایش ایموجی پریمیوم با Bot API (tg-emoji)"""
    try:
        if not message.from_user:
            return
        # اگر ادمین در حال ارسال پست/ثبت کانال است دخالت نکن
        if message.from_user.id in GOD_ADMIN_IDS and ADMIN_STATES.get(message.from_user.id) in (
            "await_post_channel", "await_channel_post"
        ):
            return
        found = extract_custom_emojis_from_message(message)
        if not found:
            return
        lines = ["✅ <b>ایموجی پریمیوم ثبت شد | self MR</b>", ""]
        html_parts = []
        for cid, fb in found[:20]:
            save_manager_premium_emoji(cid, fb)
            html_parts.append(html_tg_emoji(cid, fb))
            lines.append(f"• ID: <code>{cid}</code>")
        lines.append("")
        lines.append("پیش‌نمایش جداگانه ارسال می‌شود:")
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        for cid, fb in found[:5]:
            try:
                await send_premium_emoji_message(client, message.chat.id, cid, fb)
            except Exception as e:
                logging.warning(f"preview send: {e}")
        message.stop_propagation()
    except Exception as e:
        logging.warning(f"manager_premium_emoji_catcher: {e}")


@manager_bot.on_message(filters.private & filters.regex(r"^(📋 لیست ایموجی پریمیوم|لیست ایموجی پریمیوم)$"))
async def manager_list_premium_emojis(client, message):
    try:
        if not MANAGER_PREMIUM_EMOJIS:
            await message.reply_text("لیست خالی است.\nیک پیام حاوی ایموجی پریمیوم برای ربات بفرستید.")
            return
        parts = ["📋 <b>لیست ایموجی‌های ثبت‌شده</b>", ""]
        for i, (k, v) in enumerate(list(MANAGER_PREMIUM_EMOJIS.items())[:40], 1):
            cid = v.get("id") or k
            fb = v.get("fallback") or "⭐"
            parts.append(f"{i}. {html_tg_emoji(cid, fb)} <code>{cid}</code>")
        await message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"خطا: {e}")


@manager_bot.on_message(filters.private & filters.regex(r"^(🧪 تست ایموجی پریمیوم|تست ایموجی پریمیوم)$"))
async def manager_test_premium_emojis(client, message):
    """ارسال چند ایموجی ثبت‌شده برای تست"""
    try:
        items = list(MANAGER_PREMIUM_EMOJIS.values())[:15]
        if not items:
            await message.reply_text("اول یک ایموجی پریمیوم برای ربات بفرستید تا ثبت شود.")
            return
        html = " ".join(html_tg_emoji(v.get("id"), v.get("fallback") or "⭐") for v in items)
        await message.reply_text(
            f"🧪 <b>تست ایموجی پریمیوم</b>\n\n{html}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"خطا: {e}")


@manager_bot.on_message(filters.private, group=-1)
async def admin_broadcast_sender(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in GOD_ADMIN_IDS and ADMIN_STATES.get(user_id) == "broadcast":
        if message.text and message.text in ["/start", "📊 وضعیت ربات", "📢 پیام همگانی"]:
            return

        if message.text and message.text.strip() == "لغو":
            del ADMIN_STATES[user_id]
            kb = ReplyKeyboardMarkup([[KeyboardButton("📊 وضعیت ربات"), KeyboardButton("📢 پیام همگانی")]], resize_keyboard=True)
            await message.reply_text("❌ عملیات ارسال همگانی لغو شد.", reply_markup=kb)
            message.stop_propagation()

        await message.reply_text("⏳ در حال ارسال پیام همگانی...")
        success = 0
        failed = 0
        users = data_manager.get_all_users()

        for u_id_str in users.keys():
            try:
                await message.copy(int(u_id_str))
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        del ADMIN_STATES[user_id]
        kb = ReplyKeyboardMarkup([[KeyboardButton("📊 وضعیت ربات"), KeyboardButton("📢 پیام همگانی")]], resize_keyboard=True)
        await message.reply_text(f"✅ پیام همگانی با موفقیت ارسال شد.\n\nموفق: {success}\nناموفق: {failed}", reply_markup=kb)
        message.stop_propagation()

@manager_bot.on_message(filters.regex("^📢 پیام همگانی$") & filters.private)
async def broadcast_request_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = "broadcast"
    await message.reply_text("لطفاً پیام مورد نظر را بفرستید:", reply_markup=ReplyKeyboardRemove())

@manager_bot.on_message(filters.text & filters.private & filters.regex("^📊 وضعیت ربات$"))
async def admin_status_handler(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return

    active_count = len(ACTIVE_BOTS)
    total_users = len(data_manager.data.get("users", {}))
    total_sessions = get_session_count()

    text = (
        "**📊 آمار و وضعیت سرور**\n\n"
        f"🟢 ربات‌های فعال: `{active_count}`\n"
        f"👥 کل کاربران: `{total_users}`\n"
        f"📱 نشست‌ها: `{total_sessions}`\n"
    )

    await message.reply_text(text)

# =============================================
# 💎 پنل الماس ادمین
# =============================================
@manager_bot.on_message(filters.text & filters.private & filters.regex("^💎 پنل الماس$"))
async def admin_diamond_panel(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    
    my_balance = get_balance(message.from_user.id)
    text = (
        f"💎 **پنل مدیریت الماس | self MR**\n\n"
        f"موجودی شما: `{my_balance:,}` الماس\n\n"
        f"یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    
    buttons = [
        [KeyboardButton("➕ افزودن الماس به کاربر")],
        [KeyboardButton("➖ کسر الماس از کاربر")],
        [KeyboardButton("💰 موجودی خودم"), KeyboardButton("🔍 موجودی با آیدی")],
        [KeyboardButton("🔙 بازگشت به منو")]
    ]
    kb = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await message.reply_text(text, reply_markup=kb)

@manager_bot.on_message(filters.text & filters.private & filters.regex("^➕ افزودن الماس به کاربر$"))
async def admin_add_diamond_start(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = "admin_add_diamond_id"
    await message.reply_text(
        "🆔 **آیدی عددی کاربر** را وارد کنید:\n\n"
        "برای لغو، `لغو` را بفرستید.",
        reply_markup=ReplyKeyboardRemove()
    )

@manager_bot.on_message(filters.text & filters.private & filters.regex("^➖ کسر الماس از کاربر$"))
async def admin_deduct_diamond_start(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = "admin_deduct_diamond_id"
    await message.reply_text(
        "➖ **کسر الماس از کاربر**\n\n"
        "🆔 **آیدی عددی کاربر** را وارد کنید:\n\n"
        "برای لغو، `لغو` را بفرستید.",
        reply_markup=ReplyKeyboardRemove()
    )

@manager_bot.on_message(filters.text & filters.private & filters.regex("^💰 موجودی خودم$"))
async def admin_my_balance(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    balance = get_balance(message.from_user.id)
    await message.reply_text(f"💎 موجودی شما: `{balance:,}` الماس")

@manager_bot.on_message(filters.text & filters.private & filters.regex("^🔍 موجودی با آیدی$"))
async def admin_check_balance_start(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = "admin_check_balance_id"
    await message.reply_text(
        "🆔 **آیدی عددی کاربر** را وارد کنید:\n\n"
        "برای لغو، `لغو` را بفرستید.",
        reply_markup=ReplyKeyboardRemove()
    )

@manager_bot.on_message(filters.text & filters.private & filters.regex("^🛠 پنل ادمین$"))
async def admin_main_panel(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    
    text = (
        "🛠 **پنل ادمین | self MR**\n\n"
        "گزینه مورد نظر را انتخاب کنید:"
    )
    buttons = [
        [KeyboardButton("💎 پنل الماس")],
        [KeyboardButton("📊 وضعیت ربات"), KeyboardButton("📢 پیام همگانی")],
        [KeyboardButton("📥 دانلود دیتابیس"), KeyboardButton("📤 آپلود دیتابیس")],
        [KeyboardButton("🔙 بازگشت به منو")]
    ]
    kb = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await message.reply_text(text, reply_markup=kb)

@manager_bot.on_message(filters.text & filters.private & filters.regex("^🔙 بازگشت به منو$"))
async def admin_back_to_menu(client, message):
    if not message.from_user or message.from_user.id not in GOD_ADMIN_IDS:
        return
    ADMIN_STATES[message.from_user.id] = None
    
    buttons = [
        [KeyboardButton("📱 شماره و شروع", request_contact=True)],
        [KeyboardButton("📊 وضعیت ربات"), KeyboardButton("📢 پیام همگانی")],
        [KeyboardButton("💎 پنل الماس"), KeyboardButton("🛠 پنل ادمین")],
        [KeyboardButton("📥 دانلود دیتابیس"), KeyboardButton("📤 آپلود دیتابیس")]
    ]
    kb = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await message.reply_text("🔙 به منوی اصلی بازگشتید.", reply_markup=kb)

# ضد اسپم لاگین
LOGIN_ATTEMPTS = {}  # user_id -> last_attempt_time

@manager_bot.on_message(filters.contact)
async def contact_handler(client, message):
    user_id = message.from_user.id

    if not await force_subscribe_check(client, message):
        return

    if is_banned(user_id):
        await message.reply_text("🚫 شما توسط ادمین مسدود شده‌اید.")
        return

    balance = get_balance(user_id)
    if balance < SELF_PRICE:
        await message.reply_text(
            f"❌ الماس کافی ندارید!\n💎 الماس شما: {balance:,}\n💎 مورد نیاز: {SELF_PRICE:,}"
        )
        return

    chat_id = message.chat.id
    phone = normalize_phone(message.contact.phone_number)

    await message.reply_text("⏳ در حال اتصال...", reply_markup=ReplyKeyboardRemove())

    user_client = Client(f"login_{chat_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True, no_updates=True)
    await user_client.connect()

    try:
        sent_code = await user_client.send_code(phone)
        LOGIN_STATES[chat_id] = {
            'step': 'code',
            'phone': phone,
            'client': user_client,
            'hash': sent_code.phone_code_hash,
            'digits': '',
            'busy': False,
        }
        ok, _ = await bot_api_send_or_edit(message.chat.id, code_pad_text(""), login_code_keyboard())
        if not ok:
            await message.reply_text(code_pad_text(""), reply_markup=_fallback_markup(login_code_keyboard()))
    except Exception as e:
        try:
            await user_client.disconnect()
        except Exception:
            pass
        await message.reply_text(f"❌ خطا: {e}")


# =============================================
# هندلر پیوی
# =============================================
@manager_bot.on_message(filters.text & filters.private)
async def private_handler(client, message):
    user_id = message.from_user.id
    text = message.text or ""


    # ورود شماره متنی (مدیریت سلف)
    st_login = LOGIN_STATES.get(message.chat.id)
    if st_login and st_login.get("step") == "phone":
        phone = normalize_phone(text.strip())
        if text.strip() in ("لغو", "بازگشت", "/start"):
            LOGIN_STATES.pop(message.chat.id, None)
            await send_main_menu(client, message, user_id)
            return
        if len(phone) < 8:
            await message.reply_text("❌ شماره نامعتبر است. مثال: `+98912...`")
            return
        balance = get_balance(user_id)
        if balance < SELF_PRICE:
            await message.reply_text(
                f"❌ الماس کافی ندارید!\n💎 موجودی: {balance:,}\n💎 نیاز: {SELF_PRICE:,}"
            )
            LOGIN_STATES.pop(message.chat.id, None)
            return
        if is_banned(user_id):
            await message.reply_text("🚫 شما مسدود شده‌اید.")
            LOGIN_STATES.pop(message.chat.id, None)
            return
        await message.reply_text("⏳ در حال ارسال کد...")
        user_client = Client(f"login_{message.chat.id}", api_id=API_ID, api_hash=API_HASH, in_memory=True, no_updates=True)
        try:
            await user_client.connect()
            sent_code = await user_client.send_code(phone)
            LOGIN_STATES[message.chat.id] = {
                "step": "code",
                "phone": phone,
                "client": user_client,
                "hash": sent_code.phone_code_hash,
                "digits": "",
            }
            ok, _ = await bot_api_send_or_edit(message.chat.id, code_pad_text(""), login_code_keyboard())
            if not ok:
                await message.reply_text(code_pad_text(""), reply_markup=_fallback_markup(login_code_keyboard()))
        except Exception as e:
            try:
                await user_client.disconnect()
            except Exception:
                pass
            LOGIN_STATES.pop(message.chat.id, None)
            await message.reply_text(f"❌ خطا: {e}")
        return


    # =============================================
    # پنل ادمین - افزودن الماس (قبل از عضویت اجباری)
    # =============================================
    if user_id in GOD_ADMIN_IDS and text:
        # مرحله ۱: گرفتن آیدی
        if ADMIN_STATES.get(user_id) == "admin_add_diamond_id":
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(text.strip())
                ADMIN_STATES[user_id] = f"admin_add_diamond_amount_{target_id}"
                await message.reply_text(f"💎 مقدار الماس برای کاربر `{target_id}` را وارد کنید:")
            except ValueError:
                await message.reply_text("❌ آیدی عددی نامعتبر است. دوباره وارد کنید یا `لغو` بفرستید.")
            return

        # مرحله ۲: گرفتن مقدار
        if str(ADMIN_STATES.get(user_id, "")).startswith("admin_add_diamond_amount_"):
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(ADMIN_STATES[user_id].split("_")[-1])
                amount = int(text.strip())
                if amount <= 0:
                    await message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
                    return
                init_user_db(target_id)
                add_balance(target_id, amount)
                ADMIN_STATES[user_id] = None
                new_bal = get_balance(target_id)
                await message.reply_text(
                    f"✅ **الماس اضافه شد | self MR**\n\n"
                    f"👤 کاربر: `{target_id}`\n"
                    f"💎 مقدار: `{amount:,}`\n"
                    f"✨ موجودی جدید: `{new_bal:,}`"
                )
                try:
                    await manager_bot.send_message(
                        target_id,
                        f"💎 `{amount:,}` الماس توسط ادمین به حساب شما اضافه شد.\nموجودی جدید: `{new_bal:,}`"
                    )
                except Exception:
                    pass
            except ValueError:
                await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return

        # چک موجودی با آیدی
        if ADMIN_STATES.get(user_id) == "admin_check_balance_id":
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(text.strip())
                init_user_db(target_id)
                bal = get_balance(target_id)
                try:
                    session_info = get_session_by_user_id(target_id)
                    has_self = "✅ فعال" if session_info else "❌ غیرفعال"
                except Exception:
                    has_self = "—"
                ADMIN_STATES[user_id] = None
                await message.reply_text(
                    f"💎 اطلاعات کاربر | self MR\n\n"
                    f"🆔 آیدی: `{target_id}`\n"
                    f"💎 موجودی: `{bal:,}` الماس\n"
                    f"🔐 سلف: {has_self}"
                )
            except ValueError:
                await message.reply_text("❌ آیدی عددی نامعتبر است.")
            return

        # کسر الماس - مرحله ۱: آیدی
        if ADMIN_STATES.get(user_id) == "admin_deduct_diamond_id":
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(text.strip())
                ADMIN_STATES[user_id] = f"admin_deduct_diamond_amount_{target_id}"
                bal = get_balance(target_id)
                await message.reply_text(
                    f"➖ مقدار کسر از کاربر `{target_id}`\n"
                    f"💎 موجودی فعلی: `{bal:,}`\n\n"
                    f"عدد را وارد کنید:"
                )
            except ValueError:
                await message.reply_text("❌ آیدی عددی نامعتبر است. دوباره وارد کنید یا `لغو` بفرستید.")
            return

        # کسر الماس - مرحله ۲: مقدار
        if str(ADMIN_STATES.get(user_id, "")).startswith("admin_deduct_diamond_amount_"):
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(ADMIN_STATES[user_id].split("_")[-1])
                amount = int(text.strip())
                if amount <= 0:
                    await message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
                    return
                init_user_db(target_id)
                actual, new_bal = force_deduct_balance(target_id, amount)
                ADMIN_STATES[user_id] = None
                await message.reply_text(
                    f"✅ **الماس کسر شد | self MR**\n\n"
                    f"👤 کاربر: `{target_id}`\n"
                    f"➖ کسر شده: `{actual:,}`\n"
                    f"✨ موجودی جدید: `{new_bal:,}`"
                )
                try:
                    await manager_bot.send_message(
                        target_id,
                        f"⚠️ `{actual:,}` الماس توسط ادمین از حساب شما کسر شد.\nموجودی جدید: `{new_bal:,}`"
                    )
                except Exception:
                    pass
            except ValueError:
                await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return

        # از دکمه اینلاین قدیمی
        if str(ADMIN_STATES.get(user_id, "")).startswith("add_balance_"):
            try:
                target_id = int(ADMIN_STATES[user_id].split("_")[2])
                amount = int(text.strip())
                if amount <= 0:
                    await message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
                    return
                init_user_db(target_id)
                add_balance(target_id, amount)
                ADMIN_STATES[user_id] = None
                await message.reply_text(
                    f"✅ `{amount:,}` الماس به کاربر `{target_id}` اضافه شد.\n"
                    f"💎 موجودی جدید: `{get_balance(target_id):,}`"
                )
                try:
                    await manager_bot.send_message(
                        target_id,
                        f"💎 `{amount:,}` الماس توسط ادمین به حساب شما اضافه شد.\nموجودی جدید: `{get_balance(target_id):,}`"
                    )
                except Exception:
                    pass
            except ValueError:
                await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return

    if not await force_subscribe_check(client, message):
        return

    # =============================================
    # پنل ادمین - افزودن الماس (نسخه قبلی - دیگر نمی‌رسد اگر بالا handle شده)
    # =============================================
    if user_id in GOD_ADMIN_IDS and text:
        # مرحله ۱: گرفتن آیدی
        if ADMIN_STATES.get(user_id) == "admin_add_diamond_id":
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(text.strip())
                ADMIN_STATES[user_id] = f"admin_add_diamond_amount_{target_id}"
                await message.reply_text(f"💎 مقدار الماس برای کاربر `{target_id}` را وارد کنید:")
            except ValueError:
                await message.reply_text("❌ آیدی عددی نامعتبر است. دوباره وارد کنید یا `لغو` بفرستید.")
            return

        # مرحله ۲: گرفتن مقدار
        if str(ADMIN_STATES.get(user_id, "")).startswith("admin_add_diamond_amount_"):
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(ADMIN_STATES[user_id].split("_")[-1])
                amount = int(text.strip())
                if amount <= 0:
                    await message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
                    return
                add_balance(target_id, amount)
                ADMIN_STATES[user_id] = None
                new_bal = get_balance(target_id)
                await message.reply_text(
                    f"✅ الماس اضافه شد | self MR\n\n"
                    f"👤 کاربر: `{target_id}`\n"
                    f"💎 مقدار: `{amount:,}`\n"
                    f"✨ موجودی جدید: `{new_bal:,}`"
                )
                try:
                    await manager_bot.send_message(
                        target_id,
                        f"💎 `{amount:,}` الماس توسط ادمین به حساب شما اضافه شد.\nموجودی جدید: `{new_bal:,}`"
                    )
                except:
                    pass
            except ValueError:
                await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return

        # چک موجودی با آیدی
        if ADMIN_STATES.get(user_id) == "admin_check_balance_id":
            if text.strip() == "لغو":
                ADMIN_STATES[user_id] = None
                await message.reply_text("❌ لغو شد.")
                return
            try:
                target_id = int(text.strip())
                init_user_db(target_id)
                bal = get_balance(target_id)
                session_info = get_session_by_user_id(target_id)
                has_self = "✅ فعال" if session_info else "❌ غیرفعال"
                ADMIN_STATES[user_id] = None
                await message.reply_text(
                    f"💎 اطلاعات کاربر | self MR\n\n"
                    f"🆔 آیدی: `{target_id}`\n"
                    f"💎 موجودی: `{bal:,}` الماس\n"
                    f"🔐 سلف: {has_self}"
                )
            except ValueError:
                await message.reply_text("❌ آیدی عددی نامعتبر است.")
            return

        # از دکمه اینلاین قدیمی
        if str(ADMIN_STATES.get(user_id, "")).startswith("add_balance_"):
            try:
                target_id = int(ADMIN_STATES[user_id].split("_")[2])
                amount = int(text.strip())
                if amount <= 0:
                    await message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
                    return
                add_balance(target_id, amount)
                ADMIN_STATES[user_id] = None
                await message.reply_text(
                    f"✅ `{amount:,}` الماس به کاربر `{target_id}` اضافه شد.\n"
                    f"💎 موجودی جدید: `{get_balance(target_id):,}`"
                )
                try:
                    await manager_bot.send_message(
                        target_id,
                        f"💎 `{amount:,}` الماس توسط ادمین به حساب شما اضافه شد.\nموجودی جدید: `{get_balance(target_id):,}`"
                    )
                except:
                    pass
            except ValueError:
                await message.reply_text("❌ لطفاً فقط عدد وارد کنید.")
            return

    # ===== لغو آپلود دیتابیس =====
    if text.strip() == "لغو" and ADMIN_STATES.get(user_id) == "waiting_for_db_upload":
        ADMIN_STATES[user_id] = None
        await message.reply_text("❌ عملیات آپلود لغو شد.")
        return

    # ===== آیدی =====
    if text.strip() == "آیدی":
        if not message.reply_to_message:
            await message.reply_text("❌ روی پیام کاربر ریپلی کن و `آیدی` بفرست.")
            return
        target = message.reply_to_message.from_user
        if not target:
            await message.reply_text("❌ کاربر پیدا نشد!")
            return
        try:
            user = await client.get_users(target.id)
        except Exception as e:
            await message.reply_text(f"❌ خطا: {str(e)}")
            return
        session_info = get_session_by_user_id(user.id)
        has_session = session_info is not None
        balance = get_balance(user.id)
        info = f"""
👤 **اطلاعات کاربر | self MR**

🆔 آیدی عددی: `{user.id}`
👤 نام: {user.first_name or 'ندارد'}
📱 یوزرنیم: @{user.username if user.username else 'ندارد'}
💎 موجودی: `{balance:,}` الماس
🔐 سلف: {'فعال ✅' if has_session else 'غیرفعال ❌'}
"""
        buttons = [[InlineKeyboardButton("🔙 بستن", callback_data="close_info")]]
        if user_id in GOD_ADMIN_IDS:
            buttons.insert(0, [
                InlineKeyboardButton("💎 +الماس", callback_data=f"add_balance_{user.id}"),
                InlineKeyboardButton("🚫 بن", callback_data=f"ban_user_{user.id}")
            ])
        await message.reply_text(info, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ===== دانلود / صوت / راهنما =====
    if text.startswith("دانلود "):
        url = text.replace("دانلود ", "").strip()
        if not url.startswith("http"):
            await message.reply_text("❌ لینک نامعتبر است!")
            return
        status_msg = await message.reply_text("⏳ در حال دانلود ویدیو...")
        filename, error = await download_media(url, "video")
        if error:
            await status_msg.edit_text(error)
            return
        try:
            await message.reply_video(filename, caption="✅ ویدیو دانلود شد!")
            await status_msg.delete()
            os.remove(filename)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا: {str(e)}")
        return

    if text.startswith("صوت "):
        url = text.replace("صوت ", "").strip()
        if not url.startswith("http"):
            await message.reply_text("❌ لینک نامعتبر است!")
            return
        status_msg = await message.reply_text("⏳ در حال استخراج صوت...")
        filename, error = await download_media(url, "audio")
        if error:
            await status_msg.edit_text(error)
            return
        try:
            await message.reply_audio(filename, caption="🎵 صوت دانلود شد!")
            await status_msg.delete()
            os.remove(filename)
        except Exception as e:
            await status_msg.edit_text(f"❌ خطا: {str(e)}")
        return

    if text in ("🔙 انصراف", "انصراف"):
        LOGIN_STATES.pop(message.chat.id, None)
        try:
            await message.reply_text("لغو شد.", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass
        await send_main_menu(client, message, user_id)
        return

    if text == "راهنما":
        await message.reply_text(HELP_TEXT)
        return

    # ===== لاگین (کد و رمز) =====
    chat_id = message.chat.id
    state = LOGIN_STATES.get(chat_id)

    if not state:
        return

    user_c = state.get('client')
    if not user_c:
        return

    if state['step'] == 'code':
        # اگر از روی کیبورد عددی در حال پردازش است، پیام متنی را نادیده بگیر
        if state.get('busy'):
            return
        code = re.sub(r"\D+", "", message.text or "")
        if len(code) < 5:
            await message.reply_text("❌ کد ناقص است. حداقل ۵ رقم بفرست یا از دکمه‌ها استفاده کن.")
            return
        phone = normalize_phone(state.get('phone') or "")
        state['busy'] = True
        LOGIN_STATES[chat_id] = state
        try:
            await user_c.sign_in(phone, state['hash'], code)
            await finalize(message, user_c, phone)
        except SessionPasswordNeeded:
            state['step'] = 'password'
            state['phone'] = phone
            state['busy'] = False
            LOGIN_STATES[chat_id] = state
            await message.reply_text("🔐 رمز دو مرحله‌ای را وارد کنید:")
        except Exception as e:
            err = str(e)
            state['busy'] = False
            LOGIN_STATES[chat_id] = state
            # اگر کد قبلاً با موفقیت مصرف شده، پیام الکی نده
            if "PHONE_CODE_INVALID" in err or "PHONE_CODE_EXPIRED" in err:
                # فقط اگر هنوز سشن لاگین فعال است هشدار بده
                if LOGIN_STATES.get(chat_id) and LOGIN_STATES[chat_id].get('step') == 'code':
                    await message.reply_text(
                        "❌ کد اشتباه یا منقضی است.\n"
                        "از دکمه‌های صفحه کد یا «ارسال مجدد کد» استفاده کن."
                    )
            else:
                await message.reply_text(f"❌ خطا: {err[:150]}")

    elif state['step'] == 'password':
        try:
            await user_c.check_password(message.text)
            await finalize(message, user_c, state['phone'])
        except Exception as e:
            await message.reply_text(f"❌ خطا: {e}")


# =============================================
# هندلر گروه
# =============================================

# =============================================
# 🎭 سیستم بازی مافیا
# =============================================

def _mafia_mention(uid, name=None, username=None):
    if username:
        return f"@{username}"
    n = (name or str(uid)).replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={uid}">{n}</a>'


def _mafia_role_counts(n: int):
    if n <= 6:
        m = 1
    elif n <= 9:
        m = 2
    else:
        m = 3
    return m, 1, 1  # mafia, doctor, detective


def _mafia_alive(game):
    return [uid for uid, p in game["players"].items() if p.get("alive")]


def _mafia_alive_by_role(game, role):
    return [uid for uid, p in game["players"].items() if p.get("alive") and p.get("role") == role]


def _mafia_assign_roles(game):
    ids = list(game["players"].keys())
    random.shuffle(ids)
    nm, nd, nq = _mafia_role_counts(len(ids))
    roles = (["mafia"] * nm) + (["doctor"] * nd) + (["detective"] * nq)
    roles += ["citizen"] * (len(ids) - len(roles))
    random.shuffle(roles)
    for uid, role in zip(ids, roles):
        game["players"][uid]["role"] = role


async def _mafia_pm(uid, text, parse_mode=ParseMode.HTML):
    try:
        await manager_bot.send_message(uid, text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logging.warning("mafia pm %s: %s", uid, e)
        return False


async def _mafia_group(chat_id, text, parse_mode=ParseMode.HTML):
    try:
        await manager_bot.send_message(chat_id, text, parse_mode=parse_mode)
    except Exception as e:
        logging.warning("mafia group %s: %s", chat_id, e)


def _mafia_target_list(game, exclude_uids=None):
    exclude_uids = set(exclude_uids or [])
    lines = []
    idx_map = {}
    i = 1
    for uid in _mafia_alive(game):
        if uid in exclude_uids:
            continue
        p = game["players"][uid]
        lines.append(f"{i}. {_mafia_mention(uid, p.get('name'), p.get('username'))}")
        idx_map[i] = uid
        i += 1
    return "\n".join(lines), idx_map


async def _mafia_refund_all(game):
    bet = int(game.get("bet") or 0)
    if bet <= 0:
        return
    for uid in list(game.get("players") or {}):
        try:
            add_balance(uid, bet)
        except Exception:
            pass


async def _mafia_end(game, mafia_win: bool):
    chat_id = game["chat_id"]
    game["phase"] = "ended"
    mafias = [uid for uid, p in game["players"].items() if p.get("role") == "mafia"]
    others = [uid for uid, p in game["players"].items() if p.get("role") != "mafia"]
    winners = mafias if mafia_win else others
    losers = others if mafia_win else mafias

    def names(ids):
        parts = []
        for uid in ids:
            p = game["players"].get(uid) or {}
            parts.append(_mafia_mention(uid, p.get("name"), p.get("username")))
        return ", ".join(parts) if parts else "—"

    bet = int(game.get("bet") or 0)
    if bet > 0:
        pot = bet * len(game["players"])
        share = max(1, pot // max(1, len(winners))) if winners else 0
        for uid in winners:
            add_balance(uid, share)
        prize_txt = f"🏆 جایزه هر برنده: <code>{share:,}</code> الماس (از مجموع {pot:,})"
    else:
        for uid in winners:
            add_balance(uid, MAFIA_WIN_PRIZE)
        for uid in losers:
            add_balance(uid, MAFIA_LOSE_PRIZE)
        prize_txt = (
            f"🥇 برنده‌ها: <code>{MAFIA_WIN_PRIZE}</code> الماس\n"
            f"🥈 بازنده‌ها: <code>{MAFIA_LOSE_PRIZE}</code> الماس"
        )

    title = "🎉 مافیا برنده شد!" if mafia_win else "🎉 شهروندها برنده شدن!"
    await _mafia_group(
        chat_id,
        f"{title}\n\n"
        f"🕵️ مافیاها: {names(mafias)}\n"
        f"👥 شهروندها: {names(others)}\n\n"
        f"{prize_txt}",
    )
    for uid in list(game["players"].keys()):
        MAFIA_USER_CHAT.pop(uid, None)
    MAFIA_GAMES.pop(chat_id, None)


async def _mafia_check_win(game):
    alive_m = len(_mafia_alive_by_role(game, "mafia"))
    alive_all = len(_mafia_alive(game))
    alive_town = alive_all - alive_m
    if alive_m <= 0:
        await _mafia_end(game, mafia_win=False)
        return True
    if alive_m >= alive_town:
        await _mafia_end(game, mafia_win=True)
        return True
    return False



def _mafia_lobby_text(game) -> str:
    n = len(game.get("players") or {})
    need = int(game.get("need_players") or MAFIA_MIN_PLAYERS)
    bet = int(game.get("bet") or 0)
    lobby_sec = int(game.get("lobby_sec") or MAFIA_JOIN_SEC)
    bet_line = f"\n💰 شرط هر نفر: <code>{bet:,}</code> الماس" if bet else "\n🏆 جایزه: برنده ۵۰ / بازنده ۱۰ الماس"
    return (
        f"🎭 <b>بازی مافیا شروع شد!</b>\n"
        f"👥 بازیکن‌ها: <b>{n}</b> / <b>{need}</b>{bet_line}\n"
        f"⏳ زمان لابی: <b>{lobby_sec}</b> ثانیه\n\n"
        f"با دکمه زیر عضو شوید یا لغو کنید."
    )


def _mafia_lobby_keyboard(chat_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ عضو شدن", callback_data=f"mafia_join_{chat_id}"),
            InlineKeyboardButton("🛑 لغو", callback_data=f"mafia_cancel_{chat_id}"),
        ]
    ])


async def _mafia_try_join(user, chat_id: int) -> str:
    """عضویت در لابی — پیام نتیجه برمی‌گرداند"""
    uid = user.id
    game = MAFIA_GAMES.get(chat_id)
    if not game or game.get("phase") != "lobby":
        return "❌ لابی فعالی نیست."
    if uid in game["players"]:
        return "قبلاً عضو شدی."
    if uid in MAFIA_USER_CHAT:
        return "❌ در بازی دیگری هستی."
    need = int(game.get("need_players") or MAFIA_MIN_PLAYERS)
    if len(game["players"]) >= need:
        return f"❌ ظرفیت کامل است ({need} نفر)."
    if len(game["players"]) >= MAFIA_MAX_PLAYERS:
        return f"❌ ظرفیت کامل است (حداکثر {MAFIA_MAX_PLAYERS})."
    bet = int(game.get("bet") or 0)
    if bet > 0:
        if get_balance(uid) < bet:
            return f"❌ برای عضویت به {bet:,} الماس نیاز دارید."
        if not deduct_balance(uid, bet):
            return "❌ کسر الماس ناموفق."
    game["players"][uid] = {
        "name": user.first_name or str(uid),
        "username": user.username or "",
        "role": None,
        "alive": True,
    }
    MAFIA_USER_CHAT[uid] = chat_id
    return f"✅ عضو شدی! ({len(game['players'])}/{need})"


async def _mafia_try_cancel(user_id: int, chat_id: int) -> str:
    game = MAFIA_GAMES.get(chat_id)
    if not game:
        return "❌ بازی‌ای نیست."
    if game.get("phase") != "lobby":
        return "❌ فقط در لابی می‌توان لغو کرد."
    if user_id != game.get("host_id") and user_id not in GOD_ADMIN_IDS:
        return "❌ فقط میزبان می‌تواند لغو کند."
    await _mafia_refund_all(game)
    for uid in list(game["players"].keys()):
        MAFIA_USER_CHAT.pop(uid, None)
    MAFIA_GAMES.pop(chat_id, None)
    return "🛑 بازی مافیا لغو شد و الماس‌ها برگردانده شد."


async def mafia_game_loop(chat_id: int):
    """حلقه اصلی بازی بعد از لابی"""
    try:
        game0 = MAFIA_GAMES.get(chat_id) or {}
        lobby_sec = int(game0.get("lobby_sec") or MAFIA_JOIN_SEC)
        need = int(game0.get("need_players") or MAFIA_MIN_PLAYERS)
        # صبر لابی — اگر زودتر ظرفیت پر شد، حلقه زودتر ادامه می‌دهد
        waited = 0
        while waited < lobby_sec:
            await asyncio.sleep(1)
            waited += 1
            g = MAFIA_GAMES.get(chat_id)
            if not g or g.get("phase") != "lobby":
                return
            if len(g["players"]) >= int(g.get("need_players") or need):
                break
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("phase") != "lobby":
            return
        n = len(game["players"])
        need = int(game.get("need_players") or MAFIA_MIN_PLAYERS)
        if n < need:
            await _mafia_refund_all(game)
            await _mafia_group(chat_id, f"❌ بازی لغو شد — حداقل {need} نفر لازم بود (فعلی: {n}).")
            for uid in list(game["players"].keys()):
                MAFIA_USER_CHAT.pop(uid, None)
            MAFIA_GAMES.pop(chat_id, None)
            return

        game["phase"] = "starting"
        _mafia_assign_roles(game)
        await _mafia_group(chat_id, f"✅ بازی با <b>{n}</b> نفر شروع شد!\nنقش‌ها به پیوی ارسال می‌شود...")

        # پخش نقش
        for uid, p in game["players"].items():
            role = p["role"]
            if role == "mafia":
                teammates = [
                    _mafia_mention(t, game["players"][t].get("name"), game["players"][t].get("username"))
                    for t in game["players"]
                    if game["players"][t]["role"] == "mafia" and t != uid
                ]
                tm = ", ".join(teammates) if teammates else "تنها هستی"
                await _mafia_pm(
                    uid,
                    f"🕵️ <b>تو مافیا هستی!</b>\nهر شب با هم‌تیمی‌هات یه نفر رو می‌کشید.\nهم‌تیمی: {tm}",
                )
            elif role == "doctor":
                await _mafia_pm(uid, "👨‍⚕️ <b>تو دکتری!</b>\nهر شب می‌تونی یه نفر رو نجات بدی.")
            elif role == "detective":
                await _mafia_pm(uid, "🕵️ <b>تو کارآگاهی!</b>\nهر شب می‌تونی استعلام یه نفر رو بگیری.")
            else:
                await _mafia_pm(uid, "👤 <b>تو شهروند ساده‌ای!</b>\nروزها بحث کن و رای بده.")

        await asyncio.sleep(3)

        while chat_id in MAFIA_GAMES and MAFIA_GAMES[chat_id].get("phase") not in ("ended",):
            game = MAFIA_GAMES[chat_id]
            # ----- شب -----
            game["phase"] = "night"
            game["night"] = {"kill_votes": {}, "save": None, "investigate": None, "maps": {}}
            await _mafia_group(
                chat_id,
                "🌙 <b>شب شد!</b>\nهمه بخوابید...\n\n😴 شهروندها چشماشون رو بستن\n🕵️ مافیا بیدار شد",
            )

            # مافیا
            mafias = _mafia_alive_by_role(game, "mafia")
            tlist, idx_map = _mafia_target_list(game, exclude_uids=[])
            game["night"]["maps"]["mafia"] = idx_map
            for uid in mafias:
                await _mafia_pm(
                    uid,
                    f"🕵️ <b>کی رو می‌کشی؟</b>\n{tlist}\n\nعدد را بفرست (مثلاً <code>1</code>)\n⏳ {MAFIA_NIGHT_SEC} ثانیه",
                )
            await asyncio.sleep(MAFIA_NIGHT_SEC)
            game = MAFIA_GAMES.get(chat_id)
            if not game:
                return

            # دکتر
            doctors = _mafia_alive_by_role(game, "doctor")
            tlist, idx_map = _mafia_target_list(game)
            game["night"]["maps"]["doctor"] = idx_map
            for uid in doctors:
                await _mafia_pm(
                    uid,
                    f"👨‍⚕️ <b>کی رو نجات می‌دی؟</b>\n{tlist}\n\nعدد را بفرست\n⏳ {MAFIA_NIGHT_SEC} ثانیه",
                )
            game["phase"] = "night_doctor"
            await asyncio.sleep(MAFIA_NIGHT_SEC)
            game = MAFIA_GAMES.get(chat_id)
            if not game:
                return

            # کارآگاه
            dets = _mafia_alive_by_role(game, "detective")
            tlist, idx_map = _mafia_target_list(game)
            game["night"]["maps"]["detective"] = idx_map
            for uid in dets:
                await _mafia_pm(
                    uid,
                    f"🕵️ <b>استعلام کی؟</b>\n{tlist}\n\nعدد را بفرست\n⏳ {MAFIA_NIGHT_SEC} ثانیه",
                )
            game["phase"] = "night_detective"
            await asyncio.sleep(MAFIA_NIGHT_SEC)
            game = MAFIA_GAMES.get(chat_id)
            if not game:
                return

            # نتیجه استعلام
            inv = game["night"].get("investigate")
            for uid in dets:
                if inv and inv in game["players"]:
                    is_m = game["players"][inv]["role"] == "mafia"
                    p = game["players"][inv]
                    men = _mafia_mention(inv, p.get("name"), p.get("username"))
                    if is_m:
                        await _mafia_pm(uid, f"❌ {men} مافیا هست")
                    else:
                        await _mafia_pm(uid, f"✅ {men} مافیا نیست")
                else:
                    await _mafia_pm(uid, "⏱️ استعلام ثبت نشد.")

            # حل قتل
            votes = game["night"].get("kill_votes") or {}
            # majority
            kill_target = None
            if votes:
                from collections import Counter
                cnt = Counter(votes.values())
                kill_target = cnt.most_common(1)[0][0]
            save = game["night"].get("save")
            killed = None
            if kill_target and kill_target != save:
                if kill_target in game["players"] and game["players"][kill_target].get("alive"):
                    game["players"][kill_target]["alive"] = False
                    killed = kill_target

            await asyncio.sleep(2)
            # day announce
            if killed:
                p = game["players"][killed]
                men = _mafia_mention(killed, p.get("name"), p.get("username"))
                await _mafia_group(chat_id, f"☀️ <b>روز شد!</b>\nدیشب {men} کشته شد 💀")
            elif kill_target and kill_target == save:
                p = game["players"][kill_target]
                men = _mafia_mention(kill_target, p.get("name"), p.get("username"))
                await _mafia_group(
                    chat_id,
                    f"☀️ <b>روز شد!</b>\nدیشب {men} هدف مافیا بود\nولی دکتر نجاتش داد 🏥",
                )
            else:
                await _mafia_group(chat_id, "☀️ <b>روز شد!</b>\nدیشب کسی کشته نشد.")

            if await _mafia_check_win(game):
                return

            # بحث
            game["phase"] = "day_discuss"
            game["day_votes"] = {}
            await _mafia_group(
                chat_id,
                f"💬 <b>{MAFIA_DISCUSS_SEC // 60} دقیقه فرصت بحث</b>\nهمه حرف بزنید و مافیا رو پیدا کنید",
            )
            await asyncio.sleep(MAFIA_DISCUSS_SEC)
            game = MAFIA_GAMES.get(chat_id)
            if not game:
                return

            # رای‌گیری
            game["phase"] = "day_vote"
            game["day_votes"] = {}
            alive_txt, _ = _mafia_target_list(game)
            await _mafia_group(
                chat_id,
                f"🗳️ <b>رای‌گیری شروع شد!</b>\nبرای رای دادن: <code>.رای @user</code> یا <code>.رای 1</code>\n\n"
                f"بازیکنان زنده:\n{alive_txt}\n\n⏳ {MAFIA_VOTE_SEC} ثانیه فرصت",
            )
            # map numbers for vote
            _, vote_map = _mafia_target_list(game)
            game["vote_map"] = vote_map
            await asyncio.sleep(MAFIA_VOTE_SEC)
            game = MAFIA_GAMES.get(chat_id)
            if not game:
                return

            votes = game.get("day_votes") or {}
            expelled = None
            result_lines = []
            if votes:
                from collections import Counter
                cnt = Counter(votes.values())
                for t, n in cnt.most_common():
                    p = game["players"].get(t) or {}
                    result_lines.append(f"{_mafia_mention(t, p.get('name'), p.get('username'))}: {n} رای")
                expelled = cnt.most_common(1)[0][0]
            res = "🗳️ <b>نتایج رای‌گیری:</b>\n" + ("\n".join(result_lines) if result_lines else "هیچ رایی ثبت نشد.")
            if expelled and expelled in game["players"] and game["players"][expelled].get("alive"):
                game["players"][expelled]["alive"] = False
                p = game["players"][expelled]
                role = p.get("role")
                role_fa = {"mafia": "مافیا 🕵️", "doctor": "دکتر 👨‍⚕️", "detective": "کارآگاه 🕵️", "citizen": "شهروند ساده 👤"}.get(role, role)
                men = _mafia_mention(expelled, p.get("name"), p.get("username"))
                res += f"\n\n❌ {men} اخراج شد\n{men} {role_fa} بود"
            else:
                res += "\n\nاین دور کسی اخراج نشد."
            await _mafia_group(chat_id, res)
            await asyncio.sleep(10)

            if await _mafia_check_win(game):
                return
            game["round"] = game.get("round", 1) + 1

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error("mafia_game_loop: %s", e)
        try:
            await _mafia_group(chat_id, f"❌ خطا در بازی مافیا — بازی متوقف شد.")
        except Exception:
            pass
        game = MAFIA_GAMES.pop(chat_id, None)
        if game:
            await _mafia_refund_all(game)
            for uid in list(game.get("players") or {}):
                MAFIA_USER_CHAT.pop(uid, None)


@manager_bot.on_message(filters.group)
async def group_handler(client, message):
    text = message.text

    if not text:
        return

    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return


    # ====== مافیا ======
    mafia_text = text.strip()
    if mafia_text.startswith(".مافیا") or mafia_text == "مافیا" or mafia_text.startswith("مافیا "):
        chat_id = message.chat.id
        if chat_id in MAFIA_GAMES and MAFIA_GAMES[chat_id].get("phase") not in ("ended",):
            await message.reply_text("❌ یک بازی مافیا در این گروه در جریانه.")
            return
        if user_id in MAFIA_USER_CHAT:
            await message.reply_text("❌ شما الان در یک بازی مافیا هستید.")
            return
        bet = 0
        need_players = MAFIA_MIN_PLAYERS
        lobby_sec = MAFIA_JOIN_SEC
        parts = mafia_text.replace(".مافیا", "مافیا").split()
        # .مافیا [الماس] [تعداد نفر]
        if len(parts) >= 2 and parts[1].isdigit():
            bet = int(parts[1])
            if bet < 0:
                bet = 0
            if bet > 0 and bet < 10:
                await message.reply_text("❌ حداقل شرط مافیا ۱۰ الماس است.")
                return
        if len(parts) >= 3 and parts[2].isdigit():
            need_players = int(parts[2])
            if need_players < MAFIA_MIN_PLAYERS:
                need_players = MAFIA_MIN_PLAYERS
            if need_players > MAFIA_MAX_PLAYERS:
                need_players = MAFIA_MAX_PLAYERS
        # زمان لابی متناسب با ظرفیت (حداقل ۳۰، به ازای هر نفر اضافه +۵ تا ۹۰)
        lobby_sec = MAFIA_JOIN_SEC
        if bet > 0:
            if get_balance(user_id) < bet:
                await message.reply_text("❌ الماس کافی برای شروع بازی ندارید.")
                return
            if not deduct_balance(user_id, bet):
                await message.reply_text("❌ کسر الماس ناموفق بود.")
                return
        u = message.from_user
        game = {
            "chat_id": chat_id,
            "host_id": user_id,
            "bet": bet,
            "need_players": need_players,
            "lobby_sec": lobby_sec,
            "phase": "lobby",
            "players": {
                user_id: {
                    "name": u.first_name or str(user_id),
                    "username": u.username or "",
                    "role": None,
                    "alive": True,
                }
            },
            "round": 1,
            "night": {},
            "day_votes": {},
            "vote_map": {},
            "lobby_msg_id": None,
        }
        MAFIA_GAMES[chat_id] = game
        MAFIA_USER_CHAT[user_id] = chat_id
        sent = await message.reply_text(
            _mafia_lobby_text(game),
            reply_markup=_mafia_lobby_keyboard(chat_id),
            parse_mode=ParseMode.HTML,
        )
        try:
            game["lobby_msg_id"] = sent.id
        except Exception:
            pass
        asyncio.create_task(mafia_game_loop(chat_id))
        return

    if mafia_text in (".عضو", "عضو"):
        chat_id = message.chat.id
        msg = await _mafia_try_join(message.from_user, chat_id)
        game = MAFIA_GAMES.get(chat_id)
        if game and game.get("phase") == "lobby" and game.get("lobby_msg_id"):
            try:
                await manager_bot.edit_message_text(
                    chat_id,
                    game["lobby_msg_id"],
                    _mafia_lobby_text(game),
                    reply_markup=_mafia_lobby_keyboard(chat_id),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    if mafia_text in (".لغو", "لغو"):
        chat_id = message.chat.id
        msg = await _mafia_try_cancel(user_id, chat_id)
        await message.reply_text(msg)
        return

    if mafia_text.startswith(".رای") or mafia_text.startswith("رای "):
        chat_id = message.chat.id
        game = MAFIA_GAMES.get(chat_id)
        if not game or game.get("phase") != "day_vote":
            return
        if user_id not in game["players"] or not game["players"][user_id].get("alive"):
            await message.reply_text("❌ فقط بازیکنان زنده می‌توانند رای بدهند.")
            return
        target = None
        # .رای @user or .رای 1
        rest = mafia_text.split(None, 1)
        arg = rest[1].strip() if len(rest) > 1 else ""
        if arg.isdigit():
            target = (game.get("vote_map") or {}).get(int(arg))
        elif arg.startswith("@"):
            uname = arg.lstrip("@").lower()
            for uid, p in game["players"].items():
                if (p.get("username") or "").lower() == uname and p.get("alive"):
                    target = uid
                    break
        elif message.entities:
            for ent in message.entities:
                if ent.type.name in ("MENTION", "TEXT_MENTION") or str(ent.type) in ("MessageEntityType.MENTION", "MessageEntityType.TEXT_MENTION"):
                    if getattr(ent, "user", None):
                        target = ent.user.id
                        break
                    # @username mention
                    try:
                        uname = text[ent.offset + 1: ent.offset + ent.length].lstrip("@")
                        for uid, p in game["players"].items():
                            if (p.get("username") or "").lower() == uname.lower() and p.get("alive"):
                                target = uid
                                break
                    except Exception:
                        pass
        if not target or target not in game["players"] or not game["players"][target].get("alive"):
            await message.reply_text("❌ هدف نامعتبر. مثال: <code>.رای 1</code> یا <code>.رای @user</code>", parse_mode=ParseMode.HTML)
            return
        if target == user_id:
            await message.reply_text("❌ به خودت نمی‌توانی رای بدهی.")
            return
        game.setdefault("day_votes", {})[user_id] = target
        p = game["players"][target]
        await message.reply_text(f"✅ رای شما ثبت شد → {_mafia_mention(target, p.get('name'), p.get('username'))}", parse_mode=ParseMode.HTML)
        return


    # ====== موجودی ======
    if text.strip() == "موجودی":
        target_id = user_id
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
        
        init_user_db(target_id)
        balance = get_balance(target_id)
        session_info = get_session_by_user_id(target_id)
        has_self = "✅ فعال" if session_info else "❌ غیرفعال"
        
        is_self = (target_id == user_id)
        title = "موجودی شما" if is_self else f"موجودی کاربر"
        text_msg = f"💎 <b>{title}</b>"
        
        bal_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💎 الماس {balance:,}", callback_data="noop")]
        ])
        
        try:
            await message.reply_text(text_msg, reply_markup=bal_buttons, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(f"💎 موجودی: {balance:,} الماس")
        return

    # ====== انتقال الماس ======
    transfer_match = re.match(r'انتقال\s+(?:الماس\s+)?(\d+)$', text.strip(), re.IGNORECASE)
    if transfer_match:
        amount = int(transfer_match.group(1))
        
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.reply_text("❌ لطفاً روی پیام کاربر مورد نظر **ریپلای** کنید و بعد دستور را بفرستید.")
            return
        
        receiver_id = message.reply_to_message.from_user.id
        
        if user_id == receiver_id:
            await message.reply_text("❌ نمی‌توانید به خودتان الماس انتقال دهید.")
            return
        
        if amount < 10:
            await message.reply_text("❌ حداقل مبلغ انتقال ۱۰ الماس است.")
            return
        
        tax = max(1, int(amount * TRANSFER_TAX_PERCENT / 100))
        total_deduct = amount + tax
        
        sender_balance = get_balance(user_id)
        if sender_balance < total_deduct:
            await message.reply_text(
                f"❌ موجودی کافی نیست.\n\n"
                f"💎 موجودی شما: `{sender_balance:,}`\n"
                f"💎 مبلغ انتقال: `{amount:,}`\n"
                f"🧾 مالیات ({TRANSFER_TAX_PERCENT}%): `{tax:,}`\n"
                f"📉 مجموع کسر: `{total_deduct:,}`"
            )
            return
        
        deduct_balance(user_id, total_deduct)
        add_balance(receiver_id, amount)
        
        new_sender = get_balance(user_id)
        new_receiver = get_balance(receiver_id)
        
        await message.reply_text(
            f"✅ **انتقال الماس انجام شد | self MR**\n\n"
            f"👤 از: `{user_id}`\n"
            f"👥 به: `{receiver_id}`\n"
            f"💎 مبلغ خالص: `{amount:,}`\n"
            f"🧾 مالیات: `{tax:,}`\n"
            f"📉 کسر از فرستنده: `{total_deduct:,}`\n\n"
            f"✨ موجودی جدید فرستنده: `{new_sender:,}`\n"
            f"✨ موجودی جدید گیرنده: `{new_receiver:,}`"
        )
        return

    # ====== کسر الماس توسط ادمین (ریپلای + کسر ۱۰۰۰) ======
    deduct_match = re.match(r'کسر\s+(\d+)$', text.strip())
    if deduct_match:
        if user_id not in GOD_ADMIN_IDS:
            await message.reply_text("❌ فقط ادمین می‌تواند الماس کسر کند.")
            return
        amount = int(deduct_match.group(1))
        if amount <= 0:
            await message.reply_text("❌ مبلغ نامعتبر است.")
            return
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.reply_text("❌ روی پیام کاربر ریپلای کنید و بعد بفرستید:\n`کسر 1000`")
            return
        target_id = message.reply_to_message.from_user.id
        before = get_balance(target_id)
        actual, new_bal = force_deduct_balance(target_id, amount)
        await message.reply_text(
            f"✅ **کسر الماس | self MR**\n\n"
            f"👤 کاربر: `{target_id}`\n"
            f"📉 درخواست کسر: `{amount:,}`\n"
            f"📉 کسر شده: `{actual:,}`\n"
            f"💎 موجودی قبل: `{before:,}`\n"
            f"✨ موجودی جدید: `{new_bal:,}`"
        )
        return

    # ====== آیدی ======
    if text.strip() == "آیدی":
        if not message.reply_to_message:
            await message.reply_text("❌ روی پیام کاربر ریپلی کن و `آیدی` بفرست.")
            return

        target = message.reply_to_message.from_user
        if not target:
            await message.reply_text("❌ کاربر پیدا نشد!")
            return

        try:
            user = await client.get_users(target.id)
        except Exception as e:
            await message.reply_text(f"❌ خطا: {str(e)}")
            return

        session_info = get_session_by_user_id(user.id)
        has_session = session_info is not None
        balance = get_balance(user.id)

        info = f"""
👤 **اطلاعات کاربر | self MR**

🆔 آیدی عددی: `{user.id}`
👤 نام: {user.first_name or 'ندارد'}
📱 یوزرنیم: @{user.username if user.username else 'ندارد'}
💎 موجودی: `{balance:,}` الماس
🔐 سلف: {'فعال ✅' if has_session else 'غیرفعال ❌'}
        """

        buttons = [[InlineKeyboardButton("🔙 بستن", callback_data="close_info")]]
        if message.from_user.id in GOD_ADMIN_IDS:
            buttons.insert(0, [
                InlineKeyboardButton("💎 +الماس", callback_data=f"add_balance_{user.id}"),
                InlineKeyboardButton("🚫 بن", callback_data=f"ban_user_{user.id}")
            ])

        await message.reply_text(info, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # حذف پیام‌های خود: حذف 20  یا  .حذف 20
    del_m = re.match(r"^\.?حذف\s+(\d+)$", text.strip())
    if del_m:
        try:
            count = max(1, min(100, int(del_m.group(1))))
            msg_ids = [message.id]
            async for m in client.get_chat_history(message.chat.id, limit=count + 5):
                if m.id == message.id:
                    continue
                if m.from_user and getattr(m.from_user, "is_self", False):
                    msg_ids.append(m.id)
                if len(msg_ids) >= count + 1:
                    break
            # خود دستور هم داخل لیست است
            try:
                await client.delete_messages(message.chat.id, msg_ids[: count + 1])
            except Exception as e1:
                # تکی پاک کن
                for mid in msg_ids[: count + 1]:
                    try:
                        await client.delete_messages(message.chat.id, mid)
                    except Exception:
                        pass
        except Exception as e:
            try:
                await message.edit_text(f"❌ خطا در حذف: {e}")
            except Exception:
                pass
        return

    # ====== نبرد الماس ======
    game_match = re.match(r'بازی\s+(\d+)$', text.strip(), re.IGNORECASE)
    if game_match:
        organizer_id = message.from_user.id
        amount = int(game_match.group(1))
        
        if amount < MIN_GAME_AMOUNT:
            await message.reply_text(f'❌ مبلغ نبرد باید حداقل {MIN_GAME_AMOUNT} الماس باشد.')
            return
        
        organizer_balance = get_balance(organizer_id)
        if organizer_balance < amount:
            await message.reply_text(f'❌ موجودی الماس شما (`{organizer_balance:,}`) برای شروع نبرد با مبلغ `{amount:,}` کافی نیست.')
            return
        
        if not deduct_balance(organizer_id, amount):
            await message.reply_text("❌ خطا در کسر الماس.")
            return
        
        first_name = (message.from_user.first_name or "کاربر").replace("<", "").replace(">", "")
        game_text = (
            f"⚔️ <b>نبرد الماس | self MR</b>\n\n"
            f"👤 برگزار کننده: <a href=\"tg://user?id={organizer_id}\">{first_name}</a>\n"
            f"💰 مبلغ نبرد: <code>{amount:,}</code> الماس\n"
            f"🏆 جایزه کل: <code>{amount * 2:,}</code> الماس\n\n"
            f"📌 برای پیوستن روی دکمه زیر کلیک کنید."
        )
        
        buttons = [
            [
                InlineKeyboardButton("⚔️ پیوستن به نبرد", callback_data=f"game_join_{amount}_{organizer_id}"),
                InlineKeyboardButton("❌ لغو", callback_data=f"game_cancel_{amount}_{organizer_id}")
            ]
        ]
        
        try:
            sent_message = await message.reply_text(
                game_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            # اگر html هم مشکل داشت، بدون پارس بفرست و الماس رو برگردون
            logging.error(f"Game message error: {e}")
            add_balance(organizer_id, amount)
            await message.reply_text(f"❌ خطا در ایجاد نبرد: {e}")
            return
        
        game_key = (message.chat.id, sent_message.id)
        active_games[game_key] = {
            'organizer_id': organizer_id,
            'amount': amount,
            'chat_id': message.chat.id,
            'message_id': sent_message.id
        }
        return


    # ====== دوز (تیك تاك تو) ======
    dooz_match = re.match(r'دوز\s+(\d+)$', text.strip())
    if dooz_match:
        organizer_id = message.from_user.id
        amount = int(dooz_match.group(1))
        if amount < MIN_GAME_AMOUNT:
            await message.reply_text(f'❌ حداقل مبلغ دوز {MIN_GAME_AMOUNT} الماس است.')
            return
        if get_balance(organizer_id) < amount:
            await message.reply_text(f'❌ موجودی کافی نیست (`{get_balance(organizer_id):,}`).')
            return
        if not deduct_balance(organizer_id, amount):
            await message.reply_text("❌ خطا در کسر الماس.")
            return
        first_name = (message.from_user.first_name or "کاربر").replace("<", "").replace(">", "")
        game_text = (
            f"⭕❌ <b>دوز | self MR</b>\n\n"
            f"👤 برگزار کننده: <a href=\"tg://user?id={organizer_id}\">{first_name}</a>\n"
            f"💰 مبلغ هر نفر: <code>{amount:,}</code> الماس\n"
            f"🏆 جایزه کل: <code>{amount * 2:,}</code> الماس\n\n"
            f"📌 برای پیوستن روی دکمه زیر کلیک کنید."
        )
        buttons = [[
            {"text": "شرکت در دوز", "callback_data": f"dooz_join_{amount}_{organizer_id}", "style": "success"},
            {"text": "لغو", "callback_data": f"dooz_cancel_{amount}_{organizer_id}", "style": "danger"},
        ]]
        try:
            # دکمه‌های سبز/قرمز واقعی مثل پنل
            join_kb = [
                [
                    {"text": "شرکت در دوز", "callback_data": f"dooz_join_{amount}_{organizer_id}", "style": "success"},
                    {"text": "لغو", "callback_data": f"dooz_cancel_{amount}_{organizer_id}", "style": "danger"},
                ]
            ]
            sent = None
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": message.chat.id,
                    "text": game_text,
                    "parse_mode": "HTML",
                    "reply_to_message_id": message.id,
                    "reply_markup": json.dumps({"inline_keyboard": join_kb}, ensure_ascii=False),
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=payload) as resp:
                        data = await resp.json()
                        if data.get("ok"):
                            mid = data["result"]["message_id"]
                            class _Msg:
                                pass
                            sent = _Msg()
                            sent.id = mid
                            sent.chat = message.chat
                        else:
                            logging.warning("dooz send colored: %s", data)
            except Exception as e:
                logging.warning("dooz send colored err: %s", e)
            if sent is None:
                sent = await message.reply_text(
                    game_text,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("شرکت در دوز", callback_data=f"dooz_join_{amount}_{organizer_id}"),
                            InlineKeyboardButton("لغو", callback_data=f"dooz_cancel_{amount}_{organizer_id}"),
                        ]
                    ]),
                    parse_mode=ParseMode.HTML,
                )
            active_dooz[(message.chat.id, sent.id)] = {
                "organizer_id": organizer_id,
                "organizer_name": f'<a href="tg://user?id={organizer_id}">{first_name}</a>',
                "amount": amount,
                "board": [" "] * 9,
                "turn": None,
                "joiner_id": None,
                "finished": False,
            }
        except Exception as e:
            add_balance(organizer_id, amount)
            await message.reply_text(f"❌ خطا: {e}")
        return

async def finalize(message, user_c, phone):
    s_str = await user_c.export_session_string()
    me = await user_c.get_me()
    await user_c.disconnect()

    user_id = me.id

    # فقط بار اول فعال‌سازی ۵۰ الماس کسر شود (نه بعد از ری‌استارت)
    already = False
    try:
        if get_session_by_user_id(user_id):
            already = True
        elif get_self_start_time(user_id) and int(get_self_start_time(user_id)) > 0:
            already = True
    except Exception:
        already = False
    if not already:
        if not deduct_balance(user_id, SELF_PRICE):
            await message.reply_text("❌ خطا در کسر الماس. موجودی کافی نیست.")
            LOGIN_STATES.pop(message.chat.id, None)
            return
    else:
        logging.info(f"skip SELF_PRICE for re-activate user={user_id}")

    save_session_to_db(phone, s_str, user_id, me.first_name or "", me.username or "")
    data_manager.save_session(phone, s_str, user_id, me.first_name or "", me.username or "")
    
    # زمان شروع فقط اگر قبلاً نبود
    try:
        if not (get_self_start_time(user_id) and int(get_self_start_time(user_id)) > 0):
            set_self_start_time(user_id)
    except Exception:
        set_self_start_time(user_id)
    
    asyncio.create_task(start_bot_instance(s_str, phone, user_id, 'bold'))
    
    LOGIN_STATES.pop(message.chat.id, None)
    
    new_balance = get_balance(user_id)
    if already:
        await message.reply_text(
            f"✅ **self MR دوباره فعال شد!**\n\n"
            f"💎 هزینه فعال‌سازی کسر نشد (قبلاً فعال بودید).\n"
            f"💎 موجودی: `{new_balance:,}` الماس\n\n"
            f"دستور `پنل` را در اکانت خود بزنید."
        )
    else:
        await message.reply_text(
            f"✅ **self MR با موفقیت فعال شد!**\n\n"
            f"💎 {SELF_PRICE:,} الماس از حساب شما کسر شد.\n"
            f"💎 موجودی باقی‌مانده: `{new_balance:,}` الماس\n\n"
            f"⏰ هر ساعت `{HOURLY_COST}` الماس از حساب شما کسر می‌شود.\n"
            f"اگر موجودی تمام شود، سلف به صورت خودکار خاموش خواهد شد.\n\n"
            f"دستور `پنل` را در اکانت خود بزنید."
        )

async def restart_all_selfs():
    """خاموش کردن همه سلف‌های فعال و استارت مجدد از دیتابیس"""
    stopped = 0
    started = 0
    # توقف فعلی‌ها
    for uid in list(ACTIVE_BOTS.keys()):
        try:
            client, tasks = ACTIVE_BOTS.pop(uid)
            for t in tasks:
                try:
                    t.cancel()
                except Exception:
                    pass
            try:
                await client.stop()
            except Exception:
                pass
            stopped += 1
        except Exception as e:
            logging.error(f"restart stop {uid}: {e}")

    await asyncio.sleep(2)

    # استارت از sessions.db + bot_data
    try:
        sessions = get_all_sessions_from_db()
    except Exception as e:
        logging.error(f"restart get sessions: {e}")
        sessions = []

    seen = set()
    for item in sessions:
        try:
            if len(item) >= 3:
                phone, session_string, user_id = item[0], item[1], item[2]
            else:
                continue
            uid = int(user_id)
            if uid in seen:
                continue
            seen.add(uid)
            if not session_string:
                continue
            asyncio.create_task(start_bot_instance(session_string, phone, uid, 'bold'))
            started += 1
            await asyncio.sleep(1.5)
        except Exception as e:
            logging.error(f"restart start: {e}")

    # همچنین از bot_data.json
    try:
        for uid_str, u_data in (data_manager.get_all_users() or {}).items():
            try:
                uid = int(uid_str)
                if uid in seen:
                    continue
                s_str = u_data.get("session_string")
                phone = u_data.get("phone") or str(uid)
                if s_str:
                    seen.add(uid)
                    asyncio.create_task(start_bot_instance(s_str, phone, uid, 'bold'))
                    started += 1
                    await asyncio.sleep(1.5)
            except Exception:
                pass
    except Exception as e:
        logging.error(f"restart from json: {e}")

    msg = f"stopped={stopped}, started={started}"
    logging.info(f"restart_all_selfs: {msg}")
    return msg


async def hourly_diamond_deduction_task():
    """هر ساعت از کاربران فعال سلف، الماس کم می‌کند و در صورت کمبود سلف را خاموش می‌کند"""
    await asyncio.sleep(20)
    while True:
        try:
            # لیست کپی از کلیدها تا هنگام تغییر دیکشنری خطا ندهد
            active_ids = list(ACTIVE_BOTS.keys())
            for user_id in active_ids:
                try:
                    # اگر کاربر بن شده
                    if is_banned(user_id):
                        continue
                    start_ts = get_self_start_time(user_id) or 0
                    if start_ts <= 0:
                        # اگر زمان شروع ثبت نشده، الان ثبت کن و این دور را رد کن
                        set_self_start_time(user_id)
                        continue
                    elapsed = int(time.time()) - int(start_ts)
                    # هر ۳۶۰۰ ثانیه یک‌بار
                    hours = elapsed // 3600
                    if hours < 1:
                        continue
                    # برای جلوگیری از کسر چندباره: start_time را جلو بکش
                    # فقط یک ساعت در هر دور
                    if not deduct_balance(user_id, HOURLY_COST):
                        # موجودی کافی نیست → خاموش کردن کامل سلف
                        try:
                            if user_id in ACTIVE_BOTS:
                                client, tasks = ACTIVE_BOTS.pop(user_id)
                                for t in tasks:
                                    try:
                                        t.cancel()
                                    except Exception:
                                        pass
                                try:
                                    await client.stop()
                                except Exception:
                                    pass
                            # حذف از دیتابیس سشن تا بعد از ری‌استارت دوباره روشن نشود
                            try:
                                delete_session_by_user_id(user_id)
                            except Exception as e:
                                logging.error(f"delete_session low balance {user_id}: {e}")
                            try:
                                u_data = data_manager.get_user_data(user_id)
                                phone = u_data.get("phone") or ""
                                u_data["session_string"] = ""
                                if phone and phone in data_manager.data.get("sessions", {}):
                                    del data_manager.data["sessions"][phone]
                                data_manager.save_data()
                            except Exception as e:
                                logging.error(f"clear session json {user_id}: {e}")
                            set_self_start_time(user_id, 0)
                            try:
                                await manager_bot.send_message(
                                    user_id,
                                    f"⛔ **سلف خاموش شد | self MR**\n\n"
                                    f"الماس کافی برای کسر ساعتی (`{HOURLY_COST}`) نداشتید.\n"
                                    f"💎 موجودی فعلی: `{get_balance(user_id):,}`\n\n"
                                    f"برای فعال‌سازی مجدد حداقل `{SELF_PRICE}` الماس نیاز دارید.\n"
                                    f"دکمه «شماره و شروع» را بزنید."
                                )
                            except Exception:
                                pass
                            logging.info(f"Self fully stopped for {user_id} due to low balance")
                        except Exception as e:
                            logging.error(f"stop self on low balance {user_id}: {e}")
                    else:
                        # یک ساعت جلو
                        set_self_start_time(user_id, int(start_ts) + 3600)
                        logging.info(f"Hourly -{HOURLY_COST} diamond from {user_id}, bal={get_balance(user_id)}")
                except Exception as e:
                    logging.error(f"hourly deduct user {user_id}: {e}")
            await asyncio.sleep(60)  # هر دقیقه چک
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"hourly_diamond_deduction_task: {e}")
            await asyncio.sleep(60)




# =============================================
# 🤖 هلپر اینلاین / پریمیوم (مثل Premiumemoji bots)
# =============================================

async def helper_start_handler(client, message):
    """استارت هلپر — مثل pyiuebot"""
    uname = (HELPER_INLINE_BOT or "helperselfMR01_bot").lstrip("@")
    text = (
        f"👑 به ربات تبدیل ایموجی پریمیوم خوش آمدید\n\n"
        f"تعداد کانال‌های ثبت شده شما: 0\n\n"
        f"‼️ نحوه استفاده:\n"
        f"در هر چتی تایپ کنید:\n"
        f"<code>@{uname}</code> متن [کد] متن\n\n"
        f"مثال:\n"
        f"<code>@{uname} سلام [6298332994260175589] خوبی؟</code>\n\n"
        f"پیام شما تبدیل شده و قابل ارسال خواهد بود\n"
        f"و توجه داشته باشید کد ایموجی را از کانال\n"
        f"https://t.me/CustomEmojiPack بردارید"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐ ایموجی‌های پرکاربرد", url="https://t.me/CustomEmojiPack"),
            InlineKeyboardButton("💎 Rich Text/مقاله", url="https://t.me/CustomEmojiPack"),
        ],
        [
            InlineKeyboardButton("➡️ راهنما", callback_data="helper_help"),
        ],
    ])
    try:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        logging.warning("helper_start: %s", e)


def _helper_code_re():
    import re as _re
    return _re.compile(r"\[(\d{10,})\]")


def _helper_build_html(text: str) -> str:
    import html as _html
    import re as _re
    rx = _re.compile(r"\[(\d{10,})\]")
    parts = []
    last = 0
    for m in rx.finditer(text or ""):
        parts.append(_html.escape(text[last:m.start()]))
        parts.append(f'<tg-emoji emoji-id="{m.group(1)}">⭐</tg-emoji>')
        last = m.end()
    parts.append(_html.escape((text or "")[last:]))
    return "".join(parts)


async def helper_premium_message_handler(client, message):
    """پیوی هلپر: ثبت ایموجی پریمیوم یا راهنمای [کد]"""
    try:
        if not message or not message.from_user:
            return
        text = (message.text or message.caption or "").strip()
        uname = (HELPER_INLINE_BOT or "helperselfMR01_bot").lstrip("@")

        if text in ("/list", "list", "لیست"):
            items = list(MANAGER_PREMIUM_EMOJIS.values())
            if not items:
                await message.reply_text("هنوز ایموجی ثبت نشده.")
                return
            parts = ["📋 <b>لیست ثبت‌شده</b>", ""]
            for i, v in enumerate(items[:40], 1):
                cid = v.get("id")
                fb = v.get("fallback") or "⭐"
                parts.append(f"{i}. {html_tg_emoji(cid, fb)} <code>{cid}</code>")
            await message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)
            return

        if text in ("/test", "test", "تست"):
            items = list(MANAGER_PREMIUM_EMOJIS.values())[:15]
            if not items:
                await message.reply_text("اول یک ایموجی پریمیوم بفرستید.")
                return
            html = " ".join(html_tg_emoji(v.get("id"), v.get("fallback") or "⭐") for v in items)
            await message.reply_text(f"🧪 <b>تست</b>\n\n{html}", parse_mode=ParseMode.HTML)
            return

        if text in ("/help", "help", "راهنما"):
            await message.reply_text(
                f"در هر چت:\n<code>@{uname} سلام [6298332994260175589] خوبی؟</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # ایموجی پریمیوم در پیام → ثبت + نمایش کد
        found = extract_custom_emojis_from_message(message)
        if found:
            lines = ["✅ <b>کد ایموجی | self MR</b>", ""]
            for cid, fb in found[:20]:
                save_manager_premium_emoji(cid, fb)
                lines.append(f"{fb}")
                lines.append(f"<code>{cid}</code>")
                lines.append(f"<code>[{cid}]</code>")
                lines.append("")
            lines.append(f"استفاده:\n<code>@{uname} متن [{found[0][0]}] متن</code>")
            await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            return

        # اگر [کد] در متن بود
        rx = _helper_code_re()
        if rx.search(text or ""):
            n = len(rx.findall(text))
            html_body = _helper_build_html(text)
            await message.reply_text(
                f"پیام با {n} کد آماده است.\n\nپیش‌نمایش:\n{html_body}\n\n"
                f"در چت بنویس:\n<code>@{uname} {html.escape(text)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if text and not text.startswith("/"):
            await message.reply_text(
                f"‼️ نحوه استفاده:\n"
                f"<code>@{uname} سلام [6298332994260175589] خوبی؟</code>\n\n"
                f"یا یک <b>ایموجی پریمیوم</b> بفرست تا کدش را بگیری.",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logging.warning(f"helper_premium_message_handler: {e}")


async def start_helper_bot():
    """هلپر کاملاً جدا از منیجر — خطا/توکن منقضی باعث توقف بات اصلی نمی‌شود"""
    global HELPER_BOT_INSTANCE, HELPER_BOT_TOKEN, HELPER_BOT_ENABLED, HELPER_INLINE_BOT
    HELPER_BOT_INSTANCE = None
    if not HELPER_BOT_ENABLED:
        logging.warning("Helper disabled via HELPER_ENABLED=0")
        return None
    token = (HELPER_BOT_TOKEN or "").strip()
    if not token:
        logging.info("HELPER_BOT_TOKEN empty — helper skipped (manager only)")
        return None
    if token == (BOT_TOKEN or "").strip():
        logging.info("HELPER_BOT_TOKEN same as BOT_TOKEN — helper not started separately")
        return None
    # اعتبارسنجی توکن قبل از Client.start
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
        if not data.get("ok"):
            logging.error(
                "❌ HELPER_BOT_TOKEN invalid/expired: %s — helper disabled, manager continues",
                data.get("description") or data,
            )
            HELPER_BOT_TOKEN = ""
            HELPER_BOT_ENABLED = False
            return None
        uname = (data.get("result") or {}).get("username")
        logging.info("Helper token OK @%s", uname)
    except Exception as e:
        logging.error("❌ Helper token check failed: %s — helper skipped", e)
        HELPER_BOT_TOKEN = ""
        HELPER_BOT_ENABLED = False
        return None
    try:
        from pyrogram.handlers import InlineQueryHandler, MessageHandler
        helper_bot = Client(
            "helper_inline_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
        )
        helper_bot.add_handler(MessageHandler(helper_start_handler, filters.command("start") & filters.private))
        helper_bot.add_handler(MessageHandler(helper_premium_message_handler, filters.private & filters.incoming))
        helper_bot.add_handler(InlineQueryHandler(inline_panel_handler))
        await helper_bot.start()
        try:
            me = await helper_bot.get_me()
            logging.info("✅ Helper bot started @%s id=%s", me.username, me.id)
            if me.username:
                HELPER_INLINE_BOT = me.username.lstrip("@")
                logging.info("HELPER_INLINE_BOT set to @%s", HELPER_INLINE_BOT)
        except Exception:
            logging.info("✅ Helper bot started")
        HELPER_BOT_INSTANCE = helper_bot
        return helper_bot
    except Exception as e:
        logging.error("❌ Helper bot start failed (ignored): %s", e)
        HELPER_BOT_TOKEN = ""
        HELPER_BOT_ENABLED = False
        HELPER_BOT_INSTANCE = None
        return None





@manager_bot.on_message(filters.private & filters.photo & filters.incoming)
async def wait_receipt_photo_handler(client, message):
    """دریافت عکس رسید خرید الماس"""
    try:
        uid = message.from_user.id if message.from_user else 0
        if ADMIN_STATES.get(uid) != "wait_receipt":
            return
        info = PENDING_RECEIPTS.get(uid) or {}
        qty = int(info.get("qty") or shop_cart_qty(uid))
        amount = int(info.get("amount") or qty * DIAMOND_PRICE_TOMAN)
        oid = f"{uid}_{int(time.time())}"
        ADMIN_PENDING_ORDERS[oid] = {"user_id": uid, "qty": qty, "amount": amount}
        ADMIN_STATES.pop(uid, None)
        PENDING_RECEIPTS.pop(uid, None)
        caption = (
            f"🧾 **رسید خرید الماس**\n\n"
            f"👤 کاربر: `{uid}`\n"
            f"💎 تعداد: `{qty:,}`\n"
            f"💵 مبلغ: `{amount:,}` تومان\n"
            f"🆔 سفارش: `{oid}`"
        )
        for admin in GOD_ADMIN_IDS:
            try:
                await client.send_photo(
                    admin,
                    message.photo.file_id,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ تایید و واریز", callback_data=f"order_ok_{oid}"),
                            InlineKeyboardButton("❌ رد", callback_data=f"order_no_{oid}"),
                        ]
                    ]),
                )
            except Exception as e:
                logging.warning("send receipt to admin %s: %s", admin, e)
        await message.reply_text(
            "✅ رسید دریافت شد و برای ادمین ارسال شد.\nپس از تایید، الماس به حسابتان واریز می‌شود."
        )
    except Exception as e:
        logging.warning("wait_receipt_photo_handler: %s", e)


@manager_bot.on_message(filters.private & filters.text & filters.incoming & ~filters.command(["start", "Start"]))
async def shop_manual_qty_handler(client, message):
    """تنظیم دستی تعداد الماس فروشگاه"""
    try:
        uid = message.from_user.id if message.from_user else 0
        if ADMIN_STATES.get(uid) != "shop_manual_qty":
            return
        txt = (message.text or "").strip().replace(",", "").replace("،", "")
        if not txt.isdigit():
            await message.reply_text("❌ فقط عدد بفرستید (مثال: 2000)")
            return
        q = int(txt)
        if q < MIN_BUY_DIAMONDS:
            await message.reply_text(f"❌ حداقل خرید `{MIN_BUY_DIAMONDS:,}` الماس است.")
            return
        SHOP_CART[uid] = q
        ADMIN_STATES.pop(uid, None)
        await message.reply_text(
            shop_text(uid),
            reply_markup=None,
        )
        # نمایش دوباره با دکمه‌های رنگی سخت است بدون callback؛ کاربر دوباره خرید را باز کند
        await message.reply_text("✅ تعداد ذخیره شد. از منو دوباره **خرید الماس** را باز کنید.")
    except Exception as e:
        logging.warning("shop_manual_qty_handler: %s", e)




@manager_bot.on_message(filters.private & filters.text & filters.incoming, group=-5)
async def mafia_night_private_handler(client, message):
    """انتخاب شب مافیا/دکتر/کارآگاه با عدد در پیوی"""
    try:
        uid = message.from_user.id if message.from_user else 0
        if not uid:
            return
        chat_id = MAFIA_USER_CHAT.get(uid)
        game = MAFIA_GAMES.get(chat_id) if chat_id else None
        if not game:
            for cid, g in list(MAFIA_GAMES.items()):
                if uid in (g.get("players") or {}) and g.get("phase") in ("night", "night_doctor", "night_detective"):
                    game = g
                    chat_id = cid
                    MAFIA_USER_CHAT[uid] = cid
                    break
        if not game:
            return
        phase = game.get("phase")
        if phase not in ("night", "night_doctor", "night_detective"):
            return
        p = (game.get("players") or {}).get(uid)
        if not p or not p.get("alive"):
            return
        txt = (message.text or "").strip()
        num = None
        if txt.isdigit():
            num = int(txt)
        else:
            m = re.match(r"^(?:\.?)(?:کشتن|نجات|استعلام|قتل)?\s*(\d+)$", txt)
            if m:
                num = int(m.group(1))
        if num is None:
            return
        role = p.get("role")
        night = game.setdefault("night", {})
        maps = night.setdefault("maps", {})

        def resolve(role_key):
            idx_map = maps.get(role_key) or {}
            # normalize
            norm = {}
            for k, v in idx_map.items():
                try:
                    norm[int(k)] = int(v)
                except Exception:
                    continue
            maps[role_key] = norm
            return norm.get(num)

        if phase == "night" and role == "mafia":
            target = resolve("mafia")
            if not target:
                await message.reply_text("❌ شماره نامعتبر — عدد لیست را بفرست.")
                return
            night.setdefault("kill_votes", {})[int(uid)] = int(target)
            tp = game["players"][int(target)]
            await message.reply_text(
                f"✅ قتل ثبت شد: {_mafia_mention(int(target), tp.get('name'), tp.get('username'))}",
                parse_mode=ParseMode.HTML,
            )
            logging.info("mafia kill vote uid=%s -> %s", uid, target)
            return

        if phase == "night_doctor" and role == "doctor":
            target = resolve("doctor")
            if not target:
                await message.reply_text("❌ شماره نامعتبر — عدد لیست را بفرست.")
                return
            night["save"] = int(target)
            tp = game["players"][int(target)]
            await message.reply_text(
                f"✅ نجات ثبت شد: {_mafia_mention(int(target), tp.get('name'), tp.get('username'))}",
                parse_mode=ParseMode.HTML,
            )
            logging.info("mafia save uid=%s -> %s", uid, target)
            return

        if phase == "night_detective" and role == "detective":
            target = resolve("detective")
            if not target:
                await message.reply_text("❌ شماره نامعتبر — عدد لیست را بفرست.")
                return
            night["investigate"] = int(target)
            tp = game["players"][int(target)]
            await message.reply_text(
                f"✅ استعلام ثبت شد: {_mafia_mention(int(target), tp.get('name'), tp.get('username'))}",
                parse_mode=ParseMode.HTML,
            )
            logging.info("mafia investigate uid=%s -> %s", uid, target)
            return
    except Exception as e:
        logging.warning("mafia_night_private: %s", e)





async def main():
    try:
        init_session_db()
        logging.info("✅ Database initialized")
    except Exception as e:
        logging.error(f"❌ Database init failed: {e}")

    try:
        backup_sessions()
    except Exception:
        pass

    try:
        clear_inactive_sessions()
    except Exception:
        pass

    try:
        load_manager_premium_emojis()
    except Exception as e:
        logging.warning(f"load manager emojis: {e}")

    asyncio.create_task(cleanup_old_files())
    asyncio.create_task(hourly_diamond_deduction_task())
    asyncio.create_task(_dooz_deadline_watchdog())
    logging.info("💎 Hourly diamond deduction task started")

    # ===== اول بات اصلی (منیجر) — بدون وابستگی به هلپر/سشن‌ها =====
    manager_ok = False
    for attempt in range(1, 4):
        try:
            if manager_bot.is_connected:
                try:
                    await manager_bot.stop()
                except Exception:
                    pass
            await manager_bot.start()
            try:
                global MANAGER_BOT_USERNAME
                _mme = await manager_bot.get_me()
                MANAGER_BOT_USERNAME = _mme.username
                logging.info("Manager bot username: %s", MANAGER_BOT_USERNAME)
            except Exception as e:
                logging.warning(f"get manager username: {e}")
            logging.info("✅ Manager bot started")
            manager_ok = True
            break
        except Exception as e:
            logging.error(f"❌ Manager bot start attempt {attempt}: {e}")
            await asyncio.sleep(2 * attempt)
    if not manager_ok:
        logging.error("❌ Manager bot could not start after retries — continuing sessions only")

    # هلپر از HELPER_BOT_TOKEN روی سرور — جدا از منیجر، خطا بات اصلی را نمی‌خواباند
    try:
        await start_helper_bot()
    except Exception as e:
        logging.warning("Helper bot start ignored: %s", e)

    try:
        await ensure_premium_client()
    except Exception as e:
        logging.warning(f"premium client: {e}")

    try:
        if manager_ok:
            await ensure_premium_emoji_pack(manager_bot)
    except Exception as e:
        logging.warning(f"pack load on start: {e}")

    # ===== بعد سشن‌های سلف =====
    try:
        sessions = get_all_sessions_from_db()
        if sessions:
            logging.info(f"🔄 Found {len(sessions)} sessions, starting bots...")
            for i, (phone, session_string, user_id, first_name, username) in enumerate(sessions):
                try:
                    bal = get_balance(user_id)
                    if bal < HOURLY_COST:
                        logging.warning(f"⏭ Skip start {user_id}: low balance ({bal})")
                        try:
                            delete_session_by_user_id(user_id)
                        except Exception:
                            pass
                        try:
                            set_self_start_time(user_id, 0)
                        except Exception:
                            pass
                        continue
                    logging.info(f"🔄 Starting bot for {phone} (User: {user_id})")
                    asyncio.create_task(start_bot_instance(session_string, phone, user_id, "bold"))
                    if (i + 1) % 5 == 0:
                        await asyncio.sleep(8)
                    else:
                        await asyncio.sleep(1.5)
                except Exception as e:
                    logging.error(f"❌ Failed to start bot for {phone}: {e}")
        else:
            logging.info("📭 No sessions found in database")
    except Exception as e:
        logging.error(f"❌ Error loading sessions: {e}")

    if manager_ok:
        logging.info("✅ Manager is online — idle")
    else:
        logging.warning("⚠️ Manager offline — idle (self sessions may still run)")
    await idle()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
