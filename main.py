import os
import asyncio

# --- 1. CRITICAL Python 3.14 EVENT LOOP FIX (Must be at the TOP) ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# -------------------------------------------------------------------

from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, PeerIdInvalid, RPCError

# --- 2. DUMMY FLASK SERVER (Render Keep-Alive) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Ultimate Pro Max Saver Bot is Running Perfectly!"

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

# Task Tracker
ACTIVE_TASKS = {}

# --- 4. LINK PARSER (Handles Public, Private, Topic & Thread Links) ---
def parse_telegram_link(url: str):
    url = url.strip().replace("<", "").replace(">", "")
    if "t.me/" not in url:
        raise ValueError("செல்லுபடியாகும் டெலிகிராம் லிங்க் இல்லை!")
    
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

# --- 5. CHAT RESOLVER & AUTO-SYNC ---
async def resolve_and_get_message(ub_client, chat_id, msg_id, status_msg=None):
    try:
        return await ub_client.get_messages(chat_id, msg_id)
    except (PeerIdInvalid, RPCError):
        if status_msg:
            await status_msg.edit_text("🔄 **Syncing...** Userbot பிரைவேட் குரூப்பைச் சரிபார்க்கிறது...")
        
        try:
            await ub_client.get_chat(chat_id)
        except Exception:
            async for dialog in ub_client.get_dialogs(limit=100):
                if dialog.chat.id == chat_id:
                    break
        
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

# --- 7. COMMAND & BUTTON HANDLERS ---
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Batch Download", callback_data="help_batch"),
            InlineKeyboardButton("♻️ Clone Help", callback_data="help_clone")
        ],
        [
            InlineKeyboardButton("❌ Cancel Task", callback_data="cancel_task")
        ]
    ])
    text = (
        "🤖 **Ultimate Pro Max Saver Bot (v7.0 - Final Fixed Edition)**\n\n"
        "✨ **அம்சங்கள்:**\n"
        "1. **Single Link / Clone:** பிரைவேட்/பப்ளிக் குரூப் லிங்கை நேரடியாக அனுப்பவும் அல்லது `/clone <link>` பயன்படுத்தவும்.\n"
        "2. **Batch Download:** ரேஞ்ச் பைல்களை எடுக்க `/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>` பயன்படுத்தவும்.\n"
        "3. **Topic Support:** குரூப் Topic/Thread லிங்குகளையும் துல்லியமாக எடுக்கும்.\n"
        "4. **Original Quality:** 100% தரமான வீடியோ, ஆடியோ, டாக்குமெண்ட் டவுன்லோட்."
    )
    await message.reply_text(text, reply_markup=keyboard)

@bot.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    user_id = query.message.chat.id
    if query.data == "help_batch":
        await query.message.reply_text(
            "📦 **Batch Download பயன்பாடு:**\n"
            "`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`\n\n"
            "**உதாரணம்:**\n"
            "`/batch https://t.me/c/12345/10 https://t.me/c/12345/20`"
        )
    elif query.data == "help_clone":
        await query.message.reply_text(
            "♻️ **Clone பயன்பாடு:**\n"
            "`/clone <லிங்க்>` அல்லது பிரைவேட் குரூப் லிங்கை நேரடியாக மெசேஜாக அனுப்புங்கள்!"
        )
    elif query.data == "cancel_task":
        if ACTIVE_TASKS.get(user_id):
            ACTIVE_TASKS[user_id] = False
            await query.message.reply_text("❌ பணி உடனடியாக ரத்து செய்யப்பட்டது!")
        else:
            await query.answer("எந்தப் பணியும் தற்போது நடைபெறவில்லை!", show_alert=True)

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    user_id = message.chat.id
    if ACTIVE_TASKS.get(user_id):
        ACTIVE_TASKS[user_id] = False
        await message.reply_text("❌ பணி ரத்து செய்யப்பட்டது!")
    else:
        await message.reply_text("எந்தப் பணியும் தற்போது நடைபெறவில்லை.")

# --- 8. SINGLE LINK / CLONE HANDLER ---
@bot.on_message((filters.command("clone") | filters.regex(r"https://t\.me/")) & filters.private)
async def handle_clone_single(client, message: Message):
    if message.text.startswith("/batch"):
        return
        
    user_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ Userbot (STRING_SESSION) செட் செய்யப்படவில்லை!")
        
    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")

    if message.text.startswith("/clone"):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text("⚠️ பயன்பாடு: `/clone https://t.me/c/...`")
        url = parts[1].strip()
    else:
        url = message.text.strip()

    msg = await message.reply_text("🔄 லிங்கை ஆய்வு செய்கிறது...")
    ACTIVE_TASKS[user_id] = True

    try:
        chat_id, topic_id, msg_id = parse_telegram_link(url)
        target_msg = await resolve_and_get_message(userbot, chat_id, msg_id, msg)

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
                await msg.edit_text("❌ டவுன்லோட் ரத்து செய்யப்பட்டது.")
        elif target_msg.text:
            await client.send_message(dest_chat, target_msg.text)
            await msg.edit_text("✅ மெசேஜ் அனுப்பப்பட்டது!")
        else:
            await msg.edit_text("❌ ஆதரவற்ற மீடியா வகை.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}\n\n(Userbot அந்த குரூப்பில் உள்ளதா என உறுதிப்படுத்தவும்!)")
    finally:
        ACTIVE_TASKS[user_id] = False

# --- 9. BATCH DOWNLOAD HANDLER ---
@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    user_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ Userbot (STRING_SESSION) செட் செய்யப்படவில்லை!")
        
    if ACTIVE_TASKS.get(user_id):
        return await message.reply_text("⚠️ வேலை நடக்கிறது. ரத்து செய்ய `/cancel` அனுப்பவும்.")

    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply_text("⚠️ பயன்பாடு:\n`/batch <முதல்_லிங்க்> <கடைசி_லிங்க்>`")

    try:
        chat_id1, topic_id1, start_id = parse_telegram_link(parts[1])
        chat_id2, topic_id2, end_id = parse_telegram_link(parts[2])
        
        if chat_id1 != chat_id2:
            return await message.reply_text("❌ இரண்டு லிங்குகளும் ஒரே குரூப்பைச் சேர்ந்ததாக இருக்க வேண்டும்!")
        
        target_chat_id = chat_id1
        topic_filter = topic_id1
    except Exception as e:
        return await message.reply_text(f"❌ லிங்க் பார்மேட் தவறு: {e}")

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    msg = await message.reply_text(f"🔄 Batch ஆரம்பிக்கிறது: Message ID {start_id} முதல் {end_id} வரை...\nநிறுத்த `/cancel` அனுப்பவும்.")
    ACTIVE_TASKS[user_id] = True
    success_count = 0
    dest_chat = int(DUMP_CHANNEL) if DUMP_CHANNEL else user_id

    try:
        await resolve_and_get_message(userbot, target_chat_id, start_id, msg)

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
            await msg.edit_text(f"✅ **Batch டவுன்லோட் முடிந்தது!**\nமொத்தம் {success_count} பைல்கள் 100% ஒரிஜினலாக அனுப்பப்பட்டன.")

    except Exception as e:
        await msg.edit_text(f"❌ பிழை ஏற்பட்டது: {e}")
    finally:
        ACTIVE_TASKS[user_id] = False

# --- 10. MAIN ENGINE RUNNER ---
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
        
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot Connected!")

    await bot.set_bot_commands([
        BotCommand("start", "🏠 Home / Help"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("cancel", "❌ Cancel Task")
    ])
    
    print("🚀 Pro Max Saver Bot is Live and Running without Loop Error!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    # Pre-initialized loop running
    loop.run_until_complete(main())
