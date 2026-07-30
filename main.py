import os
import re
import asyncio

# --- 1. CRITICAL PYTHON 3.14 EVENT LOOP FIX (MUST BE AT THE VERY TOP) ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# -----------------------------------------------------------------------

from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, PeerIdInvalid, RPCError

# --- 2. FLASK KEEP-ALIVE SERVER FOR RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ World's First Perfect Pro Max Saver Bot is Live & Running!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# --- 3. ENVIRONMENT VARIABLES ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
DUMP_CHANNEL = os.environ.get("DUMP_CHANNEL", "")
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

# Task and Cache Trackers
ACTIVE_TASKS = {}
PEER_CACHE_INITIALIZED = False

# --- 4. SMART LINK PARSER & EXTRACTOR ---
def extract_telegram_links(text: str):
    # Fix glued links like /2https://t.me/
    fixed_text = re.sub(r'(\d)(https://t\.me/)', r'\1 \2', text)
    links = re.findall(r'https?://t\.me/[^\s]+', fixed_text)
    return links

def parse_telegram_link(url: str):
    url = url.strip().replace("<", "").replace(">", "")
    path = url.split("t.me/")[1].split("?")[0].strip("/")
    parts = path.split("/")
    
    if parts[0] == "c":
        chat_id = int("-100" + parts[1])
        if len(parts) == 4:
            topic_id = int(parts[2])
            msg_id = int(parts[3])
        else:
            topic_id = None
            msg_id = int(parts[2])
    else:
        chat_id = parts[0]
        if len(parts) == 3:
            topic_id = int(parts[1])
            msg_id = int(parts[2])
        else:
            topic_id = None
            msg_id = int(parts[1])
            
    return chat_id, topic_id, msg_id

# --- 5. AUTOMATIC PEER CACHE WARMER (Fixes PeerIdInvalid) ---
async def initialize_peer_cache(force=False):
    global PEER_CACHE_INITIALIZED
    if PEER_CACHE_INITIALIZED and not force:
        return
    if not userbot:
        return
    try:
        print("🔄 Userbot அனைத்து சாட்களையும் நினைவகத்தில் புதுப்பிக்கிறது...")
        async for dialog in userbot.get_dialogs(limit=200):
            pass
        PEER_CACHE_INITIALIZED = True
        print("✅ Peer Cache வெற்றிகரமாக புதுப்பிக்கப்பட்டது!")
    except Exception as e:
        print(f"⚠️ Cache warning: {e}")

async def fetch_message_safely(ub_client, chat_id, msg_id, status_msg=None):
    try:
        return await ub_client.get_messages(chat_id, msg_id)
    except Exception:
        if status_msg:
            await status_msg.edit_text("🔄 **Syncing...** பிரைவேட் குரூப் விபரங்களைச் சேகரிக்கிறது...")
        await initialize_peer_cache(force=True)
        return await ub_client.get_messages(chat_id, msg_id)

# --- 6. ORIGINAL QUALITY MEDIA SENDER ---
async def send_media_original(bot_client, target_chat, target_msg, file_path):
    caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
    
    if target_msg.audio:
        await bot_client.send_audio(
            target_chat, file_path, caption=caption,
            duration=target_msg.audio.duration,
            performer=target_msg.audio.performer,
            title=target_msg.audio.title
        )
    elif target_msg.video:
        await bot_client.send_video(
            target_chat, file_path, caption=caption,
            duration=target_msg.video.duration,
            width=target_msg.video.width,
            height=target_msg.video.height
        )
    elif target_msg.photo:
        await bot_client.send_photo(target_chat, file_path, caption=caption)
    elif target_msg.document:
        await bot_client.send_document(target_chat, file_path, caption=caption)
    elif target_msg.voice:
        await bot_client.send_voice(target_chat, file_path, caption=caption, duration=target_msg.voice.duration)
    else:
        await bot_client.send_document(target_chat, file_path, caption=caption)

# --- 7. COMMANDS & INLINE BUTTON HANDLERS ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Batch Download", callback_data="help_batch"),
            InlineKeyboardButton("♻️ Full Clone Help", callback_data="help_clone")
        ],
        [
            InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")
        ]
    ])
    text = (
        "🤖 **Pro Max Saver Bot (World Edition)**\n\n"
        "✨ **பயன்பாடுகள்:**\n"
        "1. **Single File:** ஒரு லிங்கை மட்டும் அனுப்புங்கள்.\n"
        "2. **Full Clone:** `/clone <ஆரம்ப_லிங்க்>` (கடைசி மெசேஜ் வரை Clone செய்யும்).\n"
        "3. **Range Batch:** `/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`\n"
        "4. **Cancel Task:** `/cancel`"
    )
    await message.reply_text(text, reply_markup=keyboard)

@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    user_id = query.message.chat.id
    if query.data == "help_batch":
        await query.message.reply_text("📦 **Batch Download:**\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    elif query.data == "help_clone":
        await query.message.reply_text("♻️ **Full Clone:**\n`/clone <ஆரம்ப_லிங்க்>`\n(குறிப்பிட்ட மெசேஜ் முதல் குரூப்பின் கடைசி மெசேஜ் வரை அனைத்தையும் டவுன்லோட் செய்யும்!)")
    elif query.data == "cancel_task":
        if ACTIVE_TASKS.get(user_id):
            ACTIVE_TASKS[user_id] = False
            await query.message.reply_text("❌ பணி நிறுத்தப்பட்டது!")
        else:
            await query.answer("எந்தப் பணியும் நடக்கவில்லை!", show_alert=True)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    user_id = message.chat.id
    if ACTIVE_TASKS.get(user_id):
        ACTIVE_TASKS[user_id] = False
        await message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")
    else:
        await message.reply_text("எந்தப் பணியும் தற்போது நடைபெறவில்லை.")

# --- 8. FULL GROUP / TOPIC CLONE (`/clone <start_link>`) ---
@bot.on_message(filters.command("clone") & filters.private)
async def handle_clone_full(client, message: Message):
    user_id = message.chat.id
    if not userbot: 
        return await message.reply_text("❌ STRING_SESSION அமைக்கப்படவில்லை!")
    if ACTIVE_TASKS.get(user_id): 
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")

    links = extract_telegram_links(message.text)
    if not links:
        return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")

    msg = await message.reply_text("🔄 குரூப்பை ஆய்வு செய்கிறது...")
    ACTIVE_TASKS[user_id] = True

    try:
        target_chat_id, topic_filter, start_msg_id = parse_telegram_link(links[0])
        await fetch_message_safely(userbot, target_chat_id, start_msg_id, msg)
        
        # Get latest message ID in group
        latest_msg_id = start_msg_id
        async for last_m in userbot.get_chat_history(target_chat_id, limit=1):
            latest_msg_id = last_m.id

        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_id
        await msg.edit_text(f"🚀 **Full Clone ஆரம்பமாகிறது!**\nMessage ID: {start_msg_id} முதல் {latest_msg_id} வரை...\nநிறுத்த `/cancel` அனுப்பவும்.")
        
        success_count = 0
        for i in range(start_msg_id, latest_msg_id + 1):
            if not ACTIVE_TASKS.get(user_id): 
                break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: 
                    continue

                if topic_filter:
                    msg_topic = getattr(target_msg, "message_thread_id", None) or getattr(target_msg, "reply_to_message_id", None)
                    if msg_topic != topic_filter and target_msg.id != topic_filter: 
                        continue

                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path and ACTIVE_TASKS.get(user_id):
                        await send_media_original(client, dest_chat, target_msg, file_path)
                        if os.path.exists(file_path): 
                            os.remove(file_path)
                        success_count += 1
                        await asyncio.sleep(2)
                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
                    await asyncio.sleep(1)
            except FloodWait as fw: 
                await asyncio.sleep(fw.value)
            except Exception: 
                continue

        if ACTIVE_TASKS.get(user_id):
            await msg.edit_text(f"✅ **Full Clone முடிந்தது!**\nமொத்தம் {success_count} பைல்கள் ஒரிஜினலாக அனுப்பப்பட்டன.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

# --- 9. RANGE BATCH DOWNLOAD (`/batch <link1> <link2>`) ---
@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    user_id = message.chat.id
    if not userbot: 
        return await message.reply_text("❌ STRING_SESSION அமைக்கப்படவில்லை!")
    if ACTIVE_TASKS.get(user_id): 
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")

    links = extract_telegram_links(message.text)
    if len(links) < 2:
        return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")

    try:
        chat_id1, topic_id1, start_id = parse_telegram_link(links[0])
        chat_id2, topic_id2, end_id = parse_telegram_link(links[1])
        if chat_id1 != chat_id2: 
            return await message.reply_text("❌ இரண்டு லிங்குகளும் ஒரே குரூப்பைச் சேர்ந்ததாக இருக்க வேண்டும்!")
        target_chat_id, topic_filter = chat_id1, topic_id1
    except Exception as e:
        return await message.reply_text(f"❌ லிங்க் பார்மேட் தவறு: {e}")

    if start_id > end_id: 
        start_id, end_id = end_id, start_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: Message ID {start_id} முதல் {end_id} வரை...")
    ACTIVE_TASKS[user_id] = True
    success_count = 0
    dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_id

    try:
        await fetch_message_safely(userbot, target_chat_id, start_id, msg)

        for i in range(start_id, end_id + 1):
            if not ACTIVE_TASKS.get(user_id): 
                break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: 
                    continue

                if topic_filter:
                    msg_topic = getattr(target_msg, "message_thread_id", None) or getattr(target_msg, "reply_to_message_id", None)
                    if msg_topic != topic_filter and target_msg.id != topic_filter: 
                        continue

                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path and ACTIVE_TASKS.get(user_id):
                        await send_media_original(client, dest_chat, target_msg, file_path)
                        if os.path.exists(file_path): 
                            os.remove(file_path)
                        success_count += 1
                        await asyncio.sleep(2)
                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success_count += 1
                    await asyncio.sleep(1)
            except FloodWait as fw: 
                await asyncio.sleep(fw.value)
            except Exception: 
                continue

        if ACTIVE_TASKS.get(user_id):
            await msg.edit_text(f"✅ **Batch டவுன்லோட் முடிந்தது!**\nமொத்தம் {success_count} பைல்கள் ஒரிஜினலாக அனுப்பப்பட்டன.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

# --- 10. SINGLE LINK DOWNLOAD HANDLER ---
@bot.on_message(filters.regex(r"https?://t\.me/") & filters.private)
async def handle_single_link(client, message: Message):
    if message.text.startswith("/"): 
        return
    user_id = message.chat.id
    if not userbot: 
        return await message.reply_text("❌ STRING_SESSION அமைக்கப்படவில்லை!")
    if ACTIVE_TASKS.get(user_id): 
        return await message.reply_text("⚠️ வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")

    links = extract_telegram_links(message.text)
    if not links: 
        return

    msg = await message.reply_text("🔄 லிங்கை ஆய்வு செய்கிறது...")
    ACTIVE_TASKS[user_id] = True

    try:
        chat_id, topic_id, msg_id = parse_telegram_link(links[0])
        target_msg = await fetch_message_safely(userbot, chat_id, msg_id, msg)

        if not target_msg or target_msg.empty:
            ACTIVE_TASKS[user_id] = False
            return await msg.edit_text("❌ பைல் கிடைக்கவில்லை அல்லது நீக்கப்பட்டுவிட்டது!")

        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_id

        if target_msg.media:
            await msg.edit_text("📥 ஒரிஜினல் குவாலிட்டியில் டவுன்லோட் ஆகிறது...")
            file_path = await userbot.download_media(target_msg)
            if file_path and ACTIVE_TASKS.get(user_id):
                await msg.edit_text("📤 உங்களுக்கு அனுப்பப்படுகிறது...")
                await send_media_original(client, dest_chat, target_msg, file_path)
                if os.path.exists(file_path): 
                    os.remove(file_path)
                await msg.edit_text("✅ 100% ஒரிஜினல் தரத்தில் வெற்றிகரமாக அனுப்பப்பட்டது!")
            elif file_path:
                if os.path.exists(file_path): 
                    os.remove(file_path)
        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            await msg.edit_text("✅ மெசேஜ் அனுப்பப்பட்டது!")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

# --- 11. ENGINE STARTER ---
async def main():
    if not os.path.exists("downloads"): 
        os.makedirs("downloads")
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot இணைக்கப்பட்டது!")
        await initialize_peer_cache(force=True)

    await bot.set_bot_commands([
        BotCommand("start", "🏠 Home"),
        BotCommand("clone", "♻️ Full Group Clone"),
        BotCommand("batch", "📦 Range Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ])
    
    print("🚀 Perfect Pro Max Saver Bot is Live & Ready!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
