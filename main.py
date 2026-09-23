# SavePro audited update — 2026-09-22
# Python 3.10+; existing Pyrogram-compatible environment required.
# Added: isolated jobs, cancel/pause/resume, history, atomic payment review.
import os
import re
import asyncio
import time
import sqlite3
import json
import secrets
import tempfile
import shutil
import functools
from types import SimpleNamespace
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask

try:
    from PIL import Image
except Exception:
    Image = None

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    BotCommand,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from pyrogram.errors import FloodWait, RPCError, SessionPasswordNeeded

# ============================================================
# 1. PYTHON EVENT LOOP
# ============================================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ============================================================
# 2. OPTIONAL FLASK KEEP-ALIVE
#    (Kept for compatibility with your current script.)
# ============================================================
app = Flask(__name__)
BOT_READY = False

@app.route("/")
def home():
    return ("SavePro ready", 200) if BOT_READY else ("SavePro starting or stopping", 503)

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Start HTTP only from main(), never while importing for diagnostics.

# ============================================================
# 3. ENVIRONMENT VARIABLES
# ============================================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
DUMP_CHANNEL = os.environ.get("DUMP_CHANNEL", "")
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "")

# Advanced batch / turbo settings
# Normal paid users: maximum messages in ONE batch.
USER_MAX_BATCH_MESSAGES = max(1, int(os.environ.get("USER_MAX_BATCH_MESSAGES", "250")))
# Admin: unlimited by default. Set a positive value only to explicitly cap a job.
ADMIN_MAX_BATCH_MESSAGES = max(0, int(os.environ.get("ADMIN_MAX_BATCH_MESSAGES", "0")))
# Legacy server cap is kept only as an optional hard safety ceiling.
MAX_BATCH_MESSAGES = max(0, int(os.environ.get("MAX_BATCH_MESSAGES", "0")))
BATCH_PROGRESS_EVERY = max(1, int(os.environ.get("BATCH_PROGRESS_EVERY", "1")))
# Do not add an artificial pause between successfully transferred episodes.
SEND_DELAY = max(0.0, float(os.environ.get("SEND_DELAY", "0")))
# Keep completed per-episode progress cards only when explicitly enabled.
KEEP_EPISODE_PROGRESS = os.environ.get("KEEP_EPISODE_PROGRESS", "false").lower() in {
    "1", "true", "yes", "on"
}
# Large-file transfer watchdog/progress settings. These prevent one media item
# from making clone/batch look permanently stuck while keeping original quality.
MEDIA_DOWNLOAD_TIMEOUT = max(60, int(os.environ.get("MEDIA_DOWNLOAD_TIMEOUT", "1800")))
MEDIA_UPLOAD_TIMEOUT = max(60, int(os.environ.get("MEDIA_UPLOAD_TIMEOUT", "1800")))
# If byte progress does not move for this long, cancel that transfer, retry,
# then skip only that item instead of freezing the whole clone job.
MEDIA_STALL_TIMEOUT = max(20, int(os.environ.get("MEDIA_STALL_TIMEOUT", "60")))
TRANSFER_RETRIES = max(0, min(3, int(os.environ.get("TRANSFER_RETRIES", "2"))))
TRANSFER_PROGRESS_INTERVAL = max(2.0, float(os.environ.get("TRANSFER_PROGRESS_INTERVAL", "3")))

# Existing shared STRING_SESSION can remain as a backward-compatible fallback.
# For a public/multi-user bot, set ALLOW_SHARED_SESSION=false and ask each user
# to use /login with their own Telegram account.
ALLOW_SHARED_SESSION = os.environ.get("ALLOW_SHARED_SESSION", "false").lower() in {
    "1", "true", "yes", "on"
}
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)

# Persistent per-user login data is kept OUTSIDE the Git repo.
SESSION_DIR = os.environ.get(
    "USER_SESSION_DIR",
    os.path.expanduser("~/.my-saver-bot-sessions")
)
os.makedirs(SESSION_DIR, exist_ok=True)
try:
    os.chmod(SESSION_DIR, 0o700)
except OSError:
    pass

# ============================================================
# 3A. ADMIN / LANGUAGE / SUBSCRIPTION / PAYMENT SETTINGS
# ============================================================
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

# Optional username fallback for admin notifications.
# Numeric ADMIN_IDS remains the primary/recommended delivery target.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip().lstrip("@")
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", ADMIN_USERNAME or "toonworldvsm2").strip().lstrip("@")
PAYMENT_REQUIRE_DIFFERENT_ADMIN = os.environ.get("PAYMENT_REQUIRE_DIFFERENT_ADMIN", "false").lower() in {"1", "true", "yes", "on"}

UPI_ID = os.environ.get("UPI_ID", "sathiyamoorthy8020-2@okhdfcbank").strip()
PAYEE_NAME = os.environ.get("PAYEE_NAME", "SavePro Bot").strip()
PAYMENT_QR_FILE = os.environ.get("PAYMENT_QR_FILE", "payment_qr.png").strip()
DYNAMIC_QR_DIR = os.environ.get("DYNAMIC_QR_DIR", os.path.join("/tmp", "my-saver-bot-qr"))
os.makedirs(DYNAMIC_QR_DIR, exist_ok=True)
DATA_DIR = os.environ.get(
    "BOT_DATA_DIR",
    os.path.expanduser("~/.my-saver-bot-data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
except OSError:
    pass

CUSTOMIZATION_DIR = os.path.join(DATA_DIR, "customization")
THUMBNAIL_DIR = os.path.join(CUSTOMIZATION_DIR, "thumbnails")
os.makedirs(THUMBNAIL_DIR, exist_ok=True)
try:
    os.chmod(CUSTOMIZATION_DIR, 0o700)
    os.chmod(THUMBNAIL_DIR, 0o700)
except OSError:
    pass

DB_PATH = os.path.join(DATA_DIR, "bot.db")

# Low-price sample defaults. Admin can change them later with /setprice.
DEFAULT_PLAN_PRICES = {
    "basic": {"weekly": 15, "monthly": 49, "yearly": 399},
    "standard": {"weekly": 25, "monthly": 79, "yearly": 649},
    "premium": {"weekly": 40, "monthly": 129, "yearly": 999},
    "ultimate": {"weekly": 60, "monthly": 199, "yearly": 1599},
}
DEFAULT_PLAN_LIMITS = {
    "free": 3,
    "basic": 10,
    "standard": 50,
    "premium": 100,
    "ultimate": 0,  # 0 = unlimited
}
PLAN_TITLES = {
    "free": "🆓 Free",
    "basic": "⚡ Basic",
    "standard": "🥈 Standard",
    "premium": "🥇 Premium",
    "ultimate": "💎 Ultimate",
}
PERIOD_DAYS = {"weekly": 7, "monthly": 30, "yearly": 365}
PAYMENT_STATES = {}
BATCH_STATES = {}
CUSTOMIZE_STATES = {}

@contextmanager
def db_conn():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()

def init_db():
    with db_conn() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, started_at INTEGER NOT NULL, finished_at INTEGER,
            processed INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0, last_message INTEGER)""")
        con.execute("CREATE INDEX IF NOT EXISTS jobs_user_time ON jobs(user_id, started_at)")
        con.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status IN ('running','paused')", (int(time.time()),))
        con.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language TEXT NOT NULL DEFAULT 'en',
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                banned INTEGER NOT NULL DEFAULT 0,
                usage_date TEXT,
                daily_used INTEGER NOT NULL DEFAULT 0
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                period TEXT NOT NULL,
                amount INTEGER NOT NULL,
                utr TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                verified_at INTEGER,
                verified_by INTEGER,
                reviewed_at INTEGER,
                reviewed_by INTEGER,
                proof_file_id TEXT,
                proof_type TEXT
            )"""
        )
        # Safe migration for installations created by older bot versions.
        payment_columns = {row[1] for row in con.execute("PRAGMA table_info(payments)").fetchall()}
        for column, column_type in {
            "reviewed_at": "INTEGER",
            "reviewed_by": "INTEGER",
            "verified_at": "INTEGER",
            "verified_by": "INTEGER",
            "proof_file_id": "TEXT",
            "proof_type": "TEXT",
        }.items():
            if column not in payment_columns:
                con.execute(f"ALTER TABLE payments ADD COLUMN {column} {column_type}")
        con.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS user_customization (
                user_id INTEGER PRIMARY KEY,
                caption_mode TEXT NOT NULL DEFAULT 'original',
                caption_text TEXT NOT NULL DEFAULT '',
                watermark_text TEXT NOT NULL DEFAULT '',
                watermark_enabled INTEGER NOT NULL DEFAULT 0,
                thumbnail_path TEXT NOT NULL DEFAULT '',
                text_prefix TEXT NOT NULL DEFAULT '',
                text_suffix TEXT NOT NULL DEFAULT '',
                keep_original_text INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL DEFAULT 0
            )"""
        )
        customization_columns = {row[1] for row in con.execute("PRAGMA table_info(user_customization)")}
        for column, definition in {
            "audio_artist": "TEXT NOT NULL DEFAULT ''",
            "audio_title": "TEXT NOT NULL DEFAULT ''",
            "audio_clean_card": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in customization_columns:
                con.execute(f"ALTER TABLE user_customization ADD COLUMN {column} {definition}")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('payments_enabled','1')")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('maintenance','0')")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('login_alerts','1')")
        for plan, periods in DEFAULT_PLAN_PRICES.items():
            for period, amount in periods.items():
                con.execute(
                    "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (f"price.{plan}.{period}", str(amount)),
                )
        for plan, limit in DEFAULT_PLAN_LIMITS.items():
            con.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (f"limit.{plan}", str(limit)),
            )
        con.commit()

def setting_get(key, default=""):
    with db_conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def setting_set(key, value):
    with db_conn() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def touch_user(message: Message):
    u = message.from_user
    if not u:
        return
    now = int(time.time())
    with db_conn() as con:
        con.execute(
            """INSERT INTO users(user_id,username,first_name,created_at,last_seen)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 last_seen=excluded.last_seen""",
            (u.id, u.username or "", u.first_name or "", now, now),
        )
        con.commit()

def touch_query_user(query: CallbackQuery):
    u = query.from_user
    if not u:
        return
    now = int(time.time())
    with db_conn() as con:
        con.execute(
            """INSERT INTO users(user_id,username,first_name,created_at,last_seen)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 last_seen=excluded.last_seen""",
            (u.id, u.username or "", u.first_name or "", now, now),
        )
        con.commit()

def get_language(user_id: int) -> str:
    with db_conn() as con:
        row = con.execute("SELECT language FROM users WHERE user_id=?", (user_id,)).fetchone()
    return (row["language"] if row else "en") or "en"

def set_language(user_id: int, lang: str):
    now = int(time.time())
    with db_conn() as con:
        con.execute(
            """INSERT INTO users(user_id,language,created_at,last_seen) VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET language=excluded.language,last_seen=excluded.last_seen""",
            (user_id, lang, now, now),
        )
        con.commit()

def tr(user_id: int, en: str, ta: str) -> str:
    return ta if get_language(user_id) == "ta" else en

def get_price(plan: str, period: str) -> int:
    default = DEFAULT_PLAN_PRICES.get(plan, {}).get(period, 0)
    try:
        return int(setting_get(f"price.{plan}.{period}", default))
    except Exception:
        return default

def get_daily_limit(plan: str) -> int:
    default = DEFAULT_PLAN_LIMITS.get(plan, 0)
    try:
        return int(setting_get(f"limit.{plan}", default))
    except Exception:
        return default

def active_subscription(user_id: int):
    if is_admin(user_id):
        return {"plan": "ultimate", "expires_at": 4102444800, "admin": True}
    now = int(time.time())
    with db_conn() as con:
        row = con.execute(
            "SELECT plan,expires_at FROM subscriptions WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if row and row["expires_at"] > now:
        return {"plan": row["plan"], "expires_at": row["expires_at"], "admin": False}
    return None

def effective_plan(user_id: int) -> str:
    sub = active_subscription(user_id)
    return sub["plan"] if sub else "free"

def ensure_usage_day(user_id: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = int(time.time())
    with db_conn() as con:
        row = con.execute(
            "SELECT usage_date,daily_used FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            con.execute(
                "INSERT INTO users(user_id,created_at,last_seen,usage_date,daily_used) VALUES(?,?,?,?,0)",
                (user_id, now, now, today),
            )
            con.commit()
            return 0
        if row["usage_date"] != today:
            con.execute(
                "UPDATE users SET usage_date=?,daily_used=0,last_seen=? WHERE user_id=?",
                (today, now, user_id),
            )
            con.commit()
            return 0
        return int(row["daily_used"] or 0)

def batch_limit_for_user(user_id: int):
    """Per-job batch cap. Admin can use 0 as unlimited."""
    if is_admin(user_id):
        return ADMIN_MAX_BATCH_MESSAGES
    return USER_MAX_BATCH_MESSAGES


def batch_limit_text(user_id: int):
    limit = batch_limit_for_user(user_id)
    if limit == 0:
        return "Unlimited ∞"
    return f"{limit:,}"


def remaining_quota(user_id: int):
    if is_admin(user_id):
        return None
    plan = effective_plan(user_id)
    limit = get_daily_limit(plan)
    if limit == 0:
        return None
    used = ensure_usage_day(user_id)
    return max(0, limit - used)

def consume_usage(user_id: int, count: int = 1):
    if is_admin(user_id):
        return
    plan = effective_plan(user_id)
    if get_daily_limit(plan) == 0:
        return
    ensure_usage_day(user_id)
    with db_conn() as con:
        con.execute(
            "UPDATE users SET daily_used=daily_used+? WHERE user_id=?",
            (max(0, int(count)), user_id),
        )
        con.commit()

def feature_allowed(user_id: int, feature: str, requested: int = 1):
    if is_admin(user_id):
        return True, ""
    if setting_get("maintenance", "0") == "1":
        return False, tr(user_id, "🛠 Bot is under maintenance.", "🛠 Bot maintenance-ல் உள்ளது.")
    with db_conn() as con:
        row = con.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row and row["banned"]:
        return False, tr(user_id, "🚫 You are blocked from using this bot.", "🚫 இந்த bot-ஐ பயன்படுத்த உங்களுக்கு அனுமதி இல்லை.")

    plan = effective_plan(user_id)
    rank = {"free": 0, "basic": 1, "standard": 2, "premium": 3, "ultimate": 4}.get(plan, 0)
    need = {"single": 0, "batch": 2, "range": 4, "clone": 4}.get(feature, 0)
    if rank < need:
        return False, tr(
            user_id,
            f"🔒 {feature.title()} is not included in your {PLAN_TITLES.get(plan, plan)} plan. Use /plans to upgrade.",
            f"🔒 {feature.title()} உங்கள் {PLAN_TITLES.get(plan, plan)} plan-ல் இல்லை. Upgrade செய்ய /plans பயன்படுத்துங்கள்.",
        )

    left = remaining_quota(user_id)
    if left is not None and requested > left:
        return False, tr(
            user_id,
            f"⚠️ Daily limit reached/insufficient. Remaining today: {left}. Use /plans to upgrade.",
            f"⚠️ இன்றைய limit போதவில்லை. மீதம்: {left}. Upgrade செய்ய /plans பயன்படுத்துங்கள்.",
        )
    return True, ""


CUSTOM_FEATURE_MIN_RANK = {
    "caption": 1,     # Basic+
    "thumbnail": 2,   # Standard+
    "watermark": 3,   # Premium+
    "text": 3,        # Premium+
    "audio": 3,       # Audio display branding, Premium+
}


def _plan_rank_for_user(user_id: int) -> int:
    if is_admin(user_id):
        return 99
    plan = effective_plan(user_id)
    return {"free": 0, "basic": 1, "standard": 2, "premium": 3, "ultimate": 4}.get(plan, 0)


def customization_allowed(user_id: int, feature: str) -> bool:
    if is_admin(user_id):
        return True
    return _plan_rank_for_user(user_id) >= CUSTOM_FEATURE_MIN_RANK.get(feature, 99)


def customization_locked_text(user_id: int, feature: str) -> str:
    names = {
        "caption": "Custom Caption",
        "thumbnail": "Custom Thumbnail",
        "watermark": "Text Watermark",
        "text": "Text Prefix / Suffix",
        "audio": "Audio Title / Artist",
    }
    required = {
        "caption": "Basic",
        "thumbnail": "Standard",
        "watermark": "Premium",
        "text": "Premium",
        "audio": "Premium",
    }
    return (
        f"🔒 **{names.get(feature, feature.title())}** requires "
        f"**{required.get(feature, 'a higher')} plan** or above.\n"
        "Your existing setting is kept safely, but it will not be applied while your plan is below the requirement."
    )


def ensure_user_customization(user_id: int):
    now = int(time.time())
    with db_conn() as con:
        con.execute(
            """INSERT OR IGNORE INTO user_customization(user_id,updated_at)
               VALUES(?,?)""",
            (user_id, now),
        )
        con.commit()


def get_user_customization(user_id: int):
    ensure_user_customization(user_id)
    with db_conn() as con:
        row = con.execute(
            "SELECT * FROM user_customization WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else {
        "user_id": user_id,
        "caption_mode": "original",
        "caption_text": "",
        "watermark_text": "",
        "watermark_enabled": 0,
        "thumbnail_path": "",
        "text_prefix": "",
        "text_suffix": "",
        "keep_original_text": 1,
        "updated_at": 0,
    }


def set_user_customization(user_id: int, **changes):
    allowed_columns = {
        "caption_mode",
        "caption_text",
        "watermark_text",
        "watermark_enabled",
        "thumbnail_path",
        "text_prefix",
        "text_suffix",
        "keep_original_text",
        "audio_artist",
        "audio_title",
        "audio_clean_card",
    }
    clean = {k: v for k, v in changes.items() if k in allowed_columns}
    if not clean:
        return

    ensure_user_customization(user_id)
    clean["updated_at"] = int(time.time())
    sql = ", ".join(f"{k}=?" for k in clean)
    values = list(clean.values()) + [user_id]

    with db_conn() as con:
        con.execute(
            f"UPDATE user_customization SET {sql} WHERE user_id=?",
            values,
        )
        con.commit()


def _thumbnail_path_for_user(user_id: int):
    return os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")


def remove_user_thumbnail(user_id: int):
    prefs = get_user_customization(user_id)
    paths = {prefs.get("thumbnail_path", ""), _thumbnail_path_for_user(user_id)}
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    set_user_customization(user_id, thumbnail_path="")


def reset_user_customization(user_id: int):
    remove_user_thumbnail(user_id)
    set_user_customization(
        user_id,
        caption_mode="original",
        caption_text="",
        watermark_text="",
        watermark_enabled=0,
        thumbnail_path="",
        text_prefix="",
        text_suffix="",
        keep_original_text=1,
        audio_artist="",
        audio_title="",
        audio_clean_card=0,
    )


def truncate_utf16(value, limit):
    return value.encode("utf-16-le")[:limit * 2].decode("utf-16-le", errors="ignore")

TEMPLATE_KEYS = {"original", "filename", "stem", "id", "date", "title", "artist"}

def normalize_custom_template(value):
    """Fix legacy {@handle}; recognized placeholders remain available to render once."""
    def clean(match):
        inner = match.group(1).strip()
        return "{" + inner + "}" if inner in TEMPLATE_KEYS else inner
    return re.sub(r"\{+([^{}]*)\}+", clean, value or "").strip()

def _clean_custom_text(value: str, limit: int):
    return truncate_utf16(normalize_custom_template(value), limit)

def _render_user_template(template: str, target_msg: Message, original: str = ""):
    audio = getattr(target_msg, "audio", None)
    filename = next((getattr(getattr(target_msg, attr, None), "file_name", None)
                     for attr in DOWNLOADABLE_MEDIA_ATTRS
                     if getattr(getattr(target_msg, attr, None), "file_name", None)), None)
    filename = filename or _media_display_name(target_msg)
    replacements = {
        "original": original or "",
        "filename": filename,
        "stem": os.path.splitext(filename)[0],
        "id": str(getattr(target_msg, "id", "")),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "title": getattr(audio, "title", None) or os.path.splitext(filename)[0],
        "artist": getattr(audio, "performer", None) or "",
    }
    # One pass: source text inserted by {original} cannot become another template.
    return re.sub(r"\{(original|filename|stem|id|date|title|artist)\}",
                  lambda match: replacements[match.group(1)], normalize_custom_template(template))

def _effective_audio(user_id, target_msg):
    audio = getattr(target_msg, "audio", None)
    title = getattr(audio, "title", None)
    artist = getattr(audio, "performer", None)
    clean_card = False
    if user_id is not None and customization_allowed(user_id, "audio"):
        prefs = get_user_customization(user_id)
        if prefs.get("audio_title"):
            title = _render_user_template(prefs["audio_title"], target_msg, title or "")
        if prefs.get("audio_artist"):
            artist = _render_user_template(prefs["audio_artist"], target_msg, artist or "")
        clean_card = bool(prefs.get("audio_clean_card"))
    # A metadata field must be a single line. Defaults retain original values.
    title = truncate_utf16(" ".join(title.split()), 128) if title else None
    artist = truncate_utf16(" ".join(artist.split()), 128) if artist else None
    return title, artist, clean_card

def append_watermark(body, watermark, limit):
    """Keep a configured footer visible; suppress exact duplicate lines."""
    if not watermark or watermark.strip() in {line.strip() for line in body.splitlines()} or body.strip() == watermark.strip():
        return body
    watermark = truncate_utf16(watermark, limit)
    room = limit - len(watermark.encode("utf-16-le")) // 2 - 2
    body = truncate_utf16(body, max(0, room)).rstrip()
    return "\n\n".join(part for part in (body, watermark) if part)


def _effective_caption(user_id: int, target_msg: Message):
    original = target_msg.caption or ""
    prefs = get_user_customization(user_id)

    # Per-user customization is authoritative. "Original" means original.
    caption = original
    changed = False

    if customization_allowed(user_id, "caption"):
        mode = (prefs.get("caption_mode") or "original").lower()
        custom = _render_user_template(
            prefs.get("caption_text", ""),
            target_msg,
            original=original,
        )
        if mode == "replace":
            caption = custom
            changed = True
        elif mode == "append":
            caption = "\n\n".join(x for x in (original, custom) if x)
            changed = True
        elif mode == "remove":
            caption = ""
            changed = True
        elif mode == "original":
            caption = original
            changed = False

    if (
        customization_allowed(user_id, "watermark")
        and int(prefs.get("watermark_enabled") or 0)
        and prefs.get("watermark_text")
    ):
        watermark = _render_user_template(
            prefs.get("watermark_text", ""),
            target_msg,
            original=original,
        )
        caption = append_watermark(caption, watermark, 1024)
        changed = True

    # Telegram media caption safe headroom.
    result = truncate_utf16(caption or "", 1024)
    return result, changed or result != (caption or "")


def _effective_text(user_id: int, target_msg: Message):
    original = target_msg.text or target_msg.caption or ""
    prefs = get_user_customization(user_id)
    body = original
    changed = False

    if customization_allowed(user_id, "text"):
        keep_original = bool(int(prefs.get("keep_original_text") or 0))
        prefix = _render_user_template(prefs.get("text_prefix", ""), target_msg, original)
        suffix = _render_user_template(prefs.get("text_suffix", ""), target_msg, original)

        if not keep_original:
            body = ""
            changed = True
        if prefix:
            body = "\n\n".join(x for x in (prefix, body) if x)
            changed = True
        if suffix:
            body = "\n\n".join(x for x in (body, suffix) if x)
            changed = True

    if (
        customization_allowed(user_id, "watermark")
        and int(prefs.get("watermark_enabled") or 0)
        and prefs.get("watermark_text")
    ):
        watermark = _render_user_template(
            prefs.get("watermark_text", ""),
            target_msg,
            original=original,
        )
        body = append_watermark(body, watermark, 4096)
        changed = True

    result = truncate_utf16(body or "", 4096)
    return result, changed or result != (body or "")


def _effective_thumbnail(user_id: int):
    if not customization_allowed(user_id, "thumbnail"):
        return None
    path = get_user_customization(user_id).get("thumbnail_path") or ""
    return path if path and os.path.exists(path) else None


def customization_summary(user_id: int):
    prefs = get_user_customization(user_id)
    plan = "💎 Admin Unlimited" if is_admin(user_id) else PLAN_TITLES.get(effective_plan(user_id), "Free")

    caption_mode = (prefs.get("caption_mode") or "original").title()
    watermark_on = bool(int(prefs.get("watermark_enabled") or 0)) and bool(prefs.get("watermark_text"))
    thumb_on = bool(prefs.get("thumbnail_path")) and os.path.exists(prefs.get("thumbnail_path") or "")
    keep_original = bool(int(prefs.get("keep_original_text") or 0))

    return (
        "⚙️ **CONTENT SETTINGS**\n"
        f"Plan: **{plan}**\n\n"
        f"✍️ Caption: **{caption_mode}** "
        f"{'✅' if customization_allowed(user_id, 'caption') else '🔒 Basic+'}\n"
        f"🏷 Text Watermark: **{'ON' if watermark_on else 'OFF'}** "
        f"{'✅' if customization_allowed(user_id, 'watermark') else '🔒 Premium+'}\n"
        f"🖼 Thumbnail: **{'SET' if thumb_on else 'DEFAULT'}** "
        f"{'✅' if customization_allowed(user_id, 'thumbnail') else '🔒 Standard+'}\n"
        f"📝 Text Original: **{'ON' if keep_original else 'OFF'}** "
        f"{'✅' if customization_allowed(user_id, 'text') else '🔒 Premium+'}\n"
        f"➕ Prefix: **{'SET' if prefs.get('text_prefix') else 'OFF'}**\n"
        f"➕ Suffix: **{'SET' if prefs.get('text_suffix') else 'OFF'}**\n\n"
        f"🎧 Audio artist: {'SET' if prefs.get('audio_artist') else 'Original'} • "
        f"Title: {'Custom' if prefs.get('audio_title') else 'Original'}\n"
        f"Audio layout: {'Clean card' if prefs.get('audio_clean_card') else 'With caption'}\n"
        "💡 Audio branding appears below the title. Caption watermark is separate."
    )


def settings_keyboard(user_id: int):
    def lock(feature):
        return "" if customization_allowed(user_id, feature) else " 🔒"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✍️ Caption{lock('caption')}", callback_data="cust:caption"),
            InlineKeyboardButton(f"🏷 Watermark{lock('watermark')}", callback_data="cust:watermark"),
        ],
        [
            InlineKeyboardButton(f"🖼 Thumbnail{lock('thumbnail')}", callback_data="cust:thumbnail"),
            InlineKeyboardButton(f"📝 Text{lock('text')}", callback_data="cust:text"),
        ],
        [InlineKeyboardButton(f"🎧 Audio Title / Artist{lock('audio')}", callback_data="cust:audio")],
        [
            InlineKeyboardButton("👁 Preview", callback_data="cust:preview"),
            InlineKeyboardButton("🔄 Reset All", callback_data="cust:reset"),
        ],
        [
            InlineKeyboardButton("🛒 Plans", callback_data="plans_menu"),
            InlineKeyboardButton("🏠 Home", callback_data="settings_home"),
        ],
    ])


def caption_settings_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Original", callback_data="cust:caption_mode:original"),
            InlineKeyboardButton("♻️ Replace", callback_data="cust:caption_mode:replace"),
        ],
        [
            InlineKeyboardButton("➕ Append", callback_data="cust:caption_mode:append"),
            InlineKeyboardButton("🚫 No Caption", callback_data="cust:caption_mode:remove"),
        ],
        [
            InlineKeyboardButton("✏️ Set / Change Caption", callback_data="cust:caption_set"),
            InlineKeyboardButton("⬅️ Settings", callback_data="settings_menu"),
        ],
    ])


def watermark_settings_keyboard(user_id: int):
    prefs = get_user_customization(user_id)
    enabled = bool(int(prefs.get("watermark_enabled") or 0))
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔴 Turn OFF" if enabled else "🟢 Turn ON",
                callback_data="cust:watermark_toggle",
            ),
            InlineKeyboardButton("✏️ Set / Change", callback_data="cust:watermark_set"),
        ],
        [
            InlineKeyboardButton("🗑 Remove", callback_data="cust:watermark_remove"),
            InlineKeyboardButton("⬅️ Settings", callback_data="settings_menu"),
        ],
    ])


def thumbnail_settings_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Set / Change", callback_data="cust:thumbnail_set"),
            InlineKeyboardButton("🗑 Remove", callback_data="cust:thumbnail_remove"),
        ],
        [InlineKeyboardButton("⬅️ Settings", callback_data="settings_menu")],
    ])


def text_settings_keyboard(user_id: int):
    prefs = get_user_customization(user_id)
    keep = bool(int(prefs.get("keep_original_text") or 0))
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Set Prefix", callback_data="cust:text_prefix"),
            InlineKeyboardButton("➕ Set Suffix", callback_data="cust:text_suffix"),
        ],
        [
            InlineKeyboardButton(
                "📝 Original Text: ON" if keep else "📝 Original Text: OFF",
                callback_data="cust:text_toggle_original",
            ),
        ],
        [
            InlineKeyboardButton("🗑 Clear Text Settings", callback_data="cust:text_clear"),
            InlineKeyboardButton("⬅️ Settings", callback_data="settings_menu"),
        ],
    ])


def audio_settings_keyboard(user_id):
    prefs = get_user_customization(user_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Artist / Brand", callback_data="cust:audio_artist"),
         InlineKeyboardButton("✏️ Audio title", callback_data="cust:audio_title")],
        [InlineKeyboardButton("🎵 Use filename as title", callback_data="cust:audio_filename")],
        [InlineKeyboardButton("☑ Clean card ON" if prefs.get("audio_clean_card") else "☐ Clean card OFF",
                              callback_data="cust:audio_clean")],
        [InlineKeyboardButton("👁 Preview", callback_data="cust:preview"),
         InlineKeyboardButton("↩ Original audio settings", callback_data="cust:audio_reset")],
        [InlineKeyboardButton("⬅ Settings", callback_data="settings_menu")],
    ])

def customization_preview_text(user_id: int):
    # Use production renderers so preview matches output and current plan rights.
    sample = SimpleNamespace(id=2, caption="Original caption example", text=None,
        audio=SimpleNamespace(file_name="Ep 002 Sample.mp3", title="Ep 002 Sample", performer="Original artist"))
    title, artist, clean = _effective_audio(user_id, sample)
    caption, _ = _effective_caption(user_id, sample)
    return (
        "👁 AUDIO PREVIEW / மாதிரி\n\n"
        + (title or sample.audio.file_name) + "\n" + (artist or "Unknown artist")
        + "\n0:00 / 16:38 (sample)\n\n"
        + ("Caption hidden — clean card" if clean else "Caption:\n" + (caption or "(none)"))
        + "\n\nThis is a text preview. Telegram controls the actual player layout."
    )

def plan_text(user_id: int) -> str:
    lines_en = [
        "╔═══════════════════════════════╗",
        "║   🛒  CHOOSE YOUR PLAN        ║",
        "╚═══════════════════════════════╝",
        "",
        f"🆓 Free — {get_daily_limit('free')} links/day • Single only",
        f"⚡ Basic — {get_daily_limit('basic')} links/day + Custom Caption",
        f"   🔹 Weekly ₹{get_price('basic','weekly')} | 📅 Monthly ₹{get_price('basic','monthly')} | 🔥 Yearly ₹{get_price('basic','yearly')}",
        f"🥈 Standard — {get_daily_limit('standard')} links/day + /batch + Caption + Thumbnail",
        f"   🔹 Weekly ₹{get_price('standard','weekly')} | 📅 Monthly ₹{get_price('standard','monthly')} | 🔥 Yearly ₹{get_price('standard','yearly')}",
        f"🥇 Premium — {get_daily_limit('premium')} links/day + /batch + Caption + Thumbnail + Watermark/Text",
        f"   🔹 Weekly ₹{get_price('premium','weekly')} | 📅 Monthly ₹{get_price('premium','monthly')} | 🔥 Yearly ₹{get_price('premium','yearly')}",
        "💎 Ultimate — Unlimited ♾️ + /batch + range + /clone + All Customization",
        f"   🔹 Weekly ₹{get_price('ultimate','weekly')} | 📅 Monthly ₹{get_price('ultimate','monthly')} | 🔥 Yearly ₹{get_price('ultimate','yearly')}",
        "",
        "✅ UPI / QR manual payment",
        "✅ Screenshot + UTR manual verification",
        "🔥 Yearly plans are heavily discounted",
        "",
        "👇 Select your plan:",
    ]
    lines_ta = [
        "╔═══════════════════════════════╗",
        "║   🛒  உங்கள் PLAN தேர்வு       ║",
        "╚═══════════════════════════════╝",
        "",
        f"🆓 Free — நாள் ஒன்றுக்கு {get_daily_limit('free')} links • Single மட்டும்",
        f"⚡ Basic — நாள் ஒன்றுக்கு {get_daily_limit('basic')} links + Custom Caption",
        f"   🔹 வாரம் ₹{get_price('basic','weekly')} | 📅 மாதம் ₹{get_price('basic','monthly')} | 🔥 வருடம் ₹{get_price('basic','yearly')}",
        f"🥈 Standard — நாள் ஒன்றுக்கு {get_daily_limit('standard')} links + /batch + Caption + Thumbnail",
        f"   🔹 வாரம் ₹{get_price('standard','weekly')} | 📅 மாதம் ₹{get_price('standard','monthly')} | 🔥 வருடம் ₹{get_price('standard','yearly')}",
        f"🥇 Premium — நாள் ஒன்றுக்கு {get_daily_limit('premium')} links + /batch + Caption + Thumbnail + Watermark/Text",
        f"   🔹 வாரம் ₹{get_price('premium','weekly')} | 📅 மாதம் ₹{get_price('premium','monthly')} | 🔥 வருடம் ₹{get_price('premium','yearly')}",
        "💎 Ultimate — Unlimited ♾️ + /batch + range + /clone + All Customization",
        f"   🔹 வாரம் ₹{get_price('ultimate','weekly')} | 📅 மாதம் ₹{get_price('ultimate','monthly')} | 🔥 வருடம் ₹{get_price('ultimate','yearly')}",
        "",
        "✅ UPI / QR manual payment",
        "✅ Screenshot + UTR admin verification",
        "🔥 Yearly plan அதிக discount",
        "",
        "👇 Plan தேர்வு செய்யுங்கள்:",
    ]
    return "\n".join(lines_ta if get_language(user_id) == "ta" else lines_en)


def support_url():
    return f"https://t.me/{SUPPORT_USERNAME}" if SUPPORT_USERNAME else ""

def support_row():
    url = support_url()
    if not url:
        return []
    return [InlineKeyboardButton("🆘 Support / Contact Admin", url=url)]

def support_markup():
    row = support_row()
    return InlineKeyboardMarkup([row]) if row else None

def persistent_support_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🆘 Support / Contact Admin")]],
        resize_keyboard=True,
    )

def is_obviously_fake_utr(raw: str):
    """
    Returns (is_bad, normalized, reason).
    This catches obvious fake/test values only. It cannot prove a real payment;
    final confirmation still requires bank/UPI credit verification.
    """
    normalized = re.sub(r"[\s-]+", "", (raw or "").upper())
    if not re.fullmatch(r"[A-Z0-9]{8,35}", normalized):
        return True, normalized, "UTR / transaction reference must be 8–35 letters/numbers."

    upper = normalized.upper()
    fake_words = ("FAKE", "TEST", "DEMO", "SAMPLE", "DUMMY", "ABCDEF", "QWERTY")
    if any(word in upper for word in fake_words):
        return True, normalized, "Test/fake placeholder text is not accepted."

    # Very low variety: 11111111, AAAAAAAA, 12121212-like junk.
    if len(set(normalized)) <= 2:
        return True, normalized, "This reference looks like a repeated/test value."

    if normalized.isdigit():
        asc = "0123456789" * 5
        desc = "9876543210" * 5
        if normalized in asc or normalized in desc:
            return True, normalized, "Sequential test numbers are not accepted."
        # Also reject obvious short demo sequences such as 12345678 / 87654321.
        if normalized in {
            "12345678", "123456789", "1234567890", "123456789012",
            "87654321", "987654321", "9876543210", "00000000",
        }:
            return True, normalized, "Sequential test numbers are not accepted."

    return False, normalized, ""


def upgrade_markup(user_id: int):
    rows = [
        [
            InlineKeyboardButton("🛒 View Plans", callback_data="plans_menu"),
            InlineKeyboardButton("📊 My Plan", callback_data="my_plan"),
        ],
        [
            InlineKeyboardButton("📘 User Manual", callback_data="manual_menu"),
            InlineKeyboardButton("⚡ Quick Start", callback_data="quick_start"),
        ],
    ]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def success_markup(user_id: int):
    rows = [[
        InlineKeyboardButton("📦 New Batch", callback_data="batch_new"),
        InlineKeyboardButton("🛒 Plans", callback_data="plans_menu"),
    ]]
    rows.append([
        InlineKeyboardButton("📘 Manual", callback_data="manual_menu"),
        InlineKeyboardButton("📊 My Plan", callback_data="my_plan"),
    ])
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def single_success_keyboard():
    rows = [[InlineKeyboardButton("🎧 Settings", callback_data="settings_menu"),
             InlineKeyboardButton("📦 New Batch", callback_data="batch_new")]]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def finish_single_success(message, status, body):
    # A fresh confirmation appears after the delivered media in chat order.
    await message.reply_text(body, reply_markup=single_success_keyboard())
    await safe_delete_message(status)

def formats_text(user_id: int):
    return tr(
        user_id,
        "📁 **SUPPORTED CONTENT**\n\n"
        "✅ Photo / Image\n"
        "✅ Video\n"
        "✅ Audio / Music\n"
        "✅ Voice message\n"
        "✅ Document / ZIP / PDF / other files\n"
        "✅ GIF / Animation\n"
        "✅ Sticker (static / animated / video when Telegram permits)\n"
        "✅ Video Note\n"
        "✅ Text messages\n"
        "✅ Clickable links + web previews\n"
        "✅ URL buttons\n"
        "✅ Contact\n"
        "✅ Location / Venue\n"
        "✅ Poll (re-created when possible)\n"
        "✅ Dice\n\n"
        "ℹ️ Telegram service/system messages and source-bot callback buttons cannot always be reproduced.",
        "📁 **SUPPORTED CONTENT**\n\n"
        "✅ Photo / Image\n"
        "✅ Video\n"
        "✅ Audio / Music\n"
        "✅ Voice message\n"
        "✅ Document / ZIP / PDF / மற்ற files\n"
        "✅ GIF / Animation\n"
        "✅ Sticker (Telegram அனுமதிக்கும் static / animated / video)\n"
        "✅ Video Note\n"
        "✅ Text messages\n"
        "✅ Clickable links + web previews\n"
        "✅ URL buttons\n"
        "✅ Contact\n"
        "✅ Location / Venue\n"
        "✅ Poll (முடிந்தவரை மீண்டும் உருவாக்கப்படும்)\n"
        "✅ Dice\n\n"
        "ℹ️ Telegram service/system messages மற்றும் மற்ற bot-ன் callback buttons அனைத்தையும் அப்படியே recreate செய்ய முடியாது.",
    )


def _legacy_manual_text(user_id: int):
    return tr(
        user_id,
        "📘 **SAVE PRO • USER MANUAL**\n\n"
        "**1 • Login**\n"
        "Use `/login` → Phone + OTP or Session String. Use only chats/content your account is authorized to access.\n\n"
        "**2 • Save one message**\n"
        "Paste one Telegram post link. Photo, video, audio, documents, stickers, text/links and common Telegram content are handled automatically.\n\n"
        "**3 • Batch**\n"
        "Send `/batch` → paste the starting link → choose 5 / 10 / 25 / 50 / 100 / 250 or Custom.\n"
        "Normal users: max 250 per batch. Admin: configured admin limit.\n\n"
        "**4 • Link → Link range**\n"
        "`/batch` → Start Link → End Link. Range access follows your plan.\n\n"
        "**5 • Full Clone**\n"
        "Ultimate/Admin: `/clone <start_link>` processes from that message to the latest available message.\n\n"
        "**6 • Stop**\n"
        "Use `/cancel` any time. A failed/stalled media item retries automatically, then the task can continue.\n\n"
        "**7 • Plans / Payment**\n"
        "Use `/plans` → choose plan + period → Dynamic UPI QR → I Paid → real UTR + screenshot → admin verification → activation.\n\n"
        "**8 • Customize**\n"
        "`/settings` → Caption / Fast Text Watermark / Thumbnail / Text Prefix-Suffix.\n"
        "Basic: Caption • Standard: + Thumbnail • Premium: + Watermark/Text • Ultimate/Admin: all.\n\n"
        "**9 • Help**\n"
        "`/formats` = supported content\n"
        "`/myplan` = plan/limit\n"
        "`/support` = contact admin\n\n"
        "💡 Start with the free single-save allowance, then upgrade only when you need higher limits, Batch, Range or Clone.",
        "📘 **SAVE PRO • USER MANUAL**\n\n"
        "**1 • Login**\n"
        "`/login` → Phone + OTP அல்லது Session String. உங்கள் account-க்கு authorized access உள்ள chats/content மட்டும் பயன்படுத்துங்கள்.\n\n"
        "**2 • Single Save**\n"
        "ஒரு Telegram post link paste செய்யுங்கள். Photo, video, audio, document, sticker, text/link மற்றும் common Telegram content auto handle ஆகும்.\n\n"
        "**3 • Batch**\n"
        "`/batch` → starting link paste → 5 / 10 / 25 / 50 / 100 / 250 அல்லது Custom தேர்வு செய்யுங்கள்.\n"
        "Normal users: ஒரு batch-க்கு max 250. Admin: configured admin limit.\n\n"
        "**4 • Start Link → End Link**\n"
        "`/batch` → Start Link → End Link. உங்கள் plan-க்கு ஏற்ப range access கிடைக்கும்.\n\n"
        "**5 • Full Clone**\n"
        "Ultimate/Admin: `/clone <start_link>` அந்த message முதல் latest available message வரை process செய்யும்.\n\n"
        "**6 • Stop**\n"
        "எப்போது வேண்டுமானாலும் `/cancel`. Stall/fail ஆன media auto retry ஆகி, தேவையானால் skip செய்து task continue ஆகும்.\n\n"
        "**7 • Plans / Payment**\n"
        "`/plans` → plan + period → Dynamic UPI QR → I Paid → உண்மையான UTR + screenshot → admin verification → activation.\n\n"
        "**8 • Customize**\n"
        "`/settings` → Caption / Fast Text Watermark / Thumbnail / Text Prefix-Suffix.\n"
        "Basic: Caption • Standard: + Thumbnail • Premium: + Watermark/Text • Ultimate/Admin: அனைத்தும்.\n\n"
        "**9 • Help**\n"
        "`/formats` = supported content\n"
        "`/myplan` = plan/limit\n"
        "`/support` = admin contact\n\n"
        "💡 முதலில் free single-save allowance try செய்யலாம்; Batch/Range/Clone அல்லது அதிக limits தேவைப்பட்டால் மட்டும் upgrade செய்யுங்கள்.",
    )


def manual_text(user_id: int):
    return _legacy_manual_text(user_id) + tr(user_id,
        "\n\nAudio branding: /settings → Audio Title / Artist. Set artist, optionally set title, then enable Clean card for a caption-free audio player. Only new sends change. Preview uses sample content.",
        "\n\nAudio branding: /settings → Audio Title / Artist → Artist பெயர் → Clean card ON. Sample போல caption இல்லாத audio card வரும். புதிய sends மட்டும் மாறும். Preview sample-ஐ காட்டும்.") + tr(user_id,
        "\n\nNew controls: /status /pause /resume /cancel /history /payments\nPause applies after the current message. History survives restart; jobs do not auto-resume. Admin batch default is unlimited. Other users receive files privately.",
        "\n\nபுதிய commands: /status /pause /resume /cancel /history /payments\nதற்போதைய message முடிந்ததும் pause ஆகும். Restart ஆனாலும் history இருக்கும்; job தானாக resume ஆகாது. Admin batch default unlimited. மற்ற users-க்கு files தனிப்பட்ட chat-ல் வரும்.")

def quick_start_text(user_id: int):
    free_limit = get_daily_limit("free")
    return tr(
        user_id,
        f"⚡ **QUICK START**\n\n"
        f"1️⃣ `/login` செய்து உங்கள் Telegram account connect செய்யுங்கள்.\n"
        f"2️⃣ ஒரு Telegram post link paste செய்யுங்கள்.\n"
        f"3️⃣ Free tier-ல் நாள் ஒன்றுக்கு `{free_limit}` Single saves try செய்யலாம்.\n"
        "4️⃣ பல messages வேண்டுமா? `/batch`\n"
        "5️⃣ Range/Full Clone வேண்டுமா? `/plans`\n\n"
        "👇 Ready என்றால் Telegram post link paste செய்யுங்கள்.",
        f"⚡ **QUICK START**\n\n"
        f"1️⃣ `/login` மூலம் Telegram account connect செய்யுங்கள்.\n"
        f"2️⃣ ஒரு Telegram post link paste செய்யுங்கள்.\n"
        f"3️⃣ Free tier-ல் நாள் ஒன்றுக்கு `{free_limit}` Single saves try செய்யலாம்.\n"
        "4️⃣ பல messages வேண்டுமா? `/batch`\n"
        "5️⃣ Range/Full Clone வேண்டுமா? `/plans`\n\n"
        "👇 Ready என்றால் Telegram post link paste செய்யுங்கள்.",
    )

def plans_keyboard():
    rows = [
        [
            InlineKeyboardButton("⚡ Basic", callback_data="plan:basic"),
            InlineKeyboardButton("🥈 Standard", callback_data="plan:standard"),
        ],
        [
            InlineKeyboardButton("🥇 Premium", callback_data="plan:premium"),
            InlineKeyboardButton("💎 Ultimate", callback_data="plan:ultimate"),
        ],
        [InlineKeyboardButton("🌐 English / தமிழ்", callback_data="language_menu")],
    ]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def subscription_text(user_id: int) -> str:
    sub = active_subscription(user_id)
    plan = effective_plan(user_id)
    left = remaining_quota(user_id)
    if sub and sub.get("admin"):
        expiry = "Admin unlimited"
    elif sub:
        expiry = datetime.fromtimestamp(sub["expires_at"], timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
    else:
        expiry = "Free tier"
    left_text = "Unlimited ♾️" if left is None else str(left)
    return tr(
        user_id,
        f"📊 Your Plan\nPlan: {PLAN_TITLES.get(plan, plan)}\nValid: {expiry}\nRemaining today: {left_text}",
        f"📊 உங்கள் Plan\nPlan: {PLAN_TITLES.get(plan, plan)}\nValid: {expiry}\nஇன்று மீதம்: {left_text}",
    )

def make_payment_id(user_id: int) -> str:
    # Timestamp + user suffix + random tag prevents collisions when a user creates
    # more than one invoice within the same second.
    tag = secrets.token_hex(2).upper()
    return f"SP{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}{str(user_id)[-4:]}{tag}"

def build_upi_uri(amount: int, invoice_id: str, plan: str, period: str) -> str:
    params = {
        "pa": UPI_ID,
        "pn": PAYEE_NAME,
        "am": f"{float(amount):.2f}",
        "cu": "INR",
        "tn": f"{invoice_id} {PLAN_TITLES.get(plan, plan)} {period}",
        "tr": invoice_id,
    }
    return "upi://pay?" + urlencode(params)

def create_dynamic_qr(amount: int, invoice_id: str, plan: str, period: str):
    """Create a plan-specific UPI QR. Falls back to the static QR if qrcode/Pillow is unavailable."""
    try:
        import qrcode
        uri = build_upi_uri(amount, invoice_id, plan, period)
        img = qrcode.make(uri)
        path = os.path.join(DYNAMIC_QR_DIR, f"{invoice_id}.png")
        img.save(path)
        return path, uri
    except Exception as e:
        print(f"Dynamic QR warning: {e}")
        return (PAYMENT_QR_FILE if os.path.exists(PAYMENT_QR_FILE) else None), None

def add_pending_payment(payment_id, user_id, plan, period, amount, utr, proof_file_id="", proof_type=""):
    now = int(time.time())
    utr = re.sub(r"\s+", "", utr).upper()
    with db_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM payments WHERE upper(utr)=?", (utr,)).fetchone():
            raise sqlite3.IntegrityError("UTR already submitted")
        con.execute(
            """INSERT INTO payments(
                   payment_id,user_id,plan,period,amount,utr,status,created_at,proof_file_id,proof_type
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (payment_id, user_id, plan, period, amount, utr, "pending", now, proof_file_id, proof_type),
        )
        con.commit()

def payment_by_id(payment_id):
    with db_conn() as con:
        row = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
    return dict(row) if row else None

def utr_exists(utr: str) -> bool:
    with db_conn() as con:
        row = con.execute("SELECT 1 FROM payments WHERE lower(utr)=lower(?)", (utr,)).fetchone()
    return bool(row)

def verify_payment_db(payment_id: str, admin_id: int):
    """Step 1: admin confirms that the bank/UPI transaction was checked."""
    if not is_admin(admin_id):
        return None
    now = int(time.time())
    with db_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not row or row["status"] != "pending":
            return None
        con.execute(
            "UPDATE payments SET status='verified',verified_at=?,verified_by=? WHERE payment_id=?",
            (now, admin_id, payment_id),
        )
        con.commit()
    payment = dict(row)
    payment["status"] = "verified"
    payment["verified_at"] = now
    payment["verified_by"] = admin_id
    return payment

def approve_payment_db(payment_id: str, admin_id: int):
    """Step 2: activate the plan. A pending payment cannot bypass Step 1."""
    if not is_admin(admin_id):
        return None
    now = int(time.time())
    with db_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not row or row["status"] != "verified":
            return None
        payment = dict(row)
        if PAYMENT_REQUIRE_DIFFERENT_ADMIN and payment["verified_by"] == admin_id:
            return None
        days = PERIOD_DAYS[payment["period"]]
        existing = con.execute(
            "SELECT plan,expires_at FROM subscriptions WHERE user_id=?",
            (payment["user_id"],),
        ).fetchone()
        base = now
        if existing and existing["expires_at"] > now and existing["plan"] == payment["plan"]:
            base = existing["expires_at"]
        expires = base + days * 86400
        con.execute(
            """INSERT INTO subscriptions(user_id,plan,expires_at,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
            (payment["user_id"], payment["plan"], expires, now),
        )
        con.execute(
            "UPDATE payments SET status='approved',reviewed_at=?,reviewed_by=? WHERE payment_id=?",
            (now, admin_id, payment_id),
        )
        con.commit()
    payment["expires_at"] = expires
    payment["reviewed_at"] = now
    payment["reviewed_by"] = admin_id
    payment["status"] = "approved"
    return payment

def reject_payment_db(payment_id: str, admin_id: int):
    if not is_admin(admin_id):
        return None
    now = int(time.time())
    with db_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not row or row["status"] not in {"pending", "verified"}:
            return None
        con.execute(
            "UPDATE payments SET status='rejected',reviewed_at=?,reviewed_by=? WHERE payment_id=?",
            (now, admin_id, payment_id),
        )
        con.commit()
    return dict(row)

def payment_admin_caption(payment) -> str:
    status = str(payment.get("status", "pending")).upper()
    verified_by = payment.get("verified_by")
    verify_line = f"\nStep 1 verified by: `{verified_by}`" if verified_by else ""
    return (
        "💳 **PAYMENT REVIEW**\n"
        f"Invoice: `{payment['payment_id']}`\n"
        f"User ID: `{payment['user_id']}`\n"
        f"Plan: **{PLAN_TITLES.get(payment['plan'], payment['plan'])}**\n"
        f"Period: **{payment['period'].title()}**\n"
        f"Amount: **₹{payment['amount']}**\n"
        f"UTR: `{payment['utr']}`\n"
        f"Status: **{status}**"
        f"{verify_line}\n\n"
        "⚠️ Check the real credit in your UPI/bank app. Screenshot/UTR alone is not proof."
    )

def payment_admin_keyboard(payment):
    payment_id = payment["payment_id"]
    if payment.get("status") == "verified":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Step 2 • Confirm & Activate", callback_data=f"payconfirm:{payment_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"payreject:{payment_id}")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Step 1 • Payment Verified", callback_data=f"payverify:{payment_id}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"payreject:{payment_id}")],
    ])

def admin_notification_targets():
    # Numeric IDs are authoritative. Username is only a fallback for delivery.
    return list(ADMIN_IDS)

async def notify_admins(text: str):
    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
            delivered = True
        except Exception as e:
            print(f"Admin notify failed {admin_id}: {e}")
    if not delivered and ADMIN_USERNAME:
        try:
            await bot.send_message(f"@{ADMIN_USERNAME}", text)
            delivered = True
        except Exception as e:
            print(f"Admin username fallback failed @{ADMIN_USERNAME}: {e}")
    return delivered

async def notify_admin_login(tg_user, method: str):
    if setting_get("login_alerts", "1") != "1":
        return
    username = f"@{tg_user.username}" if getattr(tg_user, "username", None) else "No username"
    await notify_admins(
        "🔐 User Login Alert\n"
        f"Name: {getattr(tg_user, 'first_name', '') or 'Telegram User'}\n"
        f"Username: {username}\n"
        f"User ID: `{tg_user.id}`\n"
        f"Method: {method}\n"
        "OTP / 2FA / Session String is never sent to admin."
    )

init_db()

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

server_userbot = (
    Client(
        "server_userbot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=STRING_SESSION,
        in_memory=True,
    )
    if STRING_SESSION
    else None
)

# ============================================================
# 4. RUNTIME STATE
# ============================================================
ACTIVE_TASKS = {}
DEPLOY_BUSY_FILE = os.environ.get("DEPLOY_BUSY_FILE", "/tmp/my-saver-bot.busy")

def _sync_deploy_busy_marker():
    """Tell the auto-deployer not to restart while clone/batch/single jobs are active."""
    busy = bool(globals().get("JOB_TASKS")) or any(bool(v) for v in ACTIVE_TASKS.values())
    try:
        if busy:
            with open(DEPLOY_BUSY_FILE, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        elif os.path.exists(DEPLOY_BUSY_FILE):
            os.remove(DEPLOY_BUSY_FILE)
    except OSError as e:
        print(f"Deploy busy marker warning: {e}")

def set_task_active(user_id: int, active: bool):
    if active:
        ACTIVE_TASKS[user_id] = True
    else:
        ACTIVE_TASKS.pop(user_id, None)
    _sync_deploy_busy_marker()

# Clear a stale marker from a previous crashed/restarted process.
try:
    if os.path.exists(DEPLOY_BUSY_FILE):
        os.remove(DEPLOY_BUSY_FILE)
except OSError:
    pass
PEER_CACHE_INITIALIZED = set()
USER_CLIENTS = {}
USER_CLIENT_LOCKS = {}
LOGIN_STATES = {}

def get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in USER_CLIENT_LOCKS:
        USER_CLIENT_LOCKS[user_id] = asyncio.Lock()
    return USER_CLIENT_LOCKS[user_id]

def phone_session_name(user_id: int) -> str:
    return f"user_{user_id}"

def phone_session_path(user_id: int) -> str:
    return os.path.join(SESSION_DIR, f"{phone_session_name(user_id)}.session")

def session_string_path(user_id: int) -> str:
    return os.path.join(SESSION_DIR, f"user_{user_id}.session_string")

def has_personal_session(user_id: int) -> bool:
    return os.path.exists(phone_session_path(user_id)) or os.path.exists(session_string_path(user_id))

def save_session_string(user_id: int, value: str):
    path = session_string_path(user_id)
    fd, temporary = tempfile.mkstemp(prefix="session-", dir=SESSION_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value.strip())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)

def load_session_string(user_id: int):
    path = session_string_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        return value or None
    except Exception:
        return None

def delete_user_session_files(user_id: int):
    candidates = [
        phone_session_path(user_id),
        phone_session_path(user_id) + "-journal",
        phone_session_path(user_id) + "-wal",
        phone_session_path(user_id) + "-shm",
        session_string_path(user_id),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

async def safe_delete_message(message: Message):
    try:
        await message.delete()
    except Exception:
        pass

# ============================================================
# 5. TELEGRAM LINK PARSER
# ============================================================
def extract_telegram_links(text: str):
    fixed_text = re.sub(r"(\d)(https://t\.me/)", r"\1 \2", text or "")
    return re.findall(r"https?://t\.me/[^\s]+", fixed_text)

def parse_telegram_link(url: str):
    parsed = urlsplit(url.strip().strip("<>"))
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "t.me":
        raise ValueError("Use a https://t.me/ message link")
    parts = parsed.path.strip("/").split("/")
    if parts and parts[0] == "s":
        parts = parts[1:]
    if not parts:
        raise ValueError("Message link required")
    if parts[0] == "c":
        if len(parts) not in {3, 4} or not parts[1].isdigit() or int(parts[1]) <= 0:
            raise ValueError("Invalid private message link")
        chat_id = int("-100" + parts[1])
        ids = parts[2:]
    else:
        if len(parts) not in {2, 3} or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", parts[0]):
            raise ValueError("Use a post link, not an invite/channel-only link")
        chat_id = parts[0].lower()
        ids = parts[1:]
    if any(not part.isdigit() or not 0 < int(part) <= 2147483647 for part in ids):
        raise ValueError("Message/topic IDs must be positive integers")
    return chat_id, int(ids[0]) if len(ids) == 2 else None, int(ids[-1])

# ============================================================
# 6. PER-USER LOGIN / CLIENT MANAGEMENT
# ============================================================
async def build_personal_client(user_id: int):
    saved_string = load_session_string(user_id)

    if saved_string:
        client = Client(
            f"user_string_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=saved_string,
            in_memory=True,
            no_updates=True,
        )
        await client.start()
        return client

    if os.path.exists(phone_session_path(user_id)):
        client = Client(
            phone_session_name(user_id),
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=SESSION_DIR,
            no_updates=True,
        )
        await client.start()
        return client

    return None

async def get_user_client(user_id: int):
    cached = USER_CLIENTS.get(user_id)
    if cached:
        try:
            await cached.get_me()
            return cached
        except Exception:
            try:
                await cached.stop()
            except Exception:
                pass
            USER_CLIENTS.pop(user_id, None)

    async with get_user_lock(user_id):
        cached = USER_CLIENTS.get(user_id)
        if cached:
            return cached

        if has_personal_session(user_id):
            try:
                client = await build_personal_client(user_id)
                if client:
                    USER_CLIENTS[user_id] = client
                    return client
            except Exception as e:
                print(f"⚠️ Personal session load failed for {user_id}: {e}")

        # Backward-compatible shared server session fallback.
        if server_userbot and (
            ALLOW_SHARED_SESSION or is_admin(user_id)
        ):
            return server_userbot

        return None

async def close_user_client(user_id: int):
    client = USER_CLIENTS.pop(user_id, None)
    if client:
        try:
            await client.stop()
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass

async def initialize_peer_cache(ub_client, cache_key, force=False):
    if cache_key in PEER_CACHE_INITIALIZED and not force:
        return

    try:
        async for _ in ub_client.get_dialogs(limit=0):
            pass
        PEER_CACHE_INITIALIZED.add(cache_key)
    except Exception as e:
        print(f"⚠️ Peer cache warning [{cache_key}]: {e}")

async def retry_flood(operation, *args, **kwargs):
    for attempt in range(4):
        try:
            return await operation(*args, **kwargs)
        except FloodWait as error:
            if attempt == 3:
                raise
            await asyncio.sleep(max(1, error.value))

async def fetch_message_safely(ub_client, cache_key, chat_id, msg_id, status_msg=None):
    try:
        return await retry_flood(ub_client.get_messages, chat_id, msg_id)
    except Exception as first_error:
        print(f"⚠️ Peer lookup retry [{cache_key}] chat={chat_id}: {first_error}")
        if status_msg:
            try:
                await status_msg.edit_text("🔄 Syncing Telegram chats...")
            except Exception:
                pass
        await initialize_peer_cache(ub_client, cache_key, force=True)
        return await retry_flood(ub_client.get_messages, chat_id, msg_id)

async def require_user_client(message: Message):
    user_id = message.chat.id
    ub = await get_user_client(user_id)
    if ub:
        return ub

    await message.reply_text(
        "🔐 Telegram login தேவை.\n\n"
        "`/login` அனுப்பி **Phone + OTP** அல்லது **Session String** மூலம் login செய்யுங்கள்.\n"
        "உங்கள் account-க்கு access உள்ள chats/content மட்டும் பயன்படுத்துங்கள்."
    )
    return None

# ============================================================
# 7. UNIVERSAL ORIGINAL-QUALITY CONTENT SENDER
#    Supports common Telegram content while preserving quality,
#    captions/entities and URL buttons where the Bot API permits.
# ============================================================
DOWNLOADABLE_MEDIA_ATTRS = (
    "audio",
    "video",
    "photo",
    "document",
    "voice",
    "animation",
    "sticker",
    "video_note",
)


def has_downloadable_media(msg: Message) -> bool:
    return any(getattr(msg, attr, None) is not None for attr in DOWNLOADABLE_MEDIA_ATTRS)


def _url_only_markup(msg: Message):
    """Preserve safe URL buttons; source-bot callback buttons cannot be reused."""
    markup = getattr(msg, "reply_markup", None)
    keyboard = getattr(markup, "inline_keyboard", None) if markup else None
    if not keyboard:
        return None

    rows = []
    for row in keyboard:
        safe_row = []
        for button in row:
            url = getattr(button, "url", None)
            text_value = getattr(button, "text", None) or "Open"
            if url:
                safe_row.append(InlineKeyboardButton(text_value, url=url))
        if safe_row:
            rows.append(safe_row)

    return InlineKeyboardMarkup(rows) if rows else None


async def send_text_original(bot_client, target_chat, target_msg, user_id=None):
    """Send text, preserving source entities when customization does not alter offsets."""
    if user_id is None:
        body = target_msg.text or target_msg.caption or ""
        changed = False
    else:
        body, changed = _effective_text(user_id, target_msg)

    if not body:
        return False

    kwargs = {"parse_mode": ParseMode.DISABLED}
    entities = getattr(target_msg, "entities", None)
    if entities and not changed:
        kwargs["entities"] = entities

    url_markup = _url_only_markup(target_msg)
    if url_markup:
        kwargs["reply_markup"] = url_markup

    try:
        if changed:
            kwargs["parse_mode"] = ParseMode.DISABLED
        await bot_client.send_message(
            target_chat,
            body,
            disable_web_page_preview=False,
            **kwargs,
        )
    except FloodWait:
        raise
    except RPCError as e:
        if "ENTITY" not in type(e).__name__.upper() and "MARKUP" not in type(e).__name__.upper():
            raise
        print(f"Text entity/markup fallback for {getattr(target_msg, 'id', '?')}: {e}")
        await bot_client.send_message(
            target_chat,
            body,
            disable_web_page_preview=False,
            parse_mode=ParseMode.DISABLED,
        )
    return True


async def send_non_file_content(bot_client, target_chat, target_msg, user_id=None):
    """Re-create common non-file Telegram content."""
    if target_msg.text:
        return await send_text_original(bot_client, target_chat, target_msg, user_id=user_id)

    contact = getattr(target_msg, "contact", None)
    if contact:
        await bot_client.send_contact(
            target_chat,
            phone_number=contact.phone_number,
            first_name=contact.first_name,
            last_name=contact.last_name or "",
            vcard=getattr(contact, "vcard", None),
        )
        return True

    venue = getattr(target_msg, "venue", None)
    if venue:
        loc = venue.location
        await bot_client.send_venue(
            target_chat,
            latitude=loc.latitude,
            longitude=loc.longitude,
            title=venue.title,
            address=venue.address,
        )
        return True

    location = getattr(target_msg, "location", None)
    if location:
        await bot_client.send_location(
            target_chat,
            latitude=location.latitude,
            longitude=location.longitude,
        )
        return True

    poll = getattr(target_msg, "poll", None)
    if poll:
        options = [opt.text for opt in (poll.options or [])]
        if len(options) >= 2:
            try:
                await bot_client.send_poll(
                    target_chat,
                    question=poll.question,
                    options=options,
                    is_anonymous=poll.is_anonymous,
                    type=poll.type,
                    allows_multiple_answers=poll.allows_multiple_answers,
                    correct_option_id=getattr(poll, "correct_option_id", None),
                    explanation=getattr(poll, "explanation", None),
                )
            except FloodWait:
                raise
            except Exception:
                # Closed/quiz/special poll metadata can be impossible to reproduce.
                summary = "📊 " + poll.question + "\n\n" + "\n".join(
                    f"• {option}" for option in options
                )
                await bot_client.send_message(target_chat, summary)
            return True

    dice = getattr(target_msg, "dice", None)
    if dice:
        await bot_client.send_dice(target_chat, emoji=dice.emoji)
        return True

    # Web-page media is represented by its text/link. If there is no text,
    # there is nothing portable to send.
    web_page = getattr(target_msg, "web_page", None)
    if web_page and target_msg.caption:
        return await send_text_original(bot_client, target_chat, target_msg, user_id=user_id)

    return False


async def send_media_original(
    bot_client,
    target_chat,
    target_msg,
    file_path,
    progress=None,
    user_id=None,
):
    if user_id is None:
        original_caption = target_msg.caption or ""
        caption = CUSTOM_CAPTION if CUSTOM_CAPTION else original_caption
        caption_changed = bool(CUSTOM_CAPTION)
        thumb_path = None
    else:
        caption, caption_changed = _effective_caption(user_id, target_msg)
        thumb_path = _effective_thumbnail(user_id)

    common = {}
    if progress is not None:
        common["progress"] = progress

    caption_entities = None
    if not caption_changed:
        caption_entities = getattr(target_msg, "caption_entities", None)

    caption_kwargs = {"parse_mode": ParseMode.DISABLED}
    if caption:
        caption_kwargs["caption"] = caption
    if caption_entities:
        caption_kwargs["caption_entities"] = caption_entities
    if caption_changed:
        caption_kwargs["parse_mode"] = ParseMode.DISABLED

    url_markup = _url_only_markup(target_msg)
    if url_markup:
        caption_kwargs["reply_markup"] = url_markup

    thumb_kwargs = {"thumb": thumb_path} if thumb_path else {}

    if target_msg.audio:
        audio_title, audio_artist, clean_card = _effective_audio(user_id, target_msg)
        if clean_card:
            caption_kwargs = {"parse_mode": ParseMode.DISABLED}
        await bot_client.send_audio(
            target_chat,
            file_path,
            duration=target_msg.audio.duration,
            performer=audio_artist,
            title=audio_title,
            **thumb_kwargs,
            **caption_kwargs,
            **common,
        )
    elif target_msg.video:
        await bot_client.send_video(
            target_chat,
            file_path,
            duration=target_msg.video.duration,
            width=target_msg.video.width,
            height=target_msg.video.height,
            supports_streaming=True,
            **thumb_kwargs,
            **caption_kwargs,
            **common,
        )
    elif target_msg.photo:
        await bot_client.send_photo(
            target_chat,
            file_path,
            **caption_kwargs,
            **common,
        )
    elif target_msg.animation:
        await bot_client.send_animation(
            target_chat,
            file_path,
            duration=target_msg.animation.duration,
            width=target_msg.animation.width,
            height=target_msg.animation.height,
            **caption_kwargs,
            **common,
        )
    elif target_msg.sticker:
        await bot_client.send_sticker(
            target_chat,
            file_path,
            **common,
        )
    elif target_msg.video_note:
        await bot_client.send_video_note(
            target_chat,
            file_path,
            duration=target_msg.video_note.duration,
            length=target_msg.video_note.length,
            **common,
        )
    elif target_msg.document:
        await bot_client.send_document(
            target_chat,
            file_path,
            **thumb_kwargs,
            **caption_kwargs,
            **common,
        )
    elif target_msg.voice:
        await bot_client.send_voice(
            target_chat,
            file_path,
            duration=target_msg.voice.duration,
            **caption_kwargs,
            **common,
        )
    else:
        await bot_client.send_document(
            target_chat,
            file_path,
            **thumb_kwargs,
            **caption_kwargs,
            **common,
        )

def _human_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


async def _safe_status_edit(status_msg, body: str):
    """Edit progress UI without allowing Telegram message editing to block file I/O."""
    try:
        await asyncio.wait_for(status_msg.edit_text(body), timeout=3)
    except Exception:
        pass


def _format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _progress_bar(pct, width=12):
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round(width * pct / 100.0))
    return "█" * filled + "░" * (width - filled)


def _media_display_name(msg):
    for attr in ("audio", "document", "video", "animation", "voice", "video_note", "sticker", "photo"):
        media = getattr(msg, attr, None)
        if media:
            name = getattr(media, "file_name", None)
            if name:
                return name[:60]
            if attr == "photo":
                return f"🖼 Photo • ID {getattr(msg, 'id', '?')}"
            if attr == "sticker":
                emoji = getattr(media, "emoji", "") or ""
                return f"🧩 Sticker {emoji}".strip()
            if attr == "video_note":
                return f"⭕ Video Note • ID {getattr(msg, 'id', '?')}"
            return f"{attr.replace('_', ' ').title()} • ID {getattr(msg, 'id', '?')}"
    if getattr(msg, "text", None):
        return "📝 Text / Link"
    return f"Message {getattr(msg, 'id', '?')}"


def _media_file_size(msg):
    for attr in ("audio", "document", "video", "animation", "voice", "photo", "sticker", "video_note"):
        media = getattr(msg, attr, None)
        if media:
            size = getattr(media, "file_size", None)
            if size:
                return int(size)
    return 0


def _batch_episode_progress_body(ctx, phase=None, current=0, total_bytes=0, speed=0.0):
    total_items = max(1, int(ctx.get("total", 1)))
    index = max(1, int(ctx.get("index", 1)))
    completed = max(0, int(ctx.get("completed_before", index - 1)))
    overall_pct = min(100.0, completed * 100.0 / total_items)

    file_name = str(ctx.get("file_name") or f"ID {ctx.get('msg_id', '?')}")
    expected_size = int(ctx.get("expected_size", 0) or 0)
    phase = phase or ctx.get("phase", "⏳ Preparing…")

    transfer = ""
    if total_bytes:
        pct = min(100.0, current * 100.0 / total_bytes)
        speed_text = f" • `{_human_bytes(speed)}/s`" if speed > 0 else ""
        transfer = (
            f"\n{_progress_bar(pct)} `{pct:.1f}%`\n"
            f"`{_human_bytes(current)}` / `{_human_bytes(total_bytes)}`{speed_text}"
        )
    elif expected_size:
        transfer = f"\nSize: `{_human_bytes(expected_size)}`"

    return (
        f"🎧 **EPISODE {index}/{total_items}**\n"
        f"ID: `{ctx.get('msg_id', '?')}`\n"
        f"📄 `{file_name}`\n"
        f"{phase}{transfer}\n\n"
        f"Overall completed: `{completed}/{total_items}` • `{overall_pct:.1f}%`\n"
        "Controls: `/status` `/pause` `/resume` `/cancel`"
    )


def _batch_overall_body(ctx, final=False, cancelled=False):
    total = max(1, int(ctx.get("total", 1)))
    processed = max(0, int(ctx.get("processed", 0)))
    pct = min(100.0, processed * 100.0 / total)
    elapsed = max(0.0, time.monotonic() - ctx.get("started", time.monotonic()))
    rate = (processed / elapsed * 60.0) if elapsed > 0 and processed else 0.0

    if final:
        title = "📋 **BATCH FINISHED — REVIEW COUNTS**"
    elif cancelled:
        title = "🛑 **BATCH CANCELLED**"
    else:
        title = "📦 **BATCH TASK PROGRESS**"

    return (
        f"{title}\n"
        f"{_progress_bar(pct)} `{pct:.1f}%`\n"
        f"Processed: `{processed}/{total}`\n"
        f"✅ Sent: `{ctx.get('success', 0)}`   "
        f"⏭ Skipped: `{ctx.get('skipped', 0)}`   "
        f"❌ Failed: `{ctx.get('failed', 0)}`\n"
        f"⏱ Time: `{_format_duration(elapsed)}`"
        + (f" • Avg: `{rate:.1f} msg/min`" if rate > 0 else "")
        + (f"\n📍 Current ID: `{ctx.get('current_id')}`" if ctx.get("current_id") else "")
        + ("\n\nReview processed/total and failed counts above." if final else "")
        + ("\n\nControls: `/status` `/pause` `/resume` `/cancel`" if not final and not cancelled else "")
    )


def batch_complete_keyboard():
    rows = [[
        InlineKeyboardButton("🔁 New Batch", callback_data="batch_new"),
        InlineKeyboardButton("🛒 Plans", callback_data="plans_menu"),
    ]]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def _update_batch_episode_progress(status_msg, ctx, phase=None):
    if not status_msg or not ctx:
        return
    ctx["ui_seq"] = int(ctx.get("ui_seq", 0)) + 1
    await _safe_status_edit(
        status_msg,
        _batch_episode_progress_body(ctx, phase=phase),
    )


def _clone_progress_body(ctx, phase=None, current=0, total_bytes=0, speed=0.0):
    total_items = max(1, int(ctx.get("total", 1)))
    index = max(1, int(ctx.get("index", 1)))
    overall_pct = min(100.0, index * 100.0 / total_items)
    started = ctx.get("started", time.monotonic())
    elapsed = max(0.0, time.monotonic() - started)

    # ETA is based on completed/visited message slots, so it stays meaningful
    # even when some Telegram message IDs are empty/skipped.
    completed_slots = max(0, int(ctx.get("processed_complete", index - 1)))
    eta_text = "Calculating…"
    if completed_slots > 0 and elapsed > 0 and completed_slots < total_items:
        rate = completed_slots / elapsed
        if rate > 0:
            eta_text = _format_duration((total_items - completed_slots) / rate)
    elif completed_slots >= total_items:
        eta_text = "0s"

    phase = phase or ctx.get("phase", "♻️ Processing")
    file_name = str(ctx.get("file_name") or f"ID {ctx.get('msg_id', '?')}")
    transfer_line = ""
    if total_bytes:
        pct = min(100.0, current * 100.0 / total_bytes)
        speed_text = f" • `{_human_bytes(speed)}/s`" if speed > 0 else ""
        transfer_line = (
            f"\n{_progress_bar(pct)} `{pct:.1f}%`\n"
            f"`{_human_bytes(current)}` / `{_human_bytes(total_bytes)}`{speed_text}"
        )

    return (
        "♻️ **FULL CLONE PROGRESS**\n"
        f"File: `{index}/{total_items}` • Overall `{overall_pct:.1f}%`\n"
        f"{_progress_bar(overall_pct)}\n"
        f"Current ID: `{ctx.get('msg_id', '?')}`\n"
        f"📄 `{file_name}`\n"
        f"{phase}{transfer_line}\n\n"
        f"✅ Sent: `{ctx.get('success', 0)}`   "
        f"⏭ Skipped: `{ctx.get('skipped', 0)}`   "
        f"❌ Failed: `{ctx.get('failed', 0)}`\n"
        f"⏱ Elapsed: `{_format_duration(elapsed)}` • ETA: `{eta_text}`\n\n"
        "Controls: `/status` `/pause` `/resume` `/cancel`"
    )


async def _update_clone_progress(status_msg, ctx, phase=None):
    if not status_msg or not ctx:
        return
    ctx["ui_seq"] = int(ctx.get("ui_seq", 0)) + 1
    await _safe_status_edit(status_msg, _clone_progress_body(ctx, phase=phase))


def make_transfer_progress(status_msg, phase: str, msg_id: int, heartbeat=None, progress_ctx=None):
    state = {
        "last_ui": 0.0,
        "editing": False,
        "last_bytes": 0,
        "last_speed_time": time.monotonic(),
        "speed": 0.0,
    }
    heartbeat = heartbeat if heartbeat is not None else {
        "last_progress": time.monotonic(),
        "current": 0,
        "total": 0,
    }

    async def progress(current, total):
        now = time.monotonic()

        if current != heartbeat.get("current", 0):
            heartbeat["last_progress"] = now
        heartbeat["current"] = current
        heartbeat["total"] = total

        dt = now - state["last_speed_time"]
        if dt >= 0.8:
            delta = max(0, current - state["last_bytes"])
            state["speed"] = delta / dt if dt else 0.0
            state["last_bytes"] = current
            state["last_speed_time"] = now

        if not status_msg:
            return
        if current < total and now - state["last_ui"] < TRANSFER_PROGRESS_INTERVAL:
            return
        if state["editing"]:
            return

        state["last_ui"] = now
        pct = (current * 100 / total) if total else 0

        if progress_ctx is not None:
            if progress_ctx.get("kind") == "batch_episode":
                body = _batch_episode_progress_body(
                    progress_ctx,
                    phase=phase,
                    current=current,
                    total_bytes=total,
                    speed=state["speed"],
                )
            else:
                body = _clone_progress_body(
                    progress_ctx,
                    phase=phase,
                    current=current,
                    total_bytes=total,
                    speed=state["speed"],
                )
            progress_ctx["ui_seq"] = int(progress_ctx.get("ui_seq", 0)) + 1
            seq = progress_ctx["ui_seq"]
        else:
            speed_text = f" • `{_human_bytes(state['speed'])}/s`" if state["speed"] > 0 else ""
            body = (
                f"{phase} • ID `{msg_id}`\n"
                f"{_progress_bar(pct)} `{pct:.1f}%`\n"
                f"`{_human_bytes(current)}` / `{_human_bytes(total)}`{speed_text}\n"
                "Controls: `/status` `/pause` `/resume` `/cancel`"
            )
            seq = None

        state["editing"] = True

        async def do_edit():
            try:
                # Prevent a delayed old progress edit from overwriting the newer
                # "file complete" or final clone summary card.
                if progress_ctx is not None and seq != progress_ctx.get("ui_seq"):
                    return
                await _safe_status_edit(status_msg, body)
            finally:
                state["editing"] = False

        asyncio.create_task(do_edit())

    return progress


async def _await_transfer_with_watchdog(coro, heartbeat, hard_timeout, label, msg_id):
    """Wait for a transfer while detecting no-byte-progress stalls."""
    task = asyncio.create_task(coro)
    started = time.monotonic()

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=3)
            if task in done:
                return await task

            now = time.monotonic()
            if now - started >= hard_timeout:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise asyncio.TimeoutError(
                    f"{label} hard timeout on message {msg_id}"
                )

            last_progress = heartbeat.get("last_progress", started)
            if now - last_progress >= MEDIA_STALL_TIMEOUT:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                current = heartbeat.get("current", 0)
                total = heartbeat.get("total", 0)
                raise asyncio.TimeoutError(
                    f"{label} stalled on message {msg_id}: "
                    f"{_human_bytes(current)}/{_human_bytes(total)} "
                    f"with no progress for {MEDIA_STALL_TIMEOUT}s"
                )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def transfer_media_original(ub, bot_client, dest_chat, target_msg, status_msg=None, progress_ctx=None, user_id=None):
    """Download + upload one media item with live progress, stall detection and retry."""
    attempts = TRANSFER_RETRIES + 1
    last_error = None

    for attempt in range(1, attempts + 1):
        file_path = None
        transfer_dir = tempfile.mkdtemp(prefix="savepro-transfer-")
        try:
            download_heartbeat = {
                "last_progress": time.monotonic(),
                "current": 0,
                "total": 0,
            }
            download_progress = (
                make_transfer_progress(
                    status_msg, "📥 Downloading", target_msg.id, download_heartbeat, progress_ctx
                )

            )

            file_path = await _await_transfer_with_watchdog(
                ub.download_media(target_msg, file_name=transfer_dir + os.sep, progress=download_progress),
                download_heartbeat,
                MEDIA_DOWNLOAD_TIMEOUT,
                "download",
                target_msg.id,
            )
            if not file_path:
                raise RuntimeError("Telegram returned no downloaded file")

            upload_heartbeat = {
                "last_progress": time.monotonic(),
                "current": 0,
                "total": 0,
            }
            upload_progress = (
                make_transfer_progress(
                    status_msg, "📤 Uploading", target_msg.id, upload_heartbeat, progress_ctx
                )

            )

            await _await_transfer_with_watchdog(
                send_media_original(
                    bot_client,
                    dest_chat,
                    target_msg,
                    file_path,
                    progress=upload_progress,
                    user_id=user_id,
                ),
                upload_heartbeat,
                MEDIA_UPLOAD_TIMEOUT,
                "upload",
                target_msg.id,
            )
            return True

        except asyncio.TimeoutError as e:
            last_error = e
            print(
                f"⚠️ Transfer watchdog: {e} "
                f"(attempt {attempt}/{attempts})"
            )
        except FloodWait:
            raise
        except Exception as e:
            last_error = e
            print(
                f"⚠️ Transfer failed for message {target_msg.id} "
                f"(attempt {attempt}/{attempts}): {e}"
            )
        finally:
            cleanup_file(file_path)
            shutil.rmtree(transfer_dir, ignore_errors=True)

        if attempt < attempts:
            try:
                if status_msg:
                    if progress_ctx is not None:
                        progress_ctx["phase"] = f"🔁 Retrying • attempt {attempt + 1}/{attempts}"
                        if progress_ctx.get("kind") == "batch_episode":
                            await _update_batch_episode_progress(status_msg, progress_ctx)
                        else:
                            await _update_clone_progress(status_msg, progress_ctx)
                    else:
                        await _safe_status_edit(
                            status_msg,
                            f"🔁 Retrying ID `{target_msg.id}` • "
                            f"attempt `{attempt + 1}/{attempts}`"
                        )
            except Exception:
                pass
            await asyncio.sleep(1)

    if last_error:
        print(f"❌ Giving up message {target_msg.id}: {last_error}")
        if status_msg:
            try:
                await _safe_status_edit(
                    status_msg,
                    f"⏭️ ID `{target_msg.id}` stalled/failed after retry.\n"
                    "Skipping this item and continuing the task…"
                )
            except Exception:
                pass
    return False

def cleanup_file(file_path):
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass

async def destination_for(user_id: int):
    return int(DUMP_CHANNEL) if DUMP_CHANNEL and is_admin(user_id) else user_id

# ============================================================
# 8. HOME / LOGIN UI
# ============================================================
# Jobs are detached from dispatcher workers so control commands remain responsive.
JOB_TASKS = {}
JOB_META = {}
JOB_GATES = {}
MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("MAX_CONCURRENT_JOBS", "4")))

def update_job(user_id, processed=0, sent=0, failed=0, last_message=None):
    meta = JOB_META.get(user_id)
    if not meta:
        return
    meta.update(processed=processed, sent=sent, failed=failed, last_message=last_message)
    with db_conn() as con:
        con.execute("UPDATE jobs SET processed=?,sent=?,failed=?,last_message=? WHERE job_id=?",
                    (processed, sent, failed, last_message, meta["job_id"]))

async def job_checkpoint(user_id):
    gate = JOB_GATES.get(user_id)
    if gate:
        await gate.wait()

def managed_job(kind):
    def decorate(fn):
        @functools.wraps(fn)
        async def launch(client, message, *args, **kwargs):
            uid = kwargs.get("user_id", args[0] if args else message.chat.id)
            # Do not create jobs for commands or input owned by another wizard.
            if kind == "single" and (
                (message.text or "").startswith("/") or
                any(uid in states for states in (BATCH_STATES, CUSTOMIZE_STATES, LOGIN_STATES))
            ):
                return
            if uid in JOB_TASKS:
                return await message.reply_text(tr(uid, "A job is active. /status /pause /resume /cancel", "வேலை நடக்கிறது. /status /pause /resume /cancel"))
            if len(JOB_TASKS) >= MAX_CONCURRENT_JOBS:
                return await message.reply_text(tr(uid, "Server is busy. Please retry shortly.", "Server busy. சிறிது நேரத்தில் முயற்சிக்கவும்."))
            job_id = secrets.token_hex(6)
            JOB_META[uid] = {"job_id": job_id, "kind": kind, "processed": 0, "sent": 0, "failed": 0}
            gate = asyncio.Event()
            gate.set()
            JOB_GATES[uid] = gate
            with db_conn() as con:
                con.execute("INSERT INTO jobs(job_id,user_id,kind,status,started_at) VALUES(?,?,?,?,?)",
                            (job_id, uid, kind, "running", int(time.time())))
            async def runner():
                outcome = "finished"
                try:
                    await fn(client, message, *args, **kwargs)
                    meta = JOB_META[uid]
                    outcome = meta.get("outcome", "finished_with_errors" if meta.get("failed") else
                                       "finished" if meta.get("sent") else "no_items_sent")
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    try:
                        await message.reply_text(tr(uid, "🛑 Job stopped. /history shows recorded results.", "🛑 வேலை நிறுத்தப்பட்டது. முடிவுகளை /history மூலம் பார்க்கலாம்."))
                    except Exception:
                        pass
                except Exception as exc:
                    outcome = "failed"
                    print(f"Job {job_id} failed: {type(exc).__name__}")
                    try:
                        await message.reply_text("❌ Job failed. Use /history or /support.")
                    except Exception:
                        pass
                finally:
                    try:
                        with db_conn() as con:
                            con.execute("UPDATE jobs SET status=?,finished_at=? WHERE job_id=?",
                                        (outcome, int(time.time()), job_id))
                    finally:
                        JOB_TASKS.pop(uid, None)
                        JOB_META.pop(uid, None)
                        JOB_GATES.pop(uid, None)
                        set_task_active(uid, False)
            JOB_TASKS[uid] = asyncio.create_task(runner(), name=f"savepro:{job_id}")
            def release_unstarted(task):
                if JOB_TASKS.get(uid) is task:
                    try:
                        with db_conn() as con:
                            con.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE job_id=?", (int(time.time()), job_id))
                    finally:
                        JOB_TASKS.pop(uid, None)
                        JOB_META.pop(uid, None)
                        JOB_GATES.pop(uid, None)
                        set_task_active(uid, False)
            JOB_TASKS[uid].add_done_callback(release_unstarted)
            _sync_deploy_busy_marker()
        return launch
    return decorate

@bot.on_message(filters.command(["pause", "resume", "status"]) & filters.private)
async def job_control_cmd(client, message: Message):
    uid = message.chat.id
    meta = JOB_META.get(uid)
    if not meta:
        return await message.reply_text(tr(uid, "No active job. Start with /batch or send a link.", "வேலை இல்லை. /batch அல்லது link அனுப்பவும்."))
    command = message.command[0].lower()
    gate = JOB_GATES[uid]
    if command in {"pause", "resume"}:
        gate.clear() if command == "pause" else gate.set()
        with db_conn() as con:
            con.execute("UPDATE jobs SET status=? WHERE job_id=?", ("paused" if command == "pause" else "running", meta["job_id"]))
        return await message.reply_text(tr(uid,
            "⏸ Pause requested after current message. /resume to continue." if command == "pause" else "▶ Job resumed.",
            "⏸ தற்போதைய message முடிந்ததும் pause ஆகும். தொடர /resume." if command == "pause" else "▶ வேலை தொடர்கிறது."))
    await message.reply_text(
        f"📊 {meta['kind'].title()} • {'Running' if gate.is_set() else 'Pause requested'}\n"
        f"Processed: {meta['processed']} • Sent: {meta['sent']} • Failed: {meta['failed']}\n"
        "/pause /resume /cancel\nPause takes effect between messages."
    )

@bot.on_message(filters.command("history") & filters.private)
async def history_cmd(client, message: Message):
    with db_conn() as con:
        rows = con.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY started_at DESC,rowid DESC LIMIT 10", (message.chat.id,)).fetchall()
    lines = ["🕘 Recent jobs / சமீபத்திய வேலைகள்"]
    for row in rows:
        stamp = datetime.fromtimestamp(row['started_at'], timezone.utc).strftime('%d %b %H:%M UTC')
        lines.append(f"{stamp} • {row['kind']} • {row['status']}\nSent {row['sent']} / processed {row['processed']} • failed {row['failed']} • last ID {row['last_message'] or '-'}")
    await message.reply_text("\n\n".join(lines) if rows else "No job history yet / வேலை வரலாறு இல்லை.")

@bot.on_message(filters.command("payments") & filters.private)
async def payments_cmd(client, message: Message):
    with db_conn() as con:
        rows = con.execute("SELECT payment_id,amount,status FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (message.chat.id,)).fetchall()
    await message.reply_text("🧾 Payment status\n\n" + ("\n".join(
        f"{row['payment_id']} • ₹{row['amount']} • {row['status']}" for row in rows
    ) or "No submitted payments."), reply_markup=support_markup())


def owns_input(state_name):
    def decorate(fn):
        @functools.wraps(fn)
        async def handle(client, message):
            uid = message.chat.id
            text = (message.text or "").strip()
            if text.startswith("/") or text == "🆘 Support / Contact Admin":
                return
            state = globals()[state_name].get(uid)
            if not state:
                return
            if state_name == "PAYMENT_STATES":
                if uid in LOGIN_STATES or uid in CUSTOMIZE_STATES or uid in BATCH_STATES:
                    return
                if extract_telegram_links(text):
                    PAYMENT_STATES.pop(uid, None)
                    return
                if state.get("step") not in {"utr", "screenshot"}:
                    return
            try:
                await fn(client, message)
            finally:
                message.stop_propagation()
        return handle
    return decorate

def login_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 Phone + OTP", callback_data="login_phone"),
            InlineKeyboardButton("🔑 Session String", callback_data="login_session"),
        ],
        [
            InlineKeyboardButton("✅ Login Status", callback_data="login_status"),
            InlineKeyboardButton("🚪 Logout", callback_data="login_logout"),
        ],
    ])

@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)
    CUSTOMIZE_STATES.pop(user_id, None)
    keyboard_rows = [
        [
            InlineKeyboardButton("🔐 Login", callback_data="login_menu"),
            InlineKeyboardButton("🛒 Plans", callback_data="plans_menu"),
        ],
        [
            InlineKeyboardButton("📊 My Plan", callback_data="my_plan"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu"),
        ],
        [
            InlineKeyboardButton("🌐 English / தமிழ்", callback_data="language_menu"),
            InlineKeyboardButton("📁 Supported Content", callback_data="formats_menu"),
        ],
        [
            InlineKeyboardButton("⚡ Quick Start", callback_data="quick_start"),
            InlineKeyboardButton("📘 User Manual", callback_data="manual_menu"),
        ],
        [
            InlineKeyboardButton("📦 Batch Help", callback_data="help_batch"),
            InlineKeyboardButton("♻️ Full Clone Help", callback_data="help_clone"),
        ],
        [InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")],
    ]
    row = support_row()
    if row:
        keyboard_rows.append(row)
    if is_admin(user_id):
        keyboard_rows.append([InlineKeyboardButton("🛡 Admin Panel", callback_data="admin_panel")])

    if get_language(user_id) == "ta":
        text = (
            "🤖 **Pro Max Saver Bot**\n\n"
            "✨ **உங்களுக்கு authorized access உள்ள Telegram content-ஐ save செய்யுங்கள்**\n"
            "• Photo / Video / Audio / Documents / Stickers\n"
            "• Text + clickable links / common Telegram content\n"
            "• Single • Batch • Range • Full Clone\n"
            "• Phone + OTP / Session String login\n"
            "• Dynamic UPI QR subscription payment\n\n"
            "📦 `/batch <start_link> 100`\n"
            "♻️ `/clone <start_link>`\n"
            "🛒 `/plans`\n\n"
            "🔒 உங்கள் Telegram account-க்கு access உள்ள chats/content மட்டும் பயன்படுத்துங்கள்."
        )
    else:
        text = (
            "🤖 **Pro Max Saver Bot**\n\n"
            "✨ **Save the Telegram content you are authorized to access**\n"
            "• Photo / Video / Audio / Documents / Stickers\n"
            "• Text + clickable links / common Telegram content\n"
            "• Single • Batch • Range • Full Clone\n"
            "• Phone + OTP / Session String login\n"
            "• Dynamic UPI QR subscription payment\n\n"
            "📦 `/batch <start_link> 100`\n"
            "♻️ `/clone <start_link>`\n"
            "🛒 `/plans`\n\n"
            "🔒 Use only chats/content your Telegram account is authorized to access."
        )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))
    await message.reply_text(
        "🆘 Support button is always available below.",
        reply_markup=persistent_support_keyboard(),
    )

@bot.on_message(filters.command("login") & filters.private)
async def login_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(
        "🔐 **Telegram Login**\n\n"
        "ஒரு method தேர்வு செய்யுங்கள்.\n"
        "OTP / 2FA password messages process ஆனதும் delete செய்யப்படும்.",
        reply_markup=login_keyboard(),
    )

@bot.on_message(filters.command("loginstatus") & filters.private)
async def login_status_cmd(client, message: Message):
    user_id = message.chat.id
    personal = has_personal_session(user_id)

    if personal:
        ub = await get_user_client(user_id)
        if ub:
            try:
                me = await ub.get_me()
                name = me.first_name or "Telegram User"
                username = f"@{me.username}" if me.username else "No username"
                return await message.reply_text(
                    f"✅ **Personal login active**\n{name} • {username}"
                )
            except Exception:
                pass

    if server_userbot and (
        ALLOW_SHARED_SESSION or is_admin(user_id)
    ):
        return await message.reply_text("✅ Shared server session active.")

    await message.reply_text("❌ Login இல்லை. `/login` பயன்படுத்துங்கள்.")

@bot.on_message(filters.command("logout") & filters.private)
async def logout_cmd(client, message: Message):
    user_id = message.chat.id
    if user_id in JOB_TASKS:
        return await message.reply_text("Use /cancel and wait for the job to stop first.")
    LOGIN_STATES.pop(user_id, None)
    await close_user_client(user_id)
    delete_user_session_files(user_id)
    PEER_CACHE_INITIALIZED.discard(f"user:{user_id}")
    await message.reply_text("🚪 Personal Telegram login logout செய்யப்பட்டது.")

@bot.on_message(filters.command("language") & filters.private)
async def language_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(
        "🌐 Choose language / மொழியைத் தேர்வு செய்யுங்கள்:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
            InlineKeyboardButton("🇮🇳 தமிழ்", callback_data="lang:ta"),
        ]]),
    )

@bot.on_message(filters.command("manual") & filters.private)
async def manual_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(manual_text(message.chat.id), reply_markup=upgrade_markup(message.chat.id))

@bot.on_message(filters.command("formats") & filters.private)
async def formats_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(formats_text(message.chat.id), reply_markup=upgrade_markup(message.chat.id))

@bot.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)
    CUSTOMIZE_STATES.pop(user_id, None)
    await message.reply_text(
        customization_summary(user_id),
        reply_markup=settings_keyboard(user_id),
    )

@bot.on_message(filters.command("plans") & filters.private)
async def plans_cmd(client, message: Message):
    touch_user(message)
    PAYMENT_STATES.pop(message.chat.id, None)
    BATCH_STATES.pop(message.chat.id, None)
    CUSTOMIZE_STATES.pop(message.chat.id, None)
    if setting_get("payments_enabled", "1") != "1" and not is_admin(message.chat.id):
        return await message.reply_text(tr(message.chat.id, "⚠️ Payments are temporarily disabled.", "⚠️ Payment தற்காலிகமாக நிறுத்தப்பட்டுள்ளது."))
    await message.reply_text(plan_text(message.chat.id), reply_markup=plans_keyboard())

@bot.on_message(filters.command("myplan") & filters.private)
async def myplan_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(subscription_text(message.chat.id))

@bot.on_message(
    filters.private & filters.regex(r"^🆘 Support / Contact Admin$"),
    group=-6,
)
async def support_button_handler(client, message: Message):
    user_id = message.chat.id
    if SUPPORT_USERNAME:
        await message.reply_text(
            tr(
                user_id,
                f"🆘 **Support / Contact Admin**\n\nAdmin: @{SUPPORT_USERNAME}",
                f"🆘 **Support / Contact Admin**\n\nAdmin: @{SUPPORT_USERNAME}",
            ),
            reply_markup=support_markup(),
        )
    else:
        await message.reply_text("⚠️ Support username is not configured.")

@bot.on_message(filters.command("support") & filters.private)
async def support_cmd(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    # Support should never be interpreted as payment UTR input.
    if SUPPORT_USERNAME:
        await message.reply_text(
            tr(
                user_id,
                f"🆘 **Support / Contact Admin**\n\nAdmin: @{SUPPORT_USERNAME}\nTap the button below to message support.",
                f"🆘 **Support / Contact Admin**\n\nAdmin: @{SUPPORT_USERNAME}\nகீழே உள்ள button-ஐ அழுத்தி support-ஐ தொடர்பு கொள்ளுங்கள்.",
            ),
            reply_markup=support_markup(),
        )
    else:
        await message.reply_text("⚠️ Support username is not configured.")

@bot.on_message(filters.command("admin") & filters.private)
async def admin_cmd(client, message: Message):
    touch_user(message)
    if not is_admin(message.chat.id):
        return await message.reply_text("⛔ Admin only.")
    await show_admin_panel(message.chat.id)

@bot.on_message(filters.command("setprice") & filters.private)
async def setprice_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 4:
        return await message.reply_text("Usage: `/setprice basic monthly 49`")
    _, plan, period, amount = parts
    plan, period = plan.lower(), period.lower()
    if plan not in DEFAULT_PLAN_PRICES or period not in PERIOD_DAYS or not amount.isdigit():
        return await message.reply_text("❌ Invalid plan/period/amount.")
    setting_set(f"price.{plan}.{period}", int(amount))
    await message.reply_text(f"✅ {plan} {period} price = ₹{amount}")

@bot.on_message(filters.command("setlimit") & filters.private)
async def setlimit_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply_text("Usage: `/setlimit premium 100`  (0 = unlimited)")
    _, plan, limit = parts
    plan = plan.lower()
    if plan not in DEFAULT_PLAN_LIMITS or not re.fullmatch(r"\d+", limit):
        return await message.reply_text("❌ Invalid plan/limit.")
    setting_set(f"limit.{plan}", int(limit))
    await message.reply_text(f"✅ {plan} daily limit = {limit}")

@bot.on_message(filters.command("grant") & filters.private)
async def grant_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 4:
        return await message.reply_text("Usage: `/grant USER_ID premium 30`")
    _, uid, plan, days = parts
    if not uid.isdigit() or plan not in DEFAULT_PLAN_PRICES or not days.isdigit():
        return await message.reply_text("❌ Invalid USER_ID / plan / days.")
    uid, days = int(uid), int(days)
    now = int(time.time())
    expires = now + days * 86400
    with db_conn() as con:
        con.execute(
            """INSERT INTO subscriptions(user_id,plan,expires_at,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
            (uid, plan, expires, now),
        )
        con.commit()
    await message.reply_text(f"✅ User `{uid}` → {plan} for {days} days.")
    try:
        await bot.send_message(uid, f"✅ Admin activated **{PLAN_TITLES[plan]}** for {days} days.")
    except Exception:
        pass

@bot.on_message(filters.command("revoke") & filters.private)
async def revoke_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.reply_text("Usage: `/revoke USER_ID`")
    uid = int(parts[1])
    with db_conn() as con:
        con.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        con.commit()
    await message.reply_text(f"✅ Subscription revoked for `{uid}`.")

@bot.on_message(filters.command("ban") & filters.private)
async def ban_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.reply_text("Usage: `/ban USER_ID`")
    uid = int(parts[1])
    now = int(time.time())
    with db_conn() as con:
        con.execute("INSERT OR IGNORE INTO users(user_id,created_at,last_seen) VALUES(?,?,?)", (uid, now, now))
        con.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
        con.commit()
    await message.reply_text(f"🚫 User `{uid}` banned.")

@bot.on_message(filters.command("unban") & filters.private)
async def unban_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.reply_text("Usage: `/unban USER_ID`")
    uid = int(parts[1])
    with db_conn() as con:
        con.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
        con.commit()
    await message.reply_text(f"✅ User `{uid}` unbanned.")

@bot.on_message(filters.command("user") & filters.private)
async def admin_user_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.reply_text("Usage: `/user USER_ID`")
    uid = int(parts[1])
    with db_conn() as con:
        u = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        sub = con.execute("SELECT * FROM subscriptions WHERE user_id=?", (uid,)).fetchone()
    if not u:
        return await message.reply_text("❌ User not found.")
    subtext = "No paid plan"
    if sub:
        subtext = f"{sub['plan']} until {datetime.fromtimestamp(sub['expires_at'], timezone.utc).strftime('%d-%m-%Y')}"
    await message.reply_text(
        f"👤 User `{uid}`\n@{u['username'] or '-'} • {u['first_name'] or '-'}\n"
        f"Language: {u['language']}\nBanned: {bool(u['banned'])}\n"
        f"Used today: {u['daily_used']}\nSubscription: {subtext}"
    )

@bot.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client, message: Message):
    if not is_admin(message.chat.id):
        return
    text = message.text.split(maxsplit=1)
    if len(text) < 2:
        return await message.reply_text("Usage: `/broadcast your message`")
    with db_conn() as con:
        ids = [r[0] for r in con.execute("SELECT user_id FROM users WHERE banned=0").fetchall()]
    sent = failed = 0
    status = await message.reply_text(f"📣 Broadcasting to {len(ids)} users...")
    for uid in ids:
        try:
            await bot.send_message(uid, text[1])
            sent += 1
            await asyncio.sleep(0.05)
        except FloodWait as fw:
            await asyncio.sleep(fw.value)
        except Exception:
            failed += 1
    await status.edit_text(f"✅ Broadcast done. Sent: {sent} • Failed: {failed}")

async def show_admin_panel(chat_id: int):
    if not is_admin(chat_id):
        return
    now = int(time.time())
    with db_conn() as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM subscriptions WHERE expires_at>?", (now,)).fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM payments WHERE status IN ('pending','verified')").fetchone()[0]
    payments = setting_get("payments_enabled", "1") == "1"
    maintenance = setting_get("maintenance", "0") == "1"
    text = (
        "🛡 **ADMIN PANEL**\n\n"
        f"👥 Users: `{users}`\n"
        f"💳 Active subscriptions: `{active}`\n"
        f"🧾 Pending payments: `{pending}`\n"
        f"💰 Payments: `{'ON' if payments else 'OFF'}`\n"
        f"🛠 Maintenance: `{'ON' if maintenance else 'OFF'}`\n\n"
        "Commands:\n"
        "`/setprice basic monthly 49`\n"
        "`/setlimit standard 50`\n"
        "`/grant USER_ID premium 30`\n"
        "`/revoke USER_ID`\n"
        "`/user USER_ID` • `/ban USER_ID` • `/unban USER_ID`\n"
        "`/broadcast message`"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧾 Pending", callback_data="admin_pending"),
            InlineKeyboardButton("💵 Plans", callback_data="admin_plans"),
        ],
        [
            InlineKeyboardButton("💰 Toggle Payments", callback_data="admin_toggle_payments"),
            InlineKeyboardButton("🛠 Toggle Maintenance", callback_data="admin_toggle_maintenance"),
        ],
        [InlineKeyboardButton("🔔 Toggle Login Alerts", callback_data="admin_toggle_login_alerts")],
    ])
    await bot.send_message(chat_id, text, reply_markup=kb)

@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    user_id = query.from_user.id
    touch_query_user(query)
    if query.data in {"login_phone", "login_session", "login_logout"} and user_id in JOB_TASKS:
        return await query.answer("Use /cancel and wait for the job to stop first.", show_alert=True)
    if query.data in {"login_phone", "login_session"}:
        old = LOGIN_STATES.pop(user_id, None)
        if old and old.get("client"):
            await old["client"].disconnect()
        PAYMENT_STATES.pop(user_id, None)
        CUSTOMIZE_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)

    if query.data == "language_menu":
        await query.message.reply_text(
            "🌐 Choose language / மொழியைத் தேர்வு செய்யுங்கள்:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
                InlineKeyboardButton("🇮🇳 தமிழ்", callback_data="lang:ta"),
            ]]),
        )
        return

    if query.data.startswith("lang:"):
        lang = query.data.split(":", 1)[1]
        if lang not in {"en", "ta"}:
            return await query.answer("Invalid language", show_alert=True)
        set_language(user_id, lang)
        await query.answer("Language updated ✅", show_alert=False)
        await query.message.reply_text(
            "✅ Language: English" if lang == "en" else "✅ மொழி: தமிழ்"
        )
        return

    if query.data == "settings_home":
        await query.answer()
        await query.message.reply_text(
            "🏠 Use /start for the full home menu.",
            reply_markup=upgrade_markup(user_id),
        )
        return

    if query.data == "settings_menu":
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        CUSTOMIZE_STATES.pop(user_id, None)
        await query.answer()
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data.startswith("cust:"):
        if user_id in LOGIN_STATES:
            return await query.answer("Finish login or use /cancel first.", show_alert=True)
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)

    if query.data.startswith("cust:audio"):
        if not customization_allowed(user_id, "audio"):
            return await query.answer("Premium plan or above required", show_alert=True)
        action = query.data.split(":", 1)[1]
        if action in {"audio_artist", "audio_title"}:
            CUSTOMIZE_STATES[user_id] = {"step": action}
            await query.answer()
            prompt = tr(user_id,
                "Send the artist/brand line, e.g. @YourChannel or your series name. No braces needed.",
                "Audio பெயருக்குக் கீழே வரவேண்டிய பெயரை அனுப்புங்கள். உதாரணம்: @YourChannel அல்லது சிறகடிக்க ஆசை. Braces தேவையில்லை.") if action == "audio_artist" else tr(user_id,
                "Send a title, or use {title} for the source title and {stem} for the filename without extension. /cancel to stop.",
                "Audio title அனுப்புங்கள். பழைய title சேர்க்க {title}; filename சேர்க்க {stem}. ரத்து செய்ய /cancel.")
            await query.message.reply_text(prompt, parse_mode=ParseMode.DISABLED)
            return
        CUSTOMIZE_STATES.pop(user_id, None)
        if action == "audio_filename":
            set_user_customization(user_id, audio_title="{stem}")
        elif action == "audio_clean":
            prefs = get_user_customization(user_id)
            set_user_customization(user_id, audio_clean_card=0 if prefs.get("audio_clean_card") else 1)
        elif action == "audio_reset":
            set_user_customization(user_id, audio_artist="", audio_title="", audio_clean_card=0)
        elif action != "audio":
            return await query.answer("Invalid audio option", show_alert=True)
        await query.answer()
        await query.message.reply_text(
            tr(user_id,
               "🎧 AUDIO TITLE / ARTIST\nArtist appears under the audio title, like your sample. Clean card hides audio captions and source buttons. Other media keep their caption settings. Changes apply to new sends.",
               "🎧 AUDIO TITLE / ARTIST\nSample போல audio பெயருக்குக் கீழே உங்கள் பெயர் வரும். Clean card ON செய்தால் audio caption/buttons மறையும். புதிய sends-க்கு மட்டும் மாற்றம் பொருந்தும்."),
            reply_markup=audio_settings_keyboard(user_id))
        return

    if query.data == "cust:preview":
        await query.answer()
        await query.message.reply_text(
            customization_preview_text(user_id),
            parse_mode=ParseMode.DISABLED,
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:reset":
        CUSTOMIZE_STATES.pop(user_id, None)
        reset_user_customization(user_id)
        await query.answer("Settings reset ✅", show_alert=False)
        await query.message.reply_text(
            "✅ Caption, watermark, thumbnail and text settings reset to default.",
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:caption":
        CUSTOMIZE_STATES.pop(user_id, None)
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        if not customization_allowed(user_id, "caption"):
            await query.answer("Basic plan or above required", show_alert=True)
            await query.message.reply_text(
                customization_locked_text(user_id, "caption"),
                reply_markup=upgrade_markup(user_id),
            )
            return
        prefs = get_user_customization(user_id)
        await query.answer()
        await query.message.reply_text(
            "✍️ **CUSTOM CAPTION**\n\n"
            f"Current mode: `{prefs.get('caption_mode', 'original')}`\n"
            "• Original = keep source caption\n"
            "• Replace = use your caption only\n"
            "• Append = original + your caption\n"
            "• No Caption = remove caption\n\n"
            "Send plain text or @YourChannel — no braces needed. Optional placeholders: `{filename}` `{id}` `{date}` `{original}`",
            reply_markup=caption_settings_keyboard(),
        )
        return

    if query.data.startswith("cust:caption_mode:"):
        if not customization_allowed(user_id, "caption"):
            return await query.answer("Basic plan or above required", show_alert=True)
        mode = query.data.rsplit(":", 1)[1]
        if mode not in {"original", "replace", "append", "remove"}:
            return await query.answer("Invalid caption mode", show_alert=True)
        set_user_customization(user_id, caption_mode=mode)
        await query.answer(f"Caption mode: {mode} ✅")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:caption_set":
        if not customization_allowed(user_id, "caption"):
            return await query.answer("Basic plan or above required", show_alert=True)
        CUSTOMIZE_STATES[user_id] = {"step": "caption_text"}
        await query.answer()
        await query.message.reply_text(
            "✏️ Send your new custom caption now.\n\n"
            "Supported placeholders:\n"
            "`{filename}` • `{id}` • `{date}` • `{original}`\n\n"
            "Maximum 900 characters. Send `/cancel` to abort."
        )
        return

    if query.data == "cust:watermark":
        CUSTOMIZE_STATES.pop(user_id, None)
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        if not customization_allowed(user_id, "watermark"):
            await query.answer("Premium plan or above required", show_alert=True)
            await query.message.reply_text(
                customization_locked_text(user_id, "watermark"),
                reply_markup=upgrade_markup(user_id),
            )
            return
        prefs = get_user_customization(user_id)
        await query.answer()
        await query.message.reply_text(
            "🏷 **FAST TEXT WATERMARK**\n\n"
            f"Status: `{'ON' if int(prefs.get('watermark_enabled') or 0) else 'OFF'}`\n"
            f"Text: `{(prefs.get('watermark_text') or 'Not set')[:250]}`\n\n"
            "This watermark is added to captions/text. It does **not** re-encode photos/videos, so original media quality and transfer speed are preserved.",
            reply_markup=watermark_settings_keyboard(user_id),
        )
        return

    if query.data == "cust:watermark_set":
        if not customization_allowed(user_id, "watermark"):
            return await query.answer("Premium plan or above required", show_alert=True)
        CUSTOMIZE_STATES[user_id] = {"step": "watermark_text"}
        await query.answer()
        await query.message.reply_text(
            "🏷 Send the watermark text now.\n"
            "Example: `📢 @YourChannel`\n"
            "Maximum 180 characters. `/cancel` to abort."
        )
        return

    if query.data == "cust:watermark_toggle":
        if not customization_allowed(user_id, "watermark"):
            return await query.answer("Premium plan or above required", show_alert=True)
        prefs = get_user_customization(user_id)
        if not prefs.get("watermark_text"):
            return await query.answer("Set watermark text first.", show_alert=True)
        new_value = 0 if int(prefs.get("watermark_enabled") or 0) else 1
        set_user_customization(user_id, watermark_enabled=new_value)
        await query.answer("Watermark ON ✅" if new_value else "Watermark OFF")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:watermark_remove":
        set_user_customization(user_id, watermark_text="", watermark_enabled=0)
        await query.answer("Watermark removed ✅")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:thumbnail":
        CUSTOMIZE_STATES.pop(user_id, None)
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        if not customization_allowed(user_id, "thumbnail"):
            await query.answer("Standard plan or above required", show_alert=True)
            await query.message.reply_text(
                customization_locked_text(user_id, "thumbnail"),
                reply_markup=upgrade_markup(user_id),
            )
            return
        prefs = get_user_customization(user_id)
        thumb_set = bool(prefs.get("thumbnail_path") and os.path.exists(prefs.get("thumbnail_path")))
        await query.answer()
        await query.message.reply_text(
            "🖼 **CUSTOM THUMBNAIL**\n\n"
            f"Current: `{'SET ✅' if thumb_set else 'DEFAULT'}`\n"
            "Applied to supported Audio / Video / Document uploads.\n"
            "The bot converts your image into a Telegram-compatible JPEG thumbnail.",
            reply_markup=thumbnail_settings_keyboard(),
        )
        return

    if query.data == "cust:thumbnail_set":
        if not customization_allowed(user_id, "thumbnail"):
            return await query.answer("Standard plan or above required", show_alert=True)
        CUSTOMIZE_STATES[user_id] = {"step": "thumbnail"}
        await query.answer()
        await query.message.reply_text(
            "🖼 Send the new thumbnail as a **photo** or image document now.\n"
            "Recommended: square image. The bot will resize it safely.\n"
            "Send `/cancel` to abort."
        )
        return

    if query.data == "cust:thumbnail_remove":
        remove_user_thumbnail(user_id)
        await query.answer("Thumbnail removed ✅")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:text":
        CUSTOMIZE_STATES.pop(user_id, None)
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        if not customization_allowed(user_id, "text"):
            await query.answer("Premium plan or above required", show_alert=True)
            await query.message.reply_text(
                customization_locked_text(user_id, "text"),
                reply_markup=upgrade_markup(user_id),
            )
            return
        prefs = get_user_customization(user_id)
        await query.answer()
        await query.message.reply_text(
            "📝 **TEXT SETTINGS**\n\n"
            f"Original text: `{'ON' if int(prefs.get('keep_original_text') or 0) else 'OFF'}`\n"
            f"Prefix: `{(prefs.get('text_prefix') or 'Not set')[:180]}`\n"
            f"Suffix: `{(prefs.get('text_suffix') or 'Not set')[:180]}`\n\n"
            "Useful for adding channel name, credits, or a short footer to text/link posts.",
            reply_markup=text_settings_keyboard(user_id),
        )
        return

    if query.data == "cust:text_prefix":
        if not customization_allowed(user_id, "text"):
            return await query.answer("Premium plan or above required", show_alert=True)
        CUSTOMIZE_STATES[user_id] = {"step": "text_prefix"}
        await query.answer()
        await query.message.reply_text(
            "➕ Send the text prefix now. Maximum 500 characters.\n"
            "Send `/cancel` to abort."
        )
        return

    if query.data == "cust:text_suffix":
        if not customization_allowed(user_id, "text"):
            return await query.answer("Premium plan or above required", show_alert=True)
        CUSTOMIZE_STATES[user_id] = {"step": "text_suffix"}
        await query.answer()
        await query.message.reply_text(
            "➕ Send the text suffix now. Maximum 500 characters.\n"
            "Send `/cancel` to abort."
        )
        return

    if query.data == "cust:text_toggle_original":
        if not customization_allowed(user_id, "text"):
            return await query.answer("Premium plan or above required", show_alert=True)
        prefs = get_user_customization(user_id)
        new_value = 0 if int(prefs.get("keep_original_text") or 0) else 1
        set_user_customization(user_id, keep_original_text=new_value)
        await query.answer("Original text ON ✅" if new_value else "Original text OFF")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "cust:text_clear":
        set_user_customization(
            user_id,
            text_prefix="",
            text_suffix="",
            keep_original_text=1,
        )
        await query.answer("Text settings cleared ✅")
        await query.message.reply_text(
            customization_summary(user_id),
            reply_markup=settings_keyboard(user_id),
        )
        return

    if query.data == "quick_start":
        await query.answer()
        await query.message.reply_text(
            quick_start_text(user_id),
            reply_markup=upgrade_markup(user_id),
        )
        return

    if query.data == "manual_menu":
        await query.answer()
        await query.message.reply_text(
            manual_text(user_id),
            reply_markup=upgrade_markup(user_id),
        )
        return

    if query.data == "formats_menu":
        await query.answer()
        await query.message.reply_text(
            formats_text(user_id),
            reply_markup=upgrade_markup(user_id),
        )
        return

    if query.data == "plans_menu":
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        CUSTOMIZE_STATES.pop(user_id, None)
        if setting_get("payments_enabled", "1") != "1" and not is_admin(user_id):
            return await query.answer("Payments temporarily disabled", show_alert=True)
        await query.message.reply_text(plan_text(user_id), reply_markup=plans_keyboard())
        return

    if query.data == "my_plan":
        await query.message.reply_text(subscription_text(user_id))
        return

    if query.data.startswith("plan:"):
        plan = query.data.split(":", 1)[1]
        if plan not in DEFAULT_PLAN_PRICES:
            return await query.answer("Invalid plan", show_alert=True)
        period_rows = [
            [
                InlineKeyboardButton(f"🔹 Weekly ₹{get_price(plan,'weekly')}", callback_data=f"buy:{plan}:weekly"),
                InlineKeyboardButton(f"📅 Monthly ₹{get_price(plan,'monthly')}", callback_data=f"buy:{plan}:monthly"),
            ],
            [InlineKeyboardButton(f"🔥 Yearly ₹{get_price(plan,'yearly')}", callback_data=f"buy:{plan}:yearly")],
        ]
        row = support_row()
        if row:
            period_rows.append(row)
        kb = InlineKeyboardMarkup(period_rows)
        await query.message.reply_text(
            tr(
                user_id,
                f"🛒 **{PLAN_TITLES[plan]}**\nChoose billing period:",
                f"🛒 **{PLAN_TITLES[plan]}**\nகால அளவைத் தேர்வு செய்யுங்கள்:",
            ),
            reply_markup=kb,
        )
        return

    if query.data.startswith("buy:"):
        if user_id in LOGIN_STATES:
            return await query.answer("Finish login or use /cancel first.", show_alert=True)
        CUSTOMIZE_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
        if setting_get("payments_enabled", "1") != "1" and not is_admin(user_id):
            return await query.answer("Payments temporarily disabled", show_alert=True)
        try:
            _, plan, period = query.data.split(":", 2)
        except ValueError:
            return await query.answer("Invalid payment option", show_alert=True)
        if plan not in DEFAULT_PLAN_PRICES or period not in PERIOD_DAYS:
            return await query.answer("Invalid payment option", show_alert=True)
        amount = get_price(plan, period)
        if amount <= 0:
            return await query.answer("Plan price unavailable. Contact support.", show_alert=True)
        if not ADMIN_IDS:
            return await query.answer("Payment review is not configured. Contact support.", show_alert=True)
        invoice_id = make_payment_id(user_id)
        qr_path, upi_uri = create_dynamic_qr(amount, invoice_id, plan, period)
        PAYMENT_STATES[user_id] = {
            "step": "waiting_paid_click",
            "plan": plan,
            "period": period,
            "amount": amount,
            "invoice_id": invoice_id,
            "upi_uri": upi_uri or "",
        }
        caption = tr(
            user_id,
            f"🏮 **SAVE PRO • PAYMENT ORDER**\n━━━━━━━━━━━━━━━━━━\n🧾 Invoice: `{invoice_id}`\n👑 Plan: **{PLAN_TITLES[plan]}**\n📅 Duration: **{period.title()}**\n💰 Pay exactly: **₹{amount}.00**\n━━━━━━━━━━━━━━━━━━\n📱 Scan this QR in any UPI app.\n✨ The selected amount is pre-filled in the dynamic QR.\n\nUPI: `{UPI_ID}`\n\nAfter payment tap **I Paid**, then send UTR + screenshot. Admin verifies before activation.\n⚠️ Never send OTP, UPI PIN, CVV or bank password.",
            f"🏮 **SAVE PRO • PAYMENT ORDER**\n━━━━━━━━━━━━━━━━━━\n🧾 Invoice: `{invoice_id}`\n👑 Plan: **{PLAN_TITLES[plan]}**\n📅 காலம்: **{period.title()}**\n💰 செலுத்த வேண்டியது: **₹{amount}.00**\n━━━━━━━━━━━━━━━━━━\n📱 எந்த UPI app-லும் இந்த QR-ஐ scan செய்யுங்கள்.\n✨ தேர்ந்தெடுத்த amount Dynamic QR-ல் முன்பே நிரப்பப்பட்டிருக்கும்.\n\nUPI: `{UPI_ID}`\n\nPayment முடிந்ததும் **I Paid** அழுத்தி UTR + screenshot அனுப்புங்கள். Admin verify செய்த பிறகே plan activate ஆகும்.\n⚠️ OTP, UPI PIN, CVV, bank password அனுப்பாதீர்கள்.",
        )
        if not upi_uri:
            caption += "\n\n⚠️ Static QR fallback: enter the exact invoice amount manually."
        pay_rows = [[
            InlineKeyboardButton("✅ I Paid / செலுத்திவிட்டேன்", callback_data=f"paid:{invoice_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="payment_cancel"),
        ]]
        row = support_row()
        if row:
            pay_rows.append(row)
        kb = InlineKeyboardMarkup(pay_rows)
        if qr_path and os.path.exists(qr_path):
            try:
                await client.send_photo(user_id, qr_path, caption=caption, reply_markup=kb)
                if qr_path.startswith(DYNAMIC_QR_DIR):
                    cleanup_file(qr_path)
                return
            except Exception as e:
                print(f"QR send warning: {e}")
                if qr_path.startswith(DYNAMIC_QR_DIR):
                    cleanup_file(qr_path)
        if os.path.exists(PAYMENT_QR_FILE):
            try:
                await client.send_photo(user_id, PAYMENT_QR_FILE,
                    caption=f"Manual QR fallback • Invoice {invoice_id} • Pay ₹{amount}. Enter amount manually.", reply_markup=kb)
                return
            except Exception:
                pass
        await query.message.reply_text(caption + "\n\n⚠️ Dynamic QR unavailable; use the UPI ID above and pay the exact amount.", reply_markup=kb)
        return

    if query.data == "payment_cancel":
        PAYMENT_STATES.pop(user_id, None)
        await query.answer("Payment order cancelled", show_alert=False)
        await query.message.reply_text(tr(user_id, "❌ Payment order cancelled.", "❌ Payment order cancel செய்யப்பட்டது."))
        return

    if query.data.startswith("paid:"):
        invoice_id = query.data.split(":", 1)[1]
        state = PAYMENT_STATES.get(user_id)
        if not state or state.get("invoice_id") != invoice_id:
            return await query.answer("Old order. Open /plans and use your latest invoice.", show_alert=True)
        if state.get("step") not in {"waiting_paid_click", "utr"}:
            return await query.answer("Send the screenshot, or /cancel to start again.", show_alert=True)
        state["step"] = "utr"
        await query.answer()
        await query.message.reply_text(
            tr(
                user_id,
                "🧾 Send your **UTR / Transaction Reference Number** now.\nDo not send OTP, PIN, CVV, or bank password.",
                "🧾 இப்போது **UTR / Transaction Reference Number** அனுப்புங்கள்.\nOTP, PIN, CVV, bank password எதையும் அனுப்பாதீர்கள்.",
            ),
            reply_markup=support_markup(),
        )
        return

    if query.data == "admin_panel":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        await show_admin_panel(user_id)
        return

    if query.data == "admin_pending":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        with db_conn() as con:
            rows = con.execute(
                "SELECT * FROM payments WHERE status IN ('pending','verified') ORDER BY created_at ASC LIMIT 20"
            ).fetchall()
        if not rows:
            return await query.message.reply_text("✅ No payments awaiting review.")
        for row in rows:
            payment = dict(row)
            caption = payment_admin_caption(payment)
            kb = payment_admin_keyboard(payment)
            proof_id = payment.get("proof_file_id") or ""
            proof_type = payment.get("proof_type") or ""
            try:
                if proof_id and proof_type == "photo":
                    await bot.send_photo(query.message.chat.id, proof_id, caption=caption, reply_markup=kb)
                elif proof_id and proof_type == "document":
                    await bot.send_document(query.message.chat.id, proof_id, caption=caption, reply_markup=kb)
                else:
                    await query.message.reply_text(caption, reply_markup=kb)
            except Exception as e:
                print(f"Pending payment proof display failed {payment['payment_id']}: {e}")
                await query.message.reply_text(caption, reply_markup=kb)
        return

    if query.data == "admin_plans":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        await query.message.reply_text(plan_text(user_id))
        return

    if query.data == "admin_toggle_payments":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        new = "0" if setting_get("payments_enabled", "1") == "1" else "1"
        setting_set("payments_enabled", new)
        await query.answer(f"Payments {'ON' if new == '1' else 'OFF'}", show_alert=True)
        await show_admin_panel(user_id)
        return

    if query.data == "admin_toggle_maintenance":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        new = "0" if setting_get("maintenance", "0") == "1" else "1"
        setting_set("maintenance", new)
        await query.answer(f"Maintenance {'ON' if new == '1' else 'OFF'}", show_alert=True)
        await show_admin_panel(user_id)
        return

    if query.data == "admin_toggle_login_alerts":
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        new = "0" if setting_get("login_alerts", "1") == "1" else "1"
        setting_set("login_alerts", new)
        await query.answer(f"Login alerts {'ON' if new == '1' else 'OFF'}", show_alert=True)
        await show_admin_panel(user_id)
        return

    if query.data.startswith("payverify:") or query.data.startswith("payapprove:"):
        # payapprove is kept as a backward-compatible alias, but it now performs
        # Step 1 only. Old keyboards can no longer activate a plan in one click.
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        payment_id = query.data.split(":", 1)[1]
        payment = verify_payment_db(payment_id, user_id)
        if not payment:
            existing = payment_by_id(payment_id)
            if existing and existing.get("status") == "verified":
                payment = existing
            else:
                return await query.answer("Already reviewed / not found", show_alert=True)
        await query.answer("Step 1 verified ✅", show_alert=True)
        confirm_text = (
            "🔐 **STEP 2 • FINAL PLAN ACTIVATION**\n\n"
            f"Invoice: `{payment['payment_id']}`\n"
            f"User: `{payment['user_id']}`\n"
            f"Plan: **{PLAN_TITLES.get(payment['plan'], payment['plan'])}**\n"
            f"Duration: **{payment['period'].title()}**\n"
            f"Amount: **₹{payment['amount']}**\n"
            f"UTR: `{payment['utr']}`\n\n"
            "Confirm only after checking the actual bank/UPI credit and selected plan."
        )
        kb = payment_admin_keyboard(payment)
        try:
            await query.message.reply_text(confirm_text, reply_markup=kb)
            await query.message.edit_reply_markup(None)
        except Exception:
            pass
        return

    if query.data.startswith("payconfirm:"):
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        payment_id = query.data.split(":", 1)[1]
        current = payment_by_id(payment_id)
        if not current or current.get("status") != "verified":
            return await query.answer("Step 1 verification is required / already reviewed", show_alert=True)
        if (
            PAYMENT_REQUIRE_DIFFERENT_ADMIN
            and current.get("verified_by")
            and int(current["verified_by"]) == int(user_id)
        ):
            return await query.answer("A different admin must complete Step 2", show_alert=True)
        payment = approve_payment_db(payment_id, user_id)
        if not payment:
            return await query.answer("Could not activate this payment", show_alert=True)
        expiry = datetime.fromtimestamp(payment["expires_at"], timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
        await query.answer("Plan activated ✅", show_alert=True)
        try:
            await bot.send_message(
                payment["user_id"],
                f"✅ **Payment Approved & Plan Activated!**\n"
                f"Plan: {PLAN_TITLES[payment['plan']]}\n"
                f"Period: {payment['period'].title()}\n"
                f"Valid until: `{expiry}`\n\n"
                "Your plan is active now. Use Quick Start or the User Manual below.",
                reply_markup=success_markup(payment["user_id"]),
            )
        except Exception as e:
            print(f"Payment approval user notification failed: {e}")
        try:
            await query.message.edit_text(
                f"✅ **ACTIVATED**\nInvoice: `{payment_id}`\nUser: `{payment['user_id']}`\nPlan: {PLAN_TITLES[payment['plan']]} • {payment['period'].title()}\nValid until: `{expiry}`\nStep 2 confirmed by admin `{user_id}`"
            )
        except Exception:
            try:
                await query.message.edit_reply_markup(None)
            except Exception:
                pass
        return

    if query.data.startswith("payreject:"):
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        payment_id = query.data.split(":", 1)[1]
        payment = reject_payment_db(payment_id, user_id)
        if not payment:
            return await query.answer("Already reviewed / not found", show_alert=True)
        await query.answer("Rejected ❌", show_alert=True)
        try:
            await bot.send_message(
                payment["user_id"],
                "❌ **Payment verification rejected.**\nPlease check the amount/UTR/screenshot and submit again from /plans.",
            )
        except Exception:
            pass
        try:
            await query.message.edit_reply_markup(None)
        except Exception:
            pass
        return

    if query.data == "login_menu":
        await query.message.reply_text(
            "🔐 **Telegram Login**\nஒரு method தேர்வு செய்யுங்கள்:",
            reply_markup=login_keyboard(),
        )
        return

    if query.data == "login_phone":
        LOGIN_STATES[user_id] = {"step": "phone"}
        await query.message.reply_text(
            "📱 Country code உடன் phone number அனுப்புங்கள்.\n"
            "Example: `+91XXXXXXXXXX`\n\n"
            "இந்த message process ஆனதும் delete செய்யப்படும்."
        )
        return

    if query.data == "login_session":
        LOGIN_STATES[user_id] = {"step": "session_string"}
        await query.message.reply_text(
            "🔑 உங்கள் Pyrogram **Session String** அனுப்புங்கள்.\n\n"
            "⚠️ இதை வேறு யாரிடமும் share செய்யாதீர்கள். "
            "இந்த message process ஆனதும் delete செய்யப்படும்."
        )
        return

    if query.data == "login_status":
        personal = has_personal_session(user_id)
        if personal:
            ub = await get_user_client(user_id)
            if ub:
                try:
                    me = await ub.get_me()
                    username = f"@{me.username}" if me.username else "No username"
                    await query.message.reply_text(
                        f"✅ Personal login active: {me.first_name or 'Telegram User'} • {username}"
                    )
                    return
                except Exception:
                    pass

        if server_userbot and (
            ALLOW_SHARED_SESSION or is_admin(user_id)
        ):
            await query.message.reply_text("✅ Shared server session active.")
        else:
            await query.message.reply_text("❌ Login இல்லை.")
        return

    if query.data == "login_logout":
        LOGIN_STATES.pop(user_id, None)
        await close_user_client(user_id)
        delete_user_session_files(user_id)
        PEER_CACHE_INITIALIZED.discard(f"user:{user_id}")
        await query.message.reply_text("🚪 Personal login removed.")
        return


    if query.data == "batch_new":
        PAYMENT_STATES.pop(user_id, None)
        CUSTOMIZE_STATES.pop(user_id, None)
        BATCH_STATES[user_id] = {"step": "start_link"}
        await query.answer()
        await query.message.reply_text(
            "📦 **New Batch • Step 1**\n\n"
            "Send the **starting Telegram post link**.\n"
            "Then choose 5 / 10 / 25 / 50 / 100 / 250, Custom, or End Link.\n\n"
            f"Your per-batch maximum: `{batch_limit_text(user_id)}`\n"
            "Send `/cancel` to abort.",
            reply_markup=support_markup(),
        )
        return

    if query.data.startswith("batch_count:"):
        state = BATCH_STATES.get(user_id)
        if not state or not state.get("start_link"):
            return await query.answer("Batch session expired. Use /batch again.", show_alert=True)
        try:
            count_value = int(query.data.split(":", 1)[1])
        except Exception:
            return await query.answer("Invalid count", show_alert=True)
        limit = batch_limit_for_user(user_id)
        if limit > 0 and abs(count_value) > limit:
            return await query.answer(
                f"Maximum per batch: {batch_limit_text(user_id)}",
                show_alert=True,
            )
        start_link = state["start_link"]
        BATCH_STATES.pop(user_id, None)
        await query.answer(f"Starting {count_value} messages…", show_alert=False)
        await run_batch_request(client, query.message, user_id, start_link, count_value=count_value)
        return

    if query.data == "batch_custom":
        state = BATCH_STATES.get(user_id)
        if not state or not state.get("start_link"):
            return await query.answer("Batch session expired. Use /batch again.", show_alert=True)
        state["step"] = "custom_count"
        BATCH_STATES[user_id] = state
        await query.answer()
        await query.message.reply_text(
            "🔢 **Send the batch count now**\n"
            "Example: `15`, `75`, `100`, `250`\n"
            f"Your per-batch maximum: `{batch_limit_text(user_id)}`\n"
            "Send `/cancel` to abort.",
            reply_markup=support_markup(),
        )
        return

    if query.data == "batch_end_link":
        state = BATCH_STATES.get(user_id)
        if not state or not state.get("start_link"):
            return await query.answer("Batch session expired. Use /batch again.", show_alert=True)
        state["step"] = "end_link"
        BATCH_STATES[user_id] = state
        await query.answer()
        await query.message.reply_text(
            "🔗 Send the **ending Telegram post link** now.\n"
            "Start and end links must be from the same chat/topic.\n"
            "Send `/cancel` to abort.",
            reply_markup=support_markup(),
        )
        return

    if query.data == "batch_wizard_cancel":
        BATCH_STATES.pop(user_id, None)
        await query.answer("Batch setup cancelled", show_alert=False)
        await query.message.reply_text("❌ Batch setup cancelled.")
        return

    if query.data == "help_batch":
        await query.message.reply_text(
            "📦 **Advanced Batch**\n\n"
            "Easy mode:\n"
            "`/batch` → send start link → choose 5/10/25/50/100 or End Link\n\n"
            "Direct range mode:\n"
            "`/batch <start_link> <end_link>`\n\n"
            "Direct count mode:\n"
            "`/batch <start_link> 100`\n\n"
            "Backward count:\n"
            "`/batch <start_link> -50`\n\n"
            f"Your per-batch maximum = `{batch_limit_text(user_id)}`.\n"
            "Admin default = unlimited (ADMIN_MAX_BATCH_MESSAGES=0)."
        )
        return

    if query.data == "help_clone":
        await query.message.reply_text(
            "♻️ **Full Clone**\n"
            "`/clone <start_link>`\n\n"
            "Start message முதல் latest message வரை process செய்யும்."
        )
        return

    if query.data == "cancel_task":
        await query.answer()
        await cancel_cmd(client, query.message)
        return

# ============================================================
# 9. MANUAL PAYMENT INPUT (UTR + SCREENSHOT)
# ============================================================
@bot.on_message(filters.private & filters.text, group=-4)
@owns_input("CUSTOMIZE_STATES")
async def customization_text_input_handler(client, message: Message):
    user_id = message.chat.id
    state = CUSTOMIZE_STATES.get(user_id)
    if not state:
        return

    value = (message.text or "").strip()
    if value.startswith("/"):
        return

    step = state.get("step")
    if step == "thumbnail":
        return await message.reply_text("🖼 Send a photo/image for the thumbnail, or /cancel.")

    if step in {"audio_artist", "audio_title"}:
        if not customization_allowed(user_id, "audio"):
            CUSTOMIZE_STATES.pop(user_id, None)
            return await message.reply_text(customization_locked_text(user_id, "audio"))
        value = _clean_custom_text(" ".join(value.split()), 128)
        if not value:
            return await message.reply_text("Send a name/title, or /cancel.")
        set_user_customization(user_id, **{step: value})
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_text("✅ Saved / சேமிக்கப்பட்டது\n\n" + customization_preview_text(user_id),
                                 parse_mode=ParseMode.DISABLED, reply_markup=audio_settings_keyboard(user_id))
        return

    if step == "caption_text":
        if not customization_allowed(user_id, "caption"):
            CUSTOMIZE_STATES.pop(user_id, None)
            return await message.reply_text(
                customization_locked_text(user_id, "caption"),
                reply_markup=upgrade_markup(user_id),
            )
        value = _clean_custom_text(value, 900)
        if not value:
            return await message.reply_text("❌ Caption cannot be empty. Use No Caption mode if you want to remove it.")
        current = get_user_customization(user_id)
        current_mode = (current.get("caption_mode") or "original").lower()
        next_mode = current_mode if current_mode in {"replace", "append"} else "replace"
        set_user_customization(user_id, caption_text=value, caption_mode=next_mode)
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_text(
            f"✅ Custom caption saved. Current mode: **{next_mode.title()}**.\n"
            "You can switch Original / Replace / Append / No Caption anytime from Settings.",
            reply_markup=settings_keyboard(user_id),
        )
        return

    if step == "watermark_text":
        if not customization_allowed(user_id, "watermark"):
            CUSTOMIZE_STATES.pop(user_id, None)
            return await message.reply_text(
                customization_locked_text(user_id, "watermark"),
                reply_markup=upgrade_markup(user_id),
            )
        value = _clean_custom_text(value, 180)
        if not value:
            return await message.reply_text("❌ Watermark text cannot be empty.")
        set_user_customization(user_id, watermark_text=value, watermark_enabled=1)
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_text(
            "✅ Fast text watermark saved and turned ON.",
            reply_markup=settings_keyboard(user_id),
        )
        return

    if step == "text_prefix":
        if not customization_allowed(user_id, "text"):
            CUSTOMIZE_STATES.pop(user_id, None)
            return await message.reply_text(
                customization_locked_text(user_id, "text"),
                reply_markup=upgrade_markup(user_id),
            )
        set_user_customization(user_id, text_prefix=_clean_custom_text(value, 500))
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_text(
            "✅ Text prefix saved.",
            reply_markup=settings_keyboard(user_id),
        )
        return

    if step == "text_suffix":
        if not customization_allowed(user_id, "text"):
            CUSTOMIZE_STATES.pop(user_id, None)
            return await message.reply_text(
                customization_locked_text(user_id, "text"),
                reply_markup=upgrade_markup(user_id),
            )
        set_user_customization(user_id, text_suffix=_clean_custom_text(value, 500))
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_text(
            "✅ Text suffix saved.",
            reply_markup=settings_keyboard(user_id),
        )
        return


@bot.on_message(filters.private & (filters.photo | filters.document), group=-4)
@owns_input("CUSTOMIZE_STATES")
async def customization_thumbnail_input_handler(client, message: Message):
    user_id = message.chat.id
    state = CUSTOMIZE_STATES.get(user_id)
    if state and state.get("step") != "thumbnail":
        return await message.reply_text("✏️ This setting needs text. Send text or /cancel.")
    if not state:
        return

    if not customization_allowed(user_id, "thumbnail"):
        CUSTOMIZE_STATES.pop(user_id, None)
        return await message.reply_text(
            customization_locked_text(user_id, "thumbnail"),
            reply_markup=upgrade_markup(user_id),
        )

    if message.document:
        mime = (message.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            return await message.reply_text("❌ Please send an image/photo for the thumbnail.")

    if Image is None:
        CUSTOMIZE_STATES.pop(user_id, None)
        return await message.reply_text(
            '❌ Pillow is not installed. Run: `./venv/bin/pip install pillow`'
        )

    raw_path = os.path.join(THUMBNAIL_DIR, f"{user_id}_source")
    final_path = _thumbnail_path_for_user(user_id)

    try:
        downloaded = await client.download_media(message, file_name=raw_path)
        if not downloaded:
            raise RuntimeError("Could not download thumbnail image")

        with Image.open(downloaded) as img:
            img = img.convert("RGB")
            img.thumbnail((320, 320))

            quality = 90
            while quality >= 45:
                img.save(final_path, "JPEG", quality=quality, optimize=True)
                if os.path.getsize(final_path) <= 200 * 1024:
                    break
                quality -= 10

        if os.path.getsize(final_path) > 200 * 1024:
            raise RuntimeError("Thumbnail could not be reduced below Telegram's safe size.")

        try:
            if downloaded != final_path and os.path.exists(downloaded):
                os.remove(downloaded)
        except OSError:
            pass

        set_user_customization(user_id, thumbnail_path=final_path)
        CUSTOMIZE_STATES.pop(user_id, None)
        await message.reply_photo(
            final_path,
            caption="✅ Custom thumbnail saved.\nIt will be used for supported Audio / Video / Document uploads.",
            reply_markup=thumbnail_settings_keyboard(),
        )

    except Exception as e:
        try:
            if os.path.exists(raw_path):
                os.remove(raw_path)
        except OSError:
            pass
        await message.reply_text(f"❌ Thumbnail error: {e}")

@bot.on_message(filters.private & filters.text, group=-3)
@owns_input("PAYMENT_STATES")
async def payment_text_handler(client, message: Message):
    user_id = message.chat.id
    state = PAYMENT_STATES.get(user_id)
    if state and state.get("step") == "screenshot":
        return await message.reply_text("📸 Send your payment screenshot as a photo/image, or /cancel.")
    if not state or state.get("step") != "utr":
        return

    text = (message.text or "").strip()

    # Commands, support, batch wizard input and Telegram links must NEVER be
    # swallowed by the payment UTR state.
    if text.startswith("/") or user_id in BATCH_STATES:
        return
    if text.lower() in {"🆘 support", "🆘 support / contact admin", "support"}:
        return
    if extract_telegram_links(text):
        PAYMENT_STATES.pop(user_id, None)
        await message.reply_text(
            tr(
                user_id,
                "ℹ️ Payment UTR entry was cancelled because you sent a Telegram link. Processing the link normally.",
                "ℹ️ Telegram link அனுப்பியதால் payment UTR entry cancel செய்யப்பட்டது. Link normal-ஆ process ஆகும்.",
            )
        )
        return

    bad, utr, reason = is_obviously_fake_utr(text)
    if bad:
        return await message.reply_text(
            tr(
                user_id,
                f"❌ Suspicious / invalid UTR. {reason}\nSend the real UTR / transaction reference shown in your UPI or bank app.",
                f"❌ UTR சந்தேகமானது / தவறானது. {reason}\nUPI / bank app-ல் காட்டும் உண்மையான UTR / transaction reference-ஐ அனுப்புங்கள்.",
            ),
            reply_markup=support_markup(),
        )

    if utr_exists(utr):
        return await message.reply_text(
            tr(user_id, "❌ This UTR was already submitted.", "❌ இந்த UTR ஏற்கனவே submit செய்யப்பட்டுள்ளது."),
            reply_markup=support_markup(),
        )

    state["utr"] = utr
    state["step"] = "screenshot"
    PAYMENT_STATES[user_id] = state
    await message.reply_text(
        tr(
            user_id,
            "📸 UTR format accepted. Now send the **payment screenshot** as a photo or image document.\n"
            "⚠️ Format checks cannot prove a payment. Admin will verify the actual bank/UPI credit.\n"
            "Do not send OTP, PIN, CVV, or bank password.",
            "📸 UTR format accepted. இப்போது **payment screenshot**-ஐ photo அல்லது image document ஆக அனுப்புங்கள்.\n"
            "⚠️ UTR format மட்டும் payment உண்மையா என்பதை நிரூபிக்காது. Admin actual bank/UPI credit-ஐ verify செய்வார்.\n"
            "OTP, PIN, CVV, bank password அனுப்பாதீர்கள்.",
        ),
        reply_markup=support_markup(),
    )

@bot.on_message(filters.private & (filters.photo | filters.document), group=-3)
@owns_input("PAYMENT_STATES")
async def payment_screenshot_handler(client, message: Message):
    user_id = message.chat.id
    state = PAYMENT_STATES.get(user_id)
    if state and state.get("step") == "utr":
        return await message.reply_text("🧾 Send the transaction reference as text first, then the screenshot.")
    if not state or state.get("step") != "screenshot":
        return

    if message.document:
        mime = (message.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            return await message.reply_text("❌ Screenshot must be an image/photo.")

    plan = state.get("plan")
    period = state.get("period")
    amount = int(state.get("amount", 0))
    utr = state.get("utr", "")

    if plan not in DEFAULT_PLAN_PRICES or period not in PERIOD_DAYS or not utr:
        PAYMENT_STATES.pop(user_id, None)
        return await message.reply_text("❌ Payment session expired. Please use /plans again.")

    payment_id = state.get("invoice_id") or make_payment_id(user_id)
    proof_file_id = message.photo.file_id if message.photo else message.document.file_id
    proof_type = "photo" if message.photo else "document"
    try:
        add_pending_payment(
            payment_id, user_id, plan, period, amount, utr, proof_file_id, proof_type
        )
    except sqlite3.IntegrityError:
        PAYMENT_STATES.pop(user_id, None)
        return await message.reply_text("❌ This payment/UTR was already submitted.")

    PAYMENT_STATES.pop(user_id, None)
    await message.reply_text(
        tr(
            user_id,
            f"✅ Payment proof submitted.\nPayment ID: `{payment_id}`\nPlan: {PLAN_TITLES[plan]}\nAmount: ₹{amount}\n\nAdmin will manually verify it. You will get a message after approval/rejection.",
            f"✅ Payment proof submit செய்யப்பட்டது.\nPayment ID: `{payment_id}`\nPlan: {PLAN_TITLES[plan]}\nAmount: ₹{amount}\n\nAdmin manual-ஆ verify செய்வார். Approve/Reject ஆனதும் message வரும்.",
        ),
        reply_markup=support_markup(),
    )

    username = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "No username"
    payment = payment_by_id(payment_id) or {
        "payment_id": payment_id, "user_id": user_id, "plan": plan, "period": period,
        "amount": amount, "utr": utr, "status": "pending"
    }
    admin_caption = (
        payment_admin_caption(payment)
        + f"\n\nUser: {message.from_user.first_name if message.from_user else 'User'} • {username}"
        + "\n\n**Approval flow:** Step 1 verify payment → Step 2 confirm plan activation."
    )
    kb = payment_admin_keyboard(payment)

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            if message.photo:
                await bot.send_photo(admin_id, message.photo.file_id, caption=admin_caption, reply_markup=kb)
            else:
                await bot.send_document(admin_id, message.document.file_id, caption=admin_caption, reply_markup=kb)
            delivered = True
        except Exception as e:
            print(f"Payment proof send to admin {admin_id} failed: {e}")

    if not delivered and ADMIN_USERNAME:
        try:
            target = f"@{ADMIN_USERNAME}"
            if message.photo:
                await bot.send_photo(target, message.photo.file_id, caption=admin_caption, reply_markup=kb)
            else:
                await bot.send_document(target, message.document.file_id, caption=admin_caption, reply_markup=kb)
            delivered = True
        except Exception as e:
            print(f"Payment proof username fallback failed @{ADMIN_USERNAME}: {e}")

    if not ADMIN_IDS and not ADMIN_USERNAME:
        await message.reply_text(
            "⚠️ ADMIN_IDS / ADMIN_USERNAME is not configured on the server; payment is saved as pending."
        )
    elif not delivered:
        await message.reply_text(
            "⚠️ Payment is saved as pending, but admin notification could not be delivered. "
            "The admin should start the bot once, and ADMIN_IDS should contain the numeric Telegram user ID."
        )

# ============================================================
# 10. PHONE / OTP / SESSION STRING LOGIN INPUT
# ============================================================
@bot.on_message(filters.private & filters.text, group=-1)
@owns_input("LOGIN_STATES")
async def login_input_handler(client, message: Message):
    user_id = message.chat.id
    state = LOGIN_STATES.get(user_id)
    if not state:
        return

    text = (message.text or "").strip()
    step = state.get("step")

    # Keep commands usable while in login flow.
    if text.startswith("/"):
        return

    if step == "phone":
        phone = re.sub(r"[^\d+]", "", text)
        await safe_delete_message(message)

        if not phone.startswith("+") or len(re.sub(r"\D", "", phone)) < 8:
            return await client.send_message(
                user_id,
                "❌ Phone number format தவறு. Country code உடன் அனுப்புங்கள். Example: `+91XXXXXXXXXX`"
            )

        await close_user_client(user_id)
        delete_user_session_files(user_id)

        temp = Client(
            phone_session_name(user_id),
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=SESSION_DIR,
            no_updates=True,
        )

        try:
            await temp.connect()
            sent = await temp.send_code(phone)
            LOGIN_STATES[user_id] = {
                "step": "otp",
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
                "client": temp,
            }
            await client.send_message(
                user_id,
                "📩 Telegram OTP அனுப்பியுள்ளது.\n"
                "OTP code மட்டும் அனுப்புங்கள்.\n"
                "Example: `12345`\n\n"
                "OTP message process ஆனதும் delete செய்யப்படும்."
            )
        except Exception as e:
            try:
                await temp.disconnect()
            except Exception:
                pass
            LOGIN_STATES.pop(user_id, None)
            await client.send_message(user_id, f"❌ OTP request failed: {e}")
        return

    if step == "otp":
        code = re.sub(r"\D", "", text)
        await safe_delete_message(message)

        temp = state.get("client")
        phone = state.get("phone")
        phone_code_hash = state.get("phone_code_hash")

        if not temp or not phone or not phone_code_hash:
            LOGIN_STATES.pop(user_id, None)
            return await client.send_message(user_id, "❌ Login session expired. `/login` மீண்டும் செய்யுங்கள்.")

        try:
            await temp.sign_in(phone, phone_code_hash, code)
            me = await temp.get_me()
            USER_CLIENTS[user_id] = temp
            LOGIN_STATES.pop(user_id, None)
            try:
                os.chmod(phone_session_path(user_id), 0o600)
            except OSError:
                pass
            await client.send_message(
                user_id,
                f"✅ Telegram login successful!\n"
                f"{me.first_name or 'Telegram User'}"
                + (f" • @{me.username}" if me.username else "")
            )
            await notify_admin_login(me, "Phone + OTP")
        except SessionPasswordNeeded:
            LOGIN_STATES[user_id]["step"] = "password"
            await client.send_message(
                user_id,
                "🔐 2-Step Verification enabled.\n"
                "உங்கள் Telegram 2FA password அனுப்புங்கள்.\n\n"
                "Password message process ஆனதும் delete செய்யப்படும்."
            )
        except Exception as e:
            await client.send_message(
                user_id,
                f"❌ OTP login failed: {e}\n`/login` மூலம் மீண்டும் முயற்சி செய்யுங்கள்."
            )
            try:
                await temp.disconnect()
            except Exception:
                pass
            LOGIN_STATES.pop(user_id, None)
            delete_user_session_files(user_id)
        return

    if step == "password":
        password = text
        await safe_delete_message(message)

        temp = state.get("client")
        if not temp:
            LOGIN_STATES.pop(user_id, None)
            return await client.send_message(user_id, "❌ Login session expired. `/login` மீண்டும் செய்யுங்கள்.")

        try:
            await temp.check_password(password)
            me = await temp.get_me()
            USER_CLIENTS[user_id] = temp
            LOGIN_STATES.pop(user_id, None)
            try:
                os.chmod(phone_session_path(user_id), 0o600)
            except OSError:
                pass
            await client.send_message(
                user_id,
                f"✅ 2FA login successful!\n"
                f"{me.first_name or 'Telegram User'}"
                + (f" • @{me.username}" if me.username else "")
            )
            await notify_admin_login(me, "Phone + OTP + 2FA")
        except Exception as e:
            await client.send_message(
                user_id,
                f"❌ 2FA password failed: {e}\n`/login` மூலம் மீண்டும் முயற்சி செய்யுங்கள்."
            )
            try:
                await temp.disconnect()
            except Exception:
                pass
            LOGIN_STATES.pop(user_id, None)
            delete_user_session_files(user_id)
        return

    if step == "session_string":
        session_string = text
        await safe_delete_message(message)

        test_client = Client(
            f"validate_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
            no_updates=True,
        )

        try:
            await test_client.start()
            me = await test_client.get_me()

            await close_user_client(user_id)
            delete_user_session_files(user_id)
            save_session_string(user_id, session_string)

            USER_CLIENTS[user_id] = test_client
            LOGIN_STATES.pop(user_id, None)

            await client.send_message(
                user_id,
                f"✅ Session String login successful!\n"
                f"{me.first_name or 'Telegram User'}"
                + (f" • @{me.username}" if me.username else "")
            )
            await notify_admin_login(me, "Session String")
        except Exception as e:
            try:
                await test_client.stop()
            except Exception:
                pass
            LOGIN_STATES.pop(user_id, None)
            await client.send_message(
                user_id,
                f"❌ Session String invalid / login failed: {e}"
            )

# ============================================================
# 10. CANCEL
# ============================================================
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    user_id = message.chat.id
    for states in (CUSTOMIZE_STATES, PAYMENT_STATES, BATCH_STATES):
        states.pop(user_id, None)
    state = LOGIN_STATES.pop(user_id, None)
    if state and state.get("client"):
        try:
            await state["client"].disconnect()
        except Exception:
            pass
    task = JOB_TASKS.get(user_id)
    if task and not task.done():
        task.cancel()
        await message.reply_text(tr(user_id, "🛑 Cancellation requested. Wait for stopped confirmation.", "🛑 ரத்து செய்யப்படுகிறது. நிறுத்தப்பட்ட தகவல் வரும் வரை காத்திருக்கவும்."))
    else:
        await message.reply_text(tr(user_id, "✅ Pending input cleared. No active job.", "✅ Input ரத்து செய்யப்பட்டது. தற்போது வேலை இல்லை."))

# ============================================================
# 11. FULL CLONE
# ============================================================
@bot.on_message(filters.command("clone") & filters.private)
@managed_job("clone")
async def handle_clone_full(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)
    allowed, reason = feature_allowed(user_id, "clone", 1)
    if not allowed:
        return await message.reply_text(reason, reply_markup=upgrade_markup(user_id))
    ub = await require_user_client(message)
    if not ub:
        return

    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text(
            "⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. `/cancel` அனுப்பவும்."
        )

    links = extract_telegram_links(message.text)
    if not links:
        return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")

    msg = await message.reply_text("🔄 Group/Channel ஆய்வு செய்கிறது...")
    set_task_active(user_id, True)
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"
    job_started = time.monotonic()

    try:
        target_chat_id, topic_filter, start_msg_id = parse_telegram_link(links[0])
        await fetch_message_safely(
            ub, cache_key, target_chat_id, start_msg_id, msg
        )

        latest_msg_id = start_msg_id
        async for last_m in ub.get_chat_history(target_chat_id, limit=1):
            latest_msg_id = last_m.id

        dest_chat = await destination_for(user_id)
        total = max(1, latest_msg_id - start_msg_id + 1)

        success_count = 0
        skipped_count = 0
        failed_count = 0
        processed = 0

        progress_ctx = {
            "started": job_started,
            "total": total,
            "index": 1,
            "processed_complete": 0,
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "msg_id": start_msg_id,
            "file_name": "Preparing…",
            "phase": "🚀 Starting full clone…",
            "ui_seq": 0,
        }
        await _update_clone_progress(msg, progress_ctx)

        for i in range(start_msg_id, latest_msg_id + 1):
            if not ACTIVE_TASKS.get(user_id):
                break

            await job_checkpoint(user_id)
            allowed, reason = feature_allowed(user_id, JOB_META.get(user_id, {}).get("kind", "single"), 1)
            if not allowed:
                JOB_META[user_id]["outcome"] = "stopped_limit"
                await message.reply_text(reason, reply_markup=upgrade_markup(user_id))
                break
            processed += 1
            progress_ctx.update({
                "index": processed,
                "processed_complete": processed - 1,
                "success": success_count,
                "skipped": skipped_count,
                "failed": failed_count,
                "msg_id": i,
                "file_name": f"Message {i}",
                "phase": "🔎 Reading source message…",
            })
            await _update_clone_progress(msg, progress_ctx)

            try:
                target_msg = await retry_flood(ub.get_messages, target_chat_id, i)
                if not target_msg or target_msg.empty:
                    skipped_count += 1
                    progress_ctx["skipped"] = skipped_count
                    progress_ctx["phase"] = "⏭ Empty/deleted message • skipped"
                    continue

                if topic_filter:
                    msg_topic = (
                        getattr(target_msg, "reply_to_top_message_id", None)
                        or getattr(target_msg, "message_thread_id", None)
                        or getattr(target_msg, "reply_to_message_id", None)
                    )
                    if msg_topic != topic_filter and target_msg.id != topic_filter:
                        skipped_count += 1
                        progress_ctx["skipped"] = skipped_count
                        progress_ctx["phase"] = "⏭ Outside selected topic • skipped"
                        continue

                progress_ctx["file_name"] = _media_display_name(target_msg)

                if has_downloadable_media(target_msg):
                    progress_ctx["phase"] = "📥 Preparing original-quality transfer…"
                    await _update_clone_progress(msg, progress_ctx)
                    if ACTIVE_TASKS.get(user_id):
                        sent_ok = await retry_flood(transfer_media_original,
                            ub, client, dest_chat, target_msg, msg, progress_ctx, user_id=user_id
                        )
                        if sent_ok:
                            success_count += 1
                            consume_usage(user_id, 1)
                            progress_ctx["success"] = success_count
                            progress_ctx["phase"] = "✅ Content sent • preparing next item…"
                            await asyncio.sleep(SEND_DELAY)
                        else:
                            failed_count += 1
                            progress_ctx["failed"] = failed_count
                            progress_ctx["phase"] = "❌ Transfer failed • continuing to next item…"
                else:
                    progress_ctx["file_name"] = _media_display_name(target_msg)
                    progress_ctx["phase"] = "📨 Re-creating message content…"
                    await _update_clone_progress(msg, progress_ctx)
                    sent_ok = await retry_flood(send_non_file_content, client, dest_chat, target_msg, user_id=user_id)
                    if sent_ok:
                        success_count += 1
                        consume_usage(user_id, 1)
                        progress_ctx["success"] = success_count
                        progress_ctx["phase"] = "✅ Content sent • preparing next item…"
                    else:
                        skipped_count += 1
                        progress_ctx["skipped"] = skipped_count
                        progress_ctx["phase"] = "⏭ Telegram/system content cannot be reproduced • skipped"

            except FloodWait as fw:
                progress_ctx["phase"] = f"⏳ Telegram FloodWait • waiting {fw.value}s"
                await _update_clone_progress(msg, progress_ctx)
                await asyncio.sleep(fw.value)
                failed_count += 1
                progress_ctx["failed"] = failed_count
            except Exception as e:
                failed_count += 1
                progress_ctx["failed"] = failed_count
                progress_ctx["phase"] = "❌ Item error • continuing…"
                print(f"Clone item {i} failed: {e}")
            finally:
                update_job(user_id, processed, success_count, failed_count, i)
                # Mark this message slot complete only after its send/upload,
                # retry/skip/error handling has fully finished.
                progress_ctx.update({
                    "processed_complete": processed,
                    "success": success_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                })
                if ACTIVE_TASKS.get(user_id):
                    await _update_clone_progress(msg, progress_ctx)

        elapsed = time.monotonic() - job_started
        progress_ctx["ui_seq"] = int(progress_ctx.get("ui_seq", 0)) + 1

        if ACTIVE_TASKS.get(user_id):
            # This final card is reached only after the LAST item has completely
            # finished its upload/send or its retry/skip handling.
            await msg.edit_text(
                "📋 **CLONE FINISHED — REVIEW COUNTS**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📦 Processed: `{processed}/{total}`\n"
                f"✅ Success: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`\n"
                f"⏱ Total time: `{_format_duration(elapsed)}`\n\n"
                "📋 Review the sent/skipped/failed counts above."
            )
        else:
            await msg.edit_text(
                "🛑 **CLONE CANCELLED**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📦 Processed: `{processed}/{total}`\n"
                f"✅ Success: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`\n"
                f"⏱ Time: `{_format_duration(elapsed)}`"
            )

    except Exception as e:
        JOB_META.get(user_id, {})["outcome"] = "failed"
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        set_task_active(user_id, False)

# ============================================================
# 12. ADVANCED BATCH
#     Guided mode: /batch -> start link -> choose 5/10/25/50/100/custom/end-link
#     Direct mode A: /batch <start_link> <end_link>
#     Direct mode B: /batch <start_link> 100
#     Direct mode C: /batch <start_link> -50
# ============================================================
def parse_batch_count(text: str):
    parts = (text or "").split()
    if len(parts) < 3:
        return None
    last = parts[-1].strip()
    if re.fullmatch(r"[+-]?\d+", last):
        return int(last)
    return None

def batch_choice_keyboard():
    rows = [
        [
            InlineKeyboardButton("5", callback_data="batch_count:5"),
            InlineKeyboardButton("10", callback_data="batch_count:10"),
            InlineKeyboardButton("25", callback_data="batch_count:25"),
        ],
        [
            InlineKeyboardButton("50", callback_data="batch_count:50"),
            InlineKeyboardButton("100", callback_data="batch_count:100"),
            InlineKeyboardButton("250", callback_data="batch_count:250"),
        ],
        [InlineKeyboardButton("🔢 Custom", callback_data="batch_custom")],
        [InlineKeyboardButton("🔗 Start Link → End Link", callback_data="batch_end_link")],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_wizard_cancel")],
    ]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

@managed_job("batch")
async def run_batch_request(
    client,
    message: Message,
    user_id: int,
    start_link: str,
    end_link: str = None,
    count_value: int = None,
):
    allowed, reason = feature_allowed(user_id, "batch", 1)
    if not allowed:
        return await message.reply_text(reason, reply_markup=upgrade_markup(user_id))

    ub = await require_user_client(message)
    if not ub:
        return

    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. `/cancel` அனுப்பவும்.")

    try:
        chat_id1, topic_id1, start_id = parse_telegram_link(start_link)

        if end_link:
            chat_id2, topic_id2, end_id = parse_telegram_link(end_link)
            if chat_id1 != chat_id2:
                return await message.reply_text("❌ Start/End links must be from the same chat/channel.")
            if topic_id1 != topic_id2 and (topic_id1 or topic_id2):
                return await message.reply_text("❌ Start/End links must be from the same topic/thread.")
            topic_filter = topic_id1
            mode_text = "Link → Link"

        elif count_value is not None:
            if count_value == 0:
                return await message.reply_text("❌ Batch count cannot be 0.")
            topic_filter = topic_id1
            if count_value > 0:
                end_id = start_id + count_value - 1
            else:
                end_id = start_id
                start_id = max(1, start_id - abs(count_value) + 1)
            mode_text = f"Count {count_value}"
        else:
            return await message.reply_text("❌ Batch request is incomplete. Use /batch again.")

    except Exception as e:
        return await message.reply_text(f"❌ Link / batch format error: {e}")

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    total = end_id - start_id + 1

    # Link-to-link range remains an Ultimate feature.
    if end_link:
        allowed, reason = feature_allowed(user_id, "range", total)
    else:
        allowed, reason = feature_allowed(user_id, "batch", total)
    if not allowed:
        return await message.reply_text(reason, reply_markup=upgrade_markup(user_id))

    # Per-user/admin one-job cap.
    per_job_limit = batch_limit_for_user(user_id)
    if per_job_limit > 0 and total > per_job_limit:
        return await message.reply_text(
            f"⚠️ This batch contains `{total}` messages.\n"
            f"Your maximum per batch: `{batch_limit_text(user_id)}`.",
            reply_markup=support_markup(),
        )

    # Optional emergency hard server ceiling.
    if not is_admin(user_id) and MAX_BATCH_MESSAGES > 0 and total > MAX_BATCH_MESSAGES:
        return await message.reply_text(
            f"⚠️ Server safety maximum: `{MAX_BATCH_MESSAGES:,}` messages."
        )

    set_task_active(user_id, True)
    dest_chat = await destination_for(user_id)
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"

    success_count = 0
    skipped_count = 0
    failed_count = 0
    processed = 0
    job_started = time.monotonic()

    overall_ctx = {
        "started": job_started,
        "total": total,
        "processed": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "current_id": start_id,
        "mode": mode_text,
    }

    # One clean overall card. It becomes TASK COMPLETE only after the last
    # requested slot has fully finished upload/send/retry/skip handling.
    overall_msg = await message.reply_text(_batch_overall_body(overall_ctx))

    try:
        await fetch_message_safely(ub, cache_key, chat_id1, start_id, overall_msg)

        for i in range(start_id, end_id + 1):
            if not ACTIVE_TASKS.get(user_id):
                break

            await job_checkpoint(user_id)
            allowed, reason = feature_allowed(user_id, JOB_META.get(user_id, {}).get("kind", "single"), 1)
            if not allowed:
                JOB_META[user_id]["outcome"] = "stopped_limit"
                await message.reply_text(reason, reply_markup=upgrade_markup(user_id))
                break
            processed += 1
            overall_ctx["current_id"] = i
            episode_status = None

            try:
                target_msg = await retry_flood(ub.get_messages, chat_id1, i)

                if not target_msg or target_msg.empty:
                    skipped_count += 1
                    continue

                if topic_filter:
                    msg_topic = (
                        getattr(target_msg, "reply_to_top_message_id", None)
                        or getattr(target_msg, "message_thread_id", None)
                        or getattr(target_msg, "reply_to_message_id", None)
                    )
                    if msg_topic != topic_filter and target_msg.id != topic_filter:
                        skipped_count += 1
                        continue

                if has_downloadable_media(target_msg):
                    episode_ctx = {
                        "kind": "batch_episode",
                        "index": processed,
                        "total": total,
                        "completed_before": processed - 1,
                        "msg_id": i,
                        "file_name": _media_display_name(target_msg),
                        "expected_size": _media_file_size(target_msg),
                        "phase": "⚡ Preparing original-quality transfer…",
                        "ui_seq": 0,
                    }

                    # One live card for each file/media episode.
                    episode_status = await message.reply_text(
                        _batch_episode_progress_body(episode_ctx)
                    )

                    sent_ok = await retry_flood(transfer_media_original,
                        ub,
                        client,
                        dest_chat,
                        target_msg,
                        episode_status,
                        episode_ctx,
                        user_id=user_id,
                    )

                    if sent_ok:
                        success_count += 1
                        consume_usage(user_id, 1)
                        episode_ctx["completed_before"] = processed
                        await _safe_status_edit(
                            episode_status,
                            _batch_episode_progress_body(
                                episode_ctx,
                                phase="✅ Episode sent successfully",
                                current=episode_ctx.get("expected_size", 0),
                                total_bytes=episode_ctx.get("expected_size", 0),
                            ),
                        )
                        if not KEEP_EPISODE_PROGRESS:
                            await asyncio.sleep(0.6)
                            await safe_delete_message(episode_status)
                    else:
                        failed_count += 1
                        await _safe_status_edit(
                            episode_status,
                            f"❌ **EPISODE {processed}/{total} FAILED**\n"
                            f"ID: `{i}`\n"
                            f"📄 `{_media_display_name(target_msg)}`\n"
                            "Auto-retry finished; this item was skipped so the task can continue."
                        )
                else:
                    sent_ok = await retry_flood(send_non_file_content, client, dest_chat, target_msg, user_id=user_id)
                    if sent_ok:
                        success_count += 1
                        consume_usage(user_id, 1)
                    else:
                        skipped_count += 1

            except FloodWait as fw:
                if episode_status:
                    await _safe_status_edit(
                        episode_status,
                        f"⏳ Telegram FloodWait: `{fw.value}s`\n"
                        f"Episode: `{processed}/{total}` • ID `{i}`"
                    )
                await asyncio.sleep(fw.value)
                failed_count += 1

            except Exception as e:
                failed_count += 1
                print(f"Batch item {i} failed: {e}")
                if episode_status:
                    await _safe_status_edit(
                        episode_status,
                        f"❌ **EPISODE {processed}/{total} ERROR**\n"
                        f"ID: `{i}`\n`{str(e)[:350]}`"
                    )

            finally:
                update_job(user_id, processed, success_count, failed_count, i)
                overall_ctx.update({
                    "processed": processed,
                    "success": success_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                    "current_id": i,
                })
                if ACTIVE_TASKS.get(user_id):
                    await _safe_status_edit(
                        overall_msg,
                        _batch_overall_body(overall_ctx),
                    )

        overall_ctx.update({
            "processed": processed,
            "success": success_count,
            "skipped": skipped_count,
            "failed": failed_count,
        })

        if ACTIVE_TASKS.get(user_id):
            await _safe_status_edit(
                overall_msg,
                _batch_overall_body(overall_ctx, final=True),
            )
            # Send a separate completion message only after the LAST item is done.
            await message.reply_text(
                "📋 **Batch finished — review results**\n"
                f"Processed: `{processed}/{total}` • Sent: `{success_count}` • "
                f"Skipped: `{skipped_count}` • Failed: `{failed_count}`",
                reply_markup=batch_complete_keyboard(),
            )
        else:
            await _safe_status_edit(
                overall_msg,
                _batch_overall_body(overall_ctx, cancelled=True),
            )

    except Exception as e:
        JOB_META.get(user_id, {})["outcome"] = "failed"
        await _safe_status_edit(overall_msg, f"❌ Batch error: `{str(e)[:600]}`")
    finally:
        set_task_active(user_id, False)

@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    touch_user(message)
    user_id = message.chat.id

    # Starting batch leaves any unfinished payment UTR state.
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)
    CUSTOMIZE_STATES.pop(user_id, None)

    allowed, reason = feature_allowed(user_id, "batch", 1)
    if not allowed:
        return await message.reply_text(reason, reply_markup=upgrade_markup(user_id))

    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. `/cancel` அனுப்பவும்.")

    links = extract_telegram_links(message.text)
    count_value = parse_batch_count(message.text)

    # Guided flow: plain /batch
    if not links:
        BATCH_STATES[user_id] = {"step": "start_link"}
        return await message.reply_text(
            "📦 **Batch Setup • Step 1**\n\n"
            "Send the **starting Telegram post link**.\n"
            "After the link, choose `5 / 10 / 25 / 50 / 100 / 250`, Custom, or End Link.\n"
            f"Your per-batch maximum: `{batch_limit_text(user_id)}`\n\n"
            "Send `/cancel` to abort.",
            reply_markup=support_markup(),
        )

    # Direct two-link range
    if len(links) >= 2:
        return await run_batch_request(
            client, message, user_id, links[0], end_link=links[1]
        )

    # Direct link + count
    if count_value is not None:
        return await run_batch_request(
            client, message, user_id, links[0], count_value=count_value
        )

    # One link only -> guided count choice
    BATCH_STATES[user_id] = {
        "step": "choose_count",
        "start_link": links[0],
    }
    await message.reply_text(
        "✅ Start link saved.\n\n"
        f"Now choose how many messages to download. Maximum: `{batch_limit_text(user_id)}`\n"
        "Or choose Start Link → End Link:",
        reply_markup=batch_choice_keyboard(),
    )

@bot.on_message(filters.private & filters.text, group=-5)
async def batch_wizard_text_handler(client, message: Message):
    text = (message.text or "").strip()
    if message.chat.id not in BATCH_STATES or text.startswith("/") or text == "🆘 Support / Contact Admin":
        return
    try:
        await _batch_wizard_text_handler(client, message)
    finally:
        message.stop_propagation()

async def _batch_wizard_text_handler(client, message: Message):
    user_id = message.chat.id
    state = BATCH_STATES.get(user_id)
    if not state:
        return

    text = (message.text or "").strip()
    if text.startswith("/") or text == "🆘 Support / Contact Admin":
        return

    step = state.get("step")

    if step == "start_link":
        links = extract_telegram_links(text)
        if not links:
            return await message.reply_text(
                "❌ Send a valid Telegram post link like `https://t.me/c/.../...`."
            )
        state["start_link"] = links[0]
        state["step"] = "choose_count"
        BATCH_STATES[user_id] = state
        await message.reply_text(
            "✅ Start link saved.\n\n"
            f"Choose message count. Maximum: `{batch_limit_text(user_id)}`\n"
            "Or choose Start Link → End Link:",
            reply_markup=batch_choice_keyboard(),
        )
        return

    if step == "custom_count":
        if not re.fullmatch(r"[+-]?\d+", text):
            return await message.reply_text("❌ Send only a number, for example `15` or `100`.")
        count_value = int(text)
        if count_value == 0:
            return await message.reply_text("❌ Count cannot be 0.")
        per_job_limit = batch_limit_for_user(user_id)
        if per_job_limit > 0 and abs(count_value) > per_job_limit:
            return await message.reply_text(
                f"❌ Your maximum per batch is `{batch_limit_text(user_id)}`."
            )
        if not is_admin(user_id) and MAX_BATCH_MESSAGES > 0 and abs(count_value) > MAX_BATCH_MESSAGES:
            return await message.reply_text(
                f"❌ Server safety maximum is `{MAX_BATCH_MESSAGES:,}`."
            )
        start_link = state.get("start_link")
        BATCH_STATES.pop(user_id, None)
        await run_batch_request(
            client, message, user_id, start_link, count_value=count_value
        )
        return

    if step == "end_link":
        links = extract_telegram_links(text)
        if not links:
            return await message.reply_text("❌ Send a valid ending Telegram post link.")
        start_link = state.get("start_link")
        BATCH_STATES.pop(user_id, None)
        await run_batch_request(
            client, message, user_id, start_link, end_link=links[0]
        )
        return

    # choose_count waits for the inline buttons.
    if step == "choose_count":
        return await message.reply_text(
            "👇 Use the `5 / 10 / 25 / 50 / 100`, Custom, or End Link buttons above."
        )

# ============================================================
# 13. SINGLE LINK
# ============================================================
@bot.on_message(filters.regex(r"https?://t\.me/") & filters.private)
@managed_job("single")
async def handle_single_link(client, message: Message):
    if message.text.startswith("/"):
        return

    touch_user(message)
    user_id = message.chat.id

    # Batch/settings wizard owns user input while it is active.
    if user_id in BATCH_STATES or user_id in CUSTOMIZE_STATES:
        return

    # A normal Telegram link must not remain trapped inside an old payment UTR state.
    if user_id in PAYMENT_STATES:
        PAYMENT_STATES.pop(user_id, None)

    # Do not treat login input as a save link.
    if user_id in LOGIN_STATES:
        return

    allowed, reason = feature_allowed(user_id, "single", 1)
    if not allowed:
        return await message.reply_text(reason, reply_markup=upgrade_markup(user_id))

    ub = await require_user_client(message)
    if not ub:
        return

    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text(
            "⚠️ வேலை நடக்கிறது. `/cancel` அனுப்பவும்."
        )

    links = extract_telegram_links(message.text)
    if not links:
        return

    msg = await message.reply_text("🔄 Link ஆய்வு செய்கிறது...")
    set_task_active(user_id, True)
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"

    try:
        chat_id, topic_id, msg_id = parse_telegram_link(links[0])
        target_msg = await fetch_message_safely(
            ub, cache_key, chat_id, msg_id, msg
        )

        if not target_msg or target_msg.empty:
            return await msg.edit_text(
                "❌ File/message கிடைக்கவில்லை அல்லது access இல்லை."
            )

        dest_chat = await destination_for(user_id)

        if has_downloadable_media(target_msg):
            await msg.edit_text("📥 Original-quality transfer starting...")
            sent_ok = await retry_flood(transfer_media_original,
                ub, client, dest_chat, target_msg, msg, user_id=user_id
            )
            if sent_ok and ACTIVE_TASKS.get(user_id):
                consume_usage(user_id, 1)
                update_job(user_id, 1, 1, 0, msg_id)
                left = remaining_quota(user_id)
                left_line = "" if left is None else f"\nRemaining today: `{left}`"
                await finish_single_success(message, msg,
                    "✅ **Saved successfully in original quality!**"
                    + left_line,
                )
            else:
                await msg.edit_text(
                    "❌ Transfer failed/timed out after automatic retries. Please retry or contact support.",
                    reply_markup=upgrade_markup(user_id),
                )
        else:
            sent_ok = await retry_flood(send_non_file_content, client, dest_chat, target_msg, user_id=user_id)
            if sent_ok:
                consume_usage(user_id, 1)
                update_job(user_id, 1, 1, 0, msg_id)
                left = remaining_quota(user_id)
                left_line = "" if left is None else f"\nRemaining today: `{left}`"
                await finish_single_success(message, msg,
                    "✅ **Message/content saved successfully!**" + left_line,
                )
            else:
                await msg.edit_text(
                    "⚠️ This Telegram/system message cannot be reproduced by the bot.",
                    reply_markup=upgrade_markup(user_id),
                )

    except Exception as e:
        JOB_META.get(user_id, {})["outcome"] = "failed"
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        set_task_active(user_id, False)

# ============================================================
# 14. ENGINE STARTER
# ============================================================
async def main():
    global BOT_READY
    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise RuntimeError("Set API_ID, API_HASH and BOT_TOKEN before starting")
    if os.environ.get("ENABLE_HEALTH_SERVER", "true").lower() in {"true", "1", "yes"}:
        Thread(target=run_server, daemon=True).start()
    os.makedirs("downloads", exist_ok=True)

    await bot.start()

    if server_userbot:
        await server_userbot.start()
        print("✅ Shared server userbot connected!")
        await initialize_peer_cache(server_userbot, "server", force=True)

    await bot.set_bot_commands([
        BotCommand("start", "🏠 Home"),
        BotCommand("login", "🔐 Telegram Login"),
        BotCommand("loginstatus", "✅ Login Status"),
        BotCommand("logout", "🚪 Logout Personal Session"),
        BotCommand("plans", "🛒 Plans & Payment"),
        BotCommand("myplan", "📊 My Plan / Daily Limit"),
        BotCommand("settings", "⚙️ Audio / Caption / Watermark / Thumbnail"),
        BotCommand("language", "🌐 English / தமிழ்"),
        BotCommand("support", "🆘 Support / Contact Admin"),
        BotCommand("manual", "📘 Full User Manual"),
        BotCommand("formats", "📁 Supported Content"),
        BotCommand("clone", "♻️ Full Group Clone"),
        BotCommand("batch", "📦 Advanced Batch Download"),
        BotCommand("pause", "⏸ Pause after current message"),
        BotCommand("resume", "▶ Resume paused job"),
        BotCommand("status", "📊 Current job"),
        BotCommand("history", "🕘 Recent jobs"),
        BotCommand("payments", "🧾 My payment status"),
        BotCommand("cancel", "❌ Cancel Task"),
    ])

    BOT_READY = True
    print("🚀 Pro Max Saver Bot is Live & Ready!")

    from pyrogram import idle
    try:
        await idle()
    finally:
        BOT_READY = False
        tasks = list(JOB_TASKS.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for uid in list(USER_CLIENTS):
            await close_user_client(uid)
        if server_userbot and server_userbot.is_connected:
            await server_userbot.stop()
        await bot.stop()

if __name__ == "__main__":
    loop.run_until_complete(main())
