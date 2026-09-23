import os
import re
import html
import hashlib
import sqlite3
import threading
import logging
import asyncio
import tempfile
import mimetypes
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import feedparser
import requests
from flask import Flask, jsonify, request, send_from_directory
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

ADMIN_IDS = {
    x.strip()
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip()
}

PORT = int(os.environ.get("PORT", "10000"))
DB_FILE = os.environ.get("DB_FILE", "/tmp/ai_prompt.db")

WEBSITE_URL = os.environ.get(
    "WEBSITE_URL",
    "https://your-site.netlify.app"
).strip().rstrip("/")

POLL_MINUTES = max(1, int(os.environ.get("POLL_MINUTES", "30")))
POSTS_PER_CYCLE = max(1, int(os.environ.get("POSTS_PER_CYCLE", "5")))

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36 "
        "AI-Prompt-Aggregator/2.0"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, "
        "application/xml, text/xml, text/html;q=0.9, */*;q=0.8"
    ),
}

HTTP_TIMEOUT = 18

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ai-prompt")
app = Flask(__name__)

AUTOMATION_STOPPED = False


# ============================================================
# RSS SOURCES
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

def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    con = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30
    )
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
        video TEXT,
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

    CREATE INDEX IF NOT EXISTS idx_prompts_created
    ON prompts(created_at);

    CREATE INDEX IF NOT EXISTS idx_prompts_score
    ON prompts(score);

    CREATE INDEX IF NOT EXISTS idx_prompts_type
    ON prompts(media_type);
    """)

    # Migrate older databases that were created without video support.
    columns = {
        row[1]
        for row in con.execute(
            "PRAGMA table_info(prompts)"
        ).fetchall()
    }

    if "video" not in columns:
        con.execute(
            "ALTER TABLE prompts ADD COLUMN video TEXT DEFAULT ''"
        )

    for name, url in RSS_FEEDS:
        con.execute(
            """
            INSERT OR IGNORE INTO feeds
            (name, url, enabled, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (name, url, now())
        )

    con.commit()
    con.close()

    log.info("Database initialised: %s", DB_FILE)


def uid_for(url, title):
    raw = url.strip() + "|" + title.strip().lower()

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:32]


def clean_text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)

    return re.sub(
        r"\s+",
        " ",
        s
    ).strip()


# ============================================================
# AI CLASSIFICATION
# ============================================================

AI_TERMS = {
    "ai",
    "prompt",
    "generative",
    "image generator",
    "video generator",
    "text to image",
    "text-to-image",
    "text to video",
    "text-to-video",
    "image generation",
    "video generation",
    "ai art",
    "ai video",
    "seedance",
    "kling",
    "runway",
    "veo",
    "sora",
    "midjourney",
    "stable diffusion",
    "flux",
    "nano banana",
    "ideogram",
    "leonardo",
    "comfyui",
    "luma",
    "pika",
    "grok imagine",
    "chatgpt image",
}

IMAGE_TERMS = {
    "image",
    "photo",
    "portrait",
    "poster",
    "illustration",
    "visual",
    "art",
    "photography",
    "thumbnail",
    "character",
    "design",
    "render",
}

VIDEO_TERMS = {
    "video",
    "cinematic",
    "motion",
    "camera movement",
    "animation",
    "film",
    "shot",
    "scene",
    "veo",
    "sora",
    "kling",
    "runway",
    "seedance",
    "pika",
    "luma",
    "camera",
}

PROMPT_TERMS = {
    "prompt",
    "prompting",
    "copy",
    "template",
    "workflow",
    "recipe",
    "generation",
    "generate",
    "create",
    "style",
    "how to",
    "tutorial",
    "guide",
}

MODEL_RE = re.compile(
    r"\b("
    r"GPT\s*Image(?:\s*2(?:\.\d+)?)?"
    r"|Nano Banana(?: Pro)?"
    r"|Seedance(?:\s*2(?:\.\d+)?)?"
    r"|Kling(?:\s*AI)?"
    r"|Runway"
    r"|Veo(?:\s*\d+(?:\.\d+)?)?"
    r"|Sora"
    r"|Midjourney"
    r"|Flux(?:\s*[\w.-]+)?"
    r"|Ideogram"
    r"|Leonardo"
    r"|ComfyUI"
    r"|Pika"
    r"|Luma"
    r"|Grok Imagine"
    r")\b",
    re.I,
)


def _absolute_url(base_url, value):
    """Convert relative media URLs into absolute HTTP(S) URLs."""
    if not value:
        return ""

    value = html.unescape(str(value).strip())

    if value.startswith("//"):
        return "https:" + value

    return urljoin(base_url or "", value)


def _is_image_url(url):
    return bool(
        url and re.search(
            r"\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?.*)?$",
            url,
            re.I
        )
    )


def _is_video_url(url):
    return bool(
        url and re.search(
            r"\.(?:mp4|webm|mov|m4v|m3u8)(?:\?.*)?$",
            url,
            re.I
        )
    )


def extract_image(entry, base_url=""):
    """
    Extract the best image URL available directly from an RSS/Atom entry.
    """
    # RSS media namespace
    for key in (
        "media_content",
        "media_thumbnail",
        "media:content",
        "media:thumbnail",
    ):
        vals = entry.get(key) or []

        if isinstance(vals, dict):
            vals = [vals]

        for value in vals:
            url = (
                value.get("url")
                or value.get("href")
                or value.get("src")
            )

            url = _absolute_url(base_url, url)

            if url and (
                _is_image_url(url)
                or str(value.get("type", "")).startswith("image/")
            ):
                return url

    # RSS enclosure
    for enclosure in entry.get("enclosures", []) or []:
        url = (
            enclosure.get("href")
            or enclosure.get("url")
        )

        url = _absolute_url(base_url, url)

        if url and (
            _is_image_url(url)
            or str(enclosure.get("type", "")).startswith("image/")
        ):
            return url

    # HTML inside summary/description/content
    blocks = []

    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            blocks.append(str(value))

    for content in entry.get("content", []) or []:
        if isinstance(content, dict):
            value = content.get("value")
            if value:
                blocks.append(str(value))

    html_block = "\n".join(blocks)

    # Prefer Open Graph / Twitter image if present in entry HTML.
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            html_block,
            re.I
        )

        if match:
            url = _absolute_url(
                base_url,
                match.group(1)
            )

            if url:
                return url

    return ""


def extract_video(entry, base_url=""):
    """
    Extract a direct video URL from an RSS/Atom entry when available.
    """
    for key in (
        "media_content",
        "media:content",
        "enclosures",
    ):
        vals = entry.get(key) or []

        if isinstance(vals, dict):
            vals = [vals]

        for value in vals:
            url = (
                value.get("url")
                or value.get("href")
                or value.get("src")
            )

            url = _absolute_url(base_url, url)

            content_type = str(
                value.get("type", "")
            ).lower()

            if url and (
                _is_video_url(url)
                or content_type.startswith("video/")
            ):
                return url

    blocks = []

    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            blocks.append(str(value))

    for content in entry.get("content", []) or []:
        if isinstance(content, dict) and content.get("value"):
            blocks.append(str(content["value"]))

    html_block = "\n".join(blocks)

    patterns = [
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']+)["\']',
        r'<video[^>]*>.*?<source[^>]+src=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            html_block,
            re.I | re.S
        )

        if match:
            url = _absolute_url(
                base_url,
                match.group(1)
            )

            if url:
                return url

    return ""


def extract_page_media(source_url):
    """
    Open the article page only when RSS did not provide media.
    Returns (image_url, video_url).
    """
    if not source_url:
        return "", ""

    try:
        response = requests.get(
            source_url,
            headers=REQUEST_HEADERS,
            timeout=10,
            allow_redirects=True,
        )

        response.raise_for_status()

        page_url = response.url or source_url
        body = response.text[:1500000]

        image = ""
        video = ""

        # OpenGraph / Twitter image.
        image_patterns = [
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        ]

        for pattern in image_patterns:
            match = re.search(
                pattern,
                body,
                re.I
            )

            if match:
                image = _absolute_url(
                    page_url,
                    match.group(1)
                )
                if image:
                    break

        # Direct HTML video/source.
        video_patterns = [
            r'<video[^>]+src=["\']([^"\']+)["\']',
            r'<source[^>]+src=["\']([^"\']+)["\']',
            r'<video[^>]*>.*?<source[^>]+src=["\']([^"\']+)["\']',
        ]

        for pattern in video_patterns:
            match = re.search(
                pattern,
                body,
                re.I | re.S
            )

            if match:
                video = _absolute_url(
                    page_url,
                    match.group(1)
                )
                if video:
                    break

        return image, video

    except Exception as exc:
        log.debug(
            "Page media extraction failed: %s | %s",
            source_url,
            exc
        )
        return "", ""


def infer_model(text):

    match = MODEL_RE.search(text)

    if match:
        return match.group(1)

    return "Other"


def classify(title, desc):
    text = (title + " " + desc).lower()

    ai_hits = sum(
        1 for x in AI_TERMS
        if x in text
    )

    image_hits = sum(
        1 for x in IMAGE_TERMS
        if x in text
    )

    video_hits = sum(
        1 for x in VIDEO_TERMS
        if x in text
    )

    prompt_hits = sum(
        1 for x in PROMPT_TERMS
        if x in text
    )

    if ai_hits < 1:
        return None

    # AI content is accepted even when the source article
    # does not literally contain the word "prompt".
    # This makes the aggregator useful for prompt extraction.
    if (
        prompt_hits < 1
        and image_hits < 1
        and video_hits < 1
    ):
        return None

    media = (
        "video"
        if video_hits > image_hits
        else "image"
    )

    if "prompt" in text or "prompting" in text:
        category = "Prompt"
    elif media == "video":
        category = "AI Video"
    else:
        category = "AI Image"

    score = min(
        100,
        ai_hits * 8
        + prompt_hits * 10
        + max(image_hits, video_hits) * 6
    )

    return media, category, score


def extract_prompt(title, desc):
    text = clean_text(desc)

    patterns = [
        r"(?:prompt|prompt:|use this prompt|copy this prompt)"
        r"\s*[:\-]\s*(.{50,1800})",

        r"(?:example prompt)"
        r"\s*[:\-]\s*(.{50,1800})",

        r"(?:prompt below|prompt is)"
        r"\s*[:\-]?\s*(.{50,1800})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:
            result = match.group(1).strip()

            if len(result) >= 50:
                return result

    # If article contains useful descriptive text,
    # generate a source-based prompt.
    if len(text) >= 80:
        kind = (
            "video"
            if any(
                x in (title + " " + desc).lower()
                for x in VIDEO_TERMS
            )
            else "image"
        )

        return (
            f"Create an AI {kind} inspired by "
            f"this concept: {title.strip()}. "
            f"Visual direction and subject: {text[:900]}. "
            f"Make the result detailed, polished and "
            f"creator-ready."
        )

    return (
        f"Create a high-quality AI image inspired by: "
        f"{title.strip()}"
        if title.strip()
        else ""
    )


def tags_for(text):
    tags = []
    low = text.lower()

    tag_words = [
        "cinematic",
        "portrait",
        "fashion",
        "anime",
        "cyberpunk",
        "fantasy",
        "product",
        "travel",
        "horror",
        "3d",
        "realistic",
        "film",
        "social media",
        "poster",
        "character",
        "architecture",
        "landscape",
    ]

    for word in tag_words:
        if word in low:
            tags.append(word)

    return ", ".join(tags[:8])


# ============================================================
# RSS
# ============================================================

def get_feeds():
    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM feeds
        WHERE enabled=1
        ORDER BY id
        """
    ).fetchall()

    con.close()

    return rows


def fetch_feed(row):
    try:
        response = requests.get(
            row["url"],
            headers=REQUEST_HEADERS,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )

        response.raise_for_status()

        parsed = feedparser.parse(
            response.content
        )

        if getattr(parsed, "bozo", False):
            log.warning(
                "Malformed RSS feed: %s",
                row["name"]
            )

        if not parsed.entries:
            log.warning(
                "No RSS entries: %s",
                row["name"]
            )
            return []

        out = []

        for entry in parsed.entries[:30]:
            title = clean_text(
                entry.get("title", "")
            )

            if not title:
                continue

            desc_parts = [
                entry.get("summary", ""),
                entry.get("description", ""),
            ]

            for content in entry.get("content", []) or []:
                if isinstance(content, dict):
                    desc_parts.append(
                        content.get("value", "")
                    )

            desc = clean_text(
                " ".join(
                    str(x)
                    for x in desc_parts
                    if x
                )
            )

            source_url = (
                entry.get("link", "")
                or row["url"]
            ).strip()

            combined = (
                title + " " + desc
            )

            classification = classify(
                title,
                desc
            )

            if not classification:
                continue

            media, category, score = classification

            prompt = extract_prompt(
                title,
                desc
            )

            if not prompt:
                continue

            model = infer_model(combined)

            # First use media supplied by RSS.
            image = extract_image(
                entry,
                source_url
            )

            video = extract_video(
                entry,
                source_url
            )

            # If RSS does not expose media, inspect the source
            # article's OpenGraph/HTML media metadata.
            if not image or (
                media == "video" and not video
            ):
                page_image, page_video = extract_page_media(
                    source_url
                )

                if not image:
                    image = page_image

                if not video:
                    video = page_video

            uid = uid_for(
                source_url,
                title
            )

            out.append({
                "uid": uid,
                "title": title[:300],
                "description": desc[:2000],
                "prompt": prompt[:5000],
                "tutorial": "",
                "media_type": media,
                "image": image,
                "video": video,
                "source": row["name"],
                "source_url": source_url,
                "model": model,
                "category": category,
                "tags": tags_for(combined),
                "score": score,
            })

        return out

    except Exception as exc:
        log.warning(
            "Feed failed: %s | %s",
            row["name"],
            exc
        )
        return []


def collect():
    feeds = get_feeds()
    items = []

    if not feeds:
        log.warning("No active RSS feeds.")
        return []

    with ThreadPoolExecutor(
        max_workers=min(12, max(1, len(feeds)))
    ) as executor:

        futures = [
            executor.submit(
                fetch_feed,
                row
            )
            for row in feeds
        ]

        for future in as_completed(futures):
            try:
                items.extend(
                    future.result()
                )
            except Exception as exc:
                log.warning(
                    "Feed worker failed: %s",
                    exc
                )

    con = db()
    new_items = []

    for item in items:
        item["score"] = round(
            float(item["score"]) + 10,
            2
        )

        try:
            cursor = con.execute(
                """
                INSERT INTO prompts
                (
                    uid,
                    title,
                    description,
                    prompt,
                    tutorial,
                    media_type,
                    image,
                    video,
                    source,
                    source_url,
                    model,
                    category,
                    tags,
                    score,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    item["uid"],
                    item["title"],
                    item["description"],
                    item["prompt"],
                    item["tutorial"],
                    item["media_type"],
                    item["image"],
                    item.get("video", ""),
                    item["source"],
                    item["source_url"],
                    item["model"],
                    item["category"],
                    item["tags"],
                    item["score"],
                    now(),
                )
            )

            item["id"] = cursor.lastrowid
            new_items.append(item)

        except sqlite3.IntegrityError:
            pass
        except Exception as exc:
            log.warning(
                "Database insert failed: %s",
                exc
            )

    con.commit()
    con.close()

    log.info(
        "Collected %d new prompt items",
        len(new_items)
    )

    return new_items


# ============================================================
# TELEGRAM DESIGNS
# ============================================================

def design_caption(item, n):
    """
    Professional media-first Telegram caption.
    The channel post is a teaser; the full prompt lives
    behind the website button.
    """

    title = html.escape(
        str(item.get("title", ""))[:220]
    )

    media_type = item.get(
        "media_type",
        "image"
    )

    if media_type == "video":
        media = "🎬 <b>AI VIDEO</b>"
    else:
        media = "🖼️ <b>AI IMAGE</b>"

    model = html.escape(
        str(item.get("model", "Other"))[:80]
    )

    source = html.escape(
        str(item.get("source", "Unknown"))[:100]
    )

    tags_raw = (
        item.get("tags")
        or "AI • Creative • Trending"
    )

    tags = html.escape(
        str(tags_raw)[:180]
    )

    prompt = clean_text(
        str(item.get("prompt", ""))
    )

    # Small preview only. Full prompt is on website.
    preview = prompt[:260]

    if media_type == "video":
        icon = "🎬"
    else:
        icon = "✨"

    templates = [
        (
            f"🔥 <b>AI CREATIVE DROP #{n}</b>\n\n"
            f"{media}\n"
            f"<b>{title}</b>\n\n"
            f"🤖 <b>Model:</b> {model}\n"
            f"🏷️ <b>Tags:</b> {tags}\n\n"
            f"{icon} <b>Prompt Preview</b>\n"
            f"<blockquote>{html.escape(preview)}</blockquote>\n\n"
            f"📡 <b>Source:</b> {source}\n\n"
            f"👇 <b>Get the full prompt below</b>"
        ),
        (
            f"╭━━━ ✦ <b>AI CREATOR FIND</b>\n"
            f"┃ {media}\n"
            f"┃ <b>{title}</b>\n"
            f"╰━━━━━━━━━━━━\n\n"
            f"🧠 <b>{model}</b>\n"
            f"🏷️ {tags}\n\n"
            f"📝 <b>Prompt Preview</b>\n"
            f"<blockquote>{html.escape(preview)}</blockquote>\n\n"
            f"📋 Full prompt • source • details ↓"
        ),
        (
            f"🚀 <b>NEW AI INSPIRATION</b>\n\n"
            f"{media}\n"
            f"<b>{title}</b>\n\n"
            f"🎯 <b>Model:</b> {model}\n"
            f"📝 <b>Preview:</b>\n"
            f"<blockquote>{html.escape(preview)}</blockquote>\n\n"
            f"✨ Explore • Copy • Create"
        ),
        (
            f"💎 <b>CREATOR CARD #{n}</b>\n\n"
            f"{media}\n"
            f"<b>{title}</b>\n\n"
            f"🤖 {model}\n"
            f"🏷️ {tags}\n\n"
            f"<blockquote>{html.escape(preview)}</blockquote>\n\n"
            f"🌐 Full prompt & source below"
        ),
        (
            f"🪄 <b>FRESH AI DISCOVERY</b>\n\n"
            f"{media}\n"
            f"<b>{title}</b>\n\n"
            f"🧩 <b>{model}</b> • {source}\n\n"
            f"📝 <b>Prompt Preview</b>\n"
            f"<blockquote>{html.escape(preview)}</blockquote>\n\n"
            f"⚡ Copy the complete prompt below"
        ),
    ]

    result = templates[
        (n - 1) % len(templates)
    ]

    if len(result) > 1000:
        result = result[:997] + "..."

    return result


# ============================================================
# FIXED INLINE KEYBOARD
# ============================================================

def safe_url(url):
    """
    Telegram inline URL buttons must contain a real
    HTTP/HTTPS URL.
    """
    if not url:
        return ""

    value = str(url).strip()

    if value.startswith("https://") or value.startswith("http://"):
        return value

    return ""


def design_keyboard(item):
    """
    IMPORTANT:
    Only InlineKeyboardButton is used here.
    KeyboardButton must NEVER be used inside
    InlineKeyboardMarkup.
    """

    buttons = []

    try:
        item_id = int(item.get("id", 0))
    except (TypeError, ValueError):
        item_id = 0

    # --------------------------------------------------------
    # FULL PROMPT
    # --------------------------------------------------------

    if item_id > 0:
        article_url = safe_url(
            f"{WEBSITE_URL}/?id={item_id}"
        )

        if article_url:
            buttons.append([
                InlineKeyboardButton(
                    text="📋 COPY / FULL PROMPT",
                    url=article_url,
                )
            ])

    # --------------------------------------------------------
    # WEBSITE
    # --------------------------------------------------------

    website_url = safe_url(
        WEBSITE_URL
    )

    if website_url:
        buttons.append([
            InlineKeyboardButton(
                text="🌐 OPEN PROMPT WEBSITE",
                url=website_url,
            )
        ])

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    source_url = safe_url(
        item.get("source_url", "")
    )

    if source_url:
        buttons.append([
            InlineKeyboardButton(
                text="📡 SOURCE",
                url=source_url,
            )
        ])

    if not buttons:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def _download_video_sync(url, item_id):
    """
    Download a source video using yt-dlp into /tmp.
    Maximum target quality: 720p.
    Returns local file path or empty string.
    """
    if not url:
        return ""

    if yt_dlp is None:
        log.warning(
            "yt-dlp is not installed; video cannot be downloaded."
        )
        return ""

    temp_dir = tempfile.mkdtemp(
        prefix=f"ai_prompt_{item_id}_"
    )

    output_template = os.path.join(
        temp_dir,
        "video.%(ext)s"
    )

    options = {
        "outtmpl": output_template,
        # Prefer a single-file MP4 so Render does not require
        # a separate ffmpeg merge step.
        "format": (
            "b[ext=mp4][height<=720]/"
            "b[height<=720]/"
            "best"
        ),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 15,
        "max_filesize": 49 * 1024 * 1024,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        candidates = []

        for name in os.listdir(temp_dir):
            path = os.path.join(
                temp_dir,
                name
            )

            if os.path.isfile(path):
                candidates.append(path)

        if not candidates:
            return ""

        # Prefer MP4.
        mp4 = [
            p for p in candidates
            if p.lower().endswith(".mp4")
        ]

        return mp4[0] if mp4 else candidates[0]

    except Exception as exc:
        log.warning(
            "Video download failed for item #%s: %s",
            item_id,
            exc
        )

        # Cleanup on failed download.
        try:
            for name in os.listdir(temp_dir):
                os.remove(
                    os.path.join(temp_dir, name)
                )
            os.rmdir(temp_dir)
        except Exception:
            pass

        return ""


async def publish_item(bot, item):
    """
    Publish a professional media-first Telegram post.

    Priority:
      1. Video
      2. Photo
      3. Text fallback

    Local downloaded media is deleted after Telegram accepts it.
    """

    item = dict(item)
    item_id = item.get("id", "?")

    try:
        n = (
            (int(item_id) - 1) % 50
        ) + 1
    except (TypeError, ValueError):
        n = 1

    caption = design_caption(
        item,
        n
    )

    markup = design_keyboard(
        item
    )

    log.info(
        "Publishing item #%s | %s",
        item_id,
        str(item.get("title", ""))[:100]
    )

    # --------------------------------------------------------
    # 1. VIDEO
    # --------------------------------------------------------

    video_url = safe_url(
        item.get("video", "")
    )

    # If RSS/page did not give a direct video URL, try the
    # source page itself with yt-dlp. This supports many
    # video platforms embedded in articles.
    if (
        item.get("media_type") == "video"
        and not video_url
    ):
        video_url = safe_url(
            item.get("source_url", "")
        )

    if (
        item.get("media_type") == "video"
        and video_url
    ):
        local_video = ""

        try:
            local_video = await asyncio.to_thread(
                _download_video_sync,
                video_url,
                item_id
            )

            if local_video and os.path.exists(local_video):

                kwargs = {
                    "chat_id": TG_CHAT_ID,
                    "video": local_video,
                    "caption": caption,
                    "parse_mode": "HTML",
                }

                if markup:
                    kwargs["reply_markup"] = markup

                try:
                    await bot.send_video(
                        **kwargs
                    )

                    log.info(
                        "Published VIDEO item #%s",
                        item_id
                    )

                    return True

                except Exception as exc:
                    log.warning(
                        "Telegram video send failed #%s: %s",
                        item_id,
                        exc
                    )

        finally:
            # Always delete downloaded video.
            if local_video:
                try:
                    temp_dir = os.path.dirname(
                        local_video
                    )

                    if os.path.exists(
                        local_video
                    ):
                        os.remove(
                            local_video
                        )

                    if os.path.isdir(
                        temp_dir
                    ):
                        os.rmdir(
                            temp_dir
                        )

                except Exception as exc:
                    log.debug(
                        "Video cleanup failed: %s",
                        exc
                    )

    # --------------------------------------------------------
    # 2. PHOTO
    # --------------------------------------------------------

    image_url = safe_url(
        item.get("image", "")
    )

    if image_url:
        try:
            kwargs = {
                "chat_id": TG_CHAT_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
            }

            if markup:
                kwargs["reply_markup"] = markup

            await bot.send_photo(
                **kwargs
            )

            log.info(
                "Published PHOTO item #%s",
                item_id
            )

            return True

        except Exception as exc:
            log.warning(
                "Telegram photo send failed #%s: %s",
                item_id,
                exc
            )

    # --------------------------------------------------------
    # 3. TEXT FALLBACK
    # --------------------------------------------------------

    try:
        kwargs = {
            "chat_id": TG_CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
        }

        if markup:
            kwargs["reply_markup"] = markup

        await bot.send_message(
            **kwargs
        )

        log.info(
            "Published TEXT fallback item #%s",
            item_id
        )

        return True

    except Exception as exc:
        log.error(
            "Telegram publish failed #%s: %s",
            item_id,
            exc
        )

        return False


async def publish_new_items(
    bot,
    limit=POSTS_PER_CYCLE
):
    """
    Publish queued records one by one.
    Failed records remain queued.
    """

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM prompts
        WHERE published=0
        ORDER BY score DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    con.close()

    if not rows:
        log.info(
            "No queued prompt items to publish."
        )
        return 0

    count = 0

    for row in rows:
        item = dict(row)

        try:
            success = await publish_item(
                bot,
                item
            )

            if success:
                con = db()

                con.execute(
                    """
                    UPDATE prompts
                    SET published=1
                    WHERE id=?
                    """,
                    (item["id"],),
                )

                con.commit()
                con.close()

                count += 1

                log.info(
                    "Item #%s marked published.",
                    item["id"]
                )
            else:
                log.warning(
                    "Item #%s remains queued.",
                    item["id"]
                )

        except Exception as exc:
            log.exception(
                "Unexpected publish error #%s: %s",
                item.get("id"),
                exc
            )

    log.info(
        "Publishing finished: %s/%s",
        count,
        len(rows)
    )

    return count


# ============================================================
# TELEGRAM ADMIN
# ============================================================

def is_admin(update: Update):
    if not ADMIN_IDS:
        return False

    user = update.effective_user

    if not user:
        return False

    return str(user.id) in ADMIN_IDS


async def alive(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    await update.message.reply_text(
        "🟢 AI Prompt Bot is alive and running."
    )


async def start_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    global AUTOMATION_STOPPED
    AUTOMATION_STOPPED = False

    await update.message.reply_text(
        "▶️ Automation resumed.\n\n"
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


async def stop_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    global AUTOMATION_STOPPED
    AUTOMATION_STOPPED = True

    await update.message.reply_text(
        "⏸ Automation stopped.\n"
        "Use /start to resume."
    )


async def add_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    args = context.args

    if (
        len(args) < 2
        or args[0].lower() != "channel"
    ):
        await update.message.reply_text(
            "Use:\n"
            "/add channel https://example.com/feed.xml"
        )
        return

    url = args[1].strip()

    if not url.startswith(
        ("http://", "https://")
    ):
        await update.message.reply_text(
            "❌ Invalid URL."
        )
        return

    name = (
        urlparse(url).netloc
        or "Custom RSS"
    )

    con = db()

    try:
        cursor = con.execute(
            """
            INSERT INTO feeds
            (name, url, enabled, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (name, url, now()),
        )

        con.commit()

        await update.message.reply_text(
            f"✅ Added source #{cursor.lastrowid}\n"
            f"{name}\n"
            f"{url}"
        )

    except sqlite3.IntegrityError:
        await update.message.reply_text(
            "⚠️ This source already exists."
        )

    finally:
        con.close()


async def remove_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    args = context.args

    if (
        len(args) < 2
        or args[0].lower() != "channel"
    ):
        await update.message.reply_text(
            "Use:\n"
            "/remove channel ID"
        )
        return

    try:
        feed_id = int(args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid ID."
        )
        return

    con = db()

    cursor = con.execute(
        """
        UPDATE feeds
        SET enabled=0
        WHERE id=?
        """,
        (feed_id,),
    )

    con.commit()
    con.close()

    if cursor.rowcount:
        await update.message.reply_text(
            "✅ Source disabled."
        )
    else:
        await update.message.reply_text(
            "❌ Source not found."
        )


async def status_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    con = db()

    feeds = con.execute(
        """
        SELECT COUNT(*) c
        FROM feeds
        WHERE enabled=1
        """
    ).fetchone()["c"]

    prompts = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        """
    ).fetchone()["c"]

    queued = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE published=0
        """
    ).fetchone()["c"]

    published = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE published=1
        """
    ).fetchone()["c"]

    con.close()

    await update.message.reply_text(
        f"📊 Status\n\n"
        f"RSS sources: {feeds}\n"
        f"Prompt records: {prompts}\n"
        f"Queued: {queued}\n"
        f"Published: {published}\n"
        f"Automation: "
        f"{'STOPPED' if AUTOMATION_STOPPED else 'RUNNING'}"
    )


async def sources_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    con = db()

    rows = con.execute(
        """
        SELECT id, name, url
        FROM feeds
        WHERE enabled=1
        ORDER BY id
        """
    ).fetchall()

    con.close()

    if not rows:
        await update.message.reply_text(
            "No active RSS sources."
        )
        return

    text = (
        "📚 Active RSS Sources\n\n"
        + "\n".join(
            f"{r['id']}. {r['name']}"
            for r in rows
        )
    )

    await update.message.reply_text(
        text[:4000]
    )


async def refresh_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    await update.message.reply_text(
        "🔄 Fetching RSS sources..."
    )

    items = await asyncio.to_thread(
        collect
    )

    await update.message.reply_text(
        f"✅ Added {len(items)} "
        f"new prompt candidates."
    )


async def publish_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    await update.message.reply_text(
        "📤 Publishing queued prompts..."
    )

    count = await publish_new_items(
        context.bot,
        limit=POSTS_PER_CYCLE
    )

    await update.message.reply_text(
        f"✅ Published {count} posts."
    )


async def stats_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update):
        return

    con = db()

    total = con.execute(
        "SELECT COUNT(*) c FROM prompts"
    ).fetchone()["c"]

    image = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE media_type='image'
        """
    ).fetchone()["c"]

    video = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE media_type='video'
        """
    ).fetchone()["c"]

    published = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE published=1
        """
    ).fetchone()["c"]

    queued = con.execute(
        """
        SELECT COUNT(*) c
        FROM prompts
        WHERE published=0
        """
    ).fetchone()["c"]

    con.close()

    await update.message.reply_text(
        f"📈 Stats\n\n"
        f"Total: {total}\n"
        f"🖼️ Image: {image}\n"
        f"🎬 Video: {video}\n"
        f"📢 Published: {published}\n"
        f"⏳ Queued: {queued}"
    )


# ============================================================
# WEB
# ============================================================

@app.route("/")
def home():
    root_dir = Path(__file__).parent

    return send_from_directory(
        str(root_dir),
        "index.html"
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ai-trending-prompt"
    })


@app.route("/api/prompts")
def api_prompts():
    q = request.args.get(
        "q",
        ""
    ).strip()

    media = request.args.get(
        "media",
        ""
    ).strip().lower()

    try:
        limit = int(
            request.args.get(
                "limit",
                60
            )
        )
    except ValueError:
        limit = 60

    limit = min(
        max(limit, 1),
        100
    )

    con = db()

    sql = """
        SELECT *
        FROM prompts
        WHERE 1=1
    """

    params = []

    if media in ("image", "video"):
        sql += " AND media_type=?"
        params.append(media)

    if q:
        sql += """
            AND (
                title LIKE ?
                OR prompt LIKE ?
                OR tags LIKE ?
                OR model LIKE ?
                OR description LIKE ?
            )
        """

        like = f"%{q}%"

        params.extend([
            like,
            like,
            like,
            like,
            like,
        ])

    sql += """
        ORDER BY score DESC,
                 created_at DESC
        LIMIT ?
    """

    params.append(limit)

    rows = [
        dict(row)
        for row in con.execute(
            sql,
            params
        ).fetchall()
    ]

    con.close()

    return jsonify({
        "items": rows,
        "count": len(rows)
    })


@app.route("/api/prompt/<int:item_id>")
def api_prompt(item_id):
    con = db()

    row = con.execute(
        """
        SELECT *
        FROM prompts
        WHERE id=?
        """,
        (item_id,),
    ).fetchone()

    con.close()

    if not row:
        return jsonify({
            "error": "not_found"
        }), 404

    return jsonify(
        dict(row)
    )


# ============================================================
# AUTOMATION
# ============================================================

async def automation_loop(bot):
    log.info(
        "Automation loop started. "
        "Interval=%s minutes, Posts=%s",
        POLL_MINUTES,
        POSTS_PER_CYCLE
    )

    while True:
        try:
            if not AUTOMATION_STOPPED:
                await asyncio.to_thread(
                    collect
                )

                await publish_new_items(
                    bot,
                    limit=POSTS_PER_CYCLE
                )
            else:
                log.info(
                    "Automation currently stopped."
                )

        except Exception as exc:
            log.exception(
                "Automation error: %s",
                exc
            )

        await asyncio.sleep(
            POLL_MINUTES * 60
        )


def start_web():
    log.info(
        "Flask keep-alive started on port %s",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


async def post_init(application):
    asyncio.create_task(
        automation_loop(
            application.bot
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    if not BOT_TOKEN:
        raise RuntimeError(
            "TG_BOT_TOKEN is required"
        )

    if not TG_CHAT_ID:
        raise RuntimeError(
            "TG_CHAT_ID is required"
        )

    threading.Thread(
        target=start_web,
        daemon=True
    ).start()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("alive", alive)
    )

    application.add_handler(
        CommandHandler("start", start_cmd)
    )

    application.add_handler(
        CommandHandler("stop", stop_cmd)
    )

    application.add_handler(
        CommandHandler("st", stop_cmd)
    )

    application.add_handler(
        CommandHandler("add", add_cmd)
    )

    application.add_handler(
        CommandHandler("remove", remove_cmd)
    )

    application.add_handler(
        CommandHandler("status", status_cmd)
    )

    application.add_handler(
        CommandHandler("sources", sources_cmd)
    )

    application.add_handler(
        CommandHandler("refresh", refresh_cmd)
    )

    application.add_handler(
        CommandHandler("publish", publish_cmd)
    )

    application.add_handler(
        CommandHandler("stats", stats_cmd)
    )

    log.info(
        "AI Prompt Bot starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
