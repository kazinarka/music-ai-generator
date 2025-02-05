import os
import json
import asyncio
import requests
import logging
import aiohttp
import aiofiles
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

# 🔹 Login settings
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUNO_API_SERVERS = [
    os.getenv("SUNO_API_SERVER_1"),
    os.getenv("SUNO_API_SERVER_2"),
]
current_server_index = 0  # Start with the first server

if not TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN not found in the .env!")
if not SUNO_API_SERVERS[0] or not SUNO_API_SERVERS[1]:
    raise ValueError("❌  Not all Suno APIs are found in the .env!")

# 🔹 Folder for temp files
TEMP_DIR = "temp_audio"
HISTORY_FILE = "user_history.json"
os.makedirs(TEMP_DIR, exist_ok=True) # Create a folder if it does not exist


# Loading history from a file
def load_history():
    #Loads user history from file.
    global user_history
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            user_history = json.load(f)
        logger.info("✅ The story is loaded")

# Saving history to a file
def save_history():
    #Saves user history to a file.
    with open(HISTORY_FILE, "w") as f:
        json.dump(user_history, f)
    logger.info("✅ History saved")

# Loading history when starting the bot
user_history = {}
load_history()

async def start(update: Update, context: CallbackContext):
    #Sends a welcome message and shows buttons
    keyboard = [["🎵 Create a song", "📜 History","⏳ Day Limit"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text("Chose the option:", reply_markup=reply_markup)

async def generate_audio_by_prompt(prompt):

    url = f"{SUNO_API_SERVERS[current_server_index]}/api/generate"
    payload = {"prompt": prompt, "make_instrumental": False, "wait_audio": False}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers={'Content-Type': 'application/json'}) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"❌ Помилка генерації аудіо: {response.status}")
        except aiohttp.ClientError as e:
            logger.error(f"❌ Помилка запиту під час генерації: {e}")
    return None

async def get_audio_information(audio_ids):
    url = f"{SUNO_API_SERVERS[current_server_index]}/api/get?ids={audio_ids}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    audio_info = await response.json()
                    return audio_info
        except aiohttp.ClientError as e:
            logger.error(f"❌ Error receiving audio : {e}")
    return None

async def download_audio(url: str, file_path: str):
    # Downloads an audio file by URL and saves it locally
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Error loading audio: {response.status}")
                async with aiofiles.open(file_path, 'wb') as file:
                    await file.write(await response.read())
        except aiohttp.ClientError as e:
            logger.error(f"❌ Error downloading audio: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error while downloading: {e}")


# 🔹 Variable to store the number of user requests per day
user_limits = {}
USER_DAILY_LIMIT = int(os.getenv("USER_DAILY_LIMIT", 5))



async def check_and_update_limit(update: Update, context: CallbackContext):
    #Checks and updates user limit
    user_id = update.message.from_user.id
    today = datetime.now().date()

    if user_id not in user_limits or user_limits[user_id]["date"] != today:
        user_limits[user_id] = {"count": 0, "date": today}

    if user_limits[user_id]["count"] >= USER_DAILY_LIMIT:
        await update.message.reply_text(f"❌ You have reached your limit for today ({USER_DAILY_LIMIT} songs).")
        return False

    return True


def get_quota_information():
    #Gets information about credits in the current Suno API.
    url = f"{SUNO_API_SERVERS[current_server_index]}/api/get_limit"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            quota_info = response.json()
            logger.info(f"📊 Limit Suno API: {quota_info}")
            return quota_info
        else:
            logger.error(f"⚠️ Error getting limit  Suno API: {response.status_code}")
    except requests.RequestException as e:
        logger.error(f"❌Request error to Suno API (get_limit): {e}")
    return None


async def check_suno_limit():
    #Checks for credits on the current Suno API server and switches server if none
    for _ in range(len(SUNO_API_SERVERS)):
        url = f"{get_active_server()}/api/get_limit"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        credits_left = data.get("credits_left", 0)
                        logger.info(f"📊 Credits {get_active_server()}: {credits_left} limit")

                        if credits_left > 0:
                            return True
                        else:
                            logger.warning(f"❌ No credits for {get_active_server()}. Switching the server...")
                    else:
                        logger.error(f"⚠️ Error getting Suno API limit ({get_active_server()}): {response.status_code}")


        except aiohttp.ClientError as e:
            logger.error(f"❌ Error connecting to Suno API ({get_active_server()}): {e}")

        switch_server()

    logger.error("🚨 All Suno accounts are out of credits or unavailable!")
    return False

def add_to_history(user_id, file_path):
        #Adds a song to the user's history, keeping only the last 10 entries.
        if user_id not in user_history:
            user_history[user_id] = []

        user_history[user_id].append(file_path)

        # If there are more than 10 songs in the history, we delete the oldest one
        if len(user_history[user_id]) > 10:
            deleted_file = user_history[user_id].pop(0)
            if os.path.exists(deleted_file):
                os.remove(deleted_file)
            logger.info(f" Deleted old file {deleted_file} for {user_id}")

        save_history()



def get_active_server():
    #Returns the currently active Suno API server
    return SUNO_API_SERVERS[current_server_index]

def switch_server():
    #Switches Suno API to another server
    global current_server_index
    current_server_index = (current_server_index + 1) % len(SUNO_API_SERVERS)
    logger.info(f"🔄 We switch to the Suno API: {get_active_server()}")


async def handle_message(update: Update, context: CallbackContext):
    # Processing message from user
    user_id = update.message.from_user.id
    text = update.message.text

    if text == "📜 History":
        if user_id in user_history and user_history[user_id]:
            await update.message.reply_text("📜 Here are your generated songs:")
            for file_path in user_history[user_id]:
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as audio_file:
                        await update.message.reply_audio(audio=audio_file)
                else:
                    await update.message.reply_text(f"❌ File not found: {file_path}")
        else:
            await update.message.reply_text("😢 You don't have generated songs.")

    elif text == "⏳ Day Limit":
        today = datetime.now().date()

        if user_id not in user_limits:
            user_limits[user_id] = {"count": 0, "date": today}

        await update.message.reply_text(
            f"📊 You have used {user_limits[user_id]['count']}/{USER_DAILY_LIMIT} songs today."
        )

    elif text == "🎵 Create a song":
        if not await check_and_update_limit(update, context):
            return

        if not await check_suno_limit():
            return

        await update.message.reply_text("🎶 Write a description of the song!")
        context.user_data['waiting_for_prompt'] = True

    elif context.user_data.get('waiting_for_prompt', False):
        context.user_data['waiting_for_prompt'] = False

        asyncio.create_task(process_song_generation(update, context, text))


async def process_song_generation(update: Update, context: CallbackContext, prompt: str):

    user_id = update.message.from_user.id
    message = await update.message.reply_text("⏳ Song generation...")


    audio_data = await generate_audio_by_prompt(prompt)
    if not audio_data:
        await message.edit_text("❌ Error generating music.")
        return

    audio_ids = ",".join([str(item["id"]) for item in audio_data])

    elapsed_time = 0
    while elapsed_time < 300:
        audio_info = await get_audio_information(audio_ids)
        if audio_info and audio_info[0]["status"] == 'streaming':
            audio_url = audio_info[0].get("audio_url")
            song_title = audio_info[0].get("title", "Unknown_Title")

            song_title = song_title.replace(" ", "_").replace("/", "_").replace("\\", "_").replace("?", "").replace(
                "*", "").replace(":", "_")

            if not audio_url:
                await message.edit_text("❌ Could not get audio.")
                return

            file_name = f"{song_title}.mp3"
            file_path = os.path.join(TEMP_DIR, file_name)

            try:
                await download_audio(audio_url, file_path)

                if os.path.exists(file_path):
                    with open(file_path, 'rb') as audio_file:
                        await update.message.reply_audio(audio=audio_file)

                    add_to_history(user_id, file_path)

                    user_limits[user_id]["count"] += 1
                    save_history()

                    return
                else:
                    logger.error(f"❌ File not found after download: {file_path}")
                    await message.edit_text("❌ The generated audio file could not be found.")
                    return
            except Exception as e:
                logger.error(f"❌ Error sending audio file: {e}")
                await message.edit_text("❌ Failed to send audio file.")
                return

        if elapsed_time % 30 == 0:
            await message.edit_text(f"🔄 Still generating...")

        await asyncio.sleep(5)
        elapsed_time += 5

    await message.edit_text("❌ The song was not created in time.")



# 🔹 Launching a bot
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()