# Anime4u Unified Upload Bot

One Telegram admin bot for two destinations:

- 🌐 **Anime4u.Tamil** → Turso metadata + MongoDB Telegram file references
- 🔴 **Anime Live** → Backblaze B2 media upload using Telethon/MTProto

## Anime4u.Tamil flow

You do **not** need to create Turso tables manually. The bot creates `anime`, `seasons`, and `episodes` automatically at startup.

Use `/add` and enter:

1. Anime title
2. Synopsis (or `skip`)
3. Poster URL (or `skip`)
4. Type (or `skip`)
5. Genre (or `skip`)
6. Release year (or `skip`)
7. Language/dub (or `skip`)
8. Season number
9. Episode number
10. Filename
11. Send the episode file

The bot automatically generates an Anime ID from the title. If the same title already exists, it reuses the existing Anime record instead of creating a duplicate.

MongoDB stores only the Telegram file reference/message information, not the video itself.

## Direct media flow

If you send a video/document without `/add`, the bot asks:

- 🌐 Anime4u.Tamil → starts the metadata flow
- 🔴 Anime Live → asks filename, B2 folder, then uploads the second file to B2

## Commands

- `/start` — help
- `/add` — create/add an Anime4u anime and episode
- `/folders` — show Anime Live B2 folders
- `/cancel` — cancel current operation

## Run

```bash
pip install -r requirements.txt
python bot.py
```

Railway/Render start command:

```bash
python bot.py
```

## Required environment variables

See `.env.example`.
