# Telegram Archive Bot

This bot allows users to archive files (videos, images, documents) in 7z, zip, or tar formats with custom filenames and password protection (for 7z and zip). It uses Pyrogram and Telegram's MTProto API to handle file uploads up to 2GB.

## Features

- Archive files in 7z, zip, or tar formats.
- Custom filenames for archives.
- Password protection for 7z and zip archives.
- Split archives into 2GB parts if necessary.
- Limit of 200 files and 20GB total size per archive.
- Live progress updates for downloading and uploading.
- Multi-part archiving for large file sets.
- Archive verification before uploading.
- Cancellation feature to stop operations.
- Uses your API ID and API hash to bypass
