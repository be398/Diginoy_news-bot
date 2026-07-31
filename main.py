import os
import re
import html
import json
import hashlib
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

import yaml
import aiohttp
import aiosqlite
import feedparser
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

DB_FILE = "seen_news.db"
COMMON_STOPWORDS = {'update', 'report', 'review', 'vs', 'how', 'to'}

CONCURRENCY_SEMAPHORE = asyncio.Semaphore(5)

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
        await conn.commit()

async def is_hash_or_similar_seen(news_hash: str, title: str, limit: int = 40) -> bool:
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute('SELECT 1 FROM sent_news WHERE hash = ?', (news_hash,)) as cursor:
                if await cursor.fetchone():
                    return True

            async with conn.execute('SELECT title FROM sent_news WHERE title IS NOT NULL ORDER BY id DESC LIMIT ?', (limit,)) as cursor:
                recent_rows = await cursor.fetchall()
                recent_titles = [row[0] for row in recent_rows if row[0]]

            clean_new_title = normalize_title_for_fuzzy(title)
            if len(clean_new_title) < 5:
                return False

            loop = asyncio.get_running_loop()
            for old_title in recent_titles:
                clean_old_title = normalize_title_for_fuzzy(old_title)
                similarity = await loop.run_in_executor(None, fuzz.token_sort_ratio, clean_new_title, clean_old_title)
                # کاهش حساسیت به ۹۵٪ جهت جلوگیری از حذف اخبار مهم
                if similarity > 95:
                    logging.info(f"⚡ خبر تکراری شناسایی شد: {title}")
                    return True
    except Exception as e:
        logging.error(f"خطا در دیتابیس: {e}")
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

async def fetch_feed(session: aiohttp.ClientSession, category: str, url: str) -> List[Dict[str, Any]]:
    articles = []
    # هدر کامل‌تر برای دور زدن فیلتر سایت‌ها
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
                        if not await is_hash_or_similar_seen(news_hash, entry['title']):
                            articles.append({
                                'hash': news_hash,
                                'title': entry['title'],
                                'link': entry['link'],
                                'summary_raw': entry['summary'],
                                'category': category
                            })
                else:
                    logging.warning(f"عدم دریافت پاسخ (کد {response.status}) از: {url}")
        except Exception as e:
            logging.warning(f"خطا در دریافت RSS از {url}: {e}")
    return articles

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

async def main():
    logging.info("🚀 شروع اجرای اسکریپت با تمرکز ۵ لینکی...")
    await init_db()

    if not os.path.exists("feeds.yaml"):
        logging.critical("❌ فایل feeds.yaml پیدا نشد!")
        return

    with open("feeds.yaml", "r", encoding="utf-8") as f:
        feed_categories = yaml.safe_load(f)

    async with aiohttp.ClientSession() as session:
        all_new_articles = []
        
        # تقسیم دقیق لینک‌ها به دسته‌های ۵‌تایی متمرکز
        for category, urls in feed_categories.items():
            batch_size = 5
            for i in range(0, len(urls), batch_size):
                batch_urls = urls[i:i + batch_size]
                tasks = [fetch_feed(session, category, url) for url in batch_urls]
                results = await asyncio.gather(*tasks)
                for sublist in results:
                    all_new_articles.extend(sublist)
                await asyncio.sleep(1.0)  # تنفس ۱ ثانیه‌ای بین دسته‌ها

        logging.info(f"📰 تعداد اخبار جدید استخراج‌شده: {len(all_new_articles)}")

        if not all_new_articles:
            logging.info("خبر جدیدی یافت نشد.")
            await send_telegram(session, "❌ <b>در این دور خبر جدیدی یافت نشد.</b>")
            return

        processed_by_category = {}

        # پردازش تمام اخبار یافت‌شده (حذف برش ۱۵ تایی)
        for article in all_new_articles:
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
                await asyncio.sleep(0.5)

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
                    await asyncio.sleep(0.5)
            else:
                await send_telegram(session, full_msg)
                await asyncio.sleep(0.5)

        logging.info("✅ تمام اخبار با موفقیت ارسال شدند.")

if __name__ == "__main__":
    asyncio.run(main())
