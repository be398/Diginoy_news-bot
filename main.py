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

# لیست کامل منابع RSS اخبار تکنولوژی، عمومی و کانال‌های تلگرام (۳۹ منبع)
RSS_FEEDS = [
    # --- فید کانال‌های تلگرامی ---
    'https://rsshub.app/telegram/channel/Khabare_vije',
    'https://rsshub.app/telegram/channel/khabarestan_farsii',
    
    # --- لیست کامل تمام ۳۷ سایت منبع شما ---
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
            title TEXT
        )
    ''')
    
    cursor.execute("PRAGMA table_info(sent_news)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'title' not in columns:
        cursor.execute("ALTER TABLE sent_news ADD COLUMN title TEXT")
        
    conn.commit()
    conn.close()

def is_link_sent(link):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_news WHERE link = ?', (link,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_link_and_title(link, title=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT OR REPLACE INTO sent_news (link, title) VALUES (?, ?)', (link, title))
        conn.commit()
    except Exception as e:
        print(f"خطا در ثبت دیتابیس: {e}")
    conn.close()

def get_recent_sent_titles(limit=15):
    """دریافت آخرین تیترهای ارسال شده برای سنجش تکراری‌ها"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT title FROM sent_news WHERE title IS NOT NULL AND title != "" ORDER BY ROWID DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"خطا در دریافت تیترهای اخیر: {e}")
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

def is_published_today(entry):
    published_parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if not published_parsed:
        return True

    today_utc = datetime.now(timezone.utc).date()
    entry_date = datetime(*published_parsed[:6], tzinfo=timezone.utc).date()
    return entry_date == today_utc

# --- بخش ارتباط با هوش مصنوعی Groq ---

def call_groq_ai(prompt_text):
    """فراخوانی هوش مصنوعی Llama 3.3 از طریق Groq API"""
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
        "messages": [
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.2
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            return data['choices'][0]['message']['content'].strip()
        else:
            print(f"Groq API Error Status: {res.status_code} -> {res.text}")
            return None
    except Exception as e:
        print(f"خطا در ارتباط با Groq API: {e}")
        return None

def analyze_and_summarize_tech_news_with_ai(title, summary_text):
    """تحلیل ماهیت خبر و فیلتر اخبار سیاسی، نظامی، مذهبی و چت‌های روزمره"""
    content = clean_html(f"Title: {title}\nSummary: {summary_text}")
    if not content:
        return None

    prompt = (
        f"این متن یک پست از یک کانال خبری یا سایت است:\n\n{content}\n\n"
        "وظایف شما:\n"
        "۱. آیا این پست یک «خبر واقعی در حوزه تکنولوژی، هوش مصنوعی، علوم، سخت‌افزار، گجت‌ها یا اخبار عام‌المنفعه و کاربردی» است؟\n"
        "پاسخ باید قطعاً NO باشد اگر پست شامل هریک از موارد زیر باشد:\n"
        "  - اخبار سیاسی، نظامی، جنگی، بین‌الملل، یا امور مربوط به دولت‌ها و سیاستمداران.\n"
        "  - مطالب مذهبی، مناسبت‌های تقویمی، ادعیه، احادیث یا تبریک/تسلیت.\n"
        "  - پست‌های روزمره و چت (مثل: صبح بخیر، شب خوش، تقویم امروز، نرخ ارز/طلا، متن ادبی).\n"
        "  - اخبار ویدیوگیم، کنسول‌ها، فیلم/سریال یا سلبریتی‌ها.\n"
        "  - تبلیغات، راهنمای خرید یا مقالات نظر شخصی.\n"
        "۲. اگر پست یک خبر واقعیِ مفید و غیرسیاسی/غیرنظامی است، یک خلاصه کوتاه ۱ یا ۲ جمله‌ای به زبان فارسی روان، جذاب و دقیق بنویسید.\n\n"
        "فرمت پاسخ حتماً و دقیقاً به این شکل باشد:\n"
        "IS_NEWS: [YES یا NO]\n"
        "SUMMARY: [خلاصه فارسی خبر]"
    )

    response = call_groq_ai(prompt)
    if not response:
        return None

    if "IS_NEWS: NO" in response:
        return None

    match = re.search(r'SUMMARY:\s*(.*)', response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return response

def is_duplicate_tech_story_ai(new_title, recent_titles):
    """بررسی درک مفاهیمی هوش مصنوعی برای جلوگیری از ارسال اخبار تکراری"""
    if not recent_titles:
        return False

    titles_to_check = recent_titles[-15:]

    prompt = (
        f"خبر جدید: {new_title}\n\n"
        f"لیست آخرین اخبار ارسال‌شده:\n" + 
        "\n".join([f"- {t}" for t in titles_to_check]) + 
        "\n\nوظیفه شما:\n"
        "آیا این خبر جدید درباره همان «رویداد، اتفاق یا سوژه واحدی» است که در یکی از اخبار بالا وجود دارد؟ "
        "(حتی اگر تیترها یا رسانه‌ها متفاوت باشند، اما اصل رویداد یکی باشد پاسخ YES است).\n\n"
        "فقط و فقط کلمه YES یا NO را پاسخ دهید."
    )

    response = call_groq_ai(prompt)
    if response and "YES" in response.upper():
        return True
    return False

# --- بدنه اصلی اسکریپت ---

def main():
    init_db()
    print("در حال جمع‌آوری اخبار از ۳۹ منبع و تحلیل هوشمند با Groq...")

    # ۱. جمع‌آوری اخبار امروز از تمام منابع و کانال‌ها
    raw_articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                link = entry.link

                if not is_link_sent(link) and is_published_today(entry):
                    raw_articles.append(entry)
        except Exception as e:
            print(f"خطا در دریافت RSS از {feed_url}: {e}")

    # ۲. پردازش، فیلتر دقیق و خلاصه‌سازی توسط AI
    recent_sent_titles = get_recent_sent_titles(15)
    processed_titles = recent_sent_titles.copy()
    processed_news = []

    for entry in raw_articles:
        raw_summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        
        # الف) سنجش ماهیت خبر و خلاصه‌سازی فارسی (رد مطالب سیاسی/نظامی/احوالپرسی)
        fa_summary = analyze_and_summarize_tech_news_with_ai(entry.title, raw_summary)
        
        if fa_summary is None:
            save_link_and_title(entry.link, entry.title)
            print(f"خبر غیرمرتبط/سیاسی/نظامی/چت رد شد: {entry.title}")
            continue

        # ب) جلوگیری از ارسال موضوع تکراری
        if is_duplicate_tech_story_ai(entry.title, processed_titles):
            save_link_and_title(entry.link, entry.title)
            print(f"❌ خبر تکراری شناسایی و رد شد: {entry.title}")
            continue

        processed_news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': fa_summary
        })
        processed_titles.append(entry.title)

    # ۳. ارسال به تلگرام
    new_messages_sent = 0
    for news in processed_news:
        msg = f"📰 **{news['title']}**\n\n"
        if news['summary']:
            msg += f"📝 **خلاصه:** {news['summary']}\n\n"
        msg += f"🔗 {news['link']}"

        send_telegram_message(msg)
        new_messages_sent += 1
        save_link_and_title(news['link'], news['title'])
        print(f"✅ خبر ارسال شد: {news['title']}")

    if new_messages_sent > 0:
        send_telegram_message("🏁 **پایان این دور از اخبار**")
    else:
        send_telegram_message("❌ **چیزی یافت نشد**")

if __name__ == '__main__':
    main()
