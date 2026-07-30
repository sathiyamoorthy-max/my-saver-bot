import os
import asyncio
import requests
import yt_dlp

# --- Event Loop Fix ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread
import google.generativeai as genai

# --- 1. WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Ultimate Pro Max Bot v6.1 (All-in-One Creator Edition) is Running!"
def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
Thread(target=run_server, daemon=True).start()

# --- 2. CONFIGURATION ---
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
        BotCommand("dl", "📥 Download YT/Insta (yt-dlp)"),
        BotCommand("clone", "♻️ Telegram Group Clone (Original)"),
        BotCommand("ai", "🤖 AI Script & Content"),
        BotCommand("wp", "🌐 Auto Post to WordPress"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- 🚀 1. NEW: UNIVERSAL DOWNLOADER (YouTube / Insta) ---
def download_yt_dlp(url):
    # நேற்றைய எக்ஸ்பிரிமெண்ட்டின் படி cookies.txt இணைக்கப்பட்டுள்ளது
    ydl_opts = {
        'format': 'best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'cookies.txt', # YouTube/Insta-வை ஏமாற்ற Cookies
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
    msg = await message.reply_text("📥 வீடியோவை சர்வரில் டவுன்லோட் செய்கிறது...")
    
    try:
        # Blocking function-ஐ தனியாக ஓட விடுகிறோம்
        file_path = await asyncio.to_thread(download_yt_dlp, url)
        await msg.edit_text("📤 வீடியோவை டெலிகிராமில் அனுப்புகிறது...")
        await client.send_video(message.chat.id, file_path, caption=f"✨ Downloaded via Pro Max Bot\n🔗 {url}")
        os.remove(file_path)
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ டவுன்லோட் எரர்: {e}\n(Cookies பைல் சரியாக உள்ளதா என செக் செய்யவும்)")

# --- 🤖 2. AI SCRIPT GENERATOR ---
@bot.on_message(filters.command("ai") & filters.private)
async def ai_generate(client, message: Message):
    if not GEMINI_API_KEY: return await message.reply_text("❌ GEMINI_API_KEY இல்லை!")
    prompt = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if not prompt: return await message.reply_text("⚠️ பயன்பாடு: `/ai Jujutsu Kaisen Episode 2 explanation in Tamil`")
    msg = await message.reply_text("🧠 AI யோசிக்கிறது...")
    try:
        response = ai_model.generate_content(prompt)
        await msg.edit_text(f"✨ **AI Response:**\n\n{response.text}")
    except Exception as e:
        await msg.edit_text(f"❌ AI எரர்: {e}")

# --- 🌐 3. WORDPRESS PUBLISHER ---
@bot.on_message(filters.command("wp") & filters.private)
async def wp_post(client, message: Message):
    if not all([WP_URL, WP_USER, WP_PASS]): return await message.reply_text("❌ WP Login Details இல்லை!")
    content = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if not content: return await message.reply_text("⚠️ பயன்பாடு: `/wp <கட்டுரை>`")
    msg = await message.reply_text("🌐 வெப்சைட்டில் போஸ்ட் செய்யப்படுகிறது...")
    try:
        res = requests.post(WP_URL, auth=(WP_USER, WP_PASS), json={"title": "Auto Post", "content": content, "status": "publish"})
        if res.status_code == 201: await msg.edit_text(f"✅ **Publish ஆகிவிட்டது!**\n🔗 {res.json().get('link')}")
        else: await msg.edit_text(f"❌ எரர்: {res.text}")
    except Exception as e: await msg.edit_text(f"❌ பிழை: {e}")

# --- ♻️ 4. TELEGRAM CLONE (100% Original Quality) ---
@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")
    link = parts[1].strip()
    
    # லிங்கில் இருந்து ID-ஐ பிரித்தெடுத்தல் (Basic Example)
    try:
        target_chat_id = int("-100" + link.split("/c/")[1].split("/")[0])
        start_msg_id = int(link.split("/")[-1])
    except:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    msg = await message.reply_text("🔄 குரூப்பை செக் செய்கிறது...")
    ACTIVE_TASKS.append(chat_id)
    success = 0
    
    try:
        # டவுன்லோட் லாஜிக்
        for i in range(start_msg_id, start_msg_id + 10): # 10 மெசேஜ்கள் மட்டும் (Batch லிமிட்)
            if chat_id not in ACTIVE_TASKS: break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: continue
                
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path:
                        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                        caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
                        
                        if target_msg.video:
                            await client.send_video(dest_chat, file_path, caption=caption, duration=target_msg.video.duration)
                        elif target_msg.document:
                            await client.send_document(dest_chat, file_path, caption=caption)
                        # Add Audio/Photo as needed
                        
                        os.remove(file_path)
                        success += 1
                        await asyncio.sleep(2)
                elif target_msg.text:
                    await client.send_message(int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id, target_msg.text)
                    success += 1
                    await asyncio.sleep(1)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception: continue
            
        if chat_id in ACTIVE_TASKS:
            await msg.edit_text(f"✅ **Clone முடிந்தது!**\nமொத்தம் {success} ஒரிஜினல் பைல்கள்.")
            ACTIVE_TASKS.remove(chat_id)
    except Exception as e:
        if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
        await msg.edit_text(f"❌ பிழை: {e}\n(Userbot அந்த குரூப்பில் உள்ளதா என செக் செய்யவும்)")

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
