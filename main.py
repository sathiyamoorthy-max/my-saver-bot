import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from threading import Thread

# --- 1. DUMMY WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Running Successfully on Render!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_server, daemon=True).start()

# --- 2. BOT CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("Userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)

@bot.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("👋 வணக்கம்! எந்தவொரு Restricted Channel Link-ஐயும் எனக்கு அனுப்பவும்.")

@bot.on_message(filters.text & filters.private)
async def process_link(client, message: Message):
    link = message.text.strip()
    if "t.me/" not in link:
        await message.reply_text("❌ சரியான Message Link-ஐ அனுப்பவும்.")
        return

    msg = await message.reply_text("⏳ சேனல் விவரங்களைத் தேடுகிறது...")
    try:
        # லிங்கில் இருந்து Chat ID மற்றும் Message ID பிரித்தெடுத்தல்
        if "/c/" in link:
            parts = link.split("/c/")
            sub_parts = parts[1].split("/")
            raw_chat_id = sub_parts[0]
            chat_id = int("-100" + raw_chat_id)
            msg_id = int(sub_parts[1])
        else:
            parts = link.split("/")
            chat_id = parts[-2]
            msg_id = int(parts[-1])

        # மிக முக்கியம்: டெலிகிராம் சர்வரில் இருந்து சேனலின் உண்மையான Access-ஐ பெறுவது (Resolve)
        try:
            chat_obj = await userbot.get_chat(chat_id)
        except Exception:
            try:
                # ஒருவேளை நேரடியாக கிடைக்கவில்லை எனில் Invite Link போல இணைக்க முயல்வது
                if not str(chat_id).startswith("-100"):
                    chat_obj = await userbot.join_chat(link.split("/")[-2])
                else:
                    chat_obj = await userbot.get_chat(chat_id)
            except Exception as e:
                await msg.edit_text(f"❌ சேனலை அணுக முடியவில்லை! Error: {e}")
                return

        await msg.edit_text("📥 பைல் டவுன்லோட் ஆகிறது... (கொஞ்சம் காத்திருக்கவும்)")

        # பிரத்யேக மெசேஜ் பெறுதல்
        target_msg = await userbot.get_messages(chat_obj.id, msg_id)

        if not target_msg or target_msg.empty:
            await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")
            return

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
    await userbot.start()
    await bot.start()
    print("✅ Advanced Resolver Bot வெற்றிகரமாக இயங்குகிறது!")
    from pyrogram import idle
    await idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
