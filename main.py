import os
import sqlite3
import requests
import feedparser
import re
from datetime import datetime, timezone
from google import genai

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# راه‌اندازی کلاینت هوش مصنوعی Gemini
ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"خطا در راه‌اندازی Gemini API: {e}")

RSS_FEEDS = [
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

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            link TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def is_link_sent(link):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_news WHERE link = ?', (link,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_link(link):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO sent_news (link) VALUES (?)', (link,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": MY_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload)

def clean_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()

def is_published_today(entry):
    published_parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if not published_parsed:
        return True

    today_utc = datetime.now(timezone.utc).date()
    entry_date = datetime(*published_parsed[:6], tzinfo=timezone.utc).date()
    return entry_date == today_utc

def analyze_and_summarize_with_ai(title, summary_text):
    """آنالیز هوشمند خبر و ساخت خلاصه فارسی توسط Gemini"""
    if not ai_client:
        return clean_html(summary_text)[:150]

    content = clean_html(f"Title: {title}\nSummary: {summary_text}")
    if not content:
        return ""

    prompt = (
        f"این متن یک پست از سایت خبری است:\n\n{content}\n\n"
        "وظایف شما:\n"
        "۱. آیا این یک «خبر واقعی» است؟ (اگر تبلیغات خرید، نقد و بررسی محصول، راهنمای بازی/خرید، یا مقاله نظر شخصی است پاسخ دهید NO).\n"
        "۲. اگر یک خبر واقعی است، یک خلاصه کوتاه ۱ یا ۲ جمله‌ای، بسیار جذاب و روان به زبان فارسی بنویسید.\n\n"
        "فرمت پاسخ حتماً و دقیقاً به این شکل باشد:\n"
        "IS_NEWS: [YES یا NO]\n"
        "SUMMARY: [خلاصه فارسی خبر]"
    )

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        text_resp = response.text.strip()
        
        if "IS_NEWS: NO" in text_resp:
            return None
        
        match = re.search(r'SUMMARY:\s*(.*)', text_resp, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text_resp
    except Exception as e:
        print(f"خطا در پردازش هوش مصنوعی: {e}")
        return clean_html(summary_text)[:150]

def is_duplicate_story_ai(new_title, existing_titles):
    """سنجش درک مفاهیمی اخبار تکراری توسط هوش مصنوعی"""
    if not ai_client or not existing_titles:
        return False

    prompt = (
        f"خبر جدید: {new_title}\n\n"
        f"لیست اخبار قبلاً ثبت‌شده:\n" + "\n".join([f"- {t}" for t in existing_titles]) + "\n\n"
        "آیا خبر جدید درباره همان «موضوع و رویداد واحدی» است که در یکی از اخبار قبلی به آن پرداخته شده؟ (حتی اگر تیترها متفاوت باشند اما سوژه اصلی یکی باشد).\n"
        "فقط پاسخ دهید YES یا NO."
    )

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return "YES" in response.text.strip().upper()
    except Exception as e:
        print(f"خطا در تشخیص تکرار با هوش مصنوعی: {e}")
        return False

def main():
    init_db()
    print("در حال جمع‌آوری اخبار و تحلیل هوشمند با Gemini...")

    raw_articles = []
    
    # ۱. جمع‌آوری اخبار امروز
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                link = entry.link

                if not is_link_sent(link) and is_published_today(entry):
                    raw_articles.append(entry)
        except Exception as e:
            print(f"خطا در دریافت RSS از {feed_url}: {e}")

    # ۲. فیلتر، خلاصه‌سازی و حذف تکراری‌ها با هوش مصنوعی
    processed_news = []
    processed_titles = []

    for entry in raw_articles:
        raw_summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        
        # الف) آنالیز ماهیت خبر + خلاصه فارسی
        fa_summary = analyze_and_summarize_with_ai(entry.title, raw_summary)
        
        # اگر هوش مصنوعی تشخیص داد مقاله غیرخبری است
        if fa_summary is None:
            save_link(entry.link)
            continue

        # ب) بررسی تک‌به‌تک جهت عدم انتشار موضوع تکراری
        if is_duplicate_story_ai(entry.title, processed_titles):
            save_link(entry.link)
            print(f"موضوع تکراری توسط AI رد شد: {entry.title}")
            continue

        processed_news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': fa_summary
        })
        processed_titles.append(entry.title)

    # ۳. ارسال خروجی‌های هوشمند به تلگرام
    new_messages_sent = 0
    for news in processed_news:
        msg = f"📰 **{news['title']}**\n\n"
        if news['summary']:
            msg += f"📝 **خلاصه:** {news['summary']}\n\n"
        msg += f"🔗 {news['link']}"

        send_telegram_message(msg)
        new_messages_sent += 1
        save_link(news['link'])
        print(f"خبر ارسال شد: {news['title']}")

    if new_messages_sent > 0:
        send_telegram_message("🏁 **پایان این دور از اخبار**")
    else:
        send_telegram_message("❌ **چیزی یافت نشد**")

if __name__ == '__main__':
    main()
