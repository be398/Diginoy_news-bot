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
GH_PAT = os.environ.get("GH_PAT")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")

# لیست کامل ۳۹ منبع خبر (تلگرام + ۳۷ سایت)
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

def is_published_today(entry):
    published_parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if not published_parsed:
        return True

    today_utc = datetime.now(timezone.utc).date()
    entry_date = datetime(*published_parsed[:6], tzinfo=timezone.utc).date()
    return entry_date == today_utc

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
    """سنجش عمیق مفاهیمی هوش مصنوعی بدون سوزاندن اخبار جدید یک فرد/تیم"""
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
        "Is the new article reporting the EXACT SAME specific event, military strike, sports match result, celebrity drama, product launch, or news story as one in the history?\n\n"
        "CRITICAL RULE:\n"
        "- Two articles can be about the SAME entity/person/team/company (e.g., Elon Musk, Real Madrid, Apple), but if they discuss DIFFERENT events, matches, or developments, they are NOT duplicates -> Answer NO.\n"
        "- ONLY answer YES if both articles cover the EXACT SAME news event.\n\n"
        "Answer ONLY with 'YES' or 'NO'."
    )

    response = call_groq_ai(prompt)
    if response and "YES" in response.upper():
        return True
    return False

def analyze_and_summarize_news_with_ai(title, summary_text, is_from_telegram=False):
    """تحلیل قطعی ماهیت خبر با تفکیک دقیق تلگرام و سایت‌ها"""
    content = clean_html(f"Title: {title}\nSummary: {summary_text}")
    if not content:
        return None

    if is_from_telegram:
        # فیلتر تلگرام: رد سیاسی، نظامی، مذهبی، احوالپرسی و چت
        rules = (
            "برای کانال‌های تلگرامی پاسخ باید NO باشد اگر پست شامل هریک از موارد زیر باشد:\n"
            "۱. اخبار سیاسی، نظامی، جنگ، یا امور دولت‌ها.\n"
            "۲. مطالب مذهبی، مناسبت‌های تقویمی، ادعیه، احادیث یا تبریک/تسلیت.\n"
            "۳. چت روزمره (صبح بخیر، شب خوش)، تقویم امروز، نرخ طلا/ارز، متون ادبی یا تبلیغات."
        )
    else:
        # قوانین سایت‌ها: اخبار نظامی، ورزشی، زرد، سلبریتی، علمی و حوادث ۱۰۰٪ مجازند
        rules = (
            "برای سایت‌های خبری قوانین به این شرح است:\n"
            "موارد مجاز (YES): اخبار تکنولوژی، علمی، حوادث، حواشی سلبریتی‌ها، سینما، اخبار ورزشی، اخبار زرد جذاب و اخبار نظامی/دفاعی همگی ۱۰۰٪ مجاز (YES) هستند.\n"
            "تنها موارد ممنوع (NO):\n"
            "۱. اخبار سیاسی حزبی/داخلی و بیانیه‌های احزاب.\n"
            "۲. مطالب مذهبی، ادعیه و تبریک/تسلیت.\n"
            "۳. تبلیغات مستقیم محصولات خریدنی."
        )

    prompt = (
        f"پست ورودی:\n{content}\n\n"
        f"دستورالعمل ارزیابی:\n{rules}\n\n"
        "وظایف شما:\n"
        "۱. آیا پست طبق دستورالعمل بالا مجاز است؟ (پاسخ YES یا NO)\n"
        "۲. اگر مجاز (YES) است، خلاصه کوتاه ۱ یا ۲ جمله‌ای روان، دقیق و جذاب به زبان فارسی بنویسید.\n\n"
        "فرمت پاسخ دقیقاً به این شکل باشد:\n"
        "IS_NEWS: [YES یا NO]\n"
        "SUMMARY: [خلاصه فارسی خبر]"
    )

    response = call_groq_ai(prompt)
    if not response or "IS_NEWS: NO" in response:
        return None

    match = re.search(r'SUMMARY:\s*(.*)', response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return response

# --- بدنه اصلی اسکریپت ---

def main():
    init_db()
    print("در حال جمع‌آوری اخبار از ۳۹ منبع با سیستم اصلاح‌شده...")

    raw_articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # ۱. جمع‌آوری اخبار از منابع
    for feed_url in RSS_FEEDS:
        is_telegram = ('telegram' in feed_url or 'rsshub' in feed_url)
        try:
            if is_telegram:
                response = requests.get(feed_url, headers=headers, timeout=15)
                feed = feedparser.parse(response.content)
            else:
                feed = feedparser.parse(feed_url)

            for entry in feed.entries[:20]:
                link = entry.link

                if not is_link_sent(link) and is_published_today(entry):
                    entry['is_telegram'] = is_telegram
                    raw_articles.append(entry)
        except Exception as e:
            print(f"خطا در دریافت RSS از {feed_url}: {e}")

    # ۲. پردازش و مقایسه عمیق ۵۰ خبر اخیر
    recent_stories_history = get_recent_sent_stories(50)
    processed_news = []

    for entry in raw_articles:
        raw_summary = clean_html(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
        is_telegram = getattr(entry, 'is_telegram', False)

        # الف) سنجش تکراری نبودن خبر (انگلیسی به انگلیسی)
        if is_duplicate_story_ai(entry.title, raw_summary, recent_stories_history):
            save_link_title_and_summary(entry.link, entry.title, raw_summary)
            print(f"❌ خبر تکراری شناسایی و رد شد: {entry.title}")
            continue

        # ب) ارزیابی مجاز بودن خبر بر اساس منبع و خلاصه‌سازی فارسی
        fa_summary = analyze_and_summarize_news_with_ai(entry.title, raw_summary, is_from_telegram=is_telegram)
        
        if fa_summary is None:
            save_link_title_and_summary(entry.link, entry.title, raw_summary)
            print(f"خبر غیرمرتبط/فیلترشده رد شد: {entry.title}")
            continue

        processed_news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': fa_summary
        })
        
        # بروزرسانی لیست تاریخچه در همان اجرای جاری
        recent_stories_history.append(f"Title: {entry.title} | Info: {raw_summary[:150]}")

    # ۳. ارسال نتایج به تلگرام
    new_messages_sent = 0
    for news in processed_news:
        msg = f"📰 **{news['title']}**\n\n"
        if news['summary']:
            msg += f"📝 **خلاصه:** {news['summary']}\n\n"
        msg += f"🔗 {news['link']}"

        send_telegram_message(msg)
        new_messages_sent += 1
        save_link_title_and_summary(news['link'], news['title'], news['summary'])
        print(f"✅ خبر ارسال شد: {news['title']}")

    if new_messages_sent > 0:
        send_telegram_message("🏁 **پایان این دور از اخبار**")
    else:
        send_telegram_message("❌ **چیزی یافت نشد**")

if __name__ == '__main__':
    main()
