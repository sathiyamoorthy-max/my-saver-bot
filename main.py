import os
import re
import asyncio
import time
import sqlite3
import json
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
SEND_DELAY = max(0.0, float(os.environ.get("SEND_DELAY", "1.5")))

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
                reviewed_at INTEGER,
                reviewed_by INTEGER
            )"""
        )
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

def plans_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Basic", callback_data="plan:basic"),
            InlineKeyboardButton("🥈 Standard", callback_data="plan:standard"),
        ],
        [
            InlineKeyboardButton("🥇 Premium", callback_data="plan:premium"),
            InlineKeyboardButton("💎 Ultimate", callback_data="plan:ultimate"),
        ],
        [InlineKeyboardButton("🌐 English / தமிழ்", callback_data="language_menu")],
    ])

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
    return f"SP{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}{str(user_id)[-4:]}"

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

def add_pending_payment(payment_id, user_id, plan, period, amount, utr):
    now = int(time.time())
    with db_conn() as con:
        con.execute(
            "INSERT INTO payments(payment_id,user_id,plan,period,amount,utr,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (payment_id, user_id, plan, period, amount, utr, "pending", now),
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

def approve_payment_db(payment_id: str, admin_id: int):
    payment = payment_by_id(payment_id)
    if not payment or payment["status"] != "pending":
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
    return payment

def reject_payment_db(payment_id: str, admin_id: int):
    now = int(time.time())
    with db_conn() as con:
        row = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not row or row["status"] != "pending":
            return None
        con.execute(
            "UPDATE payments SET status='rejected',reviewed_at=?,reviewed_by=? WHERE payment_id=?",
            (now, admin_id, payment_id),
        )
        con.commit()
    return dict(row)

async def notify_admins(text: str):
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            print(f"Admin notify failed {admin_id}: {e}")

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
        async for _ in ub_client.get_dialogs(limit=200):
            pass
        PEER_CACHE_INITIALIZED.add(cache_key)
    except Exception as e:
        print(f"⚠️ Peer cache warning [{cache_key}]: {e}")

async def fetch_message_safely(ub_client, cache_key, chat_id, msg_id, status_msg=None):
    try:
        return await ub_client.get_messages(chat_id, msg_id)
    except Exception:
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
async def send_media_original(bot_client, target_chat, target_msg, file_path):
    caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")

    if target_msg.audio:
        await bot_client.send_audio(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.audio.duration,
            performer=target_msg.audio.performer,
            title=target_msg.audio.title,
        )
    elif target_msg.video:
        await bot_client.send_video(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.video.duration,
            width=target_msg.video.width,
            height=target_msg.video.height,
        )
    elif target_msg.photo:
        await bot_client.send_photo(target_chat, file_path, caption=caption)
    elif target_msg.document:
        await bot_client.send_document(target_chat, file_path, caption=caption)
    elif target_msg.voice:
        await bot_client.send_voice(
            target_chat,
            file_path,
            caption=caption,
            duration=target_msg.voice.duration,
        )
    else:
        await bot_client.send_document(target_chat, file_path, caption=caption)

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
    if setting_get("payments_enabled", "1") != "1" and not is_admin(message.chat.id):
        return await message.reply_text(tr(message.chat.id, "⚠️ Payments are temporarily disabled.", "⚠️ Payment தற்காலிகமாக நிறுத்தப்பட்டுள்ளது."))
    await message.reply_text(plan_text(message.chat.id), reply_markup=plans_keyboard())

@bot.on_message(filters.command("myplan") & filters.private)
async def myplan_cmd(client, message: Message):
    touch_user(message)
    await message.reply_text(subscription_text(message.chat.id))

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
        pending = con.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
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
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🔹 Weekly ₹{get_price(plan,'weekly')}", callback_data=f"buy:{plan}:weekly"),
                InlineKeyboardButton(f"📅 Monthly ₹{get_price(plan,'monthly')}", callback_data=f"buy:{plan}:monthly"),
            ],
            [InlineKeyboardButton(f"🔥 Yearly ₹{get_price(plan,'yearly')}", callback_data=f"buy:{plan}:yearly")],
        ])
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
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ I Paid / செலுத்திவிட்டேன்", callback_data=f"paid:{plan}:{period}"),
            InlineKeyboardButton("❌ Cancel", callback_data="payment_cancel"),
        ]])
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
            )
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
            rows = con.execute("SELECT * FROM payments WHERE status='pending' ORDER BY created_at ASC LIMIT 20").fetchall()
        if not rows:
            return await query.message.reply_text("✅ No pending payments.")
        for row in rows:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"payapprove:{row['payment_id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"payreject:{row['payment_id']}"),
            ]])
            await query.message.reply_text(
                f"🧾 `{row['payment_id']}`\nUser: `{row['user_id']}`\nPlan: {row['plan']} / {row['period']}\nAmount: ₹{row['amount']}\nUTR: `{row['utr']}`",
                reply_markup=kb,
            )
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

    if query.data.startswith("payapprove:"):
        if not is_admin(user_id):
            return await query.answer("Admin only", show_alert=True)
        payment_id = query.data.split(":", 1)[1]
        payment = approve_payment_db(payment_id, user_id)
        if not payment:
            return await query.answer("Already reviewed / not found", show_alert=True)
        expiry = datetime.fromtimestamp(payment["expires_at"], timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
        await query.answer("Approved ✅", show_alert=True)
        try:
            await bot.send_message(
                payment["user_id"],
                f"✅ **Payment Approved!**\nPlan: {PLAN_TITLES[payment['plan']]}\nPeriod: {payment['period'].title()}\nValid until: `{expiry}`\n\nYour plan is active now.",
            )
        except Exception:
            pass
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

    if query.data == "help_batch":
        await query.message.reply_text(
            "📦 **Advanced Batch**\n\n"
            "Range mode:\n"
            "`/batch <start_link> <end_link>`\n\n"
            "Count mode:\n"
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
            ACTIVE_TASKS[user_id] = False
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
    if text.startswith("/"):
        return

    # UTRs differ by bank; accept common alphanumeric refs but block obvious junk/duplicates.
    utr = re.sub(r"\s+", "", text).upper()
    if not re.fullmatch(r"[A-Z0-9-]{6,30}", utr):
        return await message.reply_text(
            tr(
                user_id,
                "❌ Invalid UTR format. Send only the transaction reference (6–30 letters/numbers).",
                "❌ UTR format சரியில்லை. 6–30 letters/numbers உள்ள transaction reference மட்டும் அனுப்புங்கள்.",
            )
        )
    if utr_exists(utr):
        return await message.reply_text(
            tr(user_id, "❌ This UTR was already submitted.", "❌ இந்த UTR ஏற்கனவே submit செய்யப்பட்டுள்ளது.")
        )

    state["utr"] = utr
    state["step"] = "screenshot"
    PAYMENT_STATES[user_id] = state
    await message.reply_text(
        tr(
            user_id,
            "📸 Now send the **payment screenshot** as a photo or image document.\nDo not send OTP, PIN, CVV, or bank password.",
            "📸 இப்போது **payment screenshot**-ஐ photo அல்லது image document ஆக அனுப்புங்கள்.\nOTP, PIN, CVV, bank password அனுப்பாதீர்கள்.",
        )
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
    try:
        add_pending_payment(payment_id, user_id, plan, period, amount, utr)
    except sqlite3.IntegrityError:
        PAYMENT_STATES.pop(user_id, None)
        return await message.reply_text("❌ This payment/UTR was already submitted.")

    PAYMENT_STATES.pop(user_id, None)
    await message.reply_text(
        tr(
            user_id,
            f"✅ Payment proof submitted.\nPayment ID: `{payment_id}`\nPlan: {PLAN_TITLES[plan]}\nAmount: ₹{amount}\n\nAdmin will manually verify it. You will get a message after approval/rejection.",
            f"✅ Payment proof submit செய்யப்பட்டது.\nPayment ID: `{payment_id}`\nPlan: {PLAN_TITLES[plan]}\nAmount: ₹{amount}\n\nAdmin manual-ஆ verify செய்வார். Approve/Reject ஆனதும் message வரும்.",
        )
    )

    username = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "No username"
    admin_caption = (
        "💳 **NEW MANUAL PAYMENT**\n"
        f"Payment ID: `{payment_id}`\n"
        f"User: {message.from_user.first_name if message.from_user else 'User'} • {username}\n"
        f"User ID: `{user_id}`\n"
        f"Plan: {PLAN_TITLES[plan]}\n"
        f"Period: {period.title()}\n"
        f"Amount: **₹{amount}**\n"
        f"UTR: `{utr}`\n\n"
        "⚠️ Verify payment in your UPI/bank app before approving."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"payapprove:{payment_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"payreject:{payment_id}"),
    ]])

    if not ADMIN_IDS:
        await message.reply_text("⚠️ ADMIN_IDS is not configured on the server; payment is saved as pending.")
        return

    for admin_id in ADMIN_IDS:
        try:
            if message.photo:
                await bot.send_photo(admin_id, message.photo.file_id, caption=admin_caption, reply_markup=kb)
            else:
                await bot.send_document(admin_id, message.document.file_id, caption=admin_caption, reply_markup=kb)
        except Exception as e:
            print(f"Payment proof send to admin {admin_id} failed: {e}")

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
        ACTIVE_TASKS[user_id] = False
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
    ACTIVE_TASKS[user_id] = True
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"

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

        await msg.edit_text(
            f"🚀 **Full Clone ஆரம்பம்**\n"
            f"Message ID: `{start_msg_id}` → `{latest_msg_id}`\n"
            f"Total scan: `{total}`\n"
            f"Stop: `/cancel`"
        )

        success_count = 0
        skipped_count = 0
        failed_count = 0
        processed = 0
        last_update = 0.0

        for i in range(start_msg_id, latest_msg_id + 1):
            if not ACTIVE_TASKS.get(user_id):
                break

            processed += 1

            try:
                target_msg = await ub.get_messages(target_chat_id, i)
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
                    file_path = None
                    try:
                        file_path = await ub.download_media(target_msg)
                        if file_path and ACTIVE_TASKS.get(user_id):
                            await send_media_original(
                                client, dest_chat, target_msg, file_path
                            )
                            success_count += 1
                            await asyncio.sleep(SEND_DELAY)
                    finally:
                        cleanup_file(file_path)

                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
                    await asyncio.sleep(min(SEND_DELAY, 1.0))
                else:
                    skipped_count += 1

            except FloodWait as fw:
                await asyncio.sleep(fw.value)
                failed_count += 1
            except Exception:
                failed_count += 1

            now = time.monotonic()
            if processed % BATCH_PROGRESS_EVERY == 0 or now - last_update >= 8:
                pct = min(100.0, processed * 100 / total)
                try:
                    await msg.edit_text(
                        "♻️ **Clone Progress**\n"
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
                "✅ **Full Clone முடிந்தது!**\n"
                f"✅ Sent: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`"
            )
        else:
            await msg.edit_text(
                "🛑 **Clone cancelled**\n"
                f"✅ Sent: `{success_count}` • ❌ Failed: `{failed_count}`"
            )

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

# ============================================================
# 12. ADVANCED BATCH
#     Mode A: /batch <start_link> <end_link>
#     Mode B: /batch <start_link> 100
#     Mode C: /batch <start_link> -50
# ============================================================
def parse_batch_count(text: str):
    # Last argument may be +100, 100, or -50.
    parts = (text or "").split()
    if len(parts) < 3:
        return None

    last = parts[-1].strip()
    if re.fullmatch(r"[+-]?\d+", last):
        return int(last)
    return None

@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    touch_user(message)
    user_id = message.chat.id
    allowed, reason = feature_allowed(user_id, "batch", 1)
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
    count_value = parse_batch_count(message.text)

    if not links:
        return await message.reply_text(
            "⚠️ பயன்பாடு:\n"
            "`/batch <start_link> <end_link>`\n"
            "அல்லது\n"
            "`/batch <start_link> 100`"
        )

    try:
        chat_id1, topic_id1, start_id = parse_telegram_link(links[0])

        # Range mode: two Telegram links
        if len(links) >= 2:
            chat_id2, topic_id2, end_id = parse_telegram_link(links[1])

            if chat_id1 != chat_id2:
                return await message.reply_text(
                    "❌ இரண்டு links-மும் ஒரே chat/group/channel-ஐ சேர்ந்ததாக இருக்க வேண்டும்."
                )

            if topic_id1 != topic_id2 and (topic_id1 or topic_id2):
                return await message.reply_text(
                    "❌ இரண்டு links-மும் ஒரே topic/thread-ஐ சேர்ந்ததாக இருக்க வேண்டும்."
                )

            topic_filter = topic_id1
            mode_text = "Link Range"

        # Count mode: one link + count
        elif count_value is not None:
            if count_value == 0:
                return await message.reply_text("❌ Batch count 0 ஆக இருக்க முடியாது.")

            topic_filter = topic_id1

            if count_value > 0:
                end_id = start_id + count_value - 1
            else:
                # Negative count = go backwards from start message.
                end_id = start_id
                start_id = max(1, start_id - abs(count_value) + 1)

            mode_text = f"Count {count_value}"

        else:
            return await message.reply_text(
                "⚠️ Batch format:\n"
                "`/batch <start_link> <end_link>`\n"
                "அல்லது\n"
                "`/batch <start_link> 100`\n"
                "`/batch <start_link> -50`"
            )

    except Exception as e:
        return await message.reply_text(f"❌ Link / batch format தவறு: {e}")

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    total = end_id - start_id + 1

    # Two-link range mode is reserved for Ultimate. Count batch is Standard+.
    if len(links) >= 2:
        allowed, reason = feature_allowed(user_id, "range", total)
    else:
        allowed, reason = feature_allowed(user_id, "batch", total)
    if not allowed:
        return await message.reply_text(reason)

    if MAX_BATCH_MESSAGES > 0 and total > MAX_BATCH_MESSAGES:
        return await message.reply_text(
            f"⚠️ இந்த batch `{total}` messages.\n"
            f"Current limit: `{MAX_BATCH_MESSAGES}`.\n"
            "Server `.env`-ல் `MAX_BATCH_MESSAGES` value change செய்யலாம்; "
            "`0` வைத்தால் unlimited."
        )

    msg = await message.reply_text(
        "📦 **Advanced Batch Starting**\n"
        f"Mode: `{mode_text}`\n"
        f"Message ID: `{start_id}` → `{end_id}`\n"
        f"Total: `{total}`\n"
        "Stop: `/cancel`"
    )

    ACTIVE_TASKS[user_id] = True
    dest_chat = await destination_for(user_id)
    cache_key = f"user:{user_id}" if ub is not server_userbot else "server"

    success_count = 0
    skipped_count = 0
    failed_count = 0
    processed = 0
    last_update = 0.0

    try:
        await fetch_message_safely(
            ub, cache_key, chat_id1, start_id, msg
        )

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
                    file_path = None
                    try:
                        file_path = await ub.download_media(target_msg)
                        if file_path and ACTIVE_TASKS.get(user_id):
                            await send_media_original(
                                client, dest_chat, target_msg, file_path
                            )
                            success_count += 1
                            consume_usage(user_id, 1)
                            await asyncio.sleep(SEND_DELAY)
                        else:
                            failed_count += 1
                    finally:
                        cleanup_file(file_path)

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
                "✅ **Batch முடிந்தது!**\n"
                f"Total scanned: `{processed}`\n"
                f"✅ Sent: `{success_count}`\n"
                f"⏭ Skipped: `{skipped_count}`\n"
                f"❌ Failed: `{failed_count}`"
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
        ACTIVE_TASKS[user_id] = False

# ============================================================
# 13. SINGLE LINK
# ============================================================
@bot.on_message(filters.regex(r"https?://t\.me/") & filters.private)
async def handle_single_link(client, message: Message):
    if message.text.startswith("/"):
        return

    touch_user(message)
    user_id = message.chat.id

    # Do not treat login/payment input as a save link.
    if user_id in LOGIN_STATES or user_id in PAYMENT_STATES:
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
    ACTIVE_TASKS[user_id] = True
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
            await msg.edit_text("📥 Original quality download...")
            file_path = None

            try:
                file_path = await ub.download_media(target_msg)

                if file_path and ACTIVE_TASKS.get(user_id):
                    await msg.edit_text("📤 Sending...")
                    await send_media_original(
                        client, dest_chat, target_msg, file_path
                    )
                    consume_usage(user_id, 1)
                    await msg.edit_text(
                        "✅ 100% original quality-ல் அனுப்பப்பட்டது!"
                    )
                else:
                    await msg.edit_text("❌ Download cancelled / failed.")
            finally:
                cleanup_file(file_path)

        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            consume_usage(user_id, 1)
            await msg.edit_text("✅ Message அனுப்பப்பட்டது!")
        else:
            await msg.edit_text("⚠️ Supported content இல்லை.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

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
        BotCommand("clone", "♻️ Full Group Clone"),
        BotCommand("batch", "📦 Advanced Batch Download"),
        BotCommand("cancel", "❌ Cancel Task"),
    ])

    print("🚀 Pro Max Saver Bot is Live & Ready!")

    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
