import os
import asyncio

# --- FIX: Event Loop for Python 3.10+ ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# ----------------------------------------

from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
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
ACTIVE_TASKS = [] # குளோன் மற்றும் பேட்ச் வேலைகளைக் கண்காணிக்க

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home"),
        BotCommand("batch", "📦 Batch Download"),
        BotCommand("clone", "♻️ Clone from Message"),
        BotCommand("cancel", "❌ Cancel Task")
    ]
    await client.set_bot_commands(commands)

# --- HELPER: Send Media in Original Format ---
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
        "🤖 **Restricted Saver Bot (Pro Active)**\n\n"
        "• Single Link: எந்தவொரு லிங்கையும் அனுப்பவும்.\n"
        "• Batch: பல பைல்களை எடுக்க `/batch`\n"
        "• Clone: நீங்கள் அனுப்பும் லிங்கில் இருந்து குளோன் செய்ய `/clone <link>`\n"
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

# ================= CLONE COMMAND (UPDATED) =================
@bot.on_message(filters.command("clone") & filters.private)
async def clone_chat(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("❌ String Session இணைக்கப்படவில்லை.")
        
    if chat_id in ACTIVE_TASKS:
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடந்து கொண்டிருக்கிறது. முதலில் `/cancel` செய்யவும்.")

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        return await message.reply_text("⚠️ **பயன்பாடு:**\n`/clone <குரூப் லிங்க்>`\n\nஉதாரணம்:\n`/clone https://t.me/c/123456789/39549`")

    link = command_parts[1].strip()
    msg = await message.reply_text("🔄 குரூப் விவரங்களைச் சரிபார்க்கிறது...")
    
    try:
        if "/c/" in link:
            target_chat_id = int("-100" + link.split("/c/")[1].split("/")[0])
        else:
            target_chat_id = link.split("t.me/")[1].split("/")[0]

        # லிங்கில் உள்ள குறிப்பிட்ட மெசேஜ் ஐடியை மட்டும் எடுக்கிறது
        start_msg_id = int(link.split("/")[-1].split("?")[0])

        last_msg_id = start_msg_id
        async for m in userbot.get_chat_history(target_chat_id, limit=1):
            last_msg_id = m.id

        if start_msg_id > last_msg_id:
            return await msg.edit_text("❌ தவறான லிங்க். இந்த மெசேஜ் குரூப்பில் இல்லை.")

        total_msgs = (last_msg_id - start_msg_id) + 1
        await msg.edit_text(f"🚀 **Clone தொடங்குகிறது...**\n• ஆரம்பம்: {start_msg_id}\n• முடிவு: {last_msg_id}\n• மொத்தம்: {total_msgs} மெசேஜ்கள்.\n\nநிறுத்த `/cancel` கமாண்டை அனுப்பவும்.")
        
        ACTIVE_TASKS.append(chat_id)
        success_count = 0

        for msg_id in range(start_msg_id, last_msg_id + 1):
            if chat_id not in ACTIVE_TASKS:
                await message.reply_text("❌ Clone பாதியில் ரத்து செய்யப்பட்டது!")
                break
                
            try:
                target_msg = await userbot.get_messages(target_chat_id, msg_id)
                if not target_msg or target_msg.empty:
                    continue
                    
                if target_msg.media:
                    file_path = await userbot.download_media(target_msg)
                    if file_path:
                        await send_media_nicely(client, message.chat.id, target_msg, file_path)
                        os.remove(file_path)
                        success_count += 1
                elif target_msg.text:
                    await client.send_message(message.chat.id, target_msg.text)
                    success_count += 1
                
                # FloodWait வராமல் தடுக்க 2 வினாடி தாமதம்
                await asyncio.sleep(2)
                
            except Exception:
                continue # டெலீட் ஆன மெசேஜ்களைத் தவிர்த்துவிட்டு அடுத்ததற்குச் செல்லும்
        
        if chat_id in ACTIVE_TASKS:
            await message.reply_text(f"✅ **Clone முழுமையாக முடிந்தது!**\nமொத்தம் {success_count} பைல்கள்/மெசேஜ்கள் பாதுகாக்கப்பட்டன.")
            
    except Exception as e:
        await msg.edit_text(f"❌ பிழை: {e}")
    finally:
        # ஏதேனும் எரர் வந்தாலும் Task-ஐ க்ளியர் செய்துவிடும்
        if chat_id in ACTIVE_TASKS:
            ACTIVE_TASKS.remove(chat_id)

# ================= BATCH COMMAND =================
@bot.on_message(filters.command("batch") & filters.private)
async def batch_start(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        return await message.reply_text("⚠️ Session இல்லை!")
    if chat_id in ACTIVE_TASKS:
        return await message.reply_text("⚠️ ஏற்கனவே ஒரு வேலை நடக்கிறது. `/cancel` செய்யவும்.")
        
    BATCH_DATA[chat_id] = {"step": "first_link"}
    await message.reply_text("📦 **Batch Mode**\nஆரம்பக் (First) லிங்கை அனுப்பவும்:")

@bot.on_message(filters.text & filters.private & ~filters.command(["start", "clone", "cancel", "batch"]))
async def handle_inputs(client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip()

    if not userbot:
        return

    # Batch Steps Processing
    if chat_id in BATCH_DATA:
        b_step = BATCH_DATA[chat_id].get("step")
        if b_step == "first_link":
            BATCH_DATA[chat_id]["first_link"] = text
            BATCH_DATA[chat_id]["step"] = "last_link"
            await message.reply_text("📦 இப்போது இறுதித் (Last) லிங்கை அனுப்பவும்:")
            return

        elif b_step == "last_link":
            first_link = BATCH_DATA[chat_id]["first_link"]
            last_link = text
            del BATCH_DATA[chat_id]

            try:
                first_parts = first_link.split("/")
                last_parts = last_link.split("/")
                start_id = int(first_parts[-1].split("?")[0])
                end_id = int(last_parts[-1].split("?")[0])
                base_url = first_link.rsplit("/", 1)[0]
                
                ACTIVE_TASKS.append(chat_id)
                status_msg = await message.reply_text(f"🚀 Batch டவுன்லோட் தொடங்குகிறது...")

                for msg_id in range(start_id, end_id + 1):
                    if chat_id not in ACTIVE_TASKS:
                        await message.reply_text("❌ Batch ரத்து செய்யப்பட்டது!")
                        break
                    try:
                        current_link = f"{base_url}/{msg_id}"
                        if "/c/" in current_link:
                            chat_id_val = int("-100" + current_link.split("/c/")[1].split("/")[0])
                        else:
                            chat_id_val = current_link.split("t.me/")[1].split("/")[0]

                        target_msg = await userbot.get_messages(chat_id_val, msg_id)
                        if target_msg and target_msg.media:
                            file_path = await userbot.download_media(target_msg)
                            await send_media_nicely(client, message.chat.id, target_msg, file_path)
                            os.remove(file_path)
                        elif target_msg and target_msg.text:
                            await client.send_message(message.chat.id, target_msg.text)
                        
                        await asyncio.sleep(2) # FloodWait Safety
                    except Exception:
                        continue

                if chat_id in ACTIVE_TASKS:
                    await status_msg.edit_text("✅ Batch டவுன்லோட் முடிந்தது!")
            except Exception as e:
                await message.reply_text(f"❌ பிழை: {e}")
            finally:
                if chat_id in ACTIVE_TASKS:
                    ACTIVE_TASKS.remove(chat_id)
            return

    # Single Link Processing
    if "t.me/" in text:
        msg = await message.reply_text("⏳ பைலைத் தேடுகிறது...")
        try:
            if "/c/" in text:
                chat_id_val = int("-100" + text.split("/c/")[1].split("/")[0])
                msg_id = int(text.split("/")[-1].split("?")[0])
            else:
                chat_id_val = text.split("t.me/")[1].split("/")[0]
                msg_id = int(text.split("/")[-1].split("?")[0])

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
            print(f"⚠️ Sync Error: {e}")
            
    await set_bot_commands(bot)
    print("✅ Bot வெற்றிகரமாக இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
