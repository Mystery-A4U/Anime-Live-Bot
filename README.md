# Anime4u MTProto B2 Uploader

Flow: Telegram file → MTProto/Telethon → B2 multipart upload.

Bot flow:
1. Send a document/video.
2. Bot asks for the desired filename.
3. Bot shows Root + folders discovered from the configured B2 bucket.
4. Choose a folder.
5. File is transferred in chunks directly into a B2 large-file upload; the whole video is not saved to Railway disk.
6. Bot returns the final B2 object path.

Required Railway variables:
API_ID
API_HASH
BOT_TOKEN
B2_APPLICATION_KEY_ID
B2_APPLICATION_KEY
B2_BUCKET_NAME
ADMIN_IDS

Optional:
MAX_FILE_SIZE=900000000
PART_SIZE=10485760
SESSION_NAME=anime4u_bot

Railway start command:
python bot.py

Keep credentials out of GitHub. Backblaze B2 folders are object-name prefixes.
