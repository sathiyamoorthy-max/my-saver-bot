import os
import asyncio

# --- 1. RENDER EVENT LOOP FIX (Top Priority) ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import requests
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread
import google.generativeai as genai

# --- 2. WEB SERVER (For Render Keep-Alive) ---
app = Flask(__name__)
@app.route('/')
def home(): return "✅ All-in-One Ultimate Pro Max Bot is Running!"
def run_server(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
Thread(target=run_server, daemon=True).start()

# --- 3. CONFIGURATION (Environment Variables) ---
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

ACTIVE_TASKS = {}

# --- HELPER: LINK PARSER ---
def parse_link(url: str):
    url = url.replace("https://", "").replace("http://", "").replace("t.me/", "").strip()
    parts = url.split("/")
    if parts[0] == "c":
        return int("-100" + parts[1]), int(parts[2])
    return parts[0], int(parts[1])

# --- HELPER: ADVANCED SYNC (Fix for PeerIdInvalid) ---
async def fetch_target_message(client, chat_id, msg_id, status_msg=None):
    try:
        return await client.get_messages(chat_id, msg_id)
    except Exception as e:
        if "PEER_ID_INVALID" in str(e) or isinstance(e, PeerIdInvalid):
            if status_msg: await status_msg.edit_text("🔄 **Syncing...** Userbot குரூப்பைத் தேடுகிறது...")
            async for _ in client.get_dialogs(limit=100): pass
            return await client.get_messages(chat_id, msg_id)
        raise e

# --- COMMAND: START ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")]])
    text = (
        "🤖 **All-in-One Master Bot**\n\n"
        "📥 **Telegram:** லிங்கை நேரடியாக அனுப்பவும் அல்லது `/batch` பயன்படுத்தவும்.\n"
        "🎥 **YT/Insta:** `/dl <link>`\n"
        "🤖 **AI Script:** `/ai <prompt>`\n"
        "🌐 **WP Post:** `/wp <text>`"
    )
    await message.reply_text(text, reply_markup=buttons)

@bot.on_callback_query(filters.regex("cancel_task"))
async def cancel_callback(client, query):
    chat_id = query.message.chat.id
    if ACTIVE_TASKS.get(chat_id):
        ACTIVE_TASKS[chat_id] = False
        await query.message.reply_text("❌ வேலை நிறுத்தப்பட்டது!")
    else:
        await query.answer("எந்த வேலையும் நடக்கவில்லை!", show_alert=True)

# --- 🚀 ADVANCED FEATURE: AI SCRIPT GENERATOR ---
@bot.on_message(filters.command("ai") & filters.private)
async def ai_generate(client, message: Message):
    if not GEMINI_API_KEY: return await message.reply_text("❌ GEMINI_API_KEY இல்லை!")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/ai Jujutsu Kaisen Season 2 Episode 2 explanation in Tamil`")
    msg = await message.reply_text("🧠 AI யோசிக்கிறது...")
    try:
        response = ai_model.generate_content(parts[1])
        await msg.edit_text(f"✨ **AI Response:**\n\n{response.text}")
    except Exception as e: await msg.edit_text(f"❌ AI எரர்: {e}")

# --- 🚀 ADVANCED FEATURE: YOUTUBE / INSTA DOWNLOADER ---
def download_yt_dlp(url):
    ydl_opts = {
        'format': 'best', 'outtmpl': 'downloads/%(title)s.%(ext)s', 'quiet': True,
        'cookiefile': 'cookies.txt', 'http_headers': { 'User-Agent': 'Mozilla/5.0' }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

@bot.on_message(filters.command("dl") & filters.private)
async def social_media_dl(client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/dl <YouTube/Insta Link>`")
    msg = await message.reply_text("📥 வீடியோவை சர்வரில் டவுன்லோட் செய்கிறது...")
    try:
        if not os.path.exists("downloads"): os.makedirs("downloads")
        file_path = await asyncio.to_thread(download_yt_dlp, parts[1])
        await msg.edit_text("📤 வீடியோவை அனுப்புகிறது...")
        await client.send_video(message.chat.id, file_path, caption="✨ Downloaded via Master Bot")
        os.remove(file_path)
        await msg.delete()
    except Exception as e: await msg.edit_text(f"❌ டவுன்லோட் எரர்: {e}")

# --- 🚀 ADVANCED FEATURE: WORDPRESS AUTO PUBLISHER ---
@bot.on_message(filters.command("wp") & filters.private)
async def wp_post(client, message: Message):
    if not all([WP_URL, WP_USER, WP_PASS]): return await message.reply_text("❌ WP Login Details இல்லை!")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/wp <content>`")
    msg = await message.reply_text("🌐 வெப்சைட்டில் போஸ்ட் செய்யப்படுகிறது...")
    try:
        res = requests.post(WP_URL, auth=(WP_USER, WP_PASS), json={"title": "Auto Post", "content": parts[1], "status": "publish"})
        if res.status_code == 201: await msg.edit_text(f"✅ **Publish ஆகிவிட்டது!**\n🔗 {res.json().get('link')}")
        else: await msg.edit_text(f"❌ எரர்: {res.text}")
    except Exception as e: await msg.edit_text(f"❌ பிழை: {e}")

# --- 🔥 CORE: SINGLE LINK / AUTO-CLONE (FIXED INDEX ERROR) ---
@bot.on_message((filters.command("clone") | filters.regex(r"t\.me/(c/)?")) & filters.private)
async def single_link_dl(client, message: Message):
    if message.text.startswith("/batch"): return
    
    # IndexError Fix: Check if link exists in command
    if message.text.startswith("/clone"):
        parts = message.text.split()
        if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/clone <link>`")
        link = parts[1]
    else:
        link = message.text.strip()
        
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ String Session இல்லை.")
    if ACTIVE_TASKS.get(chat_id): return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    msg = await message.reply_text("🔄 பைலைத் தேடுகிறது...")
    ACTIVE_TASKS[chat_id] = True
    
    try:
        target_chat_id, msg_id = parse_link(link)
        target_msg = await fetch_target_message(userbot, target_chat_id, msg_id, msg)
        
        if not target_msg or target_msg.empty:
            ACTIVE_TASKS[chat_id] = False
            return await msg.edit_text("❌ பைல் கிடைக்கவில்லை!")
            
        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
        
        if target_msg.media:
            await msg.edit_text("📥 ஒரிஜினல் குவாலிட்டியில் டவுன்லோட் ஆகிறது...")
            file_path = await userbot.download_media(target_msg)
            
            if file_path and ACTIVE_TASKS[chat_id]:
                await msg.edit_text("📤 உங்களுக்கு அனுப்பப்படுகிறது...")
                caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
                
                if target_msg.video: await client.send_video(dest_chat, file_path, caption=caption)
                elif target_msg.document: await client.send_document(dest_chat, file_path, caption=caption)
                elif target_msg.photo: await client.send_photo(dest_chat, file_path, caption=caption)
                elif target_msg.audio: await client.send_audio(dest_chat, file_path, caption=caption)
                
                os.remove(file_path)
                await msg.edit_text("✅ வெற்றிகரமாக அனுப்பப்பட்டது!")
        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            await msg.edit_text("✅ மெசேஜ் அனுப்பப்பட்டது!")
            
    except Exception as e: await msg.edit_text(f"❌ பிழை: {e}")
    finally: ACTIVE_TASKS[chat_id] = False

# --- 🔥 CORE: BATCH DOWNLOADER ---
@bot.on_message(filters.command("batch") & filters.private)
async def batch_cmd(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ String Session இல்லை.")
    if ACTIVE_TASKS.get(chat_id): return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    parts = message.text.split()
    if len(parts) != 3: return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    
    try:
        target_chat_id, start_msg_id = parse_link(parts[1])
        _, end_msg_id = parse_link(parts[2])
    except Exception: return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    if start_msg_id > end_msg_id: start_msg_id, end_msg_id = end_msg_id, start_msg_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: {start_msg_id} முதல் {end_msg_id} வரை...")
    ACTIVE_TASKS[chat_id] = True
    success = 0
    
    try:
        await fetch_target_message(userbot, target_chat_id, start_msg_id, msg)
        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
        
        for i in range(start_msg_id, end_msg_id + 1): 
            if not ACTIVE_TASKS.get(chat_id): break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: continue
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path and ACTIVE_TASKS.get(chat_id):
                        caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
                        if target_msg.video: await client.send_video(dest_chat, file_path, caption=caption)
                        elif target_msg.document: await client.send_document(dest_chat, file_path, caption=caption)
                        elif target_msg.photo: await client.send_photo(dest_chat, file_path, caption=caption)
                        elif target_msg.audio: await client.send_audio(dest_chat, file_path, caption=caption)
                        os.remove(file_path)
                        success += 1
                        await asyncio.sleep(2)
                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success += 1
                    await asyncio.sleep(1)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception: continue
            
        if ACTIVE_TASKS.get(chat_id): await msg.edit_text(f"✅ **Batch முடிந்தது!**\nமொத்தம் {success} பைல்கள்.")
    except Exception as e: await msg.edit_text(f"❌ பிழை: {e}")
    finally: ACTIVE_TASKS[chat_id] = False

# --- 🔥 CORE: CANCEL COMMAND ---
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if ACTIVE_TASKS.get(chat_id):
        ACTIVE_TASKS[chat_id] = False
        await message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")
    else:
        await message.reply_text("எந்தப் பணியும் நடைபெறவில்லை.")

# --- MAIN RUNNER ---
async def main():
    await bot.start()
    if userbot: await userbot.start()
    await bot.set_bot_commands([
        BotCommand("start", "🏠 Home"), BotCommand("batch", "📦 Batch Download"),
        BotCommand("dl", "📥 Download YT/Insta"), BotCommand("ai", "🤖 AI Script Maker"),
        BotCommand("wp", "🌐 WP Post"), BotCommand("cancel", "❌ Cancel Task")
    ])
    print("✅ All-in-One Master Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    # Event loop setup is safely handled at the top
    loop.run_until_complete(main())
