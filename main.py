import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from pyrogram.errors import SessionPasswordNeeded
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Ultimate Save Restricted Bot is Running!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# --- 2. CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_SESSIONS = {}
TEMP_DATA = {}
BATCH_DATA = {}

# Menu Commands Setup
async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home / Start"),
        BotCommand("login", "🔑 Connect Account"),
        BotCommand("logout", "🚪 Logout Account"),
        BotCommand("batch", "📦 Batch Fetch (Bulk Download)"),
        BotCommand("cancel", "❌ Cancel Task"),
        BotCommand("id", "🆔 My User ID")
    ]
    await client.set_bot_commands(commands)

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "🤖 **Ultimate Restricted Saver Bot**\n\n"
        "✨ கீழെയുള്ള Menu பட்டனைப் பயன்படுத்தி அனைத்து வசதிகளையும் பெறலாம்:\n"
        "• **/login** - டெலிகிராம் கணக்கை இணைக்க\n"
        "• **/batch** - ஒரே நேரத்தில் பல லிங்குகளை டவுன்லோட் செய்ய\n"
        "• **/logout** - கணக்கைத் துண்டிக்க\n"
        "• **/cancel** - நடக்கும் பணியை நிறுத்த"
    )
    await message.reply_text(text)

@bot.on_message(filters.command("id") & filters.private)
async def get_id(client, message: Message):
    await message.reply_text(f"🆔 Your User ID: `{message.from_user.id}`")

@bot.on_message(filters.command("login") & filters.private)
async def login_step1(client, message: Message):
    chat_id = message.chat.id
    await message.reply_text("📱 உங்கள் டெலிகிராம் போன் நம்பரை நாட்டை குறியீட்டுடன் அனுப்பவும்.\n*(உதாரணம்: +919876543210)*")
    TEMP_DATA[chat_id] = {"step": "phone"}

@bot.on_message(filters.command("logout") & filters.private)
async def logout(client, message: Message):
    chat_id = message.chat.id
    if chat_id in USER_SESSIONS:
        await USER_SESSIONS[chat_id].stop()
        del USER_SESSIONS[chat_id]
        await message.reply_text("✅ வெற்றிகரமாக Logout செய்யப்பட்டுவிட்டது!")
    else:
        await message.reply_text("❌ நீங்கள் எந்த கணக்கிலும் இணைக்கப்படவில்லை.")

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if chat_id in TEMP_DATA:
        del TEMP_DATA[chat_id]
    if chat_id in BATCH_DATA:
        del BATCH_DATA[chat_id]
    await message.reply_text("❌ நடப்பில் இருந்த பணி ரத்து செய்யப்பட்டது!")

# --- BATCH / BULK DOWNLOAD COMMAND ---
@bot.on_message(filters.command("batch") & filters.private)
async def batch_start(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in USER_SESSIONS:
        await message.reply_text("⚠️ முதலில் உங்கள் கணக்கை இணைக்க **/login** கட்டளையைப் பயன்படுத்தவும்!")
        return
    
    BATCH_DATA[chat_id] = {"step": "first_link"}
    await message.reply_text("📦 **Batch Mode (Bulk Download)**\n\nதயவுசெய்து ஆரம்பக் (First) லிங்கை அனுப்பவும்:")

@bot.on_message(filters.text & filters.private)
async def handle_inputs(client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip()

    # Login Steps
    if chat_id in TEMP_DATA:
        step = TEMP_DATA[chat_id].get("step")

        if step == "phone":
            try:
                userbot = Client(f"user_{chat_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
                await userbot.connect()
                sent_code = await userbot.send_code(text)
                TEMP_DATA[chat_id] = {
                    "userbot": userbot,
                    "phone": text,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "step": "otp"
                }
                await message.reply_text("📩 OTP அனுப்பப்பட்டுள்ளது!\n\nஉங்கள் டெலிகிராம் ஆப்பில் 'Telegram' சாட்டைப் பார்த்து OTP-ஐ இடைவெளிவிட்டு அனுப்பவும் (எ.கா: `1 2 3 4 5`).")
            except Exception as e:
                await message.reply_text(f"❌ பிழை: {e}")
                del TEMP_DATA[chat_id]

        elif step == "otp":
            otp = text.replace(" ", "")
            data = TEMP_DATA.get(chat_id)
            if not data:
                return
            try:
                await data["userbot"].sign_in(data["phone"], data["phone_code_hash"], otp)
                USER_SESSIONS[chat_id] = data["userbot"]
                await message.reply_text("🎉 கணக்கு வெற்றிகரமாக இணைக்கப்பட்டது!")
                del TEMP_DATA[chat_id]
            except SessionPasswordNeeded:
                TEMP_DATA[chat_id]["step"] = "2fa"
                await message.reply_text("🔒 Two-Step Verification பாஸ்வோர்டை அனுப்பவும்:")
            except Exception as e:
                await message.reply_text(f"❌ OTP தவறு: {e}")
                del TEMP_DATA[chat_id]

        elif step == "2fa":
            data = TEMP_DATA.get(chat_id)
            if not data:
                return
            try:
                await data["userbot"].check_password(text)
                USER_SESSIONS[chat_id] = data["userbot"]
                await message.reply_text("🎉 2FA பாஸ்வோர்ட் மூலம் கணக்கு இணைக்கப்பட்டது!")
                del TEMP_DATA[chat_id]
            except Exception as e:
                await message.reply_text(f"❌ பாஸ்வோர்ட் தவறு: {e}")
                del TEMP_DATA[chat_id]
        return

    # Batch Steps (Bulk Links)
    if chat_id in BATCH_DATA:
        b_step = BATCH_DATA[chat_id].get("step")
        if b_step == "first_link":
            if "t.me/" not in text:
                await message.reply_text("❌ சரியான முதல் லிங்கை அனுப்பவும்.")
                return
            BATCH_DATA[chat_id]["first_link"] = text
            BATCH_DATA[chat_id]["step"] = "last_link"
            await message.reply_text("📦 இப்போது இறுதித் (Last) லிங்கை அனுப்பவும் (மொத்தமாக டவுன்லோட் செய்ய):")
            return

        elif b_step == "last_link":
            if "t.me/" not in text:
                await message.reply_text("❌ சரியான இறுதி லிங்கை அனுப்பவும்.")
                return
            
            first_link = BATCH_DATA[chat_id]["first_link"]
            last_link = text
            del BATCH_DATA[chat_id]

            # லிங்கில் இருந்து ID மற்றும் எண்களை பிரித்தல்
            try:
                first_parts = first_link.split("/")
                last_parts = last_link.split("/")
                
                start_id = int(first_parts[-1].split("?")[0])
                end_id = int(last_parts[-1].split("?")[0])
                
                base_url = first_link.rsplit("/", 1)[0]
                
                status_msg = await message.reply_text(f"🚀 Batch டவுன்லோட் தொடங்குகிறது ({abs(end_id - start_id) + 1} பைல்கள்)...")
                userbot = USER_SESSIONS[chat_id]

                for msg_id in range(start_id, end_id + 1):
                    try:
                        current_link = f"{base_url}/{msg_id}"
                        if "/c/" in current_link:
                            parts = current_link.split("/c/")
                            sub_parts = parts[1].split("/")
                            chat_id_val = int("-100" + sub_parts[0])
                        else:
                            parsed = current_link.split("t.me/")[1].split("/")
                            chat_id_val = parsed[0]

                        target_msg = await userbot.get_messages(chat_id_val, msg_id)
                        if target_msg and target_msg.media:
                            file_path = await userbot.download_media(target_msg)
                            await client.send_document(
                                message.chat.id, 
                                file_path, 
                                caption=target_msg.caption if target_msg.caption else ""
                            )
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        elif target_msg and target_msg.text:
                            await client.send_message(message.chat.id, target_msg.text)
                        
                        await asyncio.sleep(1) # டெலிகிராம் Flood limit தவிர்க்க
                    except Exception:
                        continue

                await status_msg.edit_text("✅ Batch டவுன்லோட் வெற்றிகரமாக முடிந்தது!")
            except Exception as e:
                await message.reply_text(f"❌ Batch பிழை: {e}")
            return

    # Single Link Processing
    if "t.me/" in text:
        if chat_id not in USER_SESSIONS:
            await message.reply_text("⚠️ முதலில் உங்கள் கணக்கை இணைக்க **/login** கட்டளையைப் பயன்படுத்தவும்!")
            return

        userbot = USER_SESSIONS[chat_id]
        msg = await message.reply_text("⏳ பைலைத் தேடுகிறது...")
        
        try:
            link = text
            if "/c/" in link:
                parts = link.split("/c/")
                sub_parts = parts[1].split("/")
                chat_id_val = int("-100" + sub_parts[0])
                msg_id = int(sub_parts[1].split("?")[0])
            else:
                parsed_link = link.split("t.me/")[1].split("/")
                chat_id_val = parsed_link[0]
                msg_id = int(parsed_link[1].split("?")[0])

            try:
                target_msg = await userbot.get_messages(chat_id_val, msg_id)
            except Exception:
                chat_obj = await userbot.get_chat(chat_id_val)
                target_msg = await userbot.get_messages(chat_obj.id, msg_id)

            if not target_msg or target_msg.empty:
                await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")
                return

            await msg.edit_text("📥 டவுன்லோட் ஆகிறது...")
            
            if target_msg.media:
                file_path = await userbot.download_media(target_msg)
                await msg.edit_text("📤 உங்களுக்கு அனுப்புகிறேன்...")
                
                await client.send_document(
                    message.chat.id, 
                    file_path, 
                    caption=target_msg.caption if target_msg.caption else ""
                )
                
                if os.path.exists(file_path):
                    os.remove(file_path)
            elif target_msg.text:
                await client.send_message(message.chat.id, target_msg.text)
                
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f.format(f"❌ Error: {e}"))

# --- 3. MAIN RUNNER ---
async def main():
    await bot.start()
    await set_bot_commands(bot)
    print("✅ Ultimate Bot with Menu & Batch Feature இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
