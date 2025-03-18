import os
import shutil
import asyncio
import time
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
import py7zr
import pyzipper
import tarfile
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Initialize Pyrogram client
app = Client("archive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# State management
user_states = {}
TEMP_DIR = "./temp"
SPLIT_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
MAX_ARCHIVE_SIZE = 20 * 1024 * 1024 * 1024  # 20GB
MAX_FILES_PER_ARCHIVE = 200

# States
IDLE = "IDLE"
WAITING_FOR_FORMAT = "WAITING_FOR_FORMAT"
WAITING_FOR_FILENAME = "WAITING_FOR_FILENAME"
WAITING_FOR_PASSWORD = "WAITING_FOR_PASSWORD"
WAITING_FOR_FILES = "WAITING_FOR_FILES"
ARCHIVING = "ARCHIVING"
UPLOADING = "UPLOADING"

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Helper functions
def get_user_dir(user_id):
    return os.path.join(TEMP_DIR, str(user_id))

def create_user_dir(user_id):
    os.makedirs(get_user_dir(user_id), exist_ok=True)

def delete_user_dir(user_id):
    shutil.rmtree(get_user_dir(user_id), ignore_errors=True)

def make_progress_callback(message, action):
    last_update = 0
    async def callback(current, total):
        nonlocal last_update
        now = time.time()
        if now - last_update > 1:  # Update every 1 second
            progress = (current / total) * 100
            await message.edit_text(f"{action}: {progress:.2f}%")
            last_update = now
    return callback

def group_files(files, max_size, max_count):
    groups = []
    current_group = []
    current_size = 0
    for file in files:
        file_size = os.path.getsize(file)
        if current_size + file_size > max_size or len(current_group) >= max_count:
            groups.append(current_group)
            current_group = [file]
            current_size = file_size
        else:
            current_group.append(file)
            current_size += file_size
    if current_group:
        groups.append(current_group)
    return groups

def split_file(file_path, split_size):
    parts = []
    with open(file_path, 'rb') as f:
        i = 1
        while True:
            chunk = f.read(split_size)
            if not chunk:
                break
            part_path = f"{file_path}.part{i:03d}"
            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)
            parts.append(part_path)
            i += 1
    return parts

# Command handlers
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("Welcome to the Archive Bot! Use /archive to start archiving files.")

@app.on_message(filters.command("archive"))
async def archive_command(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {
        "state": WAITING_FOR_FORMAT,
        "format": None,
        "filename": None,
        "password": None,
        "files": [],
        "total_size": 0,
        "task": None
    }
    create_user_dir(user_id)
    await message.reply("Select archive format: 7z, zip, or tar.")

# Text message handler for state management
@app.on_message(filters.text & ~filters.command(["start", "archive", "done", "cancel"]))
async def text_handler(client, message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return
    state = user_states[user_id]["state"]

    if state == WAITING_FOR_FORMAT:
        format = message.text.lower()
        if format in ["7z", "zip", "tar"]:
            user_states[user_id]["format"] = format
            user_states[user_id]["state"] = WAITING_FOR_FILENAME
            await message.reply("Provide a custom filename for the archive (e.g., myarchive).")
        else:
            await message.reply("Invalid format. Choose 7z, zip, or tar.")

    elif state == WAITING_FOR_FILENAME:
        user_states[user_id]["filename"] = message.text
        if user_states[user_id]["format"] in ["7z", "zip"]:
            user_states[user_id]["state"] = WAITING_FOR_PASSWORD
            await message.reply("Provide a password for the archive.")
        else:  # tar does not support password protection
            user_states[user_id]["state"] = WAITING_FOR_FILES
            await message.reply("Password protection is not available for tar. Send your files now.")

    elif state == WAITING_FOR_PASSWORD:
        user_states[user_id]["password"] = message.text
        user_states[user_id]["state"] = WAITING_FOR_FILES
        await message.reply("Send your files now.")

# File handler with progress
@app.on_message(filters.document | filters.photo | filters.video)
async def file_handler(client, message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id]["state"] != WAITING_FOR_FILES:
        return

    progress_message = await message.reply("Starting download...")
    progress_callback = make_progress_callback(progress_message, "Downloading")

    try:
        file_path = await message.download(file_name=get_user_dir(user_id), progress=progress_callback)
        user_states[user_id]["files"].append(file_path)
        file_size = os.path.getsize(file_path)
        user_states[user_id]["total_size"] += file_size
        await progress_message.edit_text("Download completed.")
    except Exception as e:
        await progress_message.edit_text(f"Download failed: {str(e)}")
        logger.error(f"Download failed for user {user_id}: {e}")

# Done command to start archiving
@app.on_message(filters.command("done"))
async def done_command(client, message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id]["state"] != WAITING_FOR_FILES:
        return
    if not user_states[user_id]["files"]:
        await message.reply("No files received.")
        return

    user_states[user_id]["state"] = ARCHIVING
    await message.reply("Archiving started...")
    task = asyncio.create_task(process_archive(user_id))
    user_states[user_id]["task"] = task

# Archive processing with verification
async def process_archive(user_id):
    data = user_states[user_id]
    format = data["format"]
    filename = data["filename"]
    password = data.get("password")
    files = data["files"]
    file_groups = group_files(files, MAX_ARCHIVE_SIZE, MAX_FILES_PER_ARCHIVE)
    await app.send_message(user_id, f"Starting to archive {len(files)} files into {len(file_groups)} archives...")

    for i, group in enumerate(file_groups):
        archive_filename = f"{filename}_{i+1}"
        archive_base_path = os.path.join(get_user_dir(user_id), archive_filename)
        archive_path = f"{archive_base_path}.{format}"

        try:
            if format == "7z":
                with py7zr.SevenZipFile(archive_path, 'w', password=password) as archive:
                    for file in group:
                        archive.write(file, os.path.basename(file))
            elif format == "zip":
                with pyzipper.AESZipFile(archive_path, 'w', compression=pyzipper.ZIP_LZMA, encryption=pyzipper.WZ_AES) as archive:
                    archive.setpassword(password.encode())
                    for file in group:
                        archive.write(file, os.path.basename(file))
            elif format == "tar":
                with tarfile.open(archive_path, 'w') as archive:
                    for file in group:
                        archive.add(file, arcname=os.path.basename(file))

            # Verify archive
            if format == "7z":
                with py7zr.SevenZipFile(archive_path, 'r', password=password) as archive:
                    archive.list()
            elif format == "zip":
                with pyzipper.AESZipFile(archive_path, 'r', encryption=pyzipper.WZ_AES, password=password.encode() if password else None) as archive:
                    archive.testzip()
            elif format == "tar":
                with tarfile.open(archive_path, 'r') as archive:
                    archive.list()

            await app.send_message(user_id, f"Archive {archive_filename}.{format} created and verified.")

            # Check size and split if necessary
            if os.path.getsize(archive_path) > SPLIT_SIZE:
                parts = split_file(archive_path, SPLIT_SIZE)
                os.remove(archive_path)
                await upload_files(user_id, parts)
            else:
                await upload_files(user_id, [archive_path])

        except Exception as e:
            await app.send_message(user_id, f"Error with archive {archive_filename}.{format}: {str(e)}")
            logger.error(f"Archiving error for user {user_id}: {e}")

    delete_user_dir(user_id)
    if user_id in user_states:
        del user_states[user_id]

# Upload files with progress
async def upload_files(user_id, archive_files):
    for part in archive_files:
        progress_message = await app.send_message(user_id, f"Starting upload of {os.path.basename(part)}...")
        progress_callback = make_progress_callback(progress_message, "Uploading")
        try:
            await app.send_document(user_id, part, progress=progress_callback)
            await progress_message.edit_text(f"Upload of {os.path.basename(part)} completed.")
            os.remove(part)
        except Exception as e:
            await progress_message.edit_text(f"Upload failed: {str(e)}")
            logger.error(f"Upload failed for user {user_id}: {e}")

# Cancel command
@app.on_message(filters.command("cancel"))
async def cancel_command(client, message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id]["state"] in ["WAITING_FOR_FILES", "ARCHIVING", "UPLOADING"]:
        if "task" in user_states[user_id] and user_states[user_id]["task"]:
            user_states[user_id]["task"].cancel()
        delete_user_dir(user_id)
        del user_states[user_id]
        await message.reply("Operation cancelled.")
    else:
        await message.reply("No operation to cancel.")

# Run the bot
if __name__ == "__main__":
    os.makedirs(TEMP_DIR, exist_ok=True)
    app.run()
