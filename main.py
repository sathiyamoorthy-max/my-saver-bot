import os
import asyncio
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
    return "✅ Ultimate Pro Max Bot v6.3 is Running!"
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

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

ACTIVE_TASKS = []

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home"),
        BotCommand("dl", "📥 Download YT/Insta"),
        BotCommand("clone", "♻️ Clone Single Topic/Link"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- START & BUTTONS ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Batch Download", callback_data="help_batch"), InlineKeyboardButton("♻️ Clone Group", callback_data="help_clone")],
         [InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")]]
    )
    text = "🤖 **Pro Max Saver Bot (v6.3 - Bug Fix Edition)**\n\n✨ எந்த லிங்கையும் நேரடியாக இங்கே பேஸ்ட் செய்யவும்! பாட் தானாகவே டவுன்லோட் செய்யும்."
    await message.reply_text(text, reply_markup=buttons)

@bot.on_callback_query()
async def callback_handler(client, query):
    if query.data == "help_batch": await query.message.reply_text("📦 **Batch Download:**\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    elif query.data == "help_clone": await query.message.reply_text("♻️ **Clone:**\nநேரடியாக லிங்கை மட்டும் அனுப்புங்கள்! அல்லது `/clone <லிங்க்>`")
    elif query.data == "cancel_task":
        if query.message.chat.id in ACTIVE_TASKS: ACTIVE_TASKS.remove(query.message.chat.id)
        await query.message.reply_text("❌ வேலை நிறுத்தப்பட்டது!")

# --- HELPER: AUTO-SYNC TO FIX PEER ID INVALID ---
async def fetch_msg_with_sync(client, chat_id, msg_id, status_msg):
    try:
        return await client.get_messages(chat_id, msg_id)
    except PeerIdInvalid:
        await status_msg.edit_text("🔄 குரூப்பை Sync செய்கிறது... 1 நிமிடம் காத்திருக்கவும்.")
        async for _ in client.get_dialogs(limit=100): pass
        return await client.get_messages(chat_id, msg_id)

# --- ♻️ AUTO-LINK & CLONE (Single Link) ---
@bot.on_message((filters.command("clone") | filters.regex(r"https://t\.me/(c/)?")) & filters.private)
async def clone_single_chat(client, message: Message):
    if message.text.startswith("/") and not message.text.startswith("/clone"): return
    
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    link = message.text.split()[1] if message.text.startswith("/clone") else message.text.strip()
    
    try:
        if "/c/" in link:
            target_chat_id = int("-100" + link.split("/c/")[1].split("/")[0])
        else:
            target_chat_id = link.split("t.me/")[1].split("/")[0]
        msg_id = int(link.split("/")[-1])
    except:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    msg = await message.reply_text("🔄 பைலை எடுக்கிறது...")
    try:
        target_msg = await fetch_msg_with_sync(userbot, target_chat_id, msg_id, msg)
        
        if target_msg.media:
            file_path = await userbot.download_media(target_msg)
            if file_path:
                dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                if target_msg.video: await client.send_video(dest_chat, file_path)
                elif target_msg.document: await client.send_document(dest_chat, file_path)
                elif target_msg.photo: await client.send_photo(dest_chat, file_path)
                elif target_msg.audio: await client.send_audio(dest_chat, file_path)
                os.remove(file_path)
                await msg.edit_text("✅ வெற்றிகரமாக எடுக்கப்பட்டது!")
        elif target_msg.text:
            await client.send_message(int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id, target_msg.text)
            await msg.edit_text("✅ டெக்ஸ்ட் அனுப்பப்பட்டது!")
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")

# --- 📦 BATCH DOWNLOADER ---
@bot.on_message(filters.command("batch") & filters.private)
async def batch_cmd(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    parts = message.text.split()
    if len(parts) != 3: return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    
    try:
        target_chat_id = int("-100" + parts[1].split("/c/")[1].split("/")[0])
        start_msg_id = int(parts[1].split("/")[-1])
        end_msg_id = int(parts[2].split("/")[-1])
    except:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    if start_msg_id > end_msg_id: start_msg_id, end_msg_id = end_msg_id, start_msg_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: {start_msg_id} முதல் {end_msg_id} வரை...")
    ACTIVE_TASKS.append(chat_id)
    success = 0
    
    try:
        # Check access once before loop
        await fetch_msg_with_sync(userbot, target_chat_id, start_msg_id, msg)
        
        for i in range(start_msg_id, end_msg_id + 1): 
            if chat_id not in ACTIVE_TASKS: break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty: continue
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path:
                        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                        if target_msg.video: await client.send_video(dest_chat, file_path)
                        elif target_msg.document: await client.send_document(dest_chat, file_path)
                        elif target_msg.photo: await client.send_photo(dest_chat, file_path)
                        elif target_msg.audio: await client.send_audio(dest_chat, file_path)
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

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    if message.chat.id in ACTIVE_TASKS: ACTIVE_TASKS.remove(message.chat.id)
    await message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")

async def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    await bot.start()
    if userbot: await userbot.start()
    await set_bot_commands(bot)
    print("✅ Bug-Fix Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
