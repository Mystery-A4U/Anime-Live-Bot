# Anime4u Unified Upload Bot v3

This version connects one Telegram admin bot to:

- Anime4u.Tamil → Turso + MongoDB
- Anime Live → Backblaze B2

For Anime4u.Tamil, `/add` creates the Turso anime/season/episode records automatically. After an episode is successfully saved, the anime and episode are marked `published=1` so the website integration can display them immediately.

MongoDB stores Telegram file references only.

## Environment
Copy `.env.example` to `.env` and fill the existing variables. Do not commit `.env`.
