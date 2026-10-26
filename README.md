# Anime4u Unified Upload Bot

One Telegram admin bot with two upload destinations:

1. 🌐 Anime4u.Tamil
   - Turso stores anime/season/episode metadata.
   - MongoDB stores Telegram file references only.
   - No video is copied into MongoDB or Turso.

2. 🔴 Anime Live
   - Telethon/MTProto reads the Telegram media.
   - Backblaze B2 large-file multipart upload stores the media.
   - B2 folders are object-name prefixes.
   - The whole video is not intentionally saved to disk.

## Flow

Send a video/document → choose `Anime4u.Tamil` or `Anime Live`.

### Anime4u.Tamil
The bot asks:
- Anime ID from Turso
- Season number
- Episode number
- Filename
- Send the file again

Then it creates/updates the season and episode in Turso and stores the Telegram file reference in MongoDB.

### Anime Live
The bot asks:
- Filename
- B2 Root/folder
- Send the file again

Then it uploads the Telegram media to B2 using multipart upload.

## Environment variables

Copy `.env.example` to `.env` and fill all values.

Never commit `.env` or Telegram/B2/Mongo/Turso credentials.

## Run

```bash
pip install -r requirements.txt
python bot.py
```

## Railway / Render

Start command:

```bash
python bot.py
```

This is a long-running Telegram polling/MTProto process. Use a host/service that keeps the process alive.

## Notes

- MongoDB is not a SQL database and needs no SQL schema.
- Turso tables are created automatically on startup.
- Existing Anime Live B2 folder logic is preserved.
- ADMIN_IDS must contain numeric Telegram user IDs separated by commas.
