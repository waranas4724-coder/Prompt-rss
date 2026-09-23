import os
import re
import time
import html
import hashlib
import sqlite3
import threading
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import feedparser
import requests
from flask import Flask, jsonify, request, send_from_directory
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
ADMIN_IDS = {x.strip() for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

PORT = int(os.environ.get("PORT", "10000"))
DB_FILE = os.environ.get("DB_FILE", "/tmp/ai_prompt.db")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://your-site.netlify.app").rstrip("/")
POLL_MINUTES = int(os.environ.get("POLL_MINUTES", "30"))
POSTS_PER_CYCLE = int(os.environ.get("POSTS_PER_CYCLE", "5"))

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 AI-Prompt-Aggregator/1.0 (+RSS reader)"
}
HTTP_TIMEOUT = 18

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("ai-prompt")

app = Flask(__name__)

# ============================================================
# 100 RSS SOURCES
# Note: feeds are validated at runtime. Failed feeds are skipped.
# ============================================================
RSS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai/index.xml"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("Engadget", "https://www.engadget.com/rss.xml"),
    ("ZDNET AI", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/"),
    ("AI News", "https://www.artificialintelligence-news.com/feed/"),
    ("AI Business", "https://aibusiness.com/rss.xml"),
    ("AI Magazine", "https://aimagazine.com/rss"),
    ("AI Multiple", "https://research.aimultiple.com/feed/"),
    ("Analytics India", "https://analyticsindiamag.com/feed/"),
    ("Unite.AI", "https://www.unite.ai/feed/"),
    ("KDnuggets", "https://www.kdnuggets.com/feed"),
    ("Towards Data Science", "https://towardsdatascience.com/feed"),
    ("Data Science Central", "https://www.datasciencecentral.com/feed/"),
    ("Machine Learning Mastery", "https://machinelearningmastery.com/feed/"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("NVIDIA AI", "https://blogs.nvidia.com/feed/"),
    ("Microsoft AI", "https://news.microsoft.com/source/topics/ai/feed/"),
    ("Meta AI", "https://ai.meta.com/blog/rss/"),
    ("AWS Machine Learning", "https://aws.amazon.com/blogs/machine-learning/feed/"),
    ("IBM Research", "https://research.ibm.com/blog/rss.xml"),
    ("Apple Machine Learning", "https://machinelearning.apple.com/rss.xml"),
    ("BAIR", "https://bair.berkeley.edu/blog/feed.xml"),
    ("Google Research", "https://research.google/blog/rss/"),
    ("NVIDIA Developer", "https://developer.nvidia.com/blog/feed/"),
    ("Adobe Blog", "https://blog.adobe.com/en/publish/rss"),
    ("Canva Design", "https://www.canva.com/newsroom/news/feed/"),
    ("Creative Bloq", "https://www.creativebloq.com/feeds/all"),
    ("Designboom", "https://www.designboom.com/feed/"),
    ("It's Nice That", "https://www.itsnicethat.com/rss"),
    ("AIGA Eye on Design", "https://eyeondesign.aiga.org/feed/"),
    ("Smashing Magazine", "https://www.smashingmagazine.com/feed/"),
    ("CSS-Tricks", "https://css-tricks.com/feed/"),
    ("Web Designer Depot", "https://www.webdesignerdepot.com/feed/"),
    ("Creative Market", "https://creativemarket.com/blog/feed"),
    ("Digital Arts", "https://www.digitalartsonline.co.uk/feed/"),
    ("3D Artist", "https://www.3dartistonline.com/feed/"),
    ("CG Channel", "https://www.cgchannel.com/feed/"),
    ("80 Level", "https://80.lv/feed/"),
    ("Creative Boom", "https://www.creativeboom.com/feed/"),
    ("ArtStation Blog", "https://magazine.artstation.com/feed/"),
    ("Behance Blog", "https://www.behance.net/blog/feed"),
    ("DeviantArt Blog", "https://blog.deviantart.com/feed/"),
    ("Product Hunt", "https://www.producthunt.com/feed"),
    ("Futurepedia", "https://www.futurepedia.io/blog/rss.xml"),
    ("There's An AI For That", "https://theresanaiforthat.com/feed/"),
    ("Ben's Bites", "https://www.bensbites.com/feed"),
    ("The Rundown AI", "https://www.therundown.ai/rss"),
    ("The Neuron", "https://www.theneurondaily.com/feed"),
    ("AI Breakfast", "https://aibreakfast.beehiiv.com/feed"),
    ("Import AI", "https://jack-clark.net/feed/"),
    ("Last Week in AI", "https://lastweekinai.substack.com/feed"),
    ("AI Tidbits", "https://aitidbits.substack.com/feed"),
    ("AI Supremacy", "https://aisupremacy.substack.com/feed"),
    ("AI Tool Report", "https://aitoolreport.beehiiv.com/feed"),
    ("The AI Solopreneur", "https://theaisolopreneur.substack.com/feed"),
    ("AI Art Weekly", "https://aiartweekly.com/feed/"),
    ("AI Art Magazine", "https://aiartmagazine.com/feed/"),
    ("AI Artist", "https://aiartistmagazine.com/feed/"),
    ("AI Art Generation", "https://aiartgeneration.com/feed/"),
    ("Prompt Engineering Daily", "https://promptengineeringdaily.com/feed/"),
    ("Prompt Engineering Guide", "https://www.promptingguide.ai/feed.xml"),
    ("Learn Prompting", "https://learnprompting.org/rss.xml"),
    ("PromptBase Blog", "https://promptbase.com/blog/rss.xml"),
    ("FlowGPT Blog", "https://flowgpt.com/blog/rss.xml"),
    ("Awesome AI Tools", "https://awesomeaitools.com/feed/"),
    ("Future Tools", "https://www.futuretools.io/blog/rss.xml"),
    ("Toolify AI", "https://www.toolify.ai/blog/feed"),
    ("There's An AI For That Blog", "https://theresanaiforthat.com/blog/feed/"),
    ("AI Tool Guru", "https://aitoolguru.com/feed/"),
    ("AI Tools Club", "https://aitoolsclub.com/feed/"),
    ("AI Valley", "https://aivalley.ai/feed/"),
    ("AI Scout", "https://aiscout.net/feed/"),
    ("AI Tool Hunt", "https://aitoolhunt.com/feed/"),
    ("AI Tools Directory", "https://aitoolsdirectory.com/feed/"),
    ("AI Tool Mall", "https://aitoolmall.com/feed/"),
    ("AI Tools Network", "https://aitoolsnetwork.com/feed/"),
    ("AI Tool Trek", "https://aitooltrek.com/feed/"),
    ("AI Tools Arena", "https://aitoolsarena.com/feed/"),
    ("AI Video Generator Blog", "https://www.ai-video-generator.com/feed/"),
    ("Runway Blog", "https://runwayml.com/blog/rss.xml"),
    ("Pika Blog", "https://pika.art/blog/rss.xml"),
    ("Kling AI Blog", "https://klingai.com/blog/feed/"),
    ("Luma AI Blog", "https://lumalabs.ai/blog/rss.xml"),
    ("Stability AI Blog", "https://stability.ai/news/rss.xml"),
    ("Midjourney Blog", "https://www.midjourney.com/blog/rss/"),
    ("Ideogram Blog", "https://ideogram.ai/blog/rss.xml"),
    ("Leonardo AI Blog", "https://leonardo.ai/blog/feed/"),
    ("Black Forest Labs", "https://blackforestlabs.ai/feed/"),
    ("ComfyUI Blog", "https://blog.comfy.org/rss.xml"),
    ("Civitai Blog", "https://civitai.com/articles/rss"),
    ("Tensor.Art", "https://tensor.art/articles/rss"),
    ("Artbreeder Blog", "https://www.artbreeder.com/blog/rss"),
    ("NightCafe Blog", "https://nightcafe.studio/blog/rss"),
    ("OpenArt Blog", "https://openart.ai/blog/rss.xml"),
    ("Freepik AI Blog", "https://www.freepik.com/blog/feed/"),
    ("Shutterstock AI", "https://www.shutterstock.com/blog/feed"),
    ("Getty Images AI", "https://www.gettyimages.com/blog/feed"),
    ("PetaPixel AI", "https://petapixel.com/tag/ai/feed/"),
    ("No Film School AI", "https://nofilmschool.com/rss.xml"),
    ("Creative COW", "https://creativecow.net/feed/"),
    ("Motionographer", "https://motionographer.com/feed/"),
    ("Cartoon Brew", "https://www.cartoonbrew.com/feed"),
]

# ============================================================
# DATABASE
# ============================================================
def db():
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS prompts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        prompt TEXT,
        tutorial TEXT,
        media_type TEXT NOT NULL DEFAULT 'image',
        image TEXT,
        source TEXT,
        source_url TEXT,
        model TEXT,
        category TEXT,
        tags TEXT,
        score REAL DEFAULT 0,
        published INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at);
    CREATE INDEX IF NOT EXISTS idx_prompts_score ON prompts(score);
    CREATE INDEX IF NOT EXISTS idx_prompts_type ON prompts(media_type);
    """)
    for name, url in RSS_FEEDS:
        con.execute(
            "INSERT OR IGNORE INTO feeds(name,url,enabled,created_at) VALUES(?,?,1,?)",
            (name, url, now())
        )
    con.commit()
    con.close()

def now():
    return datetime.now(timezone.utc).isoformat()

def uid_for(url, title):
    return hashlib.sha256((url.strip() + "|" + title.strip().lower()).encode()).hexdigest()[:32]

def clean_text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

# ============================================================
# PROMPT EXTRACTION / CLASSIFICATION
# ============================================================
AI_TERMS = {
    "ai", "prompt", "generative", "image generator", "video generator",
    "text to image", "text-to-image", "text to video", "text-to-video",
    "image generation", "video generation", "ai art", "ai video",
    "seedance", "kling", "runway", "veo", "sora", "midjourney",
    "stable diffusion", "flux", "nano banana", "ideogram", "leonardo",
    "comfyui", "luma", "pika", "grok imagine", "chatgpt image"
}
IMAGE_TERMS = {"image", "photo", "portrait", "poster", "illustration", "visual", "art", "photography", "thumbnail"}
VIDEO_TERMS = {"video", "cinematic", "motion", "camera movement", "animation", "film", "shot", "scene", "veo", "sora", "kling", "runway", "seedance", "pika", "luma"}
PROMPT_TERMS = {"prompt", "prompting", "copy", "template", "workflow", "recipe", "generation", "generate", "create", "style"}
MODEL_RE = re.compile(r"\b(GPT\s*Image(?:\s*2(?:\.\d+)?)?|Nano Banana(?: Pro)?|Seedance(?:\s*2(?:\.\d+)?)?|Kling(?:\s*AI)?|Runway|Veo(?:\s*\d+(?:\.\d+)?)?|Sora|Midjourney|Flux(?:\s*[\w.-]+)?|Ideogram|Leonardo|ComfyUI|Pika|Luma|Grok Imagine)\b", re.I)

def extract_image(entry):
    for key in ("media_content", "media_thumbnail"):
        vals = entry.get(key) or []
        if vals:
            u = vals[0].get("url")
            if u:
                return u
    html_block = entry.get("summary", "") or entry.get("description", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_block, re.I)
    return m.group(1) if m else ""

def infer_model(text):
    m = MODEL_RE.search(text)
    return m.group(1) if m else "Other"

def classify(title, desc):
    text = (title + " " + desc).lower()
    ai_hits = sum(1 for x in AI_TERMS if x in text)
    image_hits = sum(1 for x in IMAGE_TERMS if x in text)
    video_hits = sum(1 for x in VIDEO_TERMS if x in text)
    prompt_hits = sum(1 for x in PROMPT_TERMS if x in text)

    if ai_hits < 1 or prompt_hits < 1:
        return None

    if video_hits > image_hits:
        media = "video"
    else:
        media = "image"

    if "prompt" in text or "prompting" in text:
        category = "Prompt"
    elif media == "video":
        category = "AI Video"
    else:
        category = "AI Image"

    score = min(100, ai_hits * 8 + prompt_hits * 10 + max(image_hits, video_hits) * 6)
    return media, category, score

def extract_prompt(title, desc):
    # Prefer explicit prompt-like blocks.
    text = clean_text(desc)
    patterns = [
        r"(?:prompt|prompt:|use this prompt|copy this prompt)\s*[:\-]\s*(.{80,1800})",
        r"(?:example prompt)\s*[:\-]\s*(.{80,1800})",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).strip()
    # RSS feeds often describe a prompt but do not expose the exact text.
    # Store a concise, original-content-derived prompt seed instead of pretending it is exact.
    if len(text) >= 120:
        return f"Create an AI {('video' if 'video' in (title+' '+desc).lower() else 'image')} inspired by this concept: {title.strip()}. Preserve the key visual direction described by the source."
    return ""

def tags_for(text):
    tags = []
    low = text.lower()
    for word in ["cinematic", "portrait", "fashion", "anime", "cyberpunk", "fantasy", "product", "travel", "horror", "3d", "realistic", "film", "social media", "poster"]:
        if word in low:
            tags.append(word)
    return ", ".join(tags[:8])

# ============================================================
# RSS
# ============================================================
def get_feeds():
    con = db()
    rows = con.execute("SELECT * FROM feeds WHERE enabled=1 ORDER BY id").fetchall()
    con.close()
    return rows

def fetch_feed(row):
    try:
        r = requests.get(row["url"], headers=REQUEST_HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        out = []
        for e in parsed.entries[:30]:
            title = clean_text(e.get("title", ""))
            if not title:
                continue
            desc = clean_text(e.get("summary", "") or e.get("description", ""))
            source_url = e.get("link", "") or row["url"]
            combined = title + " " + desc
            cls = classify(title, desc)
            if not cls:
                continue
            media, category, score = cls
            prompt = extract_prompt(title, desc)
            if not prompt:
                continue
            model = infer_model(combined)
            image = extract_image(e)
            uid = uid_for(source_url, title)
            out.append({
                "uid": uid, "title": title[:300], "description": desc[:2000],
                "prompt": prompt[:5000], "tutorial": "",
                "media_type": media, "image": image,
                "source": row["name"], "source_url": source_url,
                "model": model, "category": category,
                "tags": tags_for(combined), "score": score
            })
        return out
    except Exception as e:
        log.warning("Feed failed: %s | %s", row["name"], e)
        return []

def collect():
    feeds = get_feeds()
    items = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(fetch_feed, row) for row in feeds]
        for f in as_completed(futures):
            items.extend(f.result())

    con = db()
    new_items = []
    for item in items:
        # Freshness bonus.
        item["score"] = round(float(item["score"]) + 10, 2)
        try:
            con.execute("""
                INSERT INTO prompts
                (uid,title,description,prompt,tutorial,media_type,image,source,source_url,model,category,tags,score,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item["uid"], item["title"], item["description"], item["prompt"],
                item["tutorial"], item["media_type"], item["image"], item["source"],
                item["source_url"], item["model"], item["category"], item["tags"],
                item["score"], now()
            ))
            new_items.append(item)
        except sqlite3.IntegrityError:
            pass
    con.commit()
    con.close()
    log.info("Collected %d new prompt items", len(new_items))
    return new_items

# ============================================================
# 50 TELEGRAM DESIGNS
# ============================================================
def design_caption(item, n):
    title = item["title"]
    media = "🎬 VIDEO" if item["media_type"] == "video" else "🖼️ IMAGE"
    model = item["model"]
    source = item["source"]
    tags = item["tags"] or "AI • Creative • Trending"
    prompt = item["prompt"]
    short = prompt[:900]

    templates = [
        f"🔥 TRENDING AI PROMPT #{n}\n\n{media}  •  {title}\n\n🤖 Model: {model}\n🏷️ {tags}\n\n✨ Prompt\n<blockquote>{html.escape(short)}</blockquote>\n\n📡 Source: {html.escape(source)}",
        f"╭─ ✦ DAILY AI DROP\n│ {media}\n╰─ {title}\n\n🧠 <b>MODEL</b>  {model}\n\n📝 <b>PROMPT</b>\n<blockquote>{html.escape(short)}</blockquote>\n\n🔎 {html.escape(source)}",
        f"🚀 <b>NEW CREATIVE FIND</b>\n\n{title}\n\n{media} | {model}\n\n<blockquote>{html.escape(short)}</blockquote>\n\n#AI #Prompt #{'Video' if item['media_type']=='video' else 'Image'}",
        f"💎 <b>PROMPT OF THE DAY</b>\n\n🎯 {title}\n🧩 {model}\n📂 {tags}\n\n<blockquote>{html.escape(short)}</blockquote>\n\n⚡ Save it. Copy it. Create.",
        f"🪄 <b>AI CREATOR CARD</b>\n\n{media}\n<b>{title}</b>\n\nPrompt ↓\n<blockquote>{html.escape(short)}</blockquote>\n\n🧠 {model} • {html.escape(source)}",
    ]
    # 5 base layouts × 10 wording variants = 50 visually/textually distinct designs.
    base = templates[(n - 1) % 5]
    suffixes = [
        "\n\n👇 Open the full prompt below.",
        "\n\n📌 Full prompt available on the website.",
        "\n\n🔥 Trending today.",
        "\n\n🎨 Built for creators.",
        "\n\n⚡ Try this idea with your favorite AI model.",
        "\n\n📋 Copy-ready prompt.",
        "\n\n🌐 More details inside.",
        "\n\n✨ Fresh AI inspiration.",
        "\n\n🎬 Explore • Copy • Create.",
        "\n\n💡 Keep this one saved.",
    ]
    return base + suffixes[(n - 1) // 5]

def design_keyboard(item):
    article_url = f"{WEBSITE_URL}/?id={item['id']}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 COPY / FULL PROMPT", url=article_url)],
        [InlineKeyboardButton("🌐 OPEN PROMPT WEBSITE", url=WEBSITE_URL)],
        [InlineKeyboardButton("📡 SOURCE", url=item["source_url"])]
    ])

async def publish_item(bot, item):
    caption = design_caption(item, ((item["id"] - 1) % 50) + 1)
    markup = design_keyboard(item)
    try:
        if item["image"]:
            await bot.send_photo(
                chat_id=TG_CHAT_ID,
                photo=item["image"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            await bot.send_message(
                chat_id=TG_CHAT_ID,
                text=caption,
                parse_mode="HTML",
                reply_markup=markup
            )
        return True
    except Exception as e:
        log.error("Telegram publish failed: %s", e)
        return False

async def publish_new_items(bot, limit=POSTS_PER_CYCLE):
    con = db()
    rows = con.execute("""
        SELECT * FROM prompts WHERE published=0
        ORDER BY score DESC, created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    con.close()

    count = 0
    for row in rows:
        item = dict(row)
        if await publish_item(bot, item):
            con = db()
            con.execute("UPDATE prompts SET published=1 WHERE id=?", (item["id"],))
            con.commit()
            con.close()
            count += 1
    return count

# ============================================================
# TELEGRAM BOT COMMANDS
# ============================================================
def is_admin(update: Update):
    if not ADMIN_IDS:
        return True  # Configure ADMIN_IDS in production.
    return str(update.effective_user.id) in ADMIN_IDS

async def alive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🟢 AI Prompt Bot is alive and running.")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        "🚀 Started.\n\n"
        "/alive — status\n"
        "/add channel <RSS_URL> — add RSS source\n"
        "/remove channel <ID> — remove source\n"
        "/stop or /st — pause automation\n"
        "/start — resume automation\n"
        "/status — system status\n"
        "/sources — list sources\n"
        "/refresh — fetch now\n"
        "/publish — publish queued items\n"
        "/stats — statistics"
    )

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    global AUTOMATION_STOPPED
    AUTOMATION_STOPPED = True
    await update.message.reply_text("⏸ Automation stopped. Use /start to resume.")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    args = context.args
    if len(args) < 2 or args[0].lower() != "channel":
        await update.message.reply_text("Use: /add channel https://example.com/feed.xml")
        return
    url = args[1].strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ Invalid URL.")
        return
    name = urlparse(url).netloc or "Custom RSS"
    con = db()
    try:
        cur = con.execute("INSERT INTO feeds(name,url,enabled,created_at) VALUES(?,?,1,?)", (name, url, now()))
        con.commit()
        await update.message.reply_text(f"✅ Added source #{cur.lastrowid}\n{name}\n{url}")
    except sqlite3.IntegrityError:
        await update.message.reply_text("⚠️ This source already exists.")
    finally:
        con.close()

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    args = context.args
    if len(args) < 2 or args[0].lower() != "channel":
        await update.message.reply_text("Use: /remove channel ID")
        return
    try:
        feed_id = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    con = db()
    cur = con.execute("UPDATE feeds SET enabled=0 WHERE id=?", (feed_id,))
    con.commit()
    con.close()
    await update.message.reply_text("✅ Source disabled." if cur.rowcount else "❌ Source not found.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    con = db()
    feeds = con.execute("SELECT COUNT(*) c FROM feeds WHERE enabled=1").fetchone()["c"]
    prompts = con.execute("SELECT COUNT(*) c FROM prompts").fetchone()["c"]
    queued = con.execute("SELECT COUNT(*) c FROM prompts WHERE published=0").fetchone()["c"]
    con.close()
    await update.message.reply_text(
        f"📊 Status\n\n"
        f"RSS sources: {feeds}\n"
        f"Prompt records: {prompts}\n"
        f"Queued: {queued}\n"
        f"Automation: {'STOPPED' if AUTOMATION_STOPPED else 'RUNNING'}"
    )

async def sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    con = db()
    rows = con.execute("SELECT id,name,url FROM feeds WHERE enabled=1 ORDER BY id").fetchall()
    con.close()
    text = "📚 Active RSS Sources\n\n" + "\n".join(f"{r['id']}. {r['name']}" for r in rows)
    await update.message.reply_text(text[:4000])

async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🔄 Fetching RSS sources...")
    items = collect()
    await update.message.reply_text(f"✅ Added {len(items)} new prompt candidates.")

async def publish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("📤 Publishing queued prompts...")
    count = await publish_new_items(context.bot, limit=POSTS_PER_CYCLE)
    await update.message.reply_text(f"✅ Published {count} posts.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    con = db()
    total = con.execute("SELECT COUNT(*) c FROM prompts").fetchone()["c"]
    image = con.execute("SELECT COUNT(*) c FROM prompts WHERE media_type='image'").fetchone()["c"]
    video = con.execute("SELECT COUNT(*) c FROM prompts WHERE media_type='video'").fetchone()["c"]
    published = con.execute("SELECT COUNT(*) c FROM prompts WHERE published=1").fetchone()["c"]
    con.close()
    await update.message.reply_text(
        f"📈 Stats\n\nTotal: {total}\n🖼️ Image: {image}\n🎬 Video: {video}\n📢 Published: {published}"
    )

# ============================================================
# WEB API
# ============================================================
@app.route("/")
def home():
    return send_from_directory(str(Path(__file__).parent / "website"), "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ai-trending-prompt"})

@app.route("/api/prompts")
def api_prompts():
    q = request.args.get("q", "").strip()
    media = request.args.get("media", "").strip().lower()
    limit = min(max(int(request.args.get("limit", 60)), 1), 100)

    con = db()
    sql = "SELECT * FROM prompts WHERE 1=1"
    params = []
    if media in ("image", "video"):
        sql += " AND media_type=?"
        params.append(media)
    if q:
        sql += " AND (title LIKE ? OR prompt LIKE ? OR tags LIKE ? OR model LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like, like]
    sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    con.close()
    return jsonify({"items": rows, "count": len(rows)})

@app.route("/api/prompt/<int:item_id>")
def api_prompt(item_id):
    con = db()
    row = con.execute("SELECT * FROM prompts WHERE id=?", (item_id,)).fetchone()
    con.close()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify(dict(row))

# ============================================================
# AUTOMATION LOOP
# ============================================================
AUTOMATION_STOPPED = False

async def automation_loop(bot):
    while True:
        try:
            if not AUTOMATION_STOPPED:
                collect()
                await publish_new_items(bot)
        except Exception as e:
            log.exception("Automation error: %s", e)
        await __import__("asyncio").sleep(POLL_MINUTES * 60)

def start_web():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def post_init(application):
    application.create_task(automation_loop(application.bot))

def main():
    init_db()

    if not BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is required")
    if not TG_CHAT_ID:
        raise RuntimeError("TG_CHAT_ID is required")

    threading.Thread(target=start_web, daemon=True).start()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("alive", alive))
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("st", stop_cmd))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("sources", sources_cmd))
    application.add_handler(CommandHandler("refresh", refresh_cmd))
    application.add_handler(CommandHandler("publish", publish_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))

    log.info("AI Prompt Bot starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
