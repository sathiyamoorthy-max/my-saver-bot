import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Personal Save Restricted Bot is Running!"

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

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "👋 வணக்கம்! இது உங்களுக்கான தனிப்பட்ட Restricted Saver Bot.\n\n"
        "🔑 முதலில் உங்கள் டெலிகிராம் கணக்கை இணைக்க **/login** என்ற கட்டளையைப் பயன்படுத்தவும்.\n"
        "🚪 வெளியேற **/logout** பயன்படுத்தலாம்."
    )
    await message.reply_text(text)

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

@bot.on_message(filters.text & filters.private)
async def handle_inputs(client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip()

    if chat_id in TEMP_DATA:
        step = TEMP_DATA[chat_id].get("step")

        if step == "phone":
            phone = text
            try:
                # புதிய செஷன் உருவாக்கல்
                userbot = Client(f"user_{chat_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
                await userbot.connect()
                sent_code = await userbot.send_code(phone)
                
                # தரவுகளை தற்காலிகமாக சேமித்தல்
                TEMP_DATA[chat_id] = {
                    "userbot": userbot,
                    "phone": phone,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "step": "otp"
                }
                await message.reply_text("📩 டெலிகிராமிற்கு OTP அனுப்பப்பட்டுள்ளது!\n\nதயவுசெய்து உங்கள் டெலிகிராம் ஆப்பில் 'Telegram' என்ற அதிகாரப்பூர்வ சாட்டைப் பார்த்து OTP-ஐ இடைவெளிவிட்டு அனுப்பவும் (உதாரணம்: `1 2 3 4 5`).")
            except Exception as e:
                await message.reply_text(f"❌ பிழை: {e}")
                if chat_id in TEMP_DATA:
                    del TEMP_DATA[chat_id]

        elif step == "otp":
            otp = text.replace(" ", "")
            data = TEMP_DATA.get(chat_id)
            if not data:
                await message.reply_text("❌ காலம் கடந்துவிட்டது. மீண்டும் /login கொடுக்கவும்.")
                return

            userbot = data["userbot"]
            phone = data["phone"]
            phone_code_hash = data["phone_code_hash"]

            try:
                await userbot.sign_in(phone, phone_code_hash, otp)
                USER_SESSIONS[chat_id] = userbot
                await message.reply_text("🎉 கணக்கு வெற்றிகரமாக இணைக்கப்பட்டது!\n\nஇப்போது எந்தவொரு Restricted Channel Link-ஐயும் அனுப்பவும்.")
                del TEMP_DATA[chat_id]
            except SessionPasswordNeeded:
                TEMP_DATA[chat_id]["step"] = "2fa"
                await message.reply_text("🔒 உங்கள் கணக்கிற்கு Two-Step Verification (Password) உள்ளது. பாஸ்வோர்டை அனுப்பவும்:")
            except Exception as e:
                await message.reply_text(f"❌ OTP தவறு அல்லது காலாவதியானது: {e}")
                del TEMP_DATA[chat_id]

        elif step == "2fa":
            password = text
            data = TEMP_DATA.get(chat_id)
            if not data:
                return
            userbot = data["userbot"]
            try:
                await userbot.check_password(password)
                USER_SESSIONS[chat_id] = userbot
                await message.reply_text("🎉 2FA பாஸ்வோர்ட் மூலம் கணக்கு வெற்றிகரமாக இணைக்கப்பட்டது!")
                del TEMP_DATA[chat_id]
            except Exception as e:
                await message.reply_text(f"❌ பாஸ்வோர்ட் தவறு: {e}")
                del TEMP_DATA[chat_id]
        return

    # Link Processing & Downloading
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
                if len(parsed_link) >= 2:
                    chat_id_val = parsed_link[0]
                    msg_id = int(parsed_link[1].split("?")[0])
                else:
                    await msg.edit_text("❌ தவறான லிங்க் வடிவம்.")
                    return

            try:
                target_msg = await userbot.get_messages(chat_id_val, msg_id)
            except Exception:
                try:
                    chat_obj = await userbot.get_chat(chat_id_val)
                    target_msg = await userbot.get_messages(chat_obj.id, msg_id)
                except Exception as ex:
                    await msg.edit_text(f"❌ பிழை: {ex}\n\n*குறிப்பு:* இந்தச் சேனலில் உங்கள் லாகின் கணக்கு உறுப்பினராக இருக்க வேண்டும்.")
                    return

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
            await msg.edit_text(f"❌ Error: {e}")

# --- 3. MAIN RUNNER ---
async def main():
    await bot.start()
    print("✅ Fixed Login Bot வெற்றிகரமாக இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
