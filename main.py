import os
import sqlite3
import requests
import feedparser
import re
from datetime import datetime, timezone

# دریافت متغیرهای محیطی از GitHub Secrets
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# لیست کامل ۳۹ منبع
RSS_FEEDS = [
    # --- فید کانال‌های تلگرامی ---
    'https://rsshub.app/telegram/channel/Khabare_vije',
    'https://rsshub.app/telegram/channel/khabarestan_farsii',
    
    # --- لیست ۳۷ سایت خبری، تکنولوژی، ورزشی، سرگرمی و زرد ---
    'https://www.androidauthority.com/feed/',
    'https://digiato.com/feed',
    'https://www.zoomit.ir/feed/',
    'https://petapixel.com/feed/',
    'https://www.iflscience.com/rss.xml',
    'https://www.cryptopolitan.com/feed/',
    'https://interestingengineering.com/feed',
    'https://www.phonearena.com/feed',
    'https://wccftech.com/feed/',
    'https://www.ladbible.com/rss',
    'https://www.petphotographyawards.com/feed/',
    'https://variety.com/feed/',
    'https://www.newsweek.com/rss',
    'https://nationalinterest.org/rss.xml',
    'https://www.engadget.com/rss.xml',
    'https://tech.yahoo.com/rss',
    'https://arstechnica.com/feed/',
    'https://gulfnews.com/rss',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rooziato.com/feed/',
    'https://www.kojaro.com/feed/',
    'https://www.bartarinha.ir/fa/rss/allnews',
    'https://www.yahoo.com/news/rss/entertainment',
    'https://radaronline.com/r/feed/',
    'https://www.boredpanda.com/feed/',
    'https://www.theguardian.com/society/rss',
    'https://people.com/feed/',
    'https://news.northwestern.edu/rss',
    'https://pagesix.com/feed/',
    'https://www.nature.com/nature.rss',
    'https://www.eonline.com/rss',
    'https://www.justjared.com/feed/',
    'https://www.thesun.co.uk/feed/',
    'https://www.dailymail.co.uk/tvshowbiz/index.rss',
    'https://pubity.com/feed/',
    'https://www.wired.com/feed/rss',
    'https://www.buzzfeed.com/news.xml'
]

DB_FILE = "seen_news.db"

# --- بخش مدیریت دیتابیس SQLite ---

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            link TEXT PRIMARY KEY,
            title TEXT,
            summary TEXT
        )
    ''')
    cursor.execute("PRAGMA table_info(sent_news)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'title' not in columns:
        cursor.execute("ALTER TABLE sent_news ADD COLUMN title TEXT")
    if 'summary' not in columns:
        cursor.execute("ALTER TABLE sent_news ADD COLUMN summary TEXT")
        
    conn.commit()
    conn.close()

def is_link_sent(link):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_news WHERE link = ?', (link,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_link_title_and_summary(link, title="", summary=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT OR REPLACE INTO sent_news (link, title, summary) VALUES (?, ?, ?)', (link, title, summary))
        conn.commit()
    except Exception as e:
        print(f"خطا در ثبت دیتابیس: {e}")
    conn.close()

def get_recent_sent_stories(limit=50):
    """دریافت ۵۰ خبر اخیر برای مقایسه عمیق متنی"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT title, summary FROM sent_news WHERE title IS NOT NULL AND title != "" ORDER BY ROWID DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [f"Title: {r[0]} | Info: {r[1][:150] if r[1] else ''}" for r in rows]
    except Exception as e:
        print(f"خطا در دریافت تاریخچه دیتابیس: {e}")
        conn.close()
        return []

# --- بخش ارتباط با تلگرام و ابزارها ---

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال پیام تلگرام: {e}")

def clean_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()

def is_published_recently(entry):
    """بررسی انتشار در ۴۸ ساعت گذشته برای پوشش اختلاف زمان سرورها"""
    published_parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if not published_parsed:
        return True

    today_utc = datetime.now(timezone.utc).date()
    entry_date = datetime(*published_parsed[:6], tzinfo=timezone.utc).date()
    
    # اجازه عبور به اخبار امروز و دیروز
    return (today_utc - entry_date).days <= 1

# --- بخش ارتباط با هوش مصنوعی Groq ---

def call_groq_ai(prompt_text):
    if not GROQ_API_KEY:
        print("⚠️ متغیر GROQ_API_KEY تعریف نشده است!")
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.0
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            return data['choices'][0]['message']['content'].strip()
        return None
    except Exception as e:
        print(f"خطا در ارتباط با Groq API: {e}")
        return None

def is_duplicate_story_ai(new_title, new_raw_summary, history_list):
    """سنجش هوشمند برای عدم ارسال خبر تکراری"""
    if not history_list:
        return False

    prompt = (
        f"NEW ARTICLE TO CHECK:\n"
        f"Title: {new_title}\n"
        f"Content/Summary: {new_raw_summary[:300]}\n\n"
        f"PREVIOUSLY SENT ARTICLES HISTORY (LAST 50):\n" + 
        "\n".join([f"- {item}" for item in history_list]) + 
        "\n\nYOUR INSTRUCTION:\n"
        "Compare the 'NEW ARTICLE' with the 'PREVIOUSLY SENT ARTICLES HISTORY'.\n"
        "Is the new article reporting the EXACT SAME specific news story, event, match result, or statement as one in the history?\n\n"
        "CRITICAL RULE:\n"
        "- Two articles can be about the SAME subject/person/team/company, but if they discuss DIFFERENT events, matches, or news, they are NOT duplicates -> Answer NO.\n"
        "- ONLY answer YES if both articles cover the EXACT SAME news event.\n\n"
        "Answer ONLY with 'YES' or 'NO'."
    )

    response = call_groq_ai(prompt)
    if response and "YES" in response.upper():
        return True
    return False

def summarize_news_with_ai(title, summary_text):
    """خلاصه‌سازی تمام مطالب بدون هیچ فیلتر موضوعی"""
    content = clean_html(f"Title: {title}\nSummary: {summary_text}")
    if not content:
        return None

    prompt = (
        f"متن زیر را بخوانید و یک خلاصه روان، جذاب و دقیق ۱ یا ۲ جمله‌ای به زبان فارسی بنویسید:\n\n{content}\n\n"
        "پاسخ شما فقط و فقط خلاصه فارسی باشد."
    )

    response = call_groq_ai(prompt)
    return response if response else clean_html(summary_text)[:200]

# --- بدنه اصلی اسکریپت ---

def main():
    init_db()
    print("در حال دریافت تمام ورودی‌های ۳۹ منبع بدون فیلتر...")

    raw_articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # ۱. جمع‌آوری اخبار از ۳۹ منبع (بررسی ۳۰ آیتم اخیر از هر فید)
    for feed_url in RSS_FEEDS:
        is_telegram = ('telegram' in feed_url or 'rsshub' in feed_url)
        try:
            if is_telegram:
                response = requests.get(feed_url, headers=headers, timeout=15)
                feed = feedparser.parse(response.content)
            else:
                feed = feedparser.parse(feed_url)

            for entry in feed.entries[:30]:
                link = entry.link

                if not is_link_sent(link) and is_published_recently(entry):
                    raw_articles.append(entry)
        except Exception as e:
            print(f"خطا در دریافت RSS از {feed_url}: {e}")

    # ۲. حذف تکراری‌ها و خلاصه‌سازی
    recent_stories_history = get_recent_sent_stories(50)
    processed_news = []

    for entry in raw_articles:
        raw_summary = clean_html(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))

        # الف) چک تکراری بودن خبر
        if is_duplicate_story_ai(entry.title, raw_summary, recent_stories_history):
            save_link_title_and_summary(entry.link, entry.title, raw_summary)
            print(f"❌ خبر تکراری رد شد: {entry.title}")
            continue

        # ب) خلاصه‌سازی خبر به فارسی
        fa_summary = summarize_news_with_ai(entry.title, raw_summary)

        processed_news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': fa_summary
        })
        
        recent_stories_history.append(f"Title: {entry.title} | Info: {raw_summary[:150]}")

    # ۳. ارسال همه اخبار به تلگرام
    new_messages_sent = 0
    for news in processed_news:
        msg = f"📰 **{news['title']}**\n\n"
        if news['summary']:
            msg += f"📝 **خلاصه:** {news['summary']}\n\n"
        msg += f"🔗 {news['link']}"

        send_telegram_message(msg)
        new_messages_sent += 1
        save_link_title_and_summary(news['link'], news['title'], news['summary'])
        print(f"✅ ارسال شد: {news['title']}")

    if new_messages_sent > 0:
        send_telegram_message("🏁 **پایان این دور از اخبار**")
    else:
        send_telegram_message("❌ **خبر جدیدی یافت نشد**")

if __name__ == '__main__':
    main()
