import os
import asyncio
import hashlib
import io
import re
import unicodedata
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient
import libsql
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeFilename
from b2sdk.v2 import B2Api, InMemoryAccountInfo

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = os.getenv("MONGO_DB", "anime4u")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "telegram_files")

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

B2_APPLICATION_KEY_ID = os.getenv("B2_APPLICATION_KEY_ID", "")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(900 * 1024 * 1024)))
PART_SIZE = int(os.getenv("PART_SIZE", str(10 * 1024 * 1024)))
SESSION_NAME = os.getenv("SESSION_NAME", "anime4u_merged_bot")
PAGE_SIZE = 15

mongo = None
mongo_files = None
turso = None
b2 = None
bucket = None
state = {}


def is_admin(event):
    return event.sender_id in ADMIN_IDS


def clean_name(value):
    value = (value or "").strip().replace("\\", "/").split("/")[-1]
    if not value or value in (".", "..") or "\x00" in value:
        return None
    return value


def clean_folder(value):
    value = (value or "").strip().strip("/")
    if not value:
        return ""
    if "\\" in value or ".." in value or "\x00" in value:
        return None
    return value


def human_size(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n or 0)
    for unit in units:
        if x < 1024 or unit == units[-1]:
            return f"{x:.1f} {unit}"
        x /= 1024


def original_filename(message):
    if message.document:
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
    return f"{message.id}.bin"


def make_anime_id(title):
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "anime"


def unique_anime_id(title):
    base = make_anime_id(title)
    row = turso.execute("SELECT id FROM anime WHERE id=?", (base,)).fetchone()
    if not row:
        return base
    i = 2
    while turso.execute("SELECT id FROM anime WHERE id=?", (f"{base}-{i}",)).fetchone():
        i += 1
    return f"{base}-{i}"


def ensure_turso_schema():
    turso.executescript(
        """
        CREATE TABLE IF NOT EXISTS anime (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            synopsis TEXT DEFAULT '',
            poster_url TEXT DEFAULT '',
            type TEXT DEFAULT 'anime-series',
            genre TEXT DEFAULT '',
            release_year INTEGER,
            language TEXT DEFAULT '',
            published INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_id TEXT NOT NULL,
            season_number INTEGER NOT NULL,
            title TEXT DEFAULT '',
            UNIQUE(anime_id, season_number)
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_id TEXT NOT NULL,
            season_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            title TEXT DEFAULT '',
            published INTEGER DEFAULT 0,
            UNIQUE(anime_id, season_id, episode_number)
        );
        """
    )
    turso.commit()


def get_anime(anime_id):
    return turso.execute("SELECT id,title FROM anime WHERE id=? LIMIT 1", (anime_id,)).fetchone()


def find_anime_by_title(title):
    return turso.execute(
        "SELECT id,title FROM anime WHERE lower(title)=lower(?) LIMIT 1", (title,)
    ).fetchone()


def create_anime(title, synopsis, poster_url, anime_type, genre, release_year, language):
    existing = find_anime_by_title(title)
    if existing:
        return existing[0], existing[1], False

    anime_id = unique_anime_id(title)
    turso.execute(
        """
        INSERT INTO anime(id,title,synopsis,poster_url,type,genre,release_year,language,published)
        VALUES(?,?,?,?,?,?,?,?,0)
        """,
        (anime_id, title, synopsis, poster_url, anime_type, genre, release_year, language),
    )
    turso.commit()
    return anime_id, title, True


def ensure_season(anime_id, season_number):
    turso.execute(
        "INSERT OR IGNORE INTO seasons(anime_id,season_number) VALUES(?,?)",
        (anime_id, season_number),
    )
    turso.commit()
    return turso.execute(
        "SELECT id FROM seasons WHERE anime_id=? AND season_number=?",
        (anime_id, season_number),
    ).fetchone()[0]


def upsert_episode(anime_id, season_id, episode_number, title):
    turso.execute(
        """
        INSERT INTO episodes(anime_id,season_id,episode_number,title,published)
        VALUES(?,?,?,?,0)
        ON CONFLICT(anime_id,season_id,episode_number)
        DO UPDATE SET title=excluded.title
        """,
        (anime_id, season_id, episode_number, title),
    )
    turso.commit()
    return turso.execute(
        "SELECT id FROM episodes WHERE anime_id=? AND season_id=? AND episode_number=?",
        (anime_id, season_id, episode_number),
    ).fetchone()[0]


def b2_folders():
    folders = set()
    for fv, _ in bucket.ls(folder_to_list="", recursive=True, fetch_count=1000):
        parts = fv.file_name.strip("/").split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]) + "/")
    return sorted(folders, key=str.lower)


def b2_keyboard(page=0):
    folders = b2_folders()
    start = page * PAGE_SIZE
    rows = [[Button.inline("🏠 Root", b"b2folder|")]]
    for folder in folders[start:start + PAGE_SIZE]:
        rows.append([Button.inline("📁 " + folder, ("b2folder|" + folder).encode())])
    nav = []
    if page:
        nav.append(Button.inline("⬅️ Previous", f"b2page|{page-1}".encode()))
    if start + PAGE_SIZE < len(folders):
        nav.append(Button.inline("Next ➡️", f"b2page|{page+1}".encode()))
    if nav:
        rows.append(nav)
    rows.append([Button.inline("🔄 Refresh", b"b2refresh")])
    return folders, rows


async def save_anime4u_file(event, session):
    message = session["message"]
    anime_id = session["anime_id"]
    season_number = session["season"]
    episode_number = session["episode"]
    filename = session["name"]

    anime = await asyncio.to_thread(get_anime, anime_id)
    if not anime:
        await event.respond("❌ Anime no longer exists in Turso. Please use /add again.")
        state.pop(event.sender_id, None)
        return

    season_id = await asyncio.to_thread(ensure_season, anime_id, season_number)
    episode_id = await asyncio.to_thread(upsert_episode, anime_id, season_id, episode_number, filename)

    file_id = None
    file_size = 0
    media_type = "document"
    if message.video:
        file_id = message.video.id
        file_size = message.video.size or 0
        media_type = "video"
    elif message.document:
        file_id = message.document.id
        file_size = message.document.size or 0
        media_type = "document"

    doc = {
        "anime_id": anime_id,
        "season": season_number,
        "episode": episode_number,
        "episode_id": episode_id,
        "chat_id": message.chat_id,
        "message_id": message.id,
        "file_id": file_id,
        "file_name": filename,
        "file_size": file_size,
        "media_type": media_type,
        "uploaded_at": datetime.now(timezone.utc),
    }
    await asyncio.to_thread(
        mongo_files.update_one,
        {"anime_id": anime_id, "season": season_number, "episode": episode_number},
        {"$set": doc},
        True,
    )

    await event.respond(
        "✅ **Anime4u.Tamil episode saved**\n\n"
        f"🎬 `{anime[1]}`\n"
        f"🆔 `{anime_id}`\n"
        f"📚 Season: `{season_number}`\n"
        f"🎞️ Episode: `{episode_number}`\n"
        f"📄 `{filename}`\n"
        f"📦 `{human_size(file_size)}`\n\n"
        "💾 Turso: anime + season + episode metadata\n"
        "🍃 MongoDB: Telegram file reference"
    )
    state.pop(event.sender_id, None)


async def upload_to_b2(event, session):
    message = session["message"]
    name = session["name"]
    folder = session.get("folder", "")
    obj = f"{folder}/{name}" if folder else name

    status = await event.respond(
        "⬇️ **Downloading + ☁️ uploading to Anime Live...**\n\n"
        f"📄 `{name}`\n📁 `{folder or '(root)'}`"
    )

    file_id = None
    hashes = []
    part_no = 1
    total = session.get("size") or 0
    done = 0
    buffer = bytearray()

    try:
        large_file = await asyncio.to_thread(b2.session.start_large_file, bucket.id_, obj, "b2/x-auto", {})
        file_id = large_file["fileId"]

        async for chunk in message.client.iter_download(message.media, request_size=PART_SIZE, chunk_size=PART_SIZE):
            buffer.extend(chunk)
            while len(buffer) >= PART_SIZE:
                part = bytes(buffer[:PART_SIZE])
                del buffer[:PART_SIZE]
                digest = hashlib.sha1(part).hexdigest()
                await asyncio.to_thread(
                    b2.session.upload_part, file_id, part_no, len(part), digest, io.BytesIO(part)
                )
                hashes.append(digest)
                done += len(part)
                pct = int(done * 100 / total) if total else 0
                try:
                    await status.edit(
                        "⬇️ **Anime Live upload**\n\n"
                        f"📄 `{name}`\n📁 `{folder or '(root)'}`\n\n"
                        f"`{human_size(done)}` / `{human_size(total) if total else '?'}` ({pct}%)\n"
                        f"Part: `{part_no}`"
                    )
                except Exception:
                    pass
                part_no += 1

        if buffer:
            part = bytes(buffer)
            digest = hashlib.sha1(part).hexdigest()
            await asyncio.to_thread(
                b2.session.upload_part, file_id, part_no, len(part), digest, io.BytesIO(part)
            )
            hashes.append(digest)
            done += len(part)

        await asyncio.to_thread(b2.session.finish_large_file, file_id, hashes)
        await status.edit(
            "✅ **ANIME LIVE UPLOAD COMPLETE**\n\n"
            f"📄 `{name}`\n📁 `{folder or '(root)'}`\n\n"
            f"☁️ **B2 path:**\n`{obj}`\n\n"
            f"📦 Size: `{human_size(done)}`"
        )
    except Exception as exc:
        if file_id:
            try:
                await asyncio.to_thread(b2.session.cancel_large_file, file_id)
            except Exception:
                pass
        try:
            await status.edit("❌ **B2 upload failed**\n\n`" + str(exc)[:1200] + "`")
        except Exception:
            pass
    finally:
        state.pop(event.sender_id, None)


async def start(event):
    if not is_admin(event):
        return await event.respond("⛔ Admin access only.")
    await event.respond(
        "👑 **Anime4u Unified Upload Bot**\n\n"
        "🌐 **Anime4u.Tamil** — Turso + MongoDB\n"
        "🔴 **Anime Live** — Backblaze B2\n\n"
        "/add — create/add an Anime4u anime + episode\n"
        "/folders — show Anime Live B2 folders\n"
        "/cancel — cancel current operation\n\n"
        "You do **not** need to create Turso tables manually."
    )


async def cancel(event):
    if not is_admin(event):
        return await event.respond("⛔ Admin access only.")
    state.pop(event.sender_id, None)
    await event.respond("❌ Cancelled.")


async def add_anime(event):
    if not is_admin(event):
        return await event.respond("⛔ Admin access only.")
    state[event.sender_id] = {"step": "anime_title", "destination": "anime4u"}
    await event.respond(
        "🌐 **Anime4u.Tamil — New Anime**\n\n"
        "Send the anime title.\n\n"
        "Example: `Solo Leveling`\n\n"
        "/cancel to stop."
    )


async def folders_command(event):
    if not is_admin(event):
        return await event.respond("⛔ Admin access only.")
    try:
        folders, buttons = await asyncio.to_thread(b2_keyboard, 0)
        await event.respond(f"📂 **Anime Live B2 folders**\n\nFound: `{len(folders)}`", buttons=buttons)
    except Exception as exc:
        await event.respond("❌ Folder read failed.\n`" + str(exc)[:700] + "`")


async def media(event):
    if not is_admin(event) or not (event.message.document or event.message.video):
        return

    size_bytes = getattr(event.message.document, "size", 0) or getattr(event.message.video, "size", 0) or 0
    if size_bytes > MAX_FILE_SIZE:
        return await event.respond(f"❌ File exceeds configured limit: **{human_size(MAX_FILE_SIZE)}**")

    session = state.get(event.sender_id)

    # Complete the second-file stage of an Anime4u flow.
    if session and session.get("step") == "waiting_file":
        session["message"] = event.message
        session["size"] = size_bytes
        return await save_anime4u_file(event, session)

    # Complete the second-file stage of an Anime Live flow.
    if session and session.get("step") == "live_waiting_file":
        session["message"] = event.message
        session["size"] = size_bytes
        return await upload_to_b2(event, session)

    # A new media upload: ask where it should go.
    state[event.sender_id] = {
        "step": "destination",
        "message": event.message,
        "original": original_filename(event.message),
        "size": size_bytes,
    }
    await event.respond(
        "📤 **Where do you want to upload this?**\n\nChoose one destination:",
        buttons=[
            [Button.inline("🌐 Anime4u.Tamil", b"dest|anime4u")],
            [Button.inline("🔴 Anime Live", b"dest|live")],
            [Button.inline("❌ Cancel", b"dest|cancel")],
        ],
    )


async def text(event):
    if not is_admin(event):
        return
    text_value = (event.raw_text or "").strip()
    if not text_value or text_value.startswith("/") or event.message.document or event.message.video:
        return

    session = state.get(event.sender_id)
    if not session:
        return

    step = session["step"]

    if step == "anime_title":
        title = text_value[:200]
        session["title"] = title
        session["step"] = "synopsis"
        return await event.respond("📝 Send the synopsis.\n\nOr type `skip`.")

    if step == "synopsis":
        session["synopsis"] = "" if text_value.lower() == "skip" else text_value[:5000]
        session["step"] = "poster_url"
        return await event.respond("🖼️ Send the poster URL.\n\nOr type `skip`.")

    if step == "poster_url":
        session["poster_url"] = "" if text_value.lower() == "skip" else text_value[:2000]
        session["step"] = "type"
        return await event.respond(
            "📺 Send the type.\n\nExamples: `anime-series`, `movie`, `series`\n\nOr type `skip`."
        )

    if step == "type":
        session["type"] = "anime-series" if text_value.lower() == "skip" else text_value[:50]
        session["step"] = "genre"
        return await event.respond("🏷️ Send genre(s).\n\nExample: `Action, Fantasy, Adventure`\n\nOr type `skip`.")

    if step == "genre":
        session["genre"] = "" if text_value.lower() == "skip" else text_value[:500]
        session["step"] = "release_year"
        return await event.respond("📅 Send release year.\n\nExample: `2026`\n\nOr type `skip`.")

    if step == "release_year":
        if text_value.lower() == "skip":
            session["release_year"] = None
        else:
            try:
                year = int(text_value)
                if year < 1900 or year > 2200:
                    raise ValueError
                session["release_year"] = year
            except ValueError:
                return await event.respond("❌ Send a valid year like `2026`, or `skip`.")
        session["step"] = "language"
        return await event.respond("🗣️ Send language/dub.\n\nExample: `Tamil Dubbed`\n\nOr type `skip`.")

    if step == "language":
        session["language"] = "" if text_value.lower() == "skip" else text_value[:100]
        anime_id, anime_title, created = await asyncio.to_thread(
            create_anime,
            session["title"],
            session["synopsis"],
            session["poster_url"],
            session["type"],
            session["genre"],
            session["release_year"],
            session["language"],
        )
        session["anime_id"] = anime_id
        session["anime_title"] = anime_title
        session["step"] = "season"
        action = "created" if created else "found existing"
        return await event.respond(
            f"✅ Anime `{anime_title}` {action}.\n\n"
            f"🆔 Anime ID: `{anime_id}`\n\n"
            "Send season number (example: `1`)."
        )

    if step == "anime_id":
        anime = await asyncio.to_thread(get_anime, text_value)
        if not anime:
            return await event.respond("❌ Anime ID not found in Turso. Use /add to create an anime first.")
        session["anime_id"] = text_value
        session["anime_title"] = anime[1]
        session["step"] = "season"
        return await event.respond(f"🎬 `{anime[1]}`\n\nSend season number (example: `1`).")

    if step == "season":
        try:
            season = int(text_value)
            if season < 1:
                raise ValueError
        except ValueError:
            return await event.respond("❌ Send a valid season number, e.g. `1`.")
        session["season"] = season
        session["step"] = "episode"
        return await event.respond("📚 Season saved.\n\nSend episode number (example: `1`).")

    if step == "episode":
        try:
            episode = int(text_value)
            if episode < 1:
                raise ValueError
        except ValueError:
            return await event.respond("❌ Send a valid episode number, e.g. `1`.")
        session["episode"] = episode
        session["step"] = "name"
        return await event.respond(
            "🎞️ Episode saved.\n\n"
            f"Original filename: `{session.get('original', 'unknown')}`\n\n"
            "Send the filename to store for this episode."
        )

    if step == "name":
        name = clean_name(text_value)
        if not name:
            return await event.respond("❌ Invalid filename.")
        session["name"] = name
        session["step"] = "waiting_file"
        return await event.respond(
            f"📄 Filename: `{name}`\n\n"
            "Now send the episode file again."
        )

    if step == "live_name":
        name = clean_name(text_value)
        if not name:
            return await event.respond("❌ Invalid filename.")
        session["name"] = name
        session["step"] = "live_folder"
        try:
            _, buttons = await asyncio.to_thread(b2_keyboard, 0)
            return await event.respond("📂 **Choose Anime Live B2 destination:**", buttons=buttons)
        except Exception as exc:
            state.pop(event.sender_id, None)
            return await event.respond("❌ Could not read B2 folders.\n`" + str(exc)[:700] + "`")

    await event.respond("ℹ️ Follow the buttons/prompts for the current upload.")


async def callback(event):
    if not is_admin(event):
        return await event.answer("⛔ Admin access only.", alert=True)

    data = event.data.decode()
    session = state.get(event.sender_id)

    if data == "dest|cancel":
        state.pop(event.sender_id, None)
        return await event.edit("❌ Upload cancelled.")

    if data == "dest|anime4u":
        if not session:
            return await event.answer("No pending file.", alert=True)
        session["destination"] = "anime4u"
        session["step"] = "anime_title"
        return await event.edit(
            "🌐 **Anime4u.Tamil**\n\n"
            "Send the anime title.\n\n"
            "Example: `Solo Leveling`"
        )

    if data == "dest|live":
        if not session:
            return await event.answer("No pending file.", alert=True)
        session["destination"] = "live"
        session["step"] = "live_name"
        return await event.edit(
            "🔴 **Anime Live**\n\n"
            f"Original filename: `{session['original']}`\n\n"
            "Send the filename to use in Backblaze B2."
        )

    if data.startswith("b2page|"):
        page = int(data.split("|", 1)[1])
        _, buttons = await asyncio.to_thread(b2_keyboard, page)
        return await event.edit("📂 **Choose Anime Live B2 destination:**", buttons=buttons)

    if data == "b2refresh":
        _, buttons = await asyncio.to_thread(b2_keyboard, 0)
        return await event.edit("🔄 **Folders refreshed.** Choose destination:", buttons=buttons)

    if data.startswith("b2folder|"):
        if not session or session.get("destination") != "live":
            return await event.answer("No pending Anime Live upload.", alert=True)
        folder = clean_folder(data.split("|", 1)[1])
        if folder is None:
            return await event.answer("Invalid folder.", alert=True)
        session["folder"] = folder
        session["step"] = "live_waiting_file"
        return await event.edit(
            f"📁 Selected: `{folder or '(root)'}`\n\n"
            "Now send the video/document again to upload it to Anime Live."
        )


async def main():
    global mongo, mongo_files, turso, b2, bucket

    required = {
        "API_ID": API_ID,
        "API_HASH": API_HASH,
        "BOT_TOKEN": BOT_TOKEN,
        "MONGODB_URI": MONGODB_URI,
        "TURSO_DATABASE_URL": TURSO_DATABASE_URL,
        "TURSO_AUTH_TOKEN": TURSO_AUTH_TOKEN,
        "B2_APPLICATION_KEY_ID": B2_APPLICATION_KEY_ID,
        "B2_APPLICATION_KEY": B2_APPLICATION_KEY,
        "B2_BUCKET_NAME": B2_BUCKET_NAME,
    }
    missing = [k for k, v in required.items() if not v]
    if not ADMIN_IDS:
        missing.append("ADMIN_IDS")
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    mongo = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    mongo.admin.command("ping")
    mongo_files = mongo[MONGO_DB][MONGO_COLLECTION]

    turso = libsql.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    await asyncio.to_thread(ensure_turso_schema)

    info = InMemoryAccountInfo()
    b2 = B2Api(info)
    b2.authorize_account("production", B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY)
    bucket = b2.get_bucket_by_name(B2_BUCKET_NAME)

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)

    client.add_event_handler(start, events.NewMessage(pattern=r"^/start$"))
    client.add_event_handler(cancel, events.NewMessage(pattern=r"^/cancel$"))
    client.add_event_handler(add_anime, events.NewMessage(pattern=r"^/add$"))
    client.add_event_handler(folders_command, events.NewMessage(pattern=r"^/folders$"))
    client.add_event_handler(media, events.NewMessage(incoming=True))
    client.add_event_handler(text, events.NewMessage(incoming=True))
    client.add_event_handler(callback, events.CallbackQuery())

    me = await client.get_me()
    print(f"🤖 Logged in as @{me.username or me.id}")
    print(f"☁️ B2 bucket: {B2_BUCKET_NAME}")
    print("🚀 Anime4u Unified Upload Bot is running.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
