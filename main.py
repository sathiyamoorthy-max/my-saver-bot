import os
import asyncio
import requests
import yt_dlp

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread
import google.generativeai as genai

# --- WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Ultimate Pro Max Bot v6.2 is Running!"
def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
Thread(target=run_server, daemon=True).start()

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
DUMP_CHANNEL = os.environ.get("DUMP_CHANNEL", "")
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "") 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
WP_URL = os.environ.get("WP_URL", "")
WP_USER = os.environ.get("WP_USER", "")
WP_PASS = os.environ.get("WP_PASS", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-pro')

ACTIVE_TASKS = []

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home"),
        BotCommand("dl", "📥 Download YT/Insta"),
        BotCommand("clone", "♻️ Clone Single Topic/Link"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("ai", "🤖 AI Script Maker"),
        BotCommand("wp", "🌐 Auto Post to WP"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- 🟢 START COMMAND & BUTTONS ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 Batch Download", callback_data="help_batch"),
                InlineKeyboardButton("♻️ Clone Group", callback_data="help_clone")
            ],
            [
                InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")
            ]
        ]
    )
    text = (
        "🤖 **Pro Max Saver Bot (v6.2 - The Universe Edition)**\n\n"
        "✨ நான் உங்கள் All-in-One Content Assistant!\n"
        "• YouTube / Insta லிங்குகளை `/dl <link>` என்று அனுப்பவும்.\n"
        "• Telegram குரூப் பைல்களை எடுக்க `/clone` அல்லது `/batch` பயன்படுத்தவும்."
    )
    await message.reply_text(text, reply_markup=buttons)

@bot.on_callback_query()
async def callback_handler(client, query):
    if query.data == "help_batch":
        await query.message.reply_text("📦 **Batch Download பயன்பாடு:**\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`\n\nஉதாரணம்:\n`/batch https://t.me/c/123/10 https://t.me/c/123/20`")
    elif query.data == "help_clone":
        await query.message.reply_text("♻️ **Clone பயன்பாடு:**\n`/clone <லிங்க்>`\n\nஉதாரணம்:\n`/clone https://t.me/c/12345/10`")
    elif query.data == "cancel_task":
        if query.message.chat.id in ACTIVE_TASKS:
            ACTIVE_TASKS.remove(query.message.chat.id)
        await query.message.reply_text("❌ வேலை நிறுத்தப்பட்டது!")

# --- 📥 YOUTUBE / INSTA DOWNLOADER ---
def download_yt_dlp(url):
    ydl_opts = {
        'format': 'best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'cookies.txt', 
        'http_headers': { 'User-Agent': 'Mozilla/5.0' }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

@bot.on_message(filters.command("dl") & filters.private)
async def social_media_dl(client, message: Message):
    if len(message.text.split()) < 2:
        return await message.reply_text("⚠️ பயன்பாடு: `/dl <YouTube அல்லது Insta லிங்க்>`")
    url = message.text.split(maxsplit=1)[1]
    msg = await message.reply_text("📥 வீடியோவை டவுன்லோட் செய்கிறது...")
    try:
        file_path = await asyncio.to_thread(download_yt_dlp, url)
        await msg.edit_text("📤 வீடியோவை அனுப்புகிறது...")
        await client.send_video(message.chat.id, file_path, caption=f"✨ Downloaded via Pro Max Bot")
        os.remove(file_path)
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ எரர்: {e}")

# --- 📦 BATCH DOWNLOADER ---
@bot.on_message(filters.command("batch") & filters.private)
async def batch_cmd(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    parts = message.text.split()
    if len(parts) != 3: return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    
    start_link = parts[1]
    end_link = parts[2]
    
    try:
        target_chat_id = int("-100" + start_link.split("/c/")[1].split("/")[0])
        start_msg_id = int(start_link.split("/")[-1])
        end_msg_id = int(end_link.split("/")[-1])
    except:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    if start_msg_id > end_msg_id:
        start_msg_id, end_msg_id = end_msg_id, start_msg_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: {start_msg_id} முதல் {end_msg_id} வரை...")
    ACTIVE_TASKS.append(chat_id)
    success = 0
    
    try:
        for i in range(start_msg_id, end_msg_id + 1): 
            if chat_id not in ACTIVE_TASKS: break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: continue
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path:
                        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                        if target_msg.video: await client.send_video(dest_chat, file_path, duration=target_msg.video.duration)
                        elif target_msg.document: await client.send_document(dest_chat, file_path)
                        elif target_msg.photo: await client.send_photo(dest_chat, file_path)
                        os.remove(file_path)
                        success += 1
                        await asyncio.sleep(2)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception: continue
            
        if chat_id in ACTIVE_TASKS:
            await msg.edit_text(f"✅ **Batch முடிந்தது!**\nமொத்தம் {success} பைல்கள்.")
            ACTIVE_TASKS.remove(chat_id)
    except Exception as e:
        if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
        await msg.edit_text(f"❌ பிழை: {e}")

# --- ♻️ CLONE (Single Link) ---
@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")
    link = parts[1].strip()
    
    try:
        target_chat_id = int("-100" + link.split("/c/")[1].split("/")[0])
        msg_id = int(link.split("/")[-1])
    except:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    msg = await message.reply_text("🔄 பைலை எடுக்கிறது...")
    try:
        target_msg = await userbot.get_messages(target_chat_id, msg_id)
        if target_msg.media:
            file_path = await userbot.download_media(target_msg)
            if file_path:
                dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                if target_msg.video: await client.send_video(dest_chat, file_path)
                elif target_msg.document: await client.send_document(dest_chat, file_path)
                elif target_msg.photo: await client.send_photo(dest_chat, file_path)
                os.remove(file_path)
                await msg.edit_text("✅ வெற்றிகரமாக எடுக்கப்பட்டது!")
        elif target_msg.text:
            await client.send_message(int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id, target_msg.text)
            await msg.edit_text("✅ டெக்ஸ்ட் அனுப்பப்பட்டது!")
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")

# --- AI & WP (Basic placeholders) ---
@bot.on_message(filters.command("ai") & filters.private)
async def ai_generate(client, message: Message):
    if not GEMINI_API_KEY: return await message.reply_text("❌ GEMINI_API_KEY இல்லை!")
    # AI logic...

@bot.on_message(filters.command("wp") & filters.private)
async def wp_post(client, message: Message):
    if not all([WP_URL, WP_USER, WP_PASS]): return await message.reply_text("❌ WP Login Details இல்லை!")
    # WP logic...

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    if message.chat.id in ACTIVE_TASKS: ACTIVE_TASKS.remove(message.chat.id)
    await message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")

async def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    await bot.start()
    if userbot: await userbot.start()
    await set_bot_commands(bot)
    print("✅ All-in-One Mega Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
