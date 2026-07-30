import os
import re
import html
import json
import hashlib
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Set

import yaml
import aiohttp
import aiosqlite
import feedparser
from rapidfuzz import fuzz

# تنظیمات Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

DB_FILE = "seen_news.db"
COMMON_STOPWORDS = {'android', 'google', 'samsung', 'apple', 'update', 'new', 'report', 'review', 'vs', 'best', 'how', 'to'}

# کنترل هم‌زمانی برای جلوگیری از ارور ۵۰۳ پروکسی
CONCURRENCY_SEMAPHORE = asyncio.Semaphore(3)

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def normalize_title_for_fuzzy(title: str) -> str:
    text = title.lower()
    text = re.sub(r'[^\w\s]', '', text)
    words = [w for w in text.split() if w not in COMMON_STOPWORDS]
    return " ".join(words)

def generate_hash(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}_{link.strip()}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

# --- مدیریت دیتابیس ناهمگام (aiosqlite) ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sent_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE,
                link TEXT,
                title TEXT,
                summary TEXT,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        async with conn.execute("PRAGMA table_info(sent_news)") as cursor:
            columns = [column[1] for column in await cursor.fetchall()]
            if 'hash' not in columns:
                try:
                    await conn.execute("ALTER TABLE sent_news ADD COLUMN hash TEXT")
                except Exception:
                    pass
        await conn.commit()

async def is_hash_or_similar_seen(news_hash: str, title: str, limit: int = 60) -> bool:
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # ۱. چک با Hash دقیق
            async with conn.execute('SELECT 1 FROM sent_news WHERE hash = ?', (news_hash,)) as cursor:
                if await cursor.fetchone():
                    return True

            # ۲. چک شباهت با تیتر نرمال‌شده
            async with conn.execute('SELECT title FROM sent_news WHERE title IS NOT NULL ORDER BY id DESC LIMIT ?', (limit,)) as cursor:
                recent_rows = await cursor.fetchall()
                recent_titles = [row[0] for row in recent_rows if row[0]]

            clean_new_title = normalize_title_for_fuzzy(title)
            if len(clean_new_title) < 5:
                return False

            loop = asyncio.get_running_loop()
            for old_title in recent_titles:
                clean_old_title = normalize_title_for_fuzzy(old_title)
                # اجرای مقایسه رشته‌ای سنگین در Thread
                similarity = await loop.run_in_executor(None, fuzz.token_sort_ratio, clean_new_title, clean_old_title)
                if similarity > 90:
                    logging.info(f"⚡ خبر تکراری رد شد: {title}")
                    return True
    except Exception as e:
        logging.error(f"خطا در چک دیتابیس: {e}")

    return False

async def save_news_to_db(news_hash: str, link: str, title: str, summary: str, category: str):
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                'INSERT OR IGNORE INTO sent_news (hash, link, title, summary, category) VALUES (?, ?, ?, ?, ?)',
                (news_hash, link, title, summary, category)
            )
            await conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت دیتابیس: {e}")

# --- پارس کردن فید در Thread Pool ---
def parse_feed_bytes(content: bytes) -> List[Dict[str, str]]:
    feed = feedparser.parse(content)
    extracted = []
    for entry in feed.entries[:5]:
        title = getattr(entry, 'title', '').strip()
        link = getattr(entry, 'link', '').strip()
        summary = clean_html(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
        if title and link:
            extracted.append({'title': title, 'link': link, 'summary': summary})
    return extracted

# --- دریافت ناهمگام RSS ---
async def fetch_feed(session: aiohttp.ClientSession, category: str, url: str) -> List[Dict[str, Any]]:
    articles = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    async with CONCURRENCY_SEMAPHORE:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content = await response.read()
                    loop = asyncio.get_running_loop()
                    entries = await loop.run_in_executor(None, parse_feed_bytes, content)

                    for entry in entries:
                        news_hash = generate_hash(entry['title'], entry['link'])
                        if not await is_hash_or_similar_seen(news_hash, entry['title']):
                            articles.append({
                                'hash': news_hash,
                                'title': entry['title'],
                                'link': entry['link'],
                                'summary_raw': entry['summary'],
                                'category': category
                            })
                else:
                    logging.warning(f"کد وضعیت {response.status} از source: {url}")
        except Exception as e:
            logging.warning(f"خطا در دریافت RSS از {url}: {e}")
    return articles

# --- فراخوانی Groq API ---
async def process_with_groq(session: aiohttp.ClientSession, article: Dict[str, Any]) -> Dict[str, str]:
    if not GROQ_API_KEY:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    system_instruction = (
        "You are a professional editor. Translate and summarize this article into fluent Farsi.\n"
        "Return ONLY a valid JSON object with keys 'summary' and 'takeaway'.\n"
        "Format:\n"
        "{\n"
        '  "summary": "خلاصه خبر در ۲ جمله روان به فارسی",\n'
        '  "takeaway": "نکته کلیدی در ۱ جمله به فارسی"\n'
        "}"
    )

    prompt = f"Title: {article['title']}\nContent: {article['summary_raw'][:300]}"

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status == 200:
                res_data = await response.json()
                content = res_data['choices'][0]['message']['content']
                return json.loads(content)
    except Exception as e:
        logging.error(f"خطا در پردازش Groq: {e}")

    return None

# --- ارسال به تلگرام ---
async def send_telegram(session: aiohttp.ClientSession, text: str) -> bool:
    if not BOT_TOKEN or not MY_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return resp.status == 200
    except Exception as e:
        logging.error(f"خطا در ارسال تلگرام: {e}")
        return False

# --- تابع اصلی ---
async def main():
    logging.info("🚀 شروع اجرای اسکریپت جامع اخبار...")
    await init_db()

    if not os.path.exists("feeds.yaml"):
        logging.critical("❌ فایل feeds.yaml پیدا نشد!")
        return

    with open("feeds.yaml", "r", encoding="utf-8") as f:
        feed_categories = yaml.safe_load(f)

    async with aiohttp.ClientSession() as session:
        all_new_articles = []
        
        # پردازش دسته‌ای ۳تایی لینک‌ها برای جلوگیر از فشار شبکه
        for category, urls in feed_categories.items():
            batch_size = 3
            for i in range(0, len(urls), batch_size):
                batch_urls = urls[i:i + batch_size]
                tasks = [fetch_feed(session, category, url) for url in batch_urls]
                results = await asyncio.gather(*tasks)
                for sublist in results:
                    all_new_articles.extend(sublist)
                await asyncio.sleep(0.3)

        logging.info(f"📰 تعداد اخبار جدید استخراج‌شده از منابع مختلف: {len(all_new_articles)}")

        if not all_new_articles:
            logging.info("خبر جدیدی یافت نشد.")
            await send_telegram(session, "❌ <b>در این دور خبر جدیدی یافت نشد.</b>")
            return

        processed_by_category = {}

        for article in all_new_articles[:15]:
            ai_res = await process_with_groq(session, article)

            if ai_res:
                summary = html.escape(ai_res.get("summary", ""))
                takeaway = html.escape(ai_res.get("takeaway", ""))
                title = html.escape(article['title'])
                link = html.escape(article['link'])
                cat = article['category']

                msg_chunk = (
                    f"📰 <b>{title}</b>\n"
                    f"📌 <b>خلاصه:</b> {summary}\n"
                    f"🎯 <b>نکته کلیدی:</b> {takeaway}\n"
                    f"🔗 <a href='{link}'>لینک منبع</a>\n"
                    "------------------------------------\n"
                )

                if cat not in processed_by_category:
                    processed_by_category[cat] = []
                processed_by_category[cat].append(msg_chunk)

                await save_news_to_db(article['hash'], article['link'], article['title'], summary, cat)

        if not processed_by_category:
            await send_telegram(session, "❌ <b>در این دور خبر جدیدی یافت نشد.</b>")
            return

        for category, messages in processed_by_category.items():
            header = f"📁 <b>اخبار جدید بخش: {html.escape(category)}</b>\n\n"
            full_msg = header + "\n".join(messages)

            if len(full_msg) > 4000:
                chunks = [full_msg[i:i+4000] for i in range(0, len(full_msg), 4000)]
                for chunk in chunks:
                    await send_telegram(session, chunk)
                    await asyncio.sleep(0.3)
            else:
                await send_telegram(session, full_msg)
                await asyncio.sleep(0.3)

        logging.info("✅ اخبار با موفقیت ارسال شدند.")

if __name__ == "__main__":
    asyncio.run(main())
