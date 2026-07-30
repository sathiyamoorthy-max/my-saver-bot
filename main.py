import os
import asyncio

# --- RENDER EVENT LOOP FIX ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread

# --- WEB SERVER (Render Keep-Alive) ---
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Telegram Core Cloner Bot is Running!"
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

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

ACTIVE_TASKS = {}

# --- HELPER: LINK PARSER ---
def parse_link(url: str):
    url = url.replace("https://", "").replace("http://", "").replace("t.me/", "").strip()
    parts = url.split("/")
    if parts[0] == "c":
        chat_id = int("-100" + parts[1])
        msg_id = int(parts[2])
    else:
        chat_id = parts[0]
        msg_id = int(parts[1])
    return chat_id, msg_id

# --- HELPER: SYNC CHAT (Fix Peer ID Invalid) ---
async def fetch_target_message(client, chat_id, msg_id, status_msg=None):
    try:
        return await client.get_messages(chat_id, msg_id)
    except Exception as e:
        if "PEER_ID_INVALID" in str(e) or isinstance(e, PeerIdInvalid):
            if status_msg:
                await status_msg.edit_text("🔄 **Syncing...** யூசர்பாட் குரூப்புடன் இணைகிறது...")
            async for _ in client.get_dialogs(limit=200):
                pass
            return await client.get_messages(chat_id, msg_id)
        raise e

# --- START COMMAND ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    text = (
        "🤖 **Telegram Core Cloner Bot**\n\n"
        "📥 **Clone (Single Link):**\n`/clone <லிங்க்>` அல்லது நேரடியாக லிங்கை அனுப்பவும்.\n\n"
        "📦 **Batch Download:**\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`\n\n"
        "❌ **Cancel:**\n`/cancel`"
    )
    await message.reply_text(text)

# --- CLONE (Single Link & Auto-Detect) ---
@bot.on_message((filters.command("clone") | filters.regex(r"t\.me/(c/)?")) & filters.private)
async def clone_single_msg(client, message: Message):
    if message.text.startswith("/batch"):
        return
    
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ String Session இல்லை!")
    if ACTIVE_TASKS.get(chat_id):
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")
    
    # Extract link safely
    if message.text.startswith("/clone"):
        parts = message.text.split()
        if len(parts) < 2:
            return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")
        link = parts[1]
    else:
        link = message.text.strip()
        
    msg = await message.reply_text("🔄 பைலைத் தேடுகிறது...")
    ACTIVE_TASKS[chat_id] = True
    
    try:
        target_chat_id, msg_id = parse_link(link)
        target_msg = await fetch_target_message(userbot, target_chat_id, msg_id, msg)
        
        if not target_msg or target_msg.empty:
            ACTIVE_TASKS[chat_id] = False
            return await msg.edit_text("❌ பைல் கிடைக்கவில்லை அல்லது நீக்கப்பட்டுவிட்டது.")
            
        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
        
        if target_msg.media:
            await msg.edit_text("📥 ஒரிஜினல் குவாலிட்டியில் டவுன்லோட் ஆகிறது...")
            file_path = await userbot.download_media(target_msg)
            
            if file_path and ACTIVE_TASKS.get(chat_id):
                await msg.edit_text("📤 உங்களுக்கு அனுப்பப்படுகிறது...")
                caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
                
                if target_msg.video: await client.send_video(dest_chat, file_path, caption=caption, duration=target_msg.video.duration)
                elif target_msg.document: await client.send_document(dest_chat, file_path, caption=caption)
                elif target_msg.photo: await client.send_photo(dest_chat, file_path, caption=caption)
                elif target_msg.audio: await client.send_audio(dest_chat, file_path, caption=caption, duration=target_msg.audio.duration)
                
                os.remove(file_path)
                await msg.edit_text("✅ வெற்றிகரமாக அனுப்பப்பட்டது!")
            elif file_path:
                os.remove(file_path)
        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            await msg.edit_text("✅ டெக்ஸ்ட் அனுப்பப்பட்டது!")
            
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")
    finally:
        ACTIVE_TASKS[chat_id] = False

# --- BATCH DOWNLOADER ---
@bot.on_message(filters.command("batch") & filters.private)
async def batch_download(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ String Session இல்லை!")
    if ACTIVE_TASKS.get(chat_id):
        return await message.reply_text("⚠️ வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")
    
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")
    
    try:
        target_chat_id, start_msg_id = parse_link(parts[1])
        _, end_msg_id = parse_link(parts[2])
    except Exception:
        return await message.reply_text("❌ லிங்க் பார்மேட் தவறு!")

    if start_msg_id > end_msg_id:
        start_msg_id, end_msg_id = end_msg_id, start_msg_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: {start_msg_id} முதல் {end_msg_id} வரை...")
    ACTIVE_TASKS[chat_id] = True
    success = 0
    
    try:
        await fetch_target_message(userbot, target_chat_id, start_msg_id, msg)
        dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
        
        for i in range(start_msg_id, end_msg_id + 1):
            if not ACTIVE_TASKS.get(chat_id):
                break
            try:
                target_msg = await userbot.get_messages(target_chat_id, i)
                if not target_msg or target_msg.empty:
                    continue
                    
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path and ACTIVE_TASKS.get(chat_id):
                        caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption or "")
                        if target_msg.video: await client.send_video(dest_chat, file_path, caption=caption, duration=target_msg.video.duration)
                        elif target_msg.document: await client.send_document(dest_chat, file_path, caption=caption)
                        elif target_msg.photo: await client.send_photo(dest_chat, file_path, caption=caption)
                        elif target_msg.audio: await client.send_audio(dest_chat, file_path, caption=caption, duration=target_msg.audio.duration)
                        os.remove(file_path)
                        success += 1
                        await asyncio.sleep(2)
                elif target_msg.text:
                    await client.send_message(dest_chat, target_msg.text)
                    success += 1
                    await asyncio.sleep(1)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception:
                continue
                
        if ACTIVE_TASKS.get(chat_id):
            await msg.edit_text(f"✅ **Batch முடிந்தது!**\nமொத்தம் {success} பைல்கள் எடுக்கப்பட்டன.")
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")
    finally:
        ACTIVE_TASKS[chat_id] = False

# --- CANCEL COMMAND ---
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if ACTIVE_TASKS.get(chat_id):
        ACTIVE_TASKS[chat_id] = False
        await message.reply_text("❌ பணி உடனடியாக ரத்து செய்யப்பட்டது!")
    else:
        await message.reply_text("எந்தப் பணியும் தற்போது நடைபெறவில்லை.")

# --- MAIN RUNNER ---
async def main():
    await bot.start()
    if userbot:
        await userbot.start()
    await bot.set_bot_commands([
        BotCommand("start", "🏠 Home"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ])
    print("✅ Pure Core Telegram Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
