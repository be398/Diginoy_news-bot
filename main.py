import os
import sqlite3
import hashlib
import asyncio
import aiohttp
import feedparser
import yaml
import re
import json
import logging
from datetime import datetime, timezone
from rapidfuzz import fuzz

# تنظیمات Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# متغیرهای محیطی
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

DB_FILE = "seen_news.db"

# --- مدیریت دیتابیس با SQLite ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
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
    conn.commit()
    conn.close()

def is_hash_or_similar_seen(news_hash, title, limit=100):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # ۱. بررسی دقیق با Hash
    cursor.execute('SELECT 1 FROM sent_news WHERE hash = ?', (news_hash,))
    if cursor.fetchone():
        conn.close()
        return True
    
    # ۲. بررسی شباهت تیتر با RapidFuzz
    cursor.execute('SELECT title FROM sent_news ORDER BY id DESC LIMIT ?', (limit,))
    recent_titles = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()

    for old_title in recent_titles:
        if fuzz.ratio(title.lower(), old_title.lower()) > 85:
            logging.info(f"⚡ خبر تکراری تشخیصی با RapidFuzz: {title}")
            return True
            
    return False

def save_news_to_db(news_hash, link, title, summary, category):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO sent_news (hash, link, title, summary, category)
            VALUES (?, ?, ?, ?, ?)
        ''', (news_hash, link, title, summary, category))
        conn.commit()
    except Exception as e:
        logging.error(f"خطا در ثبت دیتابیس: {e}")
    conn.close()

def generate_hash(title, content):
    raw = f"{title}{content[:200]}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def clean_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()

# --- دریافت ناهمگام (Async RSS Fetcher) ---
async def fetch_feed(session, category, url):
    articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status == 200:
                content = await response.read()
                feed = feedparser.parse(content)
                for entry in feed.entries[:10]:
                    title = getattr(entry, 'title', '')
                    link = getattr(entry, 'link', '')
                    summary = clean_html(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
                    
                    if not title or not link:
                        continue

                    news_hash = generate_hash(title, summary)
                    if not is_hash_or_similar_seen(news_hash, title):
                        articles.append({
                            'hash': news_hash,
                            'title': title,
                            'link': link,
                            'summary_raw': summary,
                            'category': category
                        })
            else:
                logging.warning(f"کد وضعیت {response.status} از source {url}")
    except Exception as e:
        logging.warning(f"خطا در دریافت RSS از {url}: {e}")
    return articles

# --- فراخوانی هوشمند Groq ---
async def process_with_groq(session, article):
    if not GROQ_API_KEY:
        logging.error("⚠️ متغیر GROQ_API_KEY تنظیم نشده است!")
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "You are an expert news editor. Translate and process English news into Farsi.\n"
        "Return ONLY a valid JSON object with keys 'summary' and 'takeaway'. No markdown, no intro.\n"
        "Format:\n"
        "{\n"
        '  "summary": "خلاصه خبر در ۲ جمله کوتاه و روان به فارسی",\n'
        '  "takeaway": "نکته کلیدی یا تاثیر خبر در ۱ جمله کوتاه به فارسی"\n'
        "}"
    )
    
    prompt = f"Title: {article['title']}\nContent: {article['summary_raw'][:400]}"

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
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as response:
            if response.status == 200:
                res_data = await response.json()
                content = res_data['choices'][0]['message']['content']
                parsed = json.loads(content)
                return parsed
            else:
                logging.error(f"خطای Groq API: کد {response.status}")
    except Exception as e:
        logging.error(f"خطا در پردازش Groq: {e}")
    
    return None

# --- ارسال به تلگرام ---
async def send_telegram(session, text):
    if not BOT_TOKEN or not MY_CHAT_ID:
        logging.error("⚠️ BOT_TOKEN یا MY_CHAT_ID تعریف نشده است!")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logging.error(f"خطا در ارسال تلگرام (کد {resp.status}): {body}")
                return False
            return True
    except Exception as e:
        logging.error(f"خطا در ارتباط با تلگرام: {e}")
        return False

# --- تابع اصلی ---
async def main():
    logging.info("🚀 شروع اجرای اسکریپت اخبار...")
    
    # بررسی متغیرها
    if not BOT_TOKEN or not MY_CHAT_ID:
        logging.critical("❌ متغیرهای BOT_TOKEN یا MY_CHAT_ID در GitHub Secrets ست نشده‌اند!")
        return

    init_db()
    
    # بررسی وجود فایل yaml
    if not os.path.exists("feeds.yaml"):
        logging.critical("❌ فایل feeds.yaml پیدا نشد! مطمئن شوید کنار main.py قرار دارد.")
        return

    with open("feeds.yaml", "r", encoding="utf-8") as f:
        feed_categories = yaml.safe_load(f)

    async with aiohttp.ClientSession() as session:
        # ۱. دریافت ناهمگام اخبار
        tasks = []
        for category, urls in feed_categories.items():
            for url in urls:
                tasks.append(fetch_feed(session, category, url))
        
        results = await asyncio.gather(*tasks)
        all_new_articles = [item for sublist in results for item in sublist]

        logging.info(f"📰 تعداد کل اخبار جدید یافت‌شده: {len(all_new_articles)}")

        if not all_new_articles:
            logging.info("خبر جدیدی یافت نشد.")
            await send_telegram(session, "❌ **در این دور خبر جدیدی یافت نشد.**")
            return

        # ۲. پردازش و خلاصه‌سازی با Groq
        processed_by_category = {}

        for article in all_new_articles[:10]: # حداکثر ۱۰ خبر در هر اجرا
            logging.info(f"در حال پردازش: {article['title']}")
            ai_res = await process_with_groq(session, article)
            
            if ai_res:
                summary = ai_res.get("summary", "")
                takeaway = ai_res.get("takeaway", "")
                cat = article['category']

                msg_chunk = (
                    f"📰 **{article['title']}**\n"
                    f"📌 **خلاصه:** {summary}\n"
                    f"🎯 **نکته کلیدی:** {takeaway}\n"
                    f"🔗 [لینک منبع]({article['link']})\n"
                    "------------------------------------\n"
                )

                if cat not in processed_by_category:
                    processed_by_category[cat] = []
                processed_by_category[cat].append(msg_chunk)

                save_news_to_db(article['hash'], article['link'], article['title'], summary, cat)

        # ۳. ارسال اخبار به تلگرام
        if not processed_by_category:
            logging.warning("اخبار دریافت شدند اما هیچ‌کدام توسط Groq خلاصه نشدند (احتمالاً خطا در API Key).")
            return

        for category, messages in processed_by_category.items():
            header = f"📁 **اخبار جدید بخش: {category}**\n\n"
            full_msg = header + "\n".join(messages)
            
            if len(full_msg) > 4000:
                chunks = [full_msg[i:i+4000] for i in range(0, len(full_msg), 4000)]
                for chunk in chunks:
                    await send_telegram(session, chunk)
            else:
                await send_telegram(session, full_msg)

        logging.info("✅ ارسال پیام‌ها به تلگرام با موفقیت انجام شد.")

if __name__ == "__main__":
    asyncio.run(main())
