import os
import re
import asyncio
import time
import sqlite3
import json
import secrets
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask

from pyrogram import Client, filters
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

@app.route("/")
def home():
    return "✅ Pro Max Saver Bot is Live & Running!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# ============================================================
# 3. ENVIRONMENT VARIABLES
# ============================================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
DUMP_CHANNEL = os.environ.get("DUMP_CHANNEL", "")
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "")

# Advanced batch settings
MAX_BATCH_MESSAGES = int(os.environ.get("MAX_BATCH_MESSAGES", "5000"))
BATCH_PROGRESS_EVERY = max(1, int(os.environ.get("BATCH_PROGRESS_EVERY", "10")))
SEND_DELAY = max(0.0, float(os.environ.get("SEND_DELAY", "0.05")))
# Large-file transfer watchdog/progress settings. These prevent one media item
# from making clone/batch look permanently stuck while keeping original quality.
MEDIA_DOWNLOAD_TIMEOUT = max(60, int(os.environ.get("MEDIA_DOWNLOAD_TIMEOUT", "1800")))
MEDIA_UPLOAD_TIMEOUT = max(60, int(os.environ.get("MEDIA_UPLOAD_TIMEOUT", "1800")))
# If byte progress does not move for this long, cancel that transfer, retry,
# then skip only that item instead of freezing the whole clone job.
MEDIA_STALL_TIMEOUT = max(15, int(os.environ.get("MEDIA_STALL_TIMEOUT", "45")))
TRANSFER_RETRIES = max(0, min(3, int(os.environ.get("TRANSFER_RETRIES", "1"))))
TRANSFER_PROGRESS_INTERVAL = max(2.0, float(os.environ.get("TRANSFER_PROGRESS_INTERVAL", "4")))

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
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", ADMIN_USERNAME).strip().lstrip("@")
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

def db_conn():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db_conn() as con:
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

def plan_text(user_id: int) -> str:
    lines_en = [
        "╔═══════════════════════════════╗",
        "║   🛒  CHOOSE YOUR PLAN        ║",
        "╚═══════════════════════════════╝",
        "",
        f"🆓 Free — {get_daily_limit('free')} links/day • Single only",
        f"⚡ Basic — {get_daily_limit('basic')} links/day",
        f"   🔹 Weekly ₹{get_price('basic','weekly')} | 📅 Monthly ₹{get_price('basic','monthly')} | 🔥 Yearly ₹{get_price('basic','yearly')}",
        f"🥈 Standard — {get_daily_limit('standard')} links/day + /batch",
        f"   🔹 Weekly ₹{get_price('standard','weekly')} | 📅 Monthly ₹{get_price('standard','monthly')} | 🔥 Yearly ₹{get_price('standard','yearly')}",
        f"🥇 Premium — {get_daily_limit('premium')} links/day + /batch",
        f"   🔹 Weekly ₹{get_price('premium','weekly')} | 📅 Monthly ₹{get_price('premium','monthly')} | 🔥 Yearly ₹{get_price('premium','yearly')}",
        "💎 Ultimate — Unlimited ♾️ + /batch + range + /clone",
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
        f"⚡ Basic — நாள் ஒன்றுக்கு {get_daily_limit('basic')} links",
        f"   🔹 வாரம் ₹{get_price('basic','weekly')} | 📅 மாதம் ₹{get_price('basic','monthly')} | 🔥 வருடம் ₹{get_price('basic','yearly')}",
        f"🥈 Standard — நாள் ஒன்றுக்கு {get_daily_limit('standard')} links + /batch",
        f"   🔹 வாரம் ₹{get_price('standard','weekly')} | 📅 மாதம் ₹{get_price('standard','monthly')} | 🔥 வருடம் ₹{get_price('standard','yearly')}",
        f"🥇 Premium — நாள் ஒன்றுக்கு {get_daily_limit('premium')} links + /batch",
        f"   🔹 வாரம் ₹{get_price('premium','weekly')} | 📅 மாதம் ₹{get_price('premium','monthly')} | 🔥 வருடம் ₹{get_price('premium','yearly')}",
        "💎 Ultimate — Unlimited ♾️ + /batch + range + /clone",
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
    with db_conn() as con:
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
    now = int(time.time())
    with db_conn() as con:
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
    payment = payment_by_id(payment_id)
    if not payment or payment["status"] != "verified":
        return None
    days = PERIOD_DAYS[payment["period"]]
    now = int(time.time())
    with db_conn() as con:
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
    now = int(time.time())
    with db_conn() as con:
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
    busy = any(bool(v) for v in ACTIVE_TASKS.values())
    try:
        if busy:
            with open(DEPLOY_BUSY_FILE, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        elif os.path.exists(DEPLOY_BUSY_FILE):
            os.remove(DEPLOY_BUSY_FILE)
    except OSError as e:
        print(f"Deploy busy marker warning: {e}")

def set_task_active(user_id: int, active: bool):
    ACTIVE_TASKS[user_id] = bool(active)
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
    with open(path, "w", encoding="utf-8") as f:
        f.write(value.strip())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

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
    url = url.strip().replace("<", "").replace(">", "")
    if "t.me/" not in url:
        raise ValueError("Telegram link இல்லை")

    path = url.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
    parts = path.split("/")

    if parts[0] == "c":
        if len(parts) < 3:
            raise ValueError("Invalid private Telegram link")
        chat_id = int("-100" + parts[1])

        if len(parts) >= 4:
            topic_id = int(parts[2])
            msg_id = int(parts[3])
        else:
            topic_id = None
            msg_id = int(parts[2])
    else:
        if len(parts) < 2:
            raise ValueError("Invalid Telegram link")
        chat_id = parts[0]

        if len(parts) >= 3 and parts[-2].isdigit():
            topic_id = int(parts[-2])
            msg_id = int(parts[-1])
        else:
            topic_id = None
            msg_id = int(parts[-1])

    return chat_id, topic_id, msg_id

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

async def fetch_message_safely(ub_client, cache_key, chat_id, msg_id, status_msg=None):
    try:
        return await ub_client.get_messages(chat_id, msg_id)
    except Exception as first_error:
        print(f"⚠️ Peer lookup retry [{cache_key}] chat={chat_id}: {first_error}")
        if status_msg:
            try:
                await status_msg.edit_text("🔄 Syncing Telegram chats...")
            except Exception:
                pass
        await initialize_peer_cache(ub_client, cache_key, force=True)
        return await ub_client.get_messages(chat_id, msg_id)

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
# 7. ORIGINAL QUALITY MEDIA SENDER
# ============================================================
async def send_media_original(bot_client, target_chat, target_msg, file_path, progress=None):
    caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")

    common = {}
    if progress is not None:
        common["progress"] = progress

    if target_msg.audio:
        await bot_client.send_audio(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.audio.duration,
            performer=target_msg.audio.performer,
            title=target_msg.audio.title,
            **common,
        )
    elif target_msg.video:
        await bot_client.send_video(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.video.duration,
            width=target_msg.video.width,
            height=target_msg.video.height,
            **common,
        )
    elif target_msg.photo:
        await bot_client.send_photo(target_chat, file_path, caption=caption, **common)
    elif target_msg.document:
        await bot_client.send_document(target_chat, file_path, caption=caption, **common)
    elif target_msg.voice:
        await bot_client.send_voice(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.voice.duration,
            **common,
        )
    else:
        await bot_client.send_document(target_chat, file_path, caption=caption, **common)


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
    for attr in ("audio", "document", "video", "animation", "voice"):
        media = getattr(msg, attr, None)
        if media:
            name = getattr(media, "file_name", None)
            if name:
                return name[:60]
    return f"Message {getattr(msg, 'id', '?')}"


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
        "Stop: `/cancel`"
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
                "Stop: `/cancel`"
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


async def transfer_media_original(ub, bot_client, dest_chat, target_msg, status_msg=None, progress_ctx=None):
    """Download + upload one media item with live progress, stall detection and retry."""
    attempts = TRANSFER_RETRIES + 1
    last_error = None

    for attempt in range(1, attempts + 1):
        file_path = None
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
                if status_msg else None
            )

            file_path = await _await_transfer_with_watchdog(
                ub.download_media(target_msg, progress=download_progress),
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
                if status_msg else None
            )

            await _await_transfer_with_watchdog(
                send_media_original(
                    bot_client,
                    dest_chat,
                    target_msg,
                    file_path,
                    progress=upload_progress,
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

        if attempt < attempts:
            try:
                if status_msg:
                    if progress_ctx is not None:
                        progress_ctx["phase"] = f"🔁 Retrying • attempt {attempt + 1}/{attempts}"
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
                    "Skipping this item and continuing the clone…"
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
    return int(DUMP_CHANNEL) if DUMP_CHANNEL else user_id

# ============================================================
# 8. HOME / LOGIN UI
# ============================================================
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
    keyboard_rows = [
        [
            InlineKeyboardButton("🔐 Login", callback_data="login_menu"),
            InlineKeyboardButton("🛒 Plans", callback_data="plans_menu"),
        ],
        [
            InlineKeyboardButton("📊 My Plan", callback_data="my_plan"),
            InlineKeyboardButton("🌐 English / தமிழ்", callback_data="language_menu"),
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
            "✨ **முக்கிய வசதிகள்**\n"
            "• Single link save\n"
            "• Advanced Batch / Range\n"
            "• Full Clone\n"
            "• Phone + OTP / Session String login\n"
            "• UPI / QR subscription payment\n\n"
            "📦 `/batch <start_link> 100`\n"
            "♻️ `/clone <start_link>`\n"
            "🛒 `/plans`\n\n"
            "🔒 உங்கள் Telegram account-க்கு access உள்ள chats/content மட்டும் பயன்படுத்துங்கள்."
        )
    else:
        text = (
            "🤖 **Pro Max Saver Bot**\n\n"
            "✨ **Main Features**\n"
            "• Single link save\n"
            "• Advanced Batch / Range\n"
            "• Full Clone\n"
            "• Phone + OTP / Session String login\n"
            "• UPI / QR subscription payment\n\n"
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

@bot.on_message(filters.command("plans") & filters.private)
async def plans_cmd(client, message: Message):
    touch_user(message)
    PAYMENT_STATES.pop(message.chat.id, None)
    BATCH_STATES.pop(message.chat.id, None)
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

    if query.data == "plans_menu":
        PAYMENT_STATES.pop(user_id, None)
        BATCH_STATES.pop(user_id, None)
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
        if setting_get("payments_enabled", "1") != "1" and not is_admin(user_id):
            return await query.answer("Payments temporarily disabled", show_alert=True)
        try:
            _, plan, period = query.data.split(":", 2)
        except ValueError:
            return await query.answer("Invalid payment option", show_alert=True)
        if plan not in DEFAULT_PLAN_PRICES or period not in PERIOD_DAYS:
            return await query.answer("Invalid payment option", show_alert=True)
        amount = get_price(plan, period)
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
        pay_rows = [[
            InlineKeyboardButton("✅ I Paid / செலுத்திவிட்டேன்", callback_data=f"paid:{plan}:{period}"),
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
        await query.message.reply_text(caption + "\n\n⚠️ Dynamic QR unavailable; use the UPI ID above and pay the exact amount.", reply_markup=kb)
        return

    if query.data == "payment_cancel":
        PAYMENT_STATES.pop(user_id, None)
        await query.answer("Payment order cancelled", show_alert=False)
        await query.message.reply_text(tr(user_id, "❌ Payment order cancelled.", "❌ Payment order cancel செய்யப்பட்டது."))
        return

    if query.data.startswith("paid:"):
        try:
            _, plan, period = query.data.split(":", 2)
        except ValueError:
            return await query.answer("Invalid payment option", show_alert=True)
        state = PAYMENT_STATES.get(user_id)
        amount = get_price(plan, period)
        if not state or state.get("plan") != plan or state.get("period") != period:
            state = {"plan": plan, "period": period, "amount": amount}
        state["step"] = "utr"
        state["amount"] = amount
        PAYMENT_STATES[user_id] = state
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
                f"✅ **Payment Approved & Plan Activated!**\nPlan: {PLAN_TITLES[payment['plan']]}\nPeriod: {payment['period'].title()}\nValid until: `{expiry}`\n\nYour plan is active now.",
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


    if query.data.startswith("batch_count:"):
        state = BATCH_STATES.get(user_id)
        if not state or not state.get("start_link"):
            return await query.answer("Batch session expired. Use /batch again.", show_alert=True)
        try:
            count_value = int(query.data.split(":", 1)[1])
        except Exception:
            return await query.answer("Invalid count", show_alert=True)
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
            "🔢 Send the batch count now. Example: `15`, `75`, `100`\\n"
            f"Maximum configured: `{MAX_BATCH_MESSAGES if MAX_BATCH_MESSAGES > 0 else 'Unlimited'}`\\n"
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
            "🔗 Send the **ending Telegram post link** now.\\n"
            "Start and end links must be from the same chat/topic.\\n"
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
            f"Current MAX_BATCH_MESSAGES = `{MAX_BATCH_MESSAGES}` "
            "(0 என்றால் unlimited)."
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
        if ACTIVE_TASKS.get(user_id):
            set_task_active(user_id, False)
            await query.message.reply_text("❌ பணி நிறுத்தப்படுகிறது...")
        else:
            await query.answer("எந்தப் பணியும் நடக்கவில்லை!", show_alert=True)

# ============================================================
# 9. MANUAL PAYMENT INPUT (UTR + SCREENSHOT)
# ============================================================
@bot.on_message(filters.private & filters.text, group=-3)
async def payment_text_handler(client, message: Message):
    user_id = message.chat.id
    state = PAYMENT_STATES.get(user_id)
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
async def payment_screenshot_handler(client, message: Message):
    user_id = message.chat.id
    state = PAYMENT_STATES.get(user_id)
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

    if user_id in PAYMENT_STATES:
        PAYMENT_STATES.pop(user_id, None)
        await message.reply_text("❌ Payment input cancelled.", reply_markup=support_markup())
        return

    if user_id in BATCH_STATES:
        BATCH_STATES.pop(user_id, None)
        await message.reply_text("❌ Batch setup cancelled.")
        return

    if user_id in LOGIN_STATES:
        state = LOGIN_STATES.pop(user_id, None)
        temp = (state or {}).get("client")
        if temp:
            try:
                await temp.disconnect()
            except Exception:
                pass
        await message.reply_text("❌ Login process cancel செய்யப்பட்டது.")
        return

    if ACTIVE_TASKS.get(user_id):
        set_task_active(user_id, False)
        await message.reply_text("❌ பணி ரத்து செய்யப்படுகிறது...")
    else:
        await message.reply_text("எந்தப் பணியும் தற்போது நடைபெறவில்லை.")

# ============================================================
# 11. FULL CLONE
# ============================================================
@bot.on_message(filters.command("clone") & filters.private)
async def handle_clone_full(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)
    allowed, reason = feature_allowed(user_id, "clone", 1)
    if not allowed:
        return await message.reply_text(reason)
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
                target_msg = await ub.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty:
                    skipped_count += 1
                    progress_ctx["skipped"] = skipped_count
                    progress_ctx["phase"] = "⏭ Empty/deleted message • skipped"
                    continue

                if topic_filter:
                    msg_topic = (
                        getattr(target_msg, "message_thread_id", None)
                        or getattr(target_msg, "reply_to_message_id", None)
                    )
                    if msg_topic != topic_filter and target_msg.id != topic_filter:
                        skipped_count += 1
                        progress_ctx["skipped"] = skipped_count
                        progress_ctx["phase"] = "⏭ Outside selected topic • skipped"
                        continue

                progress_ctx["file_name"] = _media_display_name(target_msg)

                if target_msg.media:
                    progress_ctx["phase"] = "📥 Preparing download…"
                    await _update_clone_progress(msg, progress_ctx)
                    if ACTIVE_TASKS.get(user_id):
                        sent_ok = await transfer_media_original(
                            ub, client, dest_chat, target_msg, msg, progress_ctx
                        )
                        if sent_ok:
                            success_count += 1
                            progress_ctx["success"] = success_count
                            progress_ctx["phase"] = "✅ File sent • preparing next item…"
                            await asyncio.sleep(SEND_DELAY)
                        else:
                            failed_count += 1
                            progress_ctx["failed"] = failed_count
                            progress_ctx["phase"] = "❌ File failed • continuing to next item…"

                elif target_msg.text:
                    progress_ctx["file_name"] = "Text message"
                    progress_ctx["phase"] = "📨 Sending text…"
                    await _update_clone_progress(msg, progress_ctx)
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
                    progress_ctx["success"] = success_count
                    progress_ctx["phase"] = "✅ Text sent • preparing next item…"
                    await asyncio.sleep(min(SEND_DELAY, 1.0))
                else:
                    skipped_count += 1
                    progress_ctx["skipped"] = skipped_count
                    progress_ctx["phase"] = "⏭ Unsupported/empty content • skipped"

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
                "✅ **CLONE COMPLETED**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📦 Processed: `{processed}/{total}`\n"
                f"✅ Success: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`\n"
                f"⏱ Total time: `{_format_duration(elapsed)}`\n\n"
                "🎉 **Task completed successfully**"
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
            InlineKeyboardButton("🔢 Custom", callback_data="batch_custom"),
        ],
        [InlineKeyboardButton("🔗 Start Link → End Link", callback_data="batch_end_link")],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_wizard_cancel")],
    ]
    row = support_row()
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

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
        return await message.reply_text(reason, reply_markup=support_markup())

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

    # Link-to-link range remains an Ultimate feature, as shown in /plans.
    if end_link:
        allowed, reason = feature_allowed(user_id, "range", total)
    else:
        allowed, reason = feature_allowed(user_id, "batch", total)
    if not allowed:
        return await message.reply_text(reason, reply_markup=support_markup())

    if MAX_BATCH_MESSAGES > 0 and total > MAX_BATCH_MESSAGES:
        return await message.reply_text(
            f"⚠️ This batch contains `{total}` messages.\n"
            f"Server maximum: `{MAX_BATCH_MESSAGES}`."
        )

    msg = await message.reply_text(
        "📦 **Advanced Batch Starting**\n"
        f"Mode: `{mode_text}`\n"
        f"Message ID: `{start_id}` → `{end_id}`\n"
        f"Total: `{total}`\n"
        "Stop: `/cancel`"
    )

    set_task_active(user_id, True)
    dest_chat = await destination_for(user_id)
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"

    success_count = 0
    skipped_count = 0
    failed_count = 0
    processed = 0
    last_update = 0.0

    try:
        await fetch_message_safely(ub, cache_key, chat_id1, start_id, msg)

        for i in range(start_id, end_id + 1):
            if not ACTIVE_TASKS.get(user_id):
                break

            processed += 1
            try:
                target_msg = await ub.get_messages(chat_id1, i)

                if not target_msg or target_msg.empty:
                    skipped_count += 1
                    continue

                if topic_filter:
                    msg_topic = (
                        getattr(target_msg, "message_thread_id", None)
                        or getattr(target_msg, "reply_to_message_id", None)
                    )
                    if msg_topic != topic_filter and target_msg.id != topic_filter:
                        skipped_count += 1
                        continue

                if target_msg.media:
                    sent_ok = await transfer_media_original(
                        ub, client, dest_chat, target_msg, msg
                    )
                    if sent_ok:
                        success_count += 1
                        consume_usage(user_id, 1)
                        await asyncio.sleep(SEND_DELAY)
                    else:
                        failed_count += 1

                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
                    consume_usage(user_id, 1)
                    await asyncio.sleep(min(SEND_DELAY, 1.0))
                else:
                    skipped_count += 1

            except FloodWait as fw:
                try:
                    await msg.edit_text(
                        f"⏳ Telegram FloodWait: `{fw.value}s`\n"
                        f"Progress: `{processed}/{total}`"
                    )
                except Exception:
                    pass
                await asyncio.sleep(fw.value)
                failed_count += 1

            except Exception as e:
                failed_count += 1
                print(f"Batch item {i} failed: {e}")

            now = time.monotonic()
            if (
                processed % BATCH_PROGRESS_EVERY == 0
                or now - last_update >= 8
                or processed == total
            ):
                pct = min(100.0, processed * 100 / total)
                try:
                    await msg.edit_text(
                        "📦 **Advanced Batch Progress**\n"
                        f"`{processed}/{total}` • `{pct:.1f}%`\n"
                        f"✅ Sent: `{success_count}`\n"
                        f"⏭ Skipped: `{skipped_count}`\n"
                        f"❌ Failed: `{failed_count}`\n"
                        f"Current ID: `{i}`\n\n"
                        "Stop: `/cancel`"
                    )
                    last_update = now
                except Exception:
                    pass

        if ACTIVE_TASKS.get(user_id):
            await msg.edit_text(
                "✅ **BATCH COMPLETED**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📦 Processed: `{processed}/{total}`\n"
                f"✅ Sent: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`\n\n"
                "🎉 Task completed."
            )
        else:
            await msg.edit_text(
                "🛑 **Batch cancelled**\n"
                f"Processed: `{processed}/{total}`\n"
                f"✅ Sent: `{success_count}` • ❌ Failed: `{failed_count}`"
            )

    except Exception as e:
        await msg.edit_text(f"❌ Batch error: {e}")
    finally:
        set_task_active(user_id, False)

@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    touch_user(message)
    user_id = message.chat.id

    # Starting batch leaves any unfinished payment UTR state.
    PAYMENT_STATES.pop(user_id, None)
    BATCH_STATES.pop(user_id, None)

    allowed, reason = feature_allowed(user_id, "batch", 1)
    if not allowed:
        return await message.reply_text(reason, reply_markup=support_markup())

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
            "After the link, choose `5 / 10 / 25 / 50 / 100`, Custom, or End Link.\n\n"
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
        "Now choose how many messages to download, or choose End Link:",
        reply_markup=batch_choice_keyboard(),
    )

@bot.on_message(filters.private & filters.text, group=-5)
async def batch_wizard_text_handler(client, message: Message):
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
            "Choose message count or choose Start Link → End Link:",
            reply_markup=batch_choice_keyboard(),
        )
        return

    if step == "custom_count":
        if not re.fullmatch(r"[+-]?\d+", text):
            return await message.reply_text("❌ Send only a number, for example `15` or `100`.")
        count_value = int(text)
        if count_value == 0:
            return await message.reply_text("❌ Count cannot be 0.")
        if MAX_BATCH_MESSAGES > 0 and abs(count_value) > MAX_BATCH_MESSAGES:
            return await message.reply_text(
                f"❌ Maximum configured batch size is `{MAX_BATCH_MESSAGES}`."
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
async def handle_single_link(client, message: Message):
    if message.text.startswith("/"):
        return

    touch_user(message)
    user_id = message.chat.id

    # Batch wizard owns Telegram links while it is active.
    if user_id in BATCH_STATES:
        return

    # A normal Telegram link must not remain trapped inside an old payment UTR state.
    if user_id in PAYMENT_STATES:
        PAYMENT_STATES.pop(user_id, None)

    # Do not treat login input as a save link.
    if user_id in LOGIN_STATES:
        return

    allowed, reason = feature_allowed(user_id, "single", 1)
    if not allowed:
        return await message.reply_text(reason)

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

        if target_msg.media:
            await msg.edit_text("📥 Original quality transfer starting...")
            sent_ok = await transfer_media_original(
                ub, client, dest_chat, target_msg, msg
            )
            if sent_ok and ACTIVE_TASKS.get(user_id):
                consume_usage(user_id, 1)
                await msg.edit_text(
                    "✅ 100% original quality-ல் அனுப்பப்பட்டது!"
                )
            else:
                await msg.edit_text(
                    "❌ Transfer failed/timed out. Next try can continue without hanging forever."
                )

        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            consume_usage(user_id, 1)
            await msg.edit_text("✅ Message அனுப்பப்பட்டது!")
        else:
            await msg.edit_text("⚠️ Supported content இல்லை.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        set_task_active(user_id, False)

# ============================================================
# 14. ENGINE STARTER
# ============================================================
async def main():
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
        BotCommand("language", "🌐 English / தமிழ்"),
        BotCommand("support", "🆘 Support / Contact Admin"),
        BotCommand("clone", "♻️ Full Group Clone"),
        BotCommand("batch", "📦 Advanced Batch Download"),
        BotCommand("cancel", "❌ Cancel Task"),
    ])

    print("🚀 Pro Max Saver Bot is Live & Ready!")

    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
