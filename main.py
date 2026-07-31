import os
import re
import html
import hashlib
import asyncio
import logging
from typing import List, Dict, Any

import yaml
import aiohttp
import aiosqlite
import feedparser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
DB_FILE = "seen_news.db"

CONCURRENCY_SEMAPHORE = asyncio.Semaphore(5)

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    return " ".join(text.split())

def generate_hash(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}_{link.strip()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

async def init_db():
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sent_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE,
                link TEXT,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.commit()

async def is_seen(news_hash: str) -> bool:
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute('SELECT 1 FROM sent_news WHERE hash = ?', (news_hash,)) as cursor:
                return await cursor.fetchone() is not None
    except Exception as e:
        logging.error(f"خطا در دیتابیس: {e}")
        return False

async def save_to_db(news_hash: str, link: str, title: str):
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                'INSERT OR IGNORE INTO sent_news (hash, link, title) VALUES (?, ?, ?)',
                (news_hash, link, title)
            )
            await conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت دیتابیس: {e}")

def parse_feed_bytes(content: bytes) -> List[Dict[str, str]]:
    feed = feedparser.parse(content)
    extracted = []
    for entry in feed.entries[:10]:
        title = getattr(entry, 'title', '').strip()
        link = getattr(entry, 'link', '').strip()
        summary = clean_html(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
        if title and link:
            extracted.append({'title': title, 'link': link, 'summary': summary})
    return extracted

async def fetch_feed(session: aiohttp.ClientSession, url: str) -> List[Dict[str, Any]]:
    articles = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, text/html;q=0.9',
    }
    async with CONCURRENCY_SEMAPHORE:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status == 200:
                    content = await response.read()
                    loop = asyncio.get_running_loop()
                    entries = await loop.run_in_executor(None, parse_feed_bytes, content)

                    for entry in entries:
                        news_hash = generate_hash(entry['title'], entry['link'])
                        if not await is_seen(news_hash):
                            articles.append({
                                'hash': news_hash,
                                'title': entry['title'],
                                'link': entry['link'],
                                'summary': entry['summary']
                            })
        except Exception as e:
            logging.warning(f"خطا در دریافت RSS از {url}: {e}")
    return articles

async def send_telegram(session: aiohttp.ClientSession, text: str) -> bool:
    if not BOT_TOKEN or not MY_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False  # جهت نمایش پیش‌نمایش عکس و کارت مطلب دقیقاً مثل تصویر نمونه
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return resp.status == 200
    except Exception as e:
        logging.error(f"خطا در ارسال تلگرام: {e}")
        return False

async def main():
    logging.info("🚀 شروع استخراج مستقیم اخبار بدون هوش مصنوعی...")
    await init_db()

    if not os.path.exists("feeds.yaml"):
        logging.critical("❌ فایل feeds.yaml پیدا نشد!")
        return

    with open("feeds.yaml", "r", encoding="utf-8") as f:
        feed_categories = yaml.safe_load(f)

    async with aiohttp.ClientSession() as session:
        all_new_articles = []
        
        for category, urls in feed_categories.items():
            batch_size = 5
            for i in range(0, len(urls), batch_size):
                batch_urls = urls[i:i + batch_size]
                tasks = [fetch_feed(session, url) for url in batch_urls]
                results = await asyncio.gather(*tasks)
                for sublist in results:
                    all_new_articles.extend(sublist)
                await asyncio.sleep(0.5)

        logging.info(f"📰 تعداد اخبار جدید روز: {len(all_new_articles)}")

        if not all_new_articles:
            logging.info("خبر جدیدی در این دور یافت نشد.")
            return

        for article in all_new_articles:
            title_esc = html.escape(article['title'])
            summary_esc = html.escape(article['summary'][:250]) if article['summary'] else title_esc

            # دقیقا بر اساس فرمت تصویر نمونه ارسال می‌شود
            msg = (
                f"📰 <b>{title_esc}</b>\n\n"
                f"📝 <b>خلاصه: خبر جدید در مورد:</b> {summary_esc}\n\n"
                f"🔗 {article['link']}"
            )

            success = await send_telegram(session, msg)
            if success:
                await save_to_db(article['hash'], article['link'], article['title'])
                await asyncio.sleep(1.0) # وقفه کوتاه جهت ارسال تمیز

        logging.info("✅ تمام اخبار روز با موفقیت ارسال شدند.")

if __name__ == "__main__":
    asyncio.run(main())
