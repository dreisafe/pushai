import feedparser
import requests
import json
import os
import google.generativeai as genai
from datetime import datetime
from difflib import SequenceMatcher
import time
import re

# --- AYARLAR ---
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # BURAYI KENDI TOPIC ISMINLE DEGISTIR!

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200
SIMILARITY_THRESHOLD = 0.70 

# Engellenecek Kelimeler
BLOCKED_KEYWORDS = [
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç yorumları", "astroloji", 
    "kim milyoner olmak ister", "survivor"
]

RSS_URLS = [
    "https://feeds.bbci.co.uk/turkce/rss.xml",
    "https://rss.dw.com/xml/rss-tr-all",     
    "https://tr.euronews.com/rss",            
    "https://www.trthaber.com/manset_xml.php",
    "https://www.voaturkce.com/api/zqyqyepqqt",
    "https://tr.sputniknews.com/export/rss2/archive/index.xml",
    "https://www.independentturkish.com/rss.xml"
]

# API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Key yoksa hata firlatmadan once bildirelim
if not GEMINI_API_KEY:
    print("API KEY YOK!")

genai.configure(api_key=GEMINI_API_KEY)

# HTML temizleme fonksiyonu (AI kafasi karismasin diye)
def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:1000] # Cok uzun metinleri kisaltalim

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_history(history_data):
    trimmed_data = history_data[-MAX_HISTORY_ITEMS:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed_data, f, ensure_ascii=False, indent=2)

def is_spam_or_blocked(title):
    title_lower = title.lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword in title_lower:
            return True
    return False

def is_duplicate(entry, history):
    for item in history:
        if item['link'] == entry.link:
            return True
    
    for item in history:
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > SIMILARITY_THRESHOLD:
            return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''):
                return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''):
                return link['href']
    if 'enclosures' in entry:
        for enclosure in entry.enclosures:
            if 'image' in enclosure.get('type', ''):
                return enclosure['href']
    return None

def summarize_news(title, summary):
    # Model ismini "gemini-1.5-flash" olarak netlestirdik
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Sansur ayarlarini 'dictionary' formatiyla verelim (Daha kararli)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    
    clean_summary = clean_html(summary)
    
    prompt = f"""
    Görevin: Aşağıdaki haberi okuyup, tek cümlelik, vurucu bir bildirim özeti yazmak.
    1. En başa haberi anlatan TEK BİR EMOJİ koy (Örn: 🚨, 📉, 🏛️).
    2. Sadece özet cümlesini yaz.
    
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        response = model.generate_content(prompt, safety_settings=safety_settings)
        if response.text:
            return response.text.strip()
        else:
            return f"⚠️ AI Bos Dondu: {title}"
            
    except Exception as e:
        # ISTE BURASI: Hatayi gizlemek yerine sana bildirim olarak yolluyoruz
        error_msg = str(e)
        if "403" in error_msg:
            return f"⚠️ API Key Hatası (403): Anahtarını kontrol et."
        elif "429" in error_msg:
            return f"⚠️ Limit Doldu (429): Biraz bekle."
        elif "finish_reason" in error_msg or "safety" in error_msg.lower():
            return f"⚠️ Güvenlik Filtresi: {title}"
        else:
            return f"⚠️ Hata: {error_msg[:50]}..." 

def send_push_notification(message, link, image_url=None):
    headers = {
        "Title": "Gundem Ozeti", 
        "Priority": "default",
        "Click": link,
    }
    if image_url:
        headers["Attach"] = image_url

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers=headers
        )
    except Exception as e:
        print(f"Bildirim Hatasi: {e}")

def main():
    history = load_history()
    new_entries_count = 0
    
    print("Taranıyor...")
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]: 
                if is_spam_or_blocked(entry.title):
                    continue
                    
                if not is_duplicate(entry, history):
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    # AI Cagrisi
                    ai_summary = summarize_news(entry.title, content)
                    image_url = find_image_url(entry)
                    
                    send_push_notification(ai_summary, entry.link, image_url)
                    
                    history.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": datetime.now().isoformat()
                    })
                    new_entries_count += 1
                    time.sleep(2) 
                    
        except Exception as e:
            continue

    if new_entries_count > 0:
        save_history(history)

if __name__ == "__main__":
    main()
