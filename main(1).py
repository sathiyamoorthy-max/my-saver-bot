import os
import re
import asyncio
import time
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
ALLOW_SHARED_SESSION = os.environ.get("ALLOW_SHARED_SESSION", "true").lower() in {
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
            ALLOW_SHARED_SESSION or (OWNER_ID and user_id == OWNER_ID)
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
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Login", callback_data="login_menu"),
        ],
        [
            InlineKeyboardButton("📦 Batch Help", callback_data="help_batch"),
            InlineKeyboardButton("♻️ Full Clone Help", callback_data="help_clone"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task"),
        ],
    ])

    text = (
        "🤖 **Pro Max Saver Bot**\n\n"
        "✨ **Features**\n"
        "• Single: Telegram message link அனுப்புங்கள்.\n"
        "• Advanced Batch: `/batch <start_link> <end_link>`\n"
        "• Count Batch: `/batch <start_link> 100`\n"
        "• Full Clone: `/clone <start_link>`\n"
        "• Login: `/login`\n"
        "• Cancel: `/cancel`\n\n"
        "🔒 உங்கள் Telegram account-க்கு access உள்ள chats/content மட்டும் பயன்படுத்துங்கள்."
    )
    await message.reply_text(text, reply_markup=keyboard)

@bot.on_message(filters.command("login") & filters.private)
async def login_cmd(client, message: Message):
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
        ALLOW_SHARED_SESSION or (OWNER_ID and user_id == OWNER_ID)
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

@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    user_id = query.message.chat.id

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
            ALLOW_SHARED_SESSION or (OWNER_ID and user_id == OWNER_ID)
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
# 9. PHONE / OTP / SESSION STRING LOGIN INPUT
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
    user_id = message.chat.id
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
    user_id = message.chat.id
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
                            await asyncio.sleep(SEND_DELAY)
                        else:
                            failed_count += 1
                    finally:
                        cleanup_file(file_path)

                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
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

    user_id = message.chat.id

    # Do not treat login input as a save link.
    if user_id in LOGIN_STATES:
        return

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
                    await msg.edit_text(
                        "✅ 100% original quality-ல் அனுப்பப்பட்டது!"
                    )
                else:
                    await msg.edit_text("❌ Download cancelled / failed.")
            finally:
                cleanup_file(file_path)

        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
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
        BotCommand("clone", "♻️ Full Group Clone"),
        BotCommand("batch", "📦 Advanced Batch Download"),
        BotCommand("cancel", "❌ Cancel Task"),
    ])

    print("🚀 Pro Max Saver Bot is Live & Ready!")

    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
