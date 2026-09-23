# AI Trending Prompt Platform

100 RSS sources -> AI filter -> image/video prompt records -> duplicate protection -> trending score -> SQLite -> Telegram -> 50 rotating designs -> prompt website.

## Environment variables

TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=@your_channel_or_chat_id
ADMIN_IDS=123456789
WEBSITE_URL=https://your-netlify-site.netlify.app
POLL_MINUTES=30
POSTS_PER_CYCLE=5

## Telegram commands

/alive
/start
/stop
/st
/add channel https://example.com/feed.xml
/remove channel 12
/status
/sources
/refresh
/publish
/stats

## Important

The included 100 RSS entries are a broad starter set. Feeds can change or disappear, so the application skips failed feeds rather than stopping the whole pipeline. Verify each source's terms and permitted reuse before republishing third-party media or text.
