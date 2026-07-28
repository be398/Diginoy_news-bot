import os
import sqlite3
import requests
import feedparser
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID")

# ۱. لیست آدرس‌های RSS سایت‌های درخواستی
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

# مسیرها و تگ‌های ممنوعه غیرخبری (نقد، راهنما، تبلیغات خرید)
NON_NEWS_PATTERNS = ['/reviews/', '/review/', '/guides/', '/guide/', '/deals/', '/best-deals/']

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

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower().strip()

def extract_keywords(text):
    words = set(clean_text(text).split())
    stopwords = {'this', 'that', 'with', 'from', 'have', 'more', 'about', 'will', 'been', 'their', 'which', 'would'}
    return {w for w in words if len(w) > 3 and w not in stopwords}

def is_published_today(entry):
    """بررسی اینکه آیا خبر مربوط به امروز (UTC) است یا خیر"""
    published_parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if not published_parsed:
        return True

    today_utc = datetime.now(timezone.utc).date()
    entry_date = datetime(*published_parsed[:6], tzinfo=timezone.utc).date()
    return entry_date == today_utc

def is_valid_news(entry):
    """فیلتر مطالب غیرخبری و بررسی انتشار در همان روز"""
    link = entry.link.lower()
    title = entry.title.lower()

    # ۱. فقط اخبار امروز
    if not is_published_today(entry):
        return False

    # ۲. حذف لینک‌های مربوط به راهنمای خرید و ریویو
    for pattern in NON_NEWS_PATTERNS:
        if pattern in link or pattern in title:
            return False

    return True

def are_articles_similar(art1, art2):
    """تشخیص اخبار تکراری از چند سایت مختلف با مقایسه محتوا و تیتر"""
    title1 = clean_text(art1.title)
    title2 = clean_text(art2.title)

    # مقایسه عنوان
    title_sim = SequenceMatcher(None, title1, title2).ratio()
    if title_sim >= 0.50:
        return True

    # مقایسه کلمات کلیدی متن
    kw1 = extract_keywords(f"{art1.title} {getattr(art1, 'summary', '')}")
    kw2 = extract_keywords(f"{art2.title} {getattr(art2, 'summary', '')}")
    
    if kw1 and kw2:
        intersection = kw1.intersection(kw2)
        overlap_ratio = len(intersection) / min(len(kw1), len(kw2))
        if overlap_ratio >= 0.45:
            return True

    return False

def get_content_length(entry):
    if 'content' in entry and len(entry.content) > 0:
        return len(entry.content[0].value)
    elif 'summary' in entry:
        return len(entry.summary)
    return len(entry.title)

def main():
    init_db()
    print("در حال جمع‌آوری ۳۰ خبر آخر سایت‌ها...")

    raw_articles = []
    
    # ۲. بررسی ۳۰ خبر آخر هر سایت
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                link = entry.link

                if not is_link_sent(link):
                    if is_valid_news(entry):
                        raw_articles.append(entry)
                    else:
                        save_link(link)
        except Exception as e:
            print(f"خطا در دریافت RSS از {feed_url}: {e}")

    # ۳. گروه‌بندی اخبار تکراری و انتخاب کامل‌ترین خبر
    grouped_articles = []
    for article in raw_articles:
        is_duplicate_topic = False
        for i, existing_group in enumerate(grouped_articles):
            if are_articles_similar(article, existing_group['best_article']):
                is_duplicate_topic = True
                if get_content_length(article) > existing_group['content_len']:
                    grouped_articles[i] = {
                        'title': article.title,
                        'link': article.link,
                        'best_article': article,
                        'content_len': get_content_length(article),
                        'all_links': existing_group['all_links'] + [article.link]
                    }
                else:
                    existing_group['all_links'].append(article.link)
                break
        
        if not is_duplicate_topic:
            grouped_articles.append({
                'title': article.title,
                'link': article.link,
                'best_article': article,
                'content_len': get_content_length(article),
                'all_links': [article.link]
            })

    # ۴. ارسال اخبار به تلگرام یا اعلام «چیزی یافت نشد»
    new_messages_sent = 0
    for group in grouped_articles:
        msg = f"📰 **{group['title']}**\n\n🔗 {group['link']}"
        send_telegram_message(msg)
        new_messages_sent += 1
        
        for link in group['all_links']:
            save_link(link)
        print(f"خبر ارسال شد: {group['title']}")

    if new_messages_sent > 0:
        send_telegram_message("🏁 **پایان این دور از اخبار**")
    else:
        send_telegram_message("❌ **چیزی یافت نشد**")

if __name__ == '__main__':
    main()
