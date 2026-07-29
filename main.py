import os
import asyncio

# --- FIX: Event Loop for Python 3.10+ ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# ----------------------------------------

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from pyrogram.errors import FloodWait
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "String Session Bot is Running!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# --- 2. CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

BATCH_DATA = {}
ACTIVE_TASKS = [] 

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home"),
        BotCommand("clone", "♻️ Fast Clone Group"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- HELPER: Send Media Nicely ---
async def send_media_nicely(client, chat_id, target_msg, file_path):
    caption = target_msg.caption if target_msg.caption else ""
    if target_msg.audio:
        await client.send_audio(chat_id, file_path, caption=caption, duration=target_msg.audio.duration, performer=target_msg.audio.performer, title=target_msg.audio.title)
    elif target_msg.video:
        await client.send_video(chat_id, file_path, caption=caption, duration=target_msg.video.duration, width=target_msg.video.width, height=target_msg.video.height)
    elif target_msg.photo:
        await client.send_photo(chat_id, file_path, caption=caption)
    elif target_msg.voice:
        await client.send_voice(chat_id, file_path, caption=caption, duration=target_msg.voice.duration)
    elif target_msg.animation:
        await client.send_animation(chat_id, file_path, caption=caption)
    else:
        await client.send_document(chat_id, file_path, caption=caption)

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "🤖 **Restricted Saver Bot (Anti-Spam Pro)**\n\n"
        "• Single Link: எந்தவொரு லிங்கையும் அனுப்பவும்.\n"
        "• Batch: பல பைல்களை எடுக்க `/batch`\n"
        "• Clone: ஒரு டாபிக்கை முழுமையாக எடுக்க `/clone <link>`\n"
        "• Cancel: நடக்கும் வேலையை நிறுத்த `/cancel`"
    )
    await message.reply_text(text)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS:
        ACTIVE_TASKS.remove(chat_id)
    if chat_id in BATCH_DATA:
        del BATCH_DATA[chat_id]
    await message.reply_text("❌ நடப்பில் இருந்த பணி வெற்றிகரமாக ரத்து செய்யப்பட்டது!")

# ================= FAST CLONE COMMAND (ANTI-SPAM UPDATE) =================
@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ String Session இணைக்கப்படவில்லை.")
        
    if chat_id in ACTIVE_TASKS:
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடந்து கொண்டிருக்கிறது. முதலில் `/cancel` செய்யவும்.")

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        return await message.reply_text("⚠️ **பயன்பாடு:**\n`/clone <குரூப் லிங்க்>`")

    link = command_parts[1].strip()
    msg = await message.reply_text("🔄 லிங்கை ஆராய்கிறது...")
    
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

        last_msg_id = start_msg_id
        async for m in userbot.get_chat_history(target_chat_id, limit=1):
            last_msg_id = m.id

        if start_msg_id > last_msg_id:
            return await msg.edit_text("❌ இந்த மெசேஜ் குரூப்பில் இல்லை.")

        await msg.edit_text(f"🚀 **Fast Clone தொடங்குகிறது...**\n• தேடப்படும் ID: {start_msg_id} - {last_msg_id}\n\nநிறுத்த `/cancel` அனுப்பவும்.")
        
        ACTIVE_TASKS.append(chat_id)
        success_count = 0
        chunk_size = 200 

        for i in range(start_msg_id, last_msg_id + 1, chunk_size):
            if chat_id not in ACTIVE_TASKS:
                await message.reply_text("❌ Clone பாதியில் ரத்து செய்யப்பட்டது!")
                break
                
            chunk_ids = list(range(i, min(i + chunk_size, last_msg_id + 1)))
            
            try:
                messages_list = await userbot.get_messages(target_chat_id, chunk_ids)
                
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
                                await asyncio.sleep(2.5) # பாதுகாப்பான தாமதம் 
                        elif target_msg.text:
                            await client.send_message(message.chat.id, target_msg.text)
                            success_count += 1
                            await asyncio.sleep(1)
                            
                    # Anti-Spam: Block வந்தால் தானாகவே காத்திருக்கும்!
                    except FloodWait as fw:
                        warning_msg = await client.send_message(message.chat.id, f"⚠️ **Telegram Spam Limit:** போட் {fw.value} வினாடிகள் காத்திருக்கிறது. தயவுசெய்து எதையும் டெலீட் செய்ய வேண்டாம்...")
                        await asyncio.sleep(fw.value)
                        await warning_msg.delete()
                    except Exception:
                        continue
                        
            except Exception:
                continue
        
        if chat_id in ACTIVE_TASKS:
            await message.reply_text(f"✅ **Clone முழுமையாக முடிந்தது!**\nமொத்தம் {success_count} பைல்கள் எடுக்கப்பட்டுள்ளன.")
            
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
                target_msg = await userbot.get_messages(chat_id_val, msg_id)
            except Exception:
                chat_obj = await userbot.get_chat(chat_id_val)
                target_msg = await userbot.get_messages(chat_obj.id, msg_id)

            if not target_msg or target_msg.empty:
                return await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")

            await msg.edit_text("📥 டவுன்லோட் ஆகிறது...")
            
            if target_msg.media:
                file_path = await userbot.download_media(target_msg)
                await msg.edit_text("📤 உங்களுக்கு அனுப்புகிறேன்...")
                await send_media_nicely(client, message.chat.id, target_msg, file_path)
                os.remove(file_path)
            elif target_msg.text:
                await client.send_message(message.chat.id, target_msg.text)
                
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

# --- 3. MAIN RUNNER ---
async def main():
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot String Session Connected!")
        print("🔄 குரூப் டேட்டாவை (Dialogs) சிங்க் செய்கிறது...")
        try:
            async for dialog in userbot.get_dialogs(limit=200):
                pass
            print("✅ குரூப் டேட்டா வெற்றிகரமாக சிங்க் செய்யப்பட்டது!")
        except Exception as e:
            pass
            
    await set_bot_commands(bot)
    print("✅ Bot வெற்றிகரமாக இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
