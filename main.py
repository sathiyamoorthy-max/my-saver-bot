import os
import asyncio

# --- FIX: Event Loop for Python 3.10+ ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# ----------------------------------------

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Advanced Ultimate Bot is Running!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# --- 2. CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

# --- NEW ADVANCED FEATURES ---
# Dump Channel ID (உதாரணம்: -100123456789). கொடுக்கவில்லை என்றால் உங்களுக்கு அனுப்பும்.
DUMP_CHANNEL = os.environ.get("DUMP_CHANNEL", "") 
# Custom Caption (விளம்பரங்களை நீக்கிவிட்டு நீங்கள் விரும்பும் டெக்ஸ்ட்)
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "") 

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

BATCH_DATA = {}
ACTIVE_TASKS = []

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home"),
        BotCommand("clone", "♻️ Full Topic Clone"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- HELPER 1: Smart Caption & Media Sender ---
async def send_media_nicely(client, user_chat_id, target_msg, file_path):
    dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_chat_id
    
    # கேப்ஷன் மாற்றுதல் (Ad Remover)
    caption = CUSTOM_CAPTION if CUSTOM_CAPTION else (target_msg.caption if target_msg.caption else "")
    
    if target_msg.audio:
        await client.send_audio(dest_chat, file_path, caption=caption, duration=target_msg.audio.duration, performer=target_msg.audio.performer, title=target_msg.audio.title)
    elif target_msg.video:
        await client.send_video(dest_chat, file_path, caption=caption, duration=target_msg.video.duration, width=target_msg.video.width, height=target_msg.video.height)
    elif target_msg.photo:
        await client.send_photo(dest_chat, file_path, caption=caption)
    elif target_msg.voice:
        await client.send_voice(dest_chat, file_path, caption=caption, duration=target_msg.voice.duration)
    elif target_msg.animation:
        await client.send_animation(dest_chat, file_path, caption=caption)
    else:
        await client.send_document(dest_chat, file_path, caption=caption)

# --- HELPER 2: Auto-Recovery ---
async def get_message_with_recovery(client, chat_id_val, msg_ids):
    try:
        return await client.get_messages(chat_id_val, msg_ids)
    except PeerIdInvalid:
        async for _ in client.get_dialogs(limit=500):
            pass
        return await client.get_messages(chat_id_val, msg_ids)
    except Exception as e:
        chat_obj = await client.get_chat(chat_id_val)
        return await client.get_messages(chat_obj.id, msg_ids)

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "🤖 **Pro Max Saver Bot**\n\n"
        "🔥 **Advanced Features Active:**\n"
        "• Ad-Remover & Custom Caption\n"
        "• Auto-Forward to Channel\n"
        "• Smart Anti-Spam\n\n"
        "லிங்கை அனுப்பி மேஜிக்கைப் பாருங்கள்! ✨"
    )
    await message.reply_text(text)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS:
        ACTIVE_TASKS.remove(chat_id)
    if chat_id in BATCH_DATA:
        del BATCH_DATA[chat_id]
    await message.reply_text("❌ நடப்பில் இருந்த பணி நிறுத்தப்பட்டது!")

# ================= ADVANCED CLONE =================
@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS:
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. `/cancel` செய்யவும்.")

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        return await message.reply_text("⚠️ **பயன்பாடு:** `/clone <குரூப் லிங்க்>`")

    link = command_parts[1].strip()
    msg = await message.reply_text("🔄 ஆராய்கிறது...")
    
    try:
        link_path = link.split("t.me/")[1].split("?")[0]
        parts = link_path.split("/")
        topic_id = None
        if parts[0] == "c":
            target_chat_id = int("-100" + parts[1])
            if len(parts) == 4:
                topic_id = int(parts[2])
                start_msg_id = int(parts[3])
            else:
                start_msg_id = int(parts[2])
        else:
            target_chat_id = parts[0]
            if len(parts) == 3:
                topic_id = int(parts[1])
                start_msg_id = int(parts[2])
            else:
                start_msg_id = int(parts[1])

        try:
            last_msg_id = start_msg_id
            async for m in userbot.get_chat_history(target_chat_id, limit=1):
                last_msg_id = m.id
        except PeerIdInvalid:
            await msg.edit_text("🔄 குரூப்பைத் தேடுகிறது...")
            async for _ in userbot.get_dialogs(limit=500):
                pass
            last_msg_id = start_msg_id
            async for m in userbot.get_chat_history(target_chat_id, limit=1):
                last_msg_id = m.id

        if start_msg_id > last_msg_id:
            return await msg.edit_text("❌ லிங்க் தவறானது.")

        dest_name = "உங்கள் சேனலில்" if DUMP_CHANNEL else "இங்கே"
        await msg.edit_text(f"🚀 **Clone Starts...**\n• பைல்கள் **{dest_name}** அப்லோட் ஆகும்.\n\nநிறுத்த `/cancel`")
        ACTIVE_TASKS.append(chat_id)
        success_count = 0
        chunk_size = 200 

        for i in range(start_msg_id, last_msg_id + 1, chunk_size):
            if chat_id not in ACTIVE_TASKS:
                break
                
            chunk_ids = list(range(i, min(i + chunk_size, last_msg_id + 1)))
            try:
                messages_list = await get_message_with_recovery(userbot, target_chat_id, chunk_ids)
                
                for target_msg in messages_list:
                    if chat_id not in ACTIVE_TASKS:
                        break
                    if not target_msg or target_msg.empty:
                        continue
                        
                    if topic_id:
                        msg_topic_id = getattr(target_msg, "message_thread_id", None) or getattr(target_msg, "reply_to_message_id", None)
                        if msg_topic_id != topic_id and target_msg.id != topic_id:
                            continue
                            
                    try:
                        if target_msg.media:
                            file_path = await userbot.download_media(target_msg)
                            if file_path:
                                await send_media_nicely(client, message.chat.id, target_msg, file_path)
                                os.remove(file_path)
                                success_count += 1
                                
                                # Smart Progress Update every 10 files
                                if success_count % 10 == 0:
                                    try:
                                        await msg.edit_text(f"🔄 **Downloading...**\nஇதுவரை {success_count} பைல்கள் அனுப்பப்பட்டுள்ளன.")
                                    except:
                                        pass
                                
                                await asyncio.sleep(2.5)
                        elif target_msg.text:
                            dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else message.chat.id
                            await client.send_message(dest_chat, target_msg.text)
                            success_count += 1
                            await asyncio.sleep(1)
                            
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value)
                    except Exception:
                        continue
            except Exception:
                continue
        
        if chat_id in ACTIVE_TASKS:
            await msg.edit_text(f"✅ **Clone முடிந்தது!**\nமொத்தம் {success_count} பைல்கள் எடுக்கப்பட்டன.")
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")
    finally:
        if chat_id in ACTIVE_TASKS:
            ACTIVE_TASKS.remove(chat_id)

# ================= SINGLE LINK PROCESSING =================
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "clone", "cancel", "batch"]))
async def handle_inputs(client, message: Message):
    text = message.text.strip()
    if not userbot:
        return

    if "t.me/" in text:
        msg = await message.reply_text("⏳ பைலைத் தேடுகிறது...")
        try:
            link_path = text.split("t.me/")[1].split("?")[0]
            parts = link_path.split("/")
            
            if parts[0] == "c":
                chat_id_val = int("-100" + parts[1])
                msg_id = int(parts[3]) if len(parts) == 4 else int(parts[2])
            else:
                chat_id_val = parts[0]
                msg_id = int(parts[2]) if len(parts) == 3 else int(parts[1])

            try:
                target_msg = await get_message_with_recovery(userbot, chat_id_val, msg_id)
            except Exception as e:
                return await msg.edit_text(f"❌ குரூப்பைக் கண்டுபிடிக்க முடியவில்லை!")

            if not target_msg or target_msg.empty:
                return await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")

            await msg.edit_text("📥 டவுன்லோட் ஆகிறது...")
            
            if target_msg.media:
                file_path = await userbot.download_media(target_msg)
                await msg.edit_text("📤 அனுப்புகிறேன்...")
                await send_media_nicely(client, message.chat.id, target_msg, file_path)
                os.remove(file_path)
            elif target_msg.text:
                dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else message.chat.id
                await client.send_message(dest_chat, target_msg.text)
                
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

# --- MAIN RUNNER ---
async def main():
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot Connected!")
        try:
            async for dialog in userbot.get_dialogs(limit=500):
                pass
        except Exception:
            pass
    await set_bot_commands(bot)
    print("✅ Advanced Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
