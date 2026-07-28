main.py
 import os
import asyncio
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters

# --- Web Server Trick for Render Free Tier ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def keep_alive():
    server = HTTPServer(('0.0.0.0', 8080), DummyHandler)
    server.serve_forever()

Thread(target=keep_alive, daemon=True).start()
# ---------------------------------------------

# Render Keys
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

bot = Client("Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("Userbot", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)

@bot.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("👋 வணக்கம்! Restricted Channel Message Link-ஐ எனக்கு அனுப்பவும்.")

@bot.on_message(filters.text & filters.private)
async def process_link(client, message):
    link = message.text.strip()
    if "t.me/" not in link:
        await message.reply_text("❌ சரியான Message Link-ஐ அனுப்பவும்.")
        return

    msg = await message.reply_text("⏳ தேடுகிறது...")
    try:
        if "/c/" in link:
            chat_id = int("-100" + link.split("/c/")[1].split("/")[0])
            msg_id = int(link.split("/")[-1])
        else:
            chat_id = link.split("/")[-2]
            msg_id = int(link.split("/")[-1])

        target_msg = await userbot.get_messages(chat_id, msg_id)
        if not target_msg:
            await msg.edit_text("❌ மெசேஜ் கிடைக்கவில்லை!")
            return

        await msg.edit_text("📥 டவுன்லோட் ஆகிறது... (காத்திருக்கவும்)")
        
        if target_msg.media:
            file_path = await userbot.download_media(target_msg)
            await msg.edit_text("📤 உங்களுக்கு அனுப்புகிறேன்...")
            await client.send_document(message.chat.id, file_path, caption=target_msg.caption)
            os.remove(file_path)
        elif target_msg.text:
            await client.send_message(message.chat.id, target_msg.text)
        
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def main():
    await userbot.start()
    await bot.start()
    print("✅ Bot வெற்றிகரமாக இயங்குகிறது!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
