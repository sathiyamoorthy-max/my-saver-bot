import os
import asyncio

# --- FIX: Event Loop for Python 3.10+ ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# ----------------------------------------

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, PeerIdInvalid
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Ultimate Pro Max Bot v5.1 is Running!"

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

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True) if STRING_SESSION else None

BATCH_DATA = {}
ACTIVE_TASKS = []

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home / Menu"),
        BotCommand("clone", "♻️ Full Topic Clone"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

async def send_media_nicely(client, user_chat_id, target_msg, file_path):
    dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_chat_id
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

async def check_access_and_sync(client, chat_id_val, msg_obj):
    try:
        return await client.get_chat(chat_id_val)
    except PeerIdInvalid:
        await msg_obj.edit_text("🔄 குரூப்பைக் கண்டுபிடிக்க முடியவில்லை! தேடுகிறது (Syncing)...")
        async for _ in client.get_dialogs(limit=500):
            pass
        return await client.get_chat(chat_id_val)

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "🤖 **Pro Max Saver Bot (v5.1 - Anti-Ban Tweaks)**\n\n"
        "✨ டெலிகிராம் பைல்கள் மற்றும் வெளி வெப்சைட் லிங்குகளை அனுப்புங்கள்!"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Batch Download", callback_data="btn_batch"),
         InlineKeyboardButton("♻️ Clone Group", callback_data="btn_clone")],
        [InlineKeyboardButton("❌ Cancel Task", callback_data="btn_cancel")]
    ])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    data = query.data
    chat_id = query.message.chat.id
    if data == "btn_batch":
        if chat_id in ACTIVE_TASKS:
            return await query.answer("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது!", show_alert=True)
        BATCH_DATA[chat_id] = {"step": "first_link"}
        await query.message.reply_text("📦 **Batch Mode**\nமுதல் மெசேஜ் லிங்கை அனுப்பவும்:")
        await query.answer()
    elif data == "btn_clone":
        await query.message.reply_text("♻️ **Clone Mode**\nலிங்கை அனுப்பவும்: `/clone https://t.me/c/...`")
        await query.answer()
    elif data == "btn_cancel":
        if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
        if chat_id in BATCH_DATA: del BATCH_DATA[chat_id]
        await query.message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")
        await query.answer("Cancelled!", show_alert=True)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
    if chat_id in BATCH_DATA: del BATCH_DATA[chat_id]
    await message.reply_text("❌ பணி உடனடியாக ரத்து செய்யப்பட்டது!")

@bot.on_message(filters.command("batch") & filters.private)
async def batch_start(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    BATCH_DATA[chat_id] = {"step": "first_link"}
    await message.reply_text("📦 **Batch Mode**\nமுதல் லிங்கை அனுப்பவும்:")

@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot: return await message.reply_text("❌ Session இல்லை.")
    if chat_id in ACTIVE_TASKS: return await message.reply_text("⚠️ வேலை நடக்கிறது. `/cancel` செய்யவும்.")
    
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2: return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")
    link = command_parts[1].strip().replace("<", "").replace(">", "")
    msg = await message.reply_text("🔄 ஆராய்கிறது...")
    
    try:
        link_path = link.split("t.me/")[1].split("?")[0]
        parts = link_path.split("/")
        topic_id = None
        if parts[0] == "c":
            target_chat_id = int("-100" + parts[1])
            topic_id = int(parts[2]) if len(parts) == 4 else None
            start_msg_id = int(parts[3]) if len(parts) == 4 else int(parts[2])
        else:
            target_chat_id = parts[0]
            topic_id = int(parts[1]) if len(parts) == 3 else None
            start_msg_id = int(parts[2]) if len(parts) == 3 else int(parts[1])

        try:
            await check_access_and_sync(userbot, target_chat_id, msg)
        except Exception as e:
            return await msg.edit_text(f"❌ எரர்: குரூப்பை அணுக முடியவில்லை!\n`{e}`")

        last_msg_id = start_msg_id
        async for m in userbot.get_chat_history(target_chat_id, limit=1): last_msg_id = m.id

        if start_msg_id > last_msg_id: return await msg.edit_text("❌ லிங்க் தவறானது.")

        await msg.edit_text(f"🚀 **Clone Starts...**\n• ID: {start_msg_id} - {last_msg_id}\nநிறுத்த `/cancel`")
        ACTIVE_TASKS.append(chat_id)
        success_count = 0
        chunk_size = 200 

        for i in range(start_msg_id, last_msg_id + 1, chunk_size):
            if chat_id not in ACTIVE_TASKS: break
            chunk_ids = list(range(i, min(i + chunk_size, last_msg_id + 1)))
            try:
                messages_list = await userbot.get_messages(target_chat_id, chunk_ids)
                for target_msg in messages_list:
                    if chat_id not in ACTIVE_TASKS: break
                    if not target_msg or target_msg.empty: continue
                    if topic_id:
                        msg_topic_id = getattr(target_msg, "message_thread_id", None) or getattr(target_msg, "reply_to_message_id", None)
                        if msg_topic_id != topic_id and target_msg.id != topic_id: continue
                    try:
                        if target_msg.media:
                            file_path = await userbot.download_media(target_msg)
                            if chat_id not in ACTIVE_TASKS:
                                if file_path and os.path.exists(file_path): os.remove(file_path)
                                break
                            if file_path:
                                await send_media_nicely(client, chat_id, target_msg, file_path)
                                os.remove(file_path)
                                success_count += 1
                                await asyncio.sleep(2.5)
                        elif target_msg.text:
                            dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                            await client.send_message(dest_chat, target_msg.text)
                            success_count += 1
                            await asyncio.sleep(1)
                    except FloodWait as fw: await asyncio.sleep(fw.value)
                    except Exception: continue
            except Exception: continue
        
        if chat_id in ACTIVE_TASKS:
            await message.reply_text(f"✅ **Clone முடிந்தது!**\nமொத்தம் {success_count} பைல்கள்.")
            ACTIVE_TASKS.remove(chat_id)
    except Exception as e:
        if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
        await msg.edit_text(f"❌ பிழை: {e}")

@bot.on_message(filters.text & filters.private & ~filters.command(["start", "clone", "cancel", "batch"]))
async def handle_inputs(client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip().replace("<", "").replace(">", "")
    
    # --- EXTERNAL LINK DOWNLOADER (Anti-Ban Setup) ---
    if text.startswith("http") and "t.me/" not in text:
        if "onelink.me" in text:
            return await message.reply_text("❌ இது Pocket FM-ன் App Link. PCAPdroid மூலம் எடுத்த Request URL-ஐ அனுப்புங்கள்!")
            
        msg = await message.reply_text("🔄 **Downloading External Media...**")
        try:
            import yt_dlp
            ydl_opts = {
                'format': 'best',
                'outtmpl': '%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'extractor_retries': 3,
                'http_headers': { # Fake User Agent to bypass simple blocks
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
                }
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(text, download=True)
                file_path = ydl.prepare_filename(info)
            
            await msg.edit_text("📤 உங்களுக்கு அனுப்புகிறேன்...")
            dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
            await client.send_document(dest_chat, file_path)
            os.remove(file_path)
            await msg.delete()
        except Exception as e:
            error_text = str(e)
            if "Sign in" in error_text:
                await msg.edit_text("❌ யூடியூப் செக்யூரிட்டி (Bot Protection) நமது இலவச சர்வரைத் தடுத்துவிட்டது. இதைத் தீர்க்க Cookies ஃபைல் தேவை.")
            elif "no video" in error_text:
                await msg.edit_text("❌ இன்ஸ்டாகிராம் லாகின் இல்லாமல் இந்த வீடியோவை எடுக்க அனுமதிக்கவில்லை.")
            else:
                await msg.edit_text(f"❌ பிழை: {error_text}")
        return

    if not userbot: return

    # --- BATCH PROCESSING ---
    if chat_id in BATCH_DATA:
        b_step = BATCH_DATA[chat_id].get("step")
        if b_step == "first_link":
            if "t.me/" not in text: return await message.reply_text("❌ சரியான முதல் லிங்கை அனுப்பவும்.")
            BATCH_DATA[chat_id]["first_link"] = text
            BATCH_DATA[chat_id]["step"] = "last_link"
            return await message.reply_text("📦 இப்போது **இறுதித் (Last)** மெசேஜ் லிங்கை அனுப்பவும்:")
        elif b_step == "last_link":
            if "t.me/" not in text: return await message.reply_text("❌ சரியான இறுதி லிங்கை அனுப்பவும்.")
            first_link = BATCH_DATA[chat_id]["first_link"]
            last_link = text
            del BATCH_DATA[chat_id]
            try:
                first_parts = first_link.split("t.me/")[1].split("?")[0].split("/")
                last_parts = last_link.split("t.me/")[1].split("?")[0].split("/")
                
                if first_parts[0] == "c":
                    target_chat_id = int("-100" + first_parts[1])
                    start_msg_id = int(first_parts[3]) if len(first_parts) == 4 else int(first_parts[2])
                    end_msg_id = int(last_parts[3]) if len(last_parts) == 4 else int(last_parts[2])
                else:
                    target_chat_id = first_parts[0]
                    start_msg_id = int(first_parts[2]) if len(first_parts) == 3 else int(first_parts[1])
                    end_msg_id = int(last_parts[2]) if len(last_parts) == 3 else int(last_parts[1])
                
                status_msg = await message.reply_text(f"🚀 Batch டவுன்லோட் தொடங்குகிறது...")
                try: await check_access_and_sync(userbot, target_chat_id, status_msg)
                except Exception as e: return await status_msg.edit_text(f"❌ எரர்: குரூப்பை அணுக முடியவில்லை!\n`{e}`")

                ACTIVE_TASKS.append(chat_id)
                success_count = 0
                for msg_id in range(start_msg_id, end_msg_id + 1):
                    if chat_id not in ACTIVE_TASKS: break
                    try:
                        target_msg = await userbot.get_messages(target_chat_id, msg_id)
                        if not target_msg or target_msg.empty: continue
                        if target_msg.media:
                            file_path = await userbot.download_media(target_msg)
                            if chat_id not in ACTIVE_TASKS:
                                if file_path and os.path.exists(file_path): os.remove(file_path)
                                break
                            if file_path:
                                await send_media_nicely(client, chat_id, target_msg, file_path)
                                os.remove(file_path)
                                success_count += 1
                                await asyncio.sleep(2.5)
                        elif target_msg.text:
                            dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                            await client.send_message(dest_chat, target_msg.text)
                            success_count += 1
                            await asyncio.sleep(1)
                    except FloodWait as fw: await asyncio.sleep(fw.value)
                    except Exception: continue

                if chat_id in ACTIVE_TASKS:
                    await status_msg.edit_text(f"✅ Batch டவுன்லோட் முடிந்தது! ({success_count} பைல்கள்)")
                    ACTIVE_TASKS.remove(chat_id)
            except Exception as e:
                if chat_id in ACTIVE_TASKS: ACTIVE_TASKS.remove(chat_id)
                await message.reply_text(f"❌ பிழை: {e}")
            return

    # --- SINGLE LINK PROCESSING ---
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

            try: await check_access_and_sync(userbot, chat_id_val, msg)
            except Exception as e: return await msg.edit_text(f"❌ எரர்: குரூப்பை அணுக முடியவில்லை!\n`{e}`")

            target_msg = await userbot.get_messages(chat_id_val, msg_id)
            if not target_msg or target_msg.empty: return await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")
            await msg.edit_text("📥 டவுன்லோட் ஆகிறது...")
            
            if target_msg.media:
                file_path = await userbot.download_media(target_msg)
                await msg.edit_text("📤 அனுப்புகிறேன்...")
                await send_media_nicely(client, chat_id, target_msg, file_path)
                if os.path.exists(file_path): os.remove(file_path)
            elif target_msg.text:
                dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else chat_id
                await client.send_message(dest_chat, target_msg.text)
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

async def main():
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot Connected!")
        try:
            async for dialog in userbot.get_dialogs(limit=500): pass
        except Exception: pass
    await set_bot_commands(bot)
    print("✅ Advanced Bot Running!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
