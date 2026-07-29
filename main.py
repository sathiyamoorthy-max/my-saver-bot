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

async def set_bot_commands(client):
    commands = [
        BotCommand("start", "🏠 Home / Start"),
        BotCommand("batch", "📦 Batch Fetch (Bulk Download)"),
        BotCommand("cancel", "❌ Cancel Task"),
        BotCommand("id", "🆔 My User ID")
    ]
    await client.set_bot_commands(commands)

@bot.on_message(filters.command("start"))
async def start(client, message):
    text = (
        "🤖 **Restricted Saver Bot (Active)**\n\n"
        "✨ போட் வெற்றிகரமாக இயக்கத்தில் உள்ளது!\n"
        "• எந்தவொரு Restricted Channel Link-ஐயும் அனுப்பவும்.\n"
        "• பல லிங்குகளை டவுன்லோட் செய்ய **/batch** பயன்படுத்தவும்."
    )
    await message.reply_text(text)

@bot.on_message(filters.command("id") & filters.private)
async def get_id(client, message: Message):
    await message.reply_text(f"🆔 Your User ID: `{message.from_user.id}`")

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    chat_id = message.chat.id
    if chat_id in BATCH_DATA:
        del BATCH_DATA[chat_id]
    await message.reply_text("❌ நடப்பில் இருந்த பணி ரத்து செய்யப்பட்டது!")

@bot.on_message(filters.command("batch") & filters.private)
async def batch_start(client, message: Message):
    chat_id = message.chat.id
    if not userbot:
        await message.reply_text("⚠️ Environment-ல் `STRING_SESSION` சரியாக இணைக்கப்படவில்லை!")
        return
    
    BATCH_DATA[chat_id] = {"step": "first_link"}
    await message.reply_text("📦 **Batch Mode (Bulk Download)**\n\nதயவுசெய்து ஆரம்பக் (First) லிங்கை அனுப்பவும்:")

@bot.on_message(filters.text & filters.private & ~filters.command(["start", "id", "cancel", "batch"]))
async def handle_inputs(client, message: Message):
    chat_id = message.chat.id
    text = message.text.strip()

    if not userbot:
        await message.reply_text("❌ செஷன் இணைக்கப்படவில்லை. Render-ல் `STRING_SESSION` உள்ளதா எனச் சரிபார்க்கவும்.")
        return

    # Batch Steps
    if chat_id in BATCH_DATA:
        b_step = BATCH_DATA[chat_id].get("step")
        if b_step == "first_link":
            if "t.me/" not in text:
                await message.reply_text("❌ சரியான முதல் லிங்கை அனுப்பவும்.")
                return
            BATCH_DATA[chat_id]["first_link"] = text
            BATCH_DATA[chat_id]["step"] = "last_link"
            await message.reply_text("📦 இப்போது இறுதித் (Last) லிங்கை அனுப்பவும்:")
            return

        elif b_step == "last_link":
            if "t.me/" not in text:
                await message.reply_text("❌ சரியான இறுதி லிங்கை அனுப்பவும்.")
                return
            
            first_link = BATCH_DATA[chat_id]["first_link"]
            last_link = text
            del BATCH_DATA[chat_id]

            try:
                first_parts = first_link.split("/")
                last_parts = last_link.split("/")
                
                # FIX for 3-part links (Topic Groups)
                start_id = int(first_parts[-1].split("?")[0])
                end_id = int(last_parts[-1].split("?")[0])
                
                # Get the base url by removing the very last message ID part
                base_url = first_link.rsplit("/", 1)[0]
                
                status_msg = await message.reply_text(f"🚀 Batch டவுன்லோட் தொடங்குகிறது ({abs(end_id - start_id) + 1} பைல்கள்)...")

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
                        
                        await asyncio.sleep(1)
                    except Exception:
                        continue

                await status_msg.edit_text("✅ Batch டவுன்லோட் வெற்றிகரமாக முடிந்தது!")
            except Exception as e:
                await message.reply_text(f"❌ Batch பிழை: {e}")
            return

    # Single Link Processing
    if "t.me/" in text:
        msg = await message.reply_text("⏳ பைலைத் தேடுகிறது...")
        try:
            link = text
            if "/c/" in link:
                parts = link.split("/c/")
                sub_parts = parts[1].split("/")
                chat_id_val = int("-100" + sub_parts[0])
                msg_id = int(sub_parts[-1].split("?")[0]) # FIX for 3-part links
            else:
                parsed_link = link.split("t.me/")[1].split("/")
                chat_id_val = parsed_link[0]
                msg_id = int(parsed_link[-1].split("?")[0])

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
            await msg.edit_text(f"❌ Error: {e}")

# --- 3. MAIN RUNNER ---
async def main():
    await bot.start()
    if userbot:
        await userbot.start()
        print("✅ Userbot String Session Connected!")
    await set_bot_commands(bot)
    print("✅ Bot வெற்றிகரமாக இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop.run_until_complete(main())
