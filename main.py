import os
import re
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import pytz
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BotCommand
)
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait,
    PeerIdInvalid,
    ChannelPrivate,
    UserAlreadyParticipant
)

# ----------------- CONFIGURATION -----------------
API_ID_RAW = os.environ.get("API_ID", "").strip()
DEFAULT_API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else 0
DEFAULT_API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

BOT_USERNAME = os.environ.get("BOT_USERNAME", "pro_saver_bot").strip()
PORT = int(os.environ.get("PORT", "8080").strip())

IST = pytz.timezone("Asia/Kolkata")

# Main Pyrogram Bot
bot = Client(
    "pro_saver_master_session",
    api_id=DEFAULT_API_ID,
    api_hash=DEFAULT_API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

db_lock = asyncio.Lock()
login_states = {}
task_queue = asyncio.Queue()

# ----------------- MULTI-LANGUAGE DICTIONARY -----------------
LANG = {
    "ta": {
        "welcome": "👋 **வணக்கம் {name}!**\n\nஇது உலகின் நம்பர் 1 **Restricted Saver, Cloner & Media Extractor Bot**.\n\n🏷️ **திட்டம்:** `{plan}`\n🔑 **கணக்கு:** {session}\n🎯 **மீடியா ஃபில்டர்:** `{filter}`\n\nRestricted சேனலின் போஸ்ட் லிங்கை இங்கு அனுப்புங்கள் அல்லது `/login` செய்து கணக்கை இணையுங்கள்.",
        "btn_login": "🔑 கணக்கு இணைக்க",
        "btn_plans": "📦 திட்டங்கள்",
        "btn_myplan": "📊 எனது திட்டம்",
        "btn_trial": "🎁 இலவச ட்ரையல்",
        "btn_bonus": "🎁 தினசரி போனஸ்",
        "btn_ref": "👥 ரெஃபர் & சம்பாதி",
        "btn_lang": "🌐 மொழியை மாற்ற",
        "btn_buy": "💳 வாங்க (UPI)",
        "not_connected": "⚠️ முதலில் `/login` செய்து உங்கள் Telegram கணக்கை இணைக்கவும்.",
        "limit_reached": "⛔ உங்கள் இன்றைய இலவச வரம்பு முடிந்தது. வரம்பற்ற பதிவிறக்கத்திற்கு விஐபி பிளான் எடுக்கவும்.",
        "downloading": "📥 பதிவிறக்கப்படுகிறது... ID: {id}",
        "uploading": "📤 பதிவேற்றப்படுகிறது... ID: {id}",
        "success": "✅ பதிவிறக்கம் வெற்றிகரமாக நிறைவடைந்தது!",
        "error": "❌ பிழை: {err}"
    },
    "en": {
        "welcome": "👋 **Hello {name}!**\n\nWelcome to World's Best **Restricted Saver, Cloner & Media Extractor Bot**.\n\n🏷️ **Plan:** `{plan}`\n🔑 **Session:** {session}\n🎯 **Filter:** `{filter}`\n\nSend any restricted link to download or use commands from menu.",
        "btn_login": "🔑 Connect Account",
        "btn_plans": "📦 VIP Plans",
        "btn_myplan": "📊 My Plan",
        "btn_trial": "🎁 Free Trial",
        "btn_bonus": "🎁 Daily Bonus",
        "btn_ref": "👥 Refer & Earn",
        "btn_lang": "🌐 Change Language",
        "btn_buy": "💳 Buy Plan (UPI)",
        "not_connected": "⚠️ Please `/login` first to connect your Telegram account.",
        "limit_reached": "⛔ Daily free limit reached. Upgrade to VIP for unlimited downloads.",
        "downloading": "📥 Downloading... ID: {id}",
        "uploading": "📤 Uploading... ID: {id}",
        "success": "✅ Download completed successfully!",
        "error": "❌ Error: {err}"
    },
    "hi": {
        "welcome": "👋 **नमस्ते {name}!**\n\nप्रतिबंधित सामग्री सेवर, बैच और चैनल क्लोनर बॉट में आपका स्वागत है।\n\n🏷️ **प्लान:** `{plan}`\n🔑 **सत्र:** {session}\n🎯 **फ़िल्टर:** `{filter}`",
        "btn_login": "🔑 खाता जोड़ें",
        "btn_plans": "📦 वीआईपी प्लान्स",
        "btn_myplan": "📊 मेरा प्लान",
        "btn_trial": "🎁 फ्री ट्रायल",
        "btn_bonus": "🎁 दैनिक बोनस",
        "btn_ref": "👥 रेफर और कमाएं",
        "btn_lang": "🌐 भाषा बदलें",
        "btn_buy": "💳 खरीदें (UPI)",
        "not_connected": "⚠️ कृपया पहले `/login` करके अपना खाता जोड़ें।",
        "limit_reached": "⛔ दैनिक सीमा समाप्त। असीमित के लिए अपग्रेड करें।",
        "downloading": "📥 डाउनलोड हो रहा है... ID: {id}",
        "uploading": "📤 अपलोड हो रहा है... ID: {id}",
        "success": "✅ सफलतापूर्वक पूर्ण हुआ!",
        "error": "❌ त्रुटि: {err}"
    }
}

# ----------------- THREADED HTTP KEEP-ALIVE SERVER -----------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Save Restricted Bot is 100% Online 24/7!")

    def log_message(self, format, *args):
        return

def run_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        print(f"[*] Threaded Ping Server active on port {PORT}")
        server.serve_forever()
    except Exception as e:
        print(f"[Health Server Warning] {e}")

# ----------------- DATABASE ENGINE -----------------
async def init_db():
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT,
                    plan TEXT DEFAULT 'Free',
                    daily_limit INTEGER DEFAULT 2,
                    daily_used INTEGER DEFAULT 0,
                    bonus_credits INTEGER DEFAULT 0,
                    streak_count INTEGER DEFAULT 0,
                    last_bonus_date TEXT,
                    referred_by INTEGER,
                    ref_count INTEGER DEFAULT 0,
                    lang TEXT DEFAULT 'ta',
                    session TEXT,
                    custom_api_id INTEGER,
                    custom_api_hash TEXT,
                    doc_thumb TEXT,
                    vid_thumb TEXT,
                    custom_caption TEXT,
                    file_filter TEXT DEFAULT 'all'
                )
            """)
            await db.commit()

async def get_user(user_id, name="User"):
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
            if not user:
                # Admin automatically gets Ultimate Lifetime Unlimited
                plan = "Ultimate" if user_id == ADMIN_ID else "Free"
                limit = 9999999 if user_id == ADMIN_ID else 2
                await db.execute(
                    "INSERT INTO users (user_id, name, plan, daily_limit, daily_used, lang) VALUES (?, ?, ?, ?, 0, 'ta')",
                    (user_id, name, plan, limit)
                )
                await db.commit()
                cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                user = await cursor.fetchone()
            return dict(user)

async def update_user(user_id, **kwargs):
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            for k, v in kwargs.items():
                await db.execute(f"UPDATE users SET {k} = ? WHERE user_id = ?", (v, user_id))
            await db.commit()

async def deduct_usage(user_id):
    # Absolute 100% Unlimited bypass for Admin and Ultimate users
    if user_id == ADMIN_ID:
        return True

    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT plan, daily_limit, daily_used, bonus_credits FROM users WHERE user_id = ?", (user_id,))
            u = await cursor.fetchone()
            if not u:
                return False

            if u["plan"] == "Ultimate":
                return True

            if u["daily_used"] < u["daily_limit"]:
                await db.execute("UPDATE users SET daily_used = daily_used + 1 WHERE user_id = ?", (user_id,))
                await db.commit()
                return True
            elif u["bonus_credits"] > 0:
                await db.execute("UPDATE users SET bonus_credits = bonus_credits - 1 WHERE user_id = ?", (user_id,))
                await db.commit()
                return True
            return False

async def daily_reset_job():
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute("UPDATE users SET daily_used = 0")
            await db.commit()
    print("[RESET] 12:00 AM IST Daily usage reset completed.")

# ----------------- ASYNC QUEUE WORKER -----------------
async def queue_worker():
    while True:
        task = await task_queue.get()
        try:
            handler, args, kwargs = task
            await asyncio.wait_for(handler(*args, **kwargs), timeout=1800)
        except Exception as e:
            print(f"[Queue Error] {e}")
        finally:
            task_queue.task_done()

# ----------------- COMMANDS & HANDLERS -----------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, msg: Message):
    user_id = msg.from_user.id
    user = await get_user(user_id, msg.from_user.first_name)
    lang = user.get("lang", "ta")
    t = LANG.get(lang, LANG["ta"])

    args = msg.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].replace("ref_", ""))
            if ref_id != user_id and not user.get("referred_by"):
                await update_user(user_id, referred_by=ref_id)
                async with db_lock:
                    async with aiosqlite.connect("bot_data.db") as db:
                        await db.execute("UPDATE users SET bonus_credits = bonus_credits + 2, ref_count = ref_count + 1 WHERE user_id = ?", (ref_id,))
                        await db.commit()
                try:
                    await bot.send_message(ref_id, f"🎉 **புதிய ரெஃபரல்!** {msg.from_user.first_name} உங்கள் லிங்க் மூலம் இணைந்தார். **+2 போனஸ் டவுன்லோட்கள்** கிடைத்தன!")
                except Exception:
                    pass
        except Exception:
            pass

    session_status = "✅ Connected" if user.get("session") else "❌ Not Connected"
    plan_display = "👑 Owner / Admin (Unlimited)" if user_id == ADMIN_ID else user["plan"]

    text = t["welcome"].format(
        name=msg.from_user.mention,
        plan=plan_display,
        session=session_status,
        filter=user.get("file_filter", "all").upper()
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(t["btn_login"], callback_data="btn_login"), InlineKeyboardButton(t["btn_plans"], callback_data="btn_plans")],
        [InlineKeyboardButton(t["btn_myplan"], callback_data="btn_myplan"), InlineKeyboardButton(t["btn_trial"], callback_data="btn_trial")],
        [InlineKeyboardButton(t["btn_bonus"], callback_data="btn_bonus"), InlineKeyboardButton(t["btn_ref"], callback_data="btn_ref")],
        [InlineKeyboardButton(t["btn_lang"], callback_data="btn_lang")]
    ])
    await msg.reply_text(text, reply_markup=buttons)

# ----------------- MULTI-LANGUAGE SELECTION -----------------
@bot.on_message(filters.command("lang") & filters.private)
async def lang_command(_, msg: Message):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("தமிழ் 🇮🇳", callback_data="set_ta"), InlineKeyboardButton("English 🌐", callback_data="set_en")],
        [InlineKeyboardButton("हिन्दी 🇮🇳", callback_data="set_hi")]
    ])
    await msg.reply_text("மொழியைத் தேர்ந்தெடுக்கவும் / Select Language:", reply_markup=btn)

# ----------------- LOGIN / SKIP WORKFLOW (FIXED API ID & PHONE) -----------------
@bot.on_message(filters.command("login") & filters.private)
async def login_handler(_, msg: Message):
    login_states[msg.from_user.id] = {"step": "API_ID"}
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Skip (Use Default Bot Keys)", callback_data="btn_skip_api")]
    ])
    await msg.reply_text(
        "Send your **Telegram API ID**.\n\n"
        "அல்லது பாட்டின் சொந்த சாவியைப் பயன்படுத்த கீழே உள்ள பட்டனை அழுத்தவும் அல்லது **/skip** அனுப்பவும்:",
        reply_markup=buttons
    )

@bot.on_message(filters.command("skip") & filters.private)
async def skip_command_handler(_, msg: Message):
    login_states[msg.from_user.id] = {
        "step": "PHONE",
        "api_id": DEFAULT_API_ID,
        "api_hash": DEFAULT_API_HASH
    }
    await msg.reply_text(
        "📲 **தயவுசெய்து உங்கள் மொபைல் எண்ணை நாட்டின் குறியீட்டுடன் (+91...) அனுப்பவும்:**\n\n"
        "உதாரணம்: `+919876543210`\n\n"
        "ரத்து செய்ய: `/cancel`"
    )

@bot.on_message(filters.command("logout") & filters.private)
async def logout_handler(_, msg: Message):
    await update_user(msg.from_user.id, session=None)
    login_states.pop(msg.from_user.id, None)
    await msg.reply_text("🚪 உங்கள் கணக்கு இணைப்பு துண்டிக்கப்பட்டது.")

@bot.on_message(filters.command("myplan") & filters.private)
async def myplan_handler(_, msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    is_unlimited = (user["plan"] == "Ultimate" or msg.from_user.id == ADMIN_ID)
    limit = "Unlimited" if is_unlimited else str(user["daily_limit"])
    used = user["daily_used"]
    bonus = user.get("bonus_credits", 0)
    left = "Unlimited" if is_unlimited else str(max(0, user["daily_limit"] - used) + bonus)

    reset_str = datetime.now(IST).strftime("%d-%m-%Y at 12:00 AM IST")
    text = (
        "📋 **USER PLAN DATA :**\n\n"
        f"👤 **USER**       : {user['name']}\n"
        f"⚡ **USER ID**    : `{user['user_id']}`\n"
        f"🏷️ **PLAN**       : {'👑 Admin (Unlimited)' if msg.from_user.id == ADMIN_ID else user['plan']}\n"
        f"📊 **TODAY**      : {used}/{limit} USED\n"
        f"🎁 **BONUS**      : {bonus} CREDITS\n"
        f"🎯 **TOTAL LEFT** : {left}\n\n"
        f"🔄 **RESETS**     : {reset_str}"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Upgrade Plan", callback_data="btn_plans"), InlineKeyboardButton("🎁 Free Trial", callback_data="btn_trial")]
    ])
    await msg.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("plans") & filters.private)
async def plans_handler(_, msg: Message):
    text = (
        "💎 **PREMIUM PLANS (100% UNLIMITED ACCESS)** 💎\n\n"
        "🥈 **Standard:** 50 files/day (₹50/wk | ₹180/mo)\n"
        "🥇 **Premium:** 100 files/day (₹80/wk | ₹280/mo)\n"
        "🔷 **Ultimate:** ♾️ Unlimited Downloads + Channel Cloner (₹130/wk | ₹450/mo)"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buy Plan (UPI)", callback_data="btn_buy")],
        [InlineKeyboardButton("💬 Admin Contact", url=f"tg://user?id={ADMIN_ID}")]
    ])
    await msg.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("trial") & filters.private)
async def trial_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    if user["plan"] != "Free" and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("நீங்கள் ஏற்கனவே விஐபி அல்லது ட்ரையல் திட்டத்தில் உள்ளீர்கள்.")
        return
    await update_user(msg.from_user.id, plan="Trial (Standard)", daily_limit=50)
    await msg.reply_text("🎁 1-நாள் Standard இலவச ட்ரையல் செயல்படுத்தப்பட்டது!")

@bot.on_message(filters.command("bonus") & filters.private)
async def bonus_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    today = datetime.now(IST).date()
    last_date_str = user.get("last_bonus_date")
    streak = user.get("streak_count", 0) or 0

    if last_date_str:
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        if last_date == today:
            await msg.reply_text("⚠️ இன்றைய போனஸை ஏற்கனவே பெற்றுவிட்டீர்கள்! நாளை மீண்டும் வரவும்.")
            return
        elif last_date == today - timedelta(days=1):
            streak += 1
        else:
            streak = 1
    else:
        streak = 1

    await update_user(
        msg.from_user.id,
        last_bonus_date=today.strftime("%Y-%m-%d"),
        streak_count=streak,
        bonus_credits=(user.get("bonus_credits", 0) or 0) + 1
    )
    reward_text = f"🎉 Daily bonus claimed!\n🔥 Streak: {streak} days\n🎁 +1 bonus download added."
    if streak >= 30 and user["plan"] == "Free":
        await update_user(msg.from_user.id, plan="Standard", daily_limit=50)
        reward_text += "\n\n🏆 30-day streak! You got free Standard plan!"
    await msg.reply_text(reward_text)

@bot.on_message(filters.command("referral") & filters.private)
async def referral_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{msg.from_user.id}"
    text = (
        "👥 **REFER & EARN PROGRAM**\n\n"
        "உங்கள் தனித்துவமான ரெஃபரல் லிங்க்:\n"
        f"`{ref_link}`\n\n"
        f"• அழைத்த நபர்கள்: `{user.get('ref_count', 0)}`\n"
        f"• போனஸ் கிரெடிட்கள்: `{user.get('bonus_credits', 0)}`\n\n"
        "💡 ஒவ்வொரு ரெஃபரலுக்கும் +2 கூடுதல் டவுன்லோட்கள் கிடைக்கும்!"
    )
    await msg.reply_text(text)

@bot.on_message(filters.command("filter") & filters.private)
async def filter_handler(_, msg: Message):
    args = msg.text.split()
    if len(args) < 2 or args[1].lower() not in ["all", "video", "doc", "audio", "photo"]:
        await msg.reply_text("பயன்பாடு: `/filter <all|video|doc|audio|photo>`")
        return
    chosen = args[1].lower()
    await update_user(msg.from_user.id, file_filter=chosen)
    await msg.reply_text(f"🎯 மீடியா ஃபில்டர் மாற்றப்பட்டது: `{chosen.upper()}`")

@bot.on_message(filters.command("setcaption") & filters.private)
async def setcaption_handler(_, msg: Message):
    caption = msg.text.partition(" ")[2].strip()
    if not caption:
        await msg.reply_text("பயன்பாடு: `/setcaption உங்கள் தனிப்பட்ட கேப்ஷன்`")
        return
    await update_user(msg.from_user.id, custom_caption=caption)
    await msg.reply_text(f"✅ தனிப்பட்ட கேப்ஷன் சேமிக்கப்பட்டது:\n`{caption}`")

@bot.on_message(filters.command("delcaption") & filters.private)
async def delcaption_handler(_, msg: Message):
    await update_user(msg.from_user.id, custom_caption=None)
    await msg.reply_text("🗑️ தனிப்பட்ட கேப்ஷன் நீக்கப்பட்டது.")

@bot.on_message(filters.command("id") & filters.private)
async def id_handler(_, msg: Message):
    await msg.reply_text(f"🆔 Your Telegram ID: `{msg.from_user.id}`")

# ----------------- THUMBNAIL MANAGEMENT -----------------
@bot.on_message(filters.command("setthumb") & filters.private)
async def setthumb_handler(_, msg: Message):
    if not msg.reply_to_message or not msg.reply_to_message.photo:
        await msg.reply_text("படத்திற்கு ரிப்ளை செய்து `/setthumb` அனுப்பவும்.")
        return
    os.makedirs("thumbnails", exist_ok=True)
    path = f"thumbnails/thumb_{msg.from_user.id}.jpg"
    await msg.reply_to_message.download(file_name=path)
    await update_user(msg.from_user.id, doc_thumb=path)
    await msg.reply_text("✅ Document Thumbnail சேமிக்கப்பட்டது!")

@bot.on_message(filters.command("delthumb") & filters.private)
async def delthumb_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    if user.get("doc_thumb") and os.path.exists(user["doc_thumb"]):
        os.remove(user["doc_thumb"])
    await update_user(msg.from_user.id, doc_thumb=None)
    await msg.reply_text("🗑️ Document Thumbnail நீக்கப்பட்டது.")

@bot.on_message(filters.command("mythumb") & filters.private)
async def mythumb_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    if user.get("doc_thumb") and os.path.exists(user["doc_thumb"]):
        await msg.reply_photo(user["doc_thumb"], caption="உங்கள் Document Thumbnail")
    else:
        await msg.reply_text("❌ Document Thumbnail எதுவும் வைக்கப்படவில்லை.")

@bot.on_message(filters.command("setvthumb") & filters.private)
async def setvthumb_handler(_, msg: Message):
    if not msg.reply_to_message or not msg.reply_to_message.photo:
        await msg.reply_text("படத்திற்கு ரிப்ளை செய்து `/setvthumb` அனுப்பவும்.")
        return
    os.makedirs("thumbnails", exist_ok=True)
    path = f"thumbnails/vthumb_{msg.from_user.id}.jpg"
    await msg.reply_to_message.download(file_name=path)
    await update_user(msg.from_user.id, vid_thumb=path)
    await msg.reply_text("✅ Video Thumbnail சேமிக்கப்பட்டது!")

@bot.on_message(filters.command("delvthumb") & filters.private)
async def delvthumb_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    if user.get("vid_thumb") and os.path.exists(user["vid_thumb"]):
        os.remove(user["vid_thumb"])
    await update_user(msg.from_user.id, vid_thumb=None)
    await msg.reply_text("🗑️ Video Thumbnail நீக்கப்பட்டது.")

@bot.on_message(filters.command("myvthumb") & filters.private)
async def myvthumb_handler(_, msg: Message):
    user = await get_user(msg.from_user.id)
    if user.get("vid_thumb") and os.path.exists(user["vid_thumb"]):
        await msg.reply_photo(user["vid_thumb"], caption="உங்கள் Video Thumbnail")
    else:
        await msg.reply_text("❌ Video Thumbnail எதுவும் வைக்கப்படவில்லை.")

# ----------------- LOGIN / OTP / TEXT PROCESSOR -----------------
@bot.on_message(filters.text & filters.private)
async def text_handler(client: Client, msg: Message):
    user_id = msg.from_user.id
    text = msg.text.strip()
    user = await get_user(user_id)
    lang = user.get("lang", "ta")
    t = LANG.get(lang, LANG["ta"])

    if text == "/cancel":
        login_states.pop(user_id, None)
        await msg.reply_text("செயல்பாடு ரத்து செய்யப்பட்டது.")
        return

    if text.lower() == "/skip":
        await skip_command_handler(client, msg)
        return

    # Auto phone number format trigger
    if text.startswith("+") and len(text.replace(" ", "")) >= 10 and user_id not in login_states:
        login_states[user_id] = {
            "step": "PHONE",
            "api_id": DEFAULT_API_ID,
            "api_hash": DEFAULT_API_HASH
        }

    state = login_states.get(user_id)

    # 1. Login State Machine
    if state:
        step = state.get("step")

        if step == "API_ID":
            if text.isdigit():
                login_states[user_id] = {"step": "API_HASH", "api_id": int(text)}
                await msg.reply_text("இப்போது உங்கள் **API HASH**-ஐ அனுப்பவும்:")
            else:
                await msg.reply_text("தயவுசெய்து சரியான API ID அனுப்பவும் அல்லது **/skip** தட்டவும்.")
            return

        if step == "API_HASH":
            login_states[user_id]["api_hash"] = text
            login_states[user_id]["step"] = "PHONE"
            await msg.reply_text(
                "📲 **உங்கள் மொபைல் எண்ணை நாட்டின் குறியீட்டுடன் (+91...) அனுப்பவும்:**\n"
                "உதாரணம்: `+919876543210`"
            )
            return

        if step == "PHONE":
            phone = text.replace(" ", "").replace("-", "")
            if not phone.startswith("+") or len(phone) < 10:
                await msg.reply_text("❌ சரியான மொபைல் எண்ணை நாட்டின் குறியீட்டுடன் (+91...) அனுப்பவும்:")
                return

            api_id = state.get("api_id", DEFAULT_API_ID)
            api_hash = state.get("api_hash", DEFAULT_API_HASH)
            u_client = Client(f"login_sess_{user_id}", api_id=api_id, api_hash=api_hash, in_memory=True)
            await u_client.connect()
            try:
                status_temp = await msg.reply_text("📩 Sending OTP to your Telegram account...")
                code = await u_client.send_code(phone)
                login_states[user_id].update({
                    "step": "OTP",
                    "client": u_client,
                    "phone": phone,
                    "hash": code.phone_code_hash
                })
                await status_temp.edit_text(
                    "📩 **உங்கள் அதிகாரப்பூர்வ Telegram ஆப்பில் OTP வந்துள்ளது.**\n\n"
                    "பாதுகாப்பிற்காக OTP எண்களுக்கு இடையே **இடைவெளி விட்டு** அனுப்பவும்:\n\n"
                    "👉 உதாரணம்: `1 2 3 4 5`\n\n"
                    "ரத்து செய்ய: `/cancel`"
                )
            except Exception as e:
                login_states.pop(user_id, None)
                if u_client.is_connected:
                    await u_client.disconnect()
                await msg.reply_text(f"❌ Error sending OTP: {str(e)}\n\nமீண்டும் `/login` செய்து முயற்சிக்கவும்.")
            return

        if step == "OTP":
            otp = text.replace(" ", "").replace("-", "")
            u_client = state.get("client")
            if not u_client or not u_client.is_connected:
                await msg.reply_text("⚠️ அமர்வு காலாவதியானது. மீண்டும் `/login` செய்யவும்.")
                login_states.pop(user_id, None)
                return

            try:
                await u_client.sign_in(state["phone"], state["hash"], otp)
                s_str = await u_client.export_session_string()
                await u_client.disconnect()
                await update_user(user_id, session=s_str, custom_api_id=state.get("api_id"), custom_api_hash=state.get("api_hash"))
                login_states.pop(user_id, None)
                await msg.reply_text(
                    "✅ **Account Login Successfully!** 🎉\n\n"
                    "உங்கள் கணக்கு வெற்றிகரமாக இணைக்கப்பட்டது!\n"
                    "இப்போது எந்தவொரு Restricted போஸ்ட் லிங்க்கையும் இங்கு அனுப்பலாம். 🚀"
                )
            except SessionPasswordNeeded:
                login_states[user_id]["step"] = "2FA"
                await msg.reply_text("🔐 உங்கள் கணக்கில் 2-Step Verification உள்ளது. பாஸ்வேர்டை அனுப்பவும்:")
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await msg.reply_text("❌ தவறான/காலாவதியான OTP. தயவுசெய்து எண்களுக்கு இடையே இடைவெளி விட்டு அனுப்பவும்: `1 2 3 4 5`")
            except Exception as e:
                login_states.pop(user_id, None)
                if u_client.is_connected:
                    await u_client.disconnect()
                await msg.reply_text(f"❌ Login Error: {str(e)}")
            return

        if step == "2FA":
            u_client = state.get("client")
            try:
                await u_client.check_password(password=text)
                s_str = await u_client.export_session_string()
                await u_client.disconnect()
                await update_user(user_id, session=s_str)
                login_states.pop(user_id, None)
                await msg.reply_text("✅ **Account Login Successfully with 2FA!** 🎉")
            except PasswordHashInvalid:
                await msg.reply_text("❌ தவறான 2FA பாஸ்வேர்ட். மீண்டும் பாஸ்வேர்டை அனுப்பவும்:")
            except Exception as e:
                login_states.pop(user_id, None)
                if u_client.is_connected:
                    await u_client.disconnect()
                await msg.reply_text(f"❌ 2FA Error: {str(e)}")
            return

    # 2. Direct Link Detection
    if "t.me/" in text:
        if not user.get("session"):
            btn = InlineKeyboardMarkup([[InlineKeyboardButton(t["btn_login"], callback_data="btn_login")]])
            await msg.reply_text(t["not_connected"], reply_markup=btn)
            return
        await task_queue.put((execute_download, (client, msg, user_id), {}))
        await msg.reply_text("⏳ பதிவிறக்கப் பணி வரிசையில் சேர்க்கப்பட்டது...")

# ----------------- REAL REFINED DOWNLOAD ENGINE (NO MISSING FILES) -----------------
async def execute_download(bot_client: Client, msg: Message, user_id: int):
    user = await get_user(user_id)
    lang = user.get("lang", "ta")
    t = LANG.get(lang, LANG["ta"])

    if not user.get("session"):
        await bot_client.send_message(user_id, t["not_connected"])
        return

    # Parse public or private channel link format
    # Matches: t.me/c/12345/678 or t.me/c/12345/678-685 or t.me/channelname/678
    match = re.search(r"t\.me/(?:c/)?([a-zA-Z0-9_]+)/(\d+)(?:-(\d+))?", msg.text.strip())
    if not match:
        await bot_client.send_message(user_id, "❌ தவறான டெலிகிராம் லிங்க்.")
        return

    chat_raw = match.group(1)
    start_id = int(match.group(2))
    end_id = int(match.group(3)) if match.group(3) else start_id

    # Format properly for Telegram API
    if chat_raw.isdigit():
        chat_id = int(f"-100{chat_raw}")
    else:
        chat_id = chat_raw

    filter_type = user.get("file_filter", "all")
    status_msg = await bot_client.send_message(user_id, "📥 டவுன்லோட் தொடங்குகிறது...")

    api_id = user.get("custom_api_id") or DEFAULT_API_ID
    api_hash = user.get("custom_api_hash") or DEFAULT_API_HASH

    u_client = Client(f"ub_exec_{user_id}", api_id=api_id, api_hash=api_hash, session_string=user["session"], in_memory=True)
    await u_client.connect()

    # Pre-cache channel to avoid PeerIdInvalid/ChannelPrivate
    try:
        await u_client.get_chat(chat_id)
    except Exception:
        pass

    downloaded_count = 0
    try:
        for cur_id in range(start_id, end_id + 1):
            if not await deduct_usage(user_id):
                await bot_client.send_message(user_id, t["limit_reached"])
                break

            try:
                target = await u_client.get_messages(chat_id, cur_id)
                if not target or target.empty:
                    continue

                caption = user.get("custom_caption") or target.caption or ""
                thumb_doc = user.get("doc_thumb") if user.get("doc_thumb") and os.path.exists(user["doc_thumb"]) else None
                thumb_vid = user.get("vid_thumb") if user.get("vid_thumb") and os.path.exists(user["vid_thumb"]) else None

                # 1. Plain Text Message
                if not target.media and target.text:
                    if filter_type in ["all", "doc"]:
                        await bot_client.send_message(user_id, target.text)
                        downloaded_count += 1
                    continue

                # 2. Filter Checking
                if filter_type == "video" and not target.video:
                    continue
                elif filter_type == "doc" and not target.document:
                    continue
                elif filter_type == "audio" and not (target.audio or target.voice):
                    continue
                elif filter_type == "photo" and not target.photo:
                    continue

                await status_msg.edit_text(t["downloading"].format(id=cur_id))

                # Direct High-Speed Download
                f_path = await target.download()

                await status_msg.edit_text(t["uploading"].format(id=cur_id))

                # 3. Media Uploading with Custom Thumbnails & Captions
                if target.document:
                    await bot_client.send_document(user_id, f_path, caption=caption, thumb=thumb_doc)
                elif target.video:
                    await bot_client.send_video(user_id, f_path, caption=caption, thumb=thumb_vid, supports_streaming=True)
                elif target.audio:
                    await bot_client.send_audio(user_id, f_path, caption=caption, thumb=thumb_doc)
                elif target.voice:
                    await bot_client.send_voice(user_id, f_path, caption=caption)
                elif target.photo:
                    await bot_client.send_photo(user_id, f_path, caption=caption)

                if os.path.exists(f_path):
                    os.remove(f_path)

                downloaded_count += 1
                await asyncio.sleep(1.5)

            except FloodWait as fw:
                await asyncio.sleep(fw.value)
            except Exception as item_err:
                print(f"[Fetch Error ID: {cur_id}]: {item_err}")
                continue

        if downloaded_count > 0:
            await status_msg.edit_text(t["success"])
        else:
            await status_msg.edit_text("⚠️ இந்த ஐடியில் பதிவிறக்க மீடியா எதுவும் கிடைக்கவில்லை (Empty Message or Restricted Peer).")
    except Exception as e:
        await status_msg.edit_text(t["error"].format(err=str(e)))
    finally:
        if u_client.is_connected:
            await u_client.disconnect()

# ----------------- FORWARDED MESSAGES HANDLER -----------------
@bot.on_message(filters.forwarded & filters.private)
async def forwarded_handler(client: Client, msg: Message):
    if not msg.media and not msg.text:
        await msg.reply_text("❌ இந்த செய்தியில் தரவிறக்க எதுவும் இல்லை.")
        return
    await task_queue.put((execute_forward_download, (client, msg, msg.from_user.id), {}))
    await msg.reply_text("⏳ பார்வர்ட் மீடியா வரிசையில் சேர்க்கப்பட்டது...")

async def execute_forward_download(bot_client: Client, msg: Message, user_id: int):
    user = await get_user(user_id)
    lang = user.get("lang", "ta")
    t = LANG.get(lang, LANG["ta"])

    if not user.get("session"):
        await bot_client.send_message(user_id, t["not_connected"])
        return

    if not await deduct_usage(user_id):
        await bot_client.send_message(user_id, t["limit_reached"])
        return

    if not msg.media and msg.text:
        await bot_client.send_message(user_id, msg.text)
        return

    status_msg = await bot_client.send_message(user_id, "📥 பதிவிறக்கப்படுகிறது...")
    try:
        file_path = await msg.download()
        await status_msg.edit_text("📤 பதிவேற்றப்படுகிறது...")

        caption = user.get("custom_caption") or msg.caption or ""
        thumb_doc = user.get("doc_thumb") if user.get("doc_thumb") and os.path.exists(user["doc_thumb"]) else None
        thumb_vid = user.get("vid_thumb") if user.get("vid_thumb") and os.path.exists(user["vid_thumb"]) else None

        if msg.document:
            await bot_client.send_document(user_id, file_path, caption=caption, thumb=thumb_doc)
        elif msg.video:
            await bot_client.send_video(user_id, file_path, caption=caption, thumb=thumb_vid, supports_streaming=True)
        elif msg.audio:
            await bot_client.send_audio(user_id, file_path, caption=caption, thumb=thumb_doc)
        elif msg.voice:
            await bot_client.send_voice(user_id, file_path, caption=caption)
        elif msg.photo:
            await bot_client.send_photo(user_id, file_path, caption=caption)

        if os.path.exists(file_path):
            os.remove(file_path)
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(t["error"].format(err=str(e)))

# ----------------- BATCH, CLONE & EXTRACT HANDLERS -----------------
@bot.on_message(filters.command("batch") & filters.private)
async def batch_handler(client: Client, msg: Message):
    user = await get_user(msg.from_user.id)
    if user["plan"] == "Free" and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("🔒 Batch வசதி Standard மற்றும் அதற்கு மேற்பட்ட பிளான்களில் மட்டுமே கிடைக்கும்.")
        return
    await msg.reply_text("📦 Batch Mode: `https://t.me/c/1234567890/10-30` என்ற வடிவில் அனுப்பவும்.")

@bot.on_message(filters.command("clone") & filters.private)
async def clone_handler(client: Client, msg: Message):
    user = await get_user(msg.from_user.id)
    if user["plan"] != "Ultimate" and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("⭐ சேனல் குளோனிங் வசதி Ultimate Plan-ல் மட்டுமே கிடைக்கும்.")
        return
    if not user.get("session"):
        await msg.reply_text("⚠️ முதலில் `/login` செய்யவும்.")
        return
    args = msg.text.split()
    if len(args) < 3:
        await msg.reply_text("பயன்பாடு: `/clone <source_id> <target_id>`")
        return
    await task_queue.put((execute_clone, (client, msg, user, args[1], args[2]), {}))
    await msg.reply_text("🚀 சேனல் குளோனிங் வரிசையில் சேர்க்கப்பட்டது...")

async def execute_clone(bot_client: Client, msg: Message, user: dict, src_raw, dest_raw):
    user_id = user["user_id"]
    src = int(src_raw) if src_raw.lstrip("-").isdigit() else src_raw
    dest = int(dest_raw) if dest_raw.lstrip("-").isdigit() else dest_raw

    status_msg = await bot_client.send_message(user_id, "🚀 குளோனிங் தொடங்குகிறது...")
    u_client = Client(f"ub_clone_{user_id}", session_string=user["session"], in_memory=True)
    await u_client.connect()
    count = 0
    try:
        async for post in u_client.get_chat_history(src):
            if not await deduct_usage(user_id):
                await bot_client.send_message(user_id, "⛔ வரம்பு முடிந்தது.")
                break
            try:
                cap = user.get("custom_caption") or post.caption or ""
                thumb_doc = user.get("doc_thumb") if user.get("doc_thumb") and os.path.exists(user["doc_thumb"]) else None
                thumb_vid = user.get("vid_thumb") if user.get("vid_thumb") and os.path.exists(user["vid_thumb"]) else None

                if post.media:
                    f = await post.download()
                    if post.document:
                        await u_client.send_document(dest, f, caption=cap, thumb=thumb_doc)
                    elif post.video:
                        await u_client.send_video(dest, f, caption=cap, thumb=thumb_vid)
                    elif post.audio:
                        await u_client.send_audio(dest, f, caption=cap, thumb=thumb_doc)
                    elif post.voice:
                        await u_client.send_voice(dest, f, caption=cap)
                    elif post.photo:
                        await u_client.send_photo(dest, f, caption=cap)

                    if os.path.exists(f):
                        os.remove(f)
                elif post.text:
                    await u_client.send_message(dest, post.text)

                count += 1
                if count % 5 == 0:
                    await status_msg.edit_text(f"🔄 Cloned {count} items...")
                await asyncio.sleep(2)
            except FloodWait as fw:
                await asyncio.sleep(fw.value)
            except Exception:
                continue

        await status_msg.edit_text(f"✅ குளோனிங் முடிந்தது. மொத்தம் மாற்றப்பட்ட பதிவுகள்: {count}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        if u_client.is_connected:
            await u_client.disconnect()

@bot.on_message(filters.command("extract") & filters.private)
async def extract_handler(client: Client, msg: Message):
    user = await get_user(msg.from_user.id)
    if not user.get("session"):
        await msg.reply_text("⚠️ முதலில் `/login` செய்யவும்.")
        return
    args = msg.text.split()
    if len(args) < 2:
        await msg.reply_text("பயன்பாடு: `/extract <channel_id>`")
        return
    target = int(args[1]) if args[1].lstrip("-").isdigit() else args[1]
    await task_queue.put((execute_extract, (client, msg, user, target), {}))
    await msg.reply_text("📑 பிளேலிஸ்ட் தொகுக்கப்பட்டு வருகிறது...")

async def execute_extract(bot_client: Client, msg: Message, user: dict, target):
    user_id = user["user_id"]
    status_msg = await bot_client.send_message(user_id, "📑 தொகுக்கப்படுகிறது...")
    u_client = Client(f"ub_ext_{user_id}", session_string=user["session"], in_memory=True)
    await u_client.connect()
    file_name = f"playlist_{user_id}.txt"
    count = 0
    try:
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(f"--- Playlist for {target} ---\n\n")
            async for post in u_client.get_chat_history(target):
                count += 1
                media_type = "Document" if post.document else "Video" if post.video else "Audio" if post.photo else "Media"
                f.write(f"{count}. {media_type} | ID: {post.id} | https://t.me/c/{str(target).replace('-100', '')}/{post.id}\n")
        await bot_client.send_document(user_id, file_name, caption=f"✅ {count} பதிவுகள் தொகுக்கப்பட்டன.")
        if os.path.exists(file_name):
            os.remove(file_name)
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        if u_client.is_connected:
            await u_client.disconnect()

# ----------------- ADMIN COMMANDS (/ap, /rp, /ps, /stats, /broadcast) -----------------
@bot.on_message(filters.command("ap") & filters.user(ADMIN_ID))
async def admin_ap(_, msg: Message):
    args = msg.text.split()
    if len(args) < 3:
        await msg.reply_text("Usage: `/ap <user_id> <Standard|Premium|Ultimate>`")
        return
    t_uid = int(args[1])
    t_plan = args[2].capitalize()
    limits = {"Standard": 50, "Premium": 100, "Ultimate": 9999999}
    if t_plan not in limits:
        await msg.reply_text("தவறான திட்டம்! (Standard, Premium, Ultimate).")
        return
    await update_user(t_uid, plan=t_plan, daily_limit=limits[t_plan])
    await msg.reply_text(f"✅ User `{t_uid}` திட்டம் `{t_plan}` ஆக மாற்றப்பட்டது.")

@bot.on_message(filters.command("rp") & filters.user(ADMIN_ID))
async def admin_rp(_, msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        await msg.reply_text("Usage: `/rp <user_id>`")
        return
    t_uid = int(args[1])
    await update_user(t_uid, plan="Free", daily_limit=2, daily_used=0)
    await msg.reply_text(f"🔄 User `{t_uid}` இலவச நிலைக்கு ரீசெட் செய்யப்பட்டார்.")

@bot.on_message(filters.command("ps") & filters.user(ADMIN_ID))
async def admin_ps(_, msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        await msg.reply_text("Usage: `/ps <user_id>`")
        return
    t_uid = int(args[1])
    u = await get_user(t_uid)
    await msg.reply_text(f"📋 User `{t_uid}`:\nPlan: {u['plan']}\nLimit: {u['daily_limit']}\nUsed: {u['daily_used']}\nBonus: {u['bonus_credits']}")

@bot.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def admin_stats(_, msg: Message):
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            c1 = await db.execute("SELECT COUNT(*) FROM users")
            total = (await c1.fetchone())[0]
            c2 = await db.execute("SELECT COUNT(*) FROM users WHERE plan != 'Free'")
            prem = (await c2.fetchone())[0]
    await msg.reply_text(f"📊 **Statistics:**\nTotal Users: `{total}`\nVIP Members: `{prem}`\nQueue Tasks: `{task_queue.qsize()}`")

@bot.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def admin_broadcast(_, msg: Message):
    if not msg.reply_to_message:
        await msg.reply_text("மெசேஜுக்கு ரிப்ளை செய்து `/broadcast` அனுப்பவும்.")
        return
    async with db_lock:
        async with aiosqlite.connect("bot_data.db") as db:
            c = await db.execute("SELECT user_id FROM users")
            rows = await c.fetchall()
    sent = 0
    for (uid,) in rows:
        try:
            await msg.reply_to_message.copy(uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await msg.reply_text(f"✅ பிராட்காஸ்ட் நிறைவடைந்தது: {sent} பயனர்கள்.")

# ----------------- CALLBACK BUTTONS ROUTER -----------------
@bot.on_callback_query()
async def cb_handler(client: Client, q: CallbackQuery):
    data = q.data
    user_id = q.from_user.id
    if data == "btn_login":
        await login_handler(client, q.message)
    elif data == "btn_skip_api":
        await skip_command_handler(client, q.message)
    elif data == "btn_plans":
        await plans_handler(client, q.message)
    elif data == "btn_myplan":
        await myplan_handler(client, q.message)
    elif data == "btn_trial":
        await trial_handler(client, q.message)
    elif data == "btn_bonus":
        await bonus_handler(client, q.message)
    elif data == "btn_ref":
        await referral_handler(client, q.message)
    elif data == "btn_buy":
        await q.message.reply_text("💳 **Payment (UPI):**\n\nUPI ID: `your-upi@okaxis`\nபணம் செலுத்தியதும் ஸ்கிரீன்ஷாட்டை அட்மினுக்கு அனுப்பவும்.")
    elif data == "btn_lang":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("தமிழ் 🇮🇳", callback_data="set_ta"), InlineKeyboardButton("English 🌐", callback_data="set_en")],
            [InlineKeyboardButton("हिन्दी 🇮🇳", callback_data="set_hi")]
        ])
        await q.message.edit_text("மொழியைத் தேர்ந்தெடுக்கவும் / Select Language:", reply_markup=btn)
    elif data.startswith("set_"):
        new_lang = data.replace("set_", "")
        await update_user(user_id, lang=new_lang)
        await q.answer("Language Updated!")
        await start_handler(client, q.message)
    await q.answer()

# ----------------- MAIN BOOTSTRAP -----------------
async def start_services():
    await init_db()
    asyncio.create_task(queue_worker())

    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(daily_reset_job, "cron", hour=0, minute=0)
    scheduler.start()

    # Start Client First
    await bot.start()
    print("[*] Pyrogram Client Connected Successfully!")

    # Set Menu Commands
    try:
        commands = [
            BotCommand("start", "Home"),
            BotCommand("login", "Connect Telegram Account"),
            BotCommand("skip", "Skip API ID (Phone Login)"),
            BotCommand("logout", "Logout Session"),
            BotCommand("batch", "Batch Fetch (Standard+)"),
            BotCommand("clone", "Clone Entire Channel"),
            BotCommand("extract", "Extract Playlist Index"),
            BotCommand("filter", "Set Media Filter"),
            BotCommand("setcaption", "Custom Caption & Watermark"),
            BotCommand("delcaption", "Delete Caption"),
            BotCommand("referral", "Refer & Earn"),
            BotCommand("bonus", "Daily Streak Bonus"),
            BotCommand("trial", "1-Day Free Trial"),
            BotCommand("myplan", "My Plan & Limits"),
            BotCommand("plans", "View VIP Plans"),
            BotCommand("lang", "Change Language"),
            BotCommand("id", "Get Telegram ID"),
            BotCommand("setthumb", "Set Document Thumbnail"),
            BotCommand("delthumb", "Delete Document Thumbnail"),
            BotCommand("mythumb", "View Document Thumbnail"),
            BotCommand("setvthumb", "Set Video Thumbnail"),
            BotCommand("delvthumb", "Delete Video Thumbnail"),
            BotCommand("myvthumb", "View Video Thumbnail")
        ]
        await bot.set_bot_commands(commands)
        print("[*] Commands menu registered successfully!")
    except Exception as e:
        print(f"[Warning] Menu register: {e}")

    print("========================================")
    print("  [SUCCESS] Bot is online and polling!  ")
    print("========================================")

    await idle()
    await bot.stop()

def main():
    if not DEFAULT_API_ID or not DEFAULT_API_HASH or not BOT_TOKEN:
        print("[FATAL ERROR] API_ID, API_HASH, அல்லது BOT_TOKEN அமைக்கப்படவில்லை!")
        return

    # Render Web Service Keep-Alive
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_services())

if __name__ == "__main__":
    main()
