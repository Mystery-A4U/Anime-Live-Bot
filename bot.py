import os, asyncio, hashlib
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
from b2sdk.v2 import B2Api, InMemoryAccountInfo

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
B2_APPLICATION_KEY_ID = os.getenv("B2_APPLICATION_KEY_ID", "")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()}

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(900 * 1024 * 1024)))
PART_SIZE = int(os.getenv("PART_SIZE", str(10 * 1024 * 1024)))
SESSION_NAME = os.getenv("SESSION_NAME", "anime4u_bot")
PAGE_SIZE = 15

info = InMemoryAccountInfo()
b2 = B2Api(info)
bucket = None
state = {}

def admin(e): return e.sender_id in ADMIN_IDS

def fname(msg):
    if msg.document:
        for a in msg.document.attributes:
            if isinstance(a, DocumentAttributeFilename):
                return a.file_name
    return f"{msg.id}.bin"

def clean_name(s):
    s=(s or "").strip().replace("\\","/").split("/")[-1]
    return s if s and s not in (".","..") and "\x00" not in s else None

def clean_folder(s):
    s=(s or "").strip().strip("/")
    if not s: return ""
    if "\\" in s or ".." in s or "\x00" in s: return None
    return s

def size(n):
    u=["B","KB","MB","GB","TB"]; x=float(n)
    for z in u:
        if x<1024 or z==u[-1]: return f"{x:.1f} {z}"
        x/=1024

def folders():
    out=set()
    for fv,_ in bucket.ls(folder_to_list="", recursive=True, fetch_count=1000):
        p=fv.file_name.strip("/").split("/")
        for i in range(1,len(p)): out.add("/".join(p[:i])+"/")
    return sorted(out,key=str.lower)

def kb(page=0):
    from telethon.tl.custom import Button
    fs=folders(); start=page*PAGE_SIZE
    rows=[[Button.inline("🏠 Root", b"folder|")]]
    for f in fs[start:start+PAGE_SIZE]:
        rows.append([Button.inline("📁 "+f, ("folder|"+f).encode())])
    nav=[]
    if page: nav.append(Button.inline("⬅️ Previous", f"page|{page-1}".encode()))
    if start+PAGE_SIZE<len(fs): nav.append(Button.inline("Next ➡️", f"page|{page+1}".encode()))
    if nav: rows.append(nav)
    rows.append([Button.inline("🔄 Refresh", b"refresh")])
    return fs,rows

async def upload(e, s):
    msg=s["msg"]; name=s["name"]; folder=s["folder"]
    obj=f"{folder}/{name}" if folder else name
    status=await e.respond("⬇️ **Downloading + ☁️ uploading...**\n\n"+f"📄 `{name}`\n📁 `{folder or '(root)'}`")
    fid=None; sha=[]; partno=1; total=s.get("size") or 0; done=0; buf=bytearray()
    try:
        # b2sdk v2 exposes large-file operations on B2Api, not Bucket.
        lf=await asyncio.to_thread(
            b2.start_large_file,
            bucket_id=bucket.id_,
            file_name=obj,
            content_type="b2/x-auto",
            file_info={},
        )
        fid=lf.file_id
        async for chunk in msg.client.iter_download(msg.media, request_size=PART_SIZE, chunk_size=PART_SIZE):
            buf.extend(chunk)
            while len(buf)>=PART_SIZE:
                part=bytes(buf[:PART_SIZE]); del buf[:PART_SIZE]
                h=hashlib.sha1(part).hexdigest()
                await asyncio.to_thread(
                    b2.upload_part,
                    file_id=fid,
                    part_number=partno,
                    content_length=len(part),
                    sha1_sum=h,
                    input_stream=__import__("io").BytesIO(part),
                )
                sha.append(h); done+=len(part)
                pct=int(done*100/total) if total else 0
                try: await status.edit(f"⬇️ **Downloading + ☁️ uploading**\n\n📄 `{name}`\n📁 `{folder or '(root)'}`\n\n`{size(done)}` / `{size(total) if total else '?'}` ({pct}%)\nPart: `{partno}`")
                except: pass
                partno+=1
        if buf:
            part=bytes(buf); h=hashlib.sha1(part).hexdigest()
            await asyncio.to_thread(
                b2.upload_part,
                file_id=fid,
                part_number=partno,
                content_length=len(part),
                sha1_sum=h,
                input_stream=__import__("io").BytesIO(part),
            )
            sha.append(h); done+=len(part)
        await asyncio.to_thread(b2.finish_large_file, file_id=fid, part_sha1_array=sha)
        await status.edit(f"✅ **UPLOAD COMPLETE**\n\n📄 `{name}`\n📁 `{folder or '(root)'}`\n\n☁️ **B2 path:**\n`{obj}`\n\n📦 Size: `{size(done)}`")
    except Exception as ex:
        if fid:
            try: await asyncio.to_thread(b2.cancel_large_file, fid)
            except: pass
        try: await status.edit("❌ **Upload failed**\n\n`"+str(ex)[:1200]+"`")
        except: pass
    finally: state.pop(e.sender_id,None)

@events.register(events.NewMessage(pattern=r"^/start$"))
async def start(e):
    if not admin(e): return await e.respond("⛔ Admin access only.")
    await e.respond("👑 **Anime4u MTProto B2 Uploader**\n\nSend a file → I ask for filename → choose Root/folder → upload to B2.\n\n/folders — show folders\n/cancel — cancel")

@events.register(events.NewMessage(pattern=r"^/cancel$"))
async def cancel(e):
    if not admin(e): return await e.respond("⛔ Admin access only.")
    state.pop(e.sender_id,None); await e.respond("❌ Cancelled.")

@events.register(events.NewMessage(pattern=r"^/folders$"))
async def showfolders(e):
    if not admin(e): return await e.respond("⛔ Admin access only.")
    try:
        fs,buttons=await asyncio.to_thread(kb,0)
        await e.respond(f"📂 **B2 folders**\n\nFound: `{len(fs)}`",buttons=buttons)
    except Exception as ex: await e.respond("❌ Folder read failed.\n`"+str(ex)[:700]+"`")

@events.register(events.NewMessage(incoming=True))
async def media(e):
    if not admin(e) or not (e.message.document or e.message.video): return
    n=getattr(e.message.document,"size",0) or 0
    if n>MAX_FILE_SIZE: return await e.respond(f"❌ File exceeds configured limit: **{size(MAX_FILE_SIZE)}**")
    state[e.sender_id]={"step":"name","msg":e.message,"original":fname(e.message),"size":n}
    await e.respond(f"✏️ **What filename should I use?**\n\nOriginal: `{fname(e.message)}`\n\nSend the new filename.")

@events.register(events.NewMessage(incoming=True))
async def text(e):
    if not admin(e): return
    t=(e.raw_text or "").strip()
    if not t or t.startswith("/") or e.message.document or e.message.video: return
    s=state.get(e.sender_id)
    if not s: return
    if s["step"]=="name":
        n=clean_name(t)
        if not n: return await e.respond("❌ Invalid filename.")
        s["name"]=n; s["step"]="folder"
        try:
            fs,buttons=await asyncio.to_thread(kb,0)
            await e.respond(f"📄 Filename: `{n}`\n\n📂 **Where should I upload it?**",buttons=buttons)
        except Exception as ex:
            state.pop(e.sender_id,None); await e.respond("❌ Could not read folders.\n`"+str(ex)[:700]+"`")
    else:
        await e.respond("📂 Choose Root or a folder using the buttons.")

@events.register(events.CallbackQuery)
async def callback(e):
    if not admin(e): return await e.answer("⛔ Admin access only.",alert=True)
    d=e.data.decode()
    if d.startswith("page|"):
        _,buttons=await asyncio.to_thread(kb,int(d.split("|",1)[1]))
        return await e.edit("📂 **Choose upload location:**",buttons=buttons)
    if d=="refresh":
        _,buttons=await asyncio.to_thread(kb,0)
        return await e.edit("🔄 **Folders refreshed.** Choose location:",buttons=buttons)
    if d.startswith("folder|"):
        s=state.get(e.sender_id)
        if not s or s["step"]!="folder": return await e.answer("No pending file.",alert=True)
        f=clean_folder(d.split("|",1)[1])
        if f is None: return await e.answer("Invalid folder.",alert=True)
        s["folder"]=f; s["step"]="uploading"; await e.answer("Starting...")
        await upload(e,s)

async def main():
    global bucket
    missing=[]
    for k,v in {"API_ID":API_ID,"API_HASH":API_HASH,"BOT_TOKEN":BOT_TOKEN,"B2_APPLICATION_KEY_ID":B2_APPLICATION_KEY_ID,"B2_APPLICATION_KEY":B2_APPLICATION_KEY,"B2_BUCKET_NAME":B2_BUCKET_NAME}.items():
        if not v: missing.append(k)
    if not ADMIN_IDS: missing.append("ADMIN_IDS")
    if missing: raise RuntimeError("Missing: "+", ".join(missing))
    print("☁️ Connecting to Backblaze B2...")
    b2.authorize_account("production",B2_APPLICATION_KEY_ID,B2_APPLICATION_KEY)
    bucket=b2.get_bucket_by_name(B2_BUCKET_NAME)
    print(f"✅ Connected to bucket: {B2_BUCKET_NAME}")
    client=TelegramClient(SESSION_NAME,API_ID,API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    me=await client.get_me()
    print(f"🤖 Logged in as @{me.username or me.id}")

    # Register the handlers defined with @events.register(...)
    # Telethon does not automatically attach those handlers to a new client.
    for handler in (start, cancel, showfolders, media, text, callback):
        client.add_event_handler(handler)

    print("🚀 Anime4u MTProto B2 Uploader is running.")
    await client.run_until_disconnected()

if __name__=="__main__": asyncio.run(main())
