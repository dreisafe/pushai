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
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # <-- BURAYI KENDI KANAL ADINLA DUZELT!

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200
SIMILARITY_THRESHOLD = 0.70 

BLOCKED_KEYWORDS = [
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç yorumları", "astroloji", 
    "kim milyoner olmak ister", "survivor", "gelin evi"
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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:1500]

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
    return None

def summarize_news(title, summary):
    # Kutuphane guncellendigi icin (v0.8.6) artik bu model kesin calisir!
    model_name = 'gemini-1.5-flash'
    
    clean_summary = clean_html(summary)
    
    prompt = f"""
    Haber editörü gibi davran.
    1. Haberi en iyi anlatan TEK BİR EMOJİ ile başla (Örn: 🚨, 📉, 🏛️).
    2. Tek bir özet cümlesi yaz.
    3. Asla "Haberde..." diye başlama.
    
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return response.text.strip()
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            return "KOTA_DOLDU" 
        else:
            return f"⚠️ Hata: {error_msg[:40]}..." 

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
    
    print("Sakin modda taranıyor (1.5 Flash)...")
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            # Her kanaldan EN YENI 1 haber (Kota icin)
            for entry in feed.entries[:1]: 
                if is_spam_or_blocked(entry.title):
                    continue
                    
                if not is_duplicate(entry, history):
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    ai_summary = summarize_news(entry.title, content)
                    
                    if ai_summary == "KOTA_DOLDU":
                        send_push_notification("⚠️ Kota limitine takıldı. Bekleniyor.", "https://google.com")
                        break 

                    image_url = find_image_url(entry)
                    send_push_notification(ai_summary, entry.link, image_url)
                    
                    history.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": datetime.now().isoformat()
                    })
                    new_entries_count += 1
                    
                    # 12 saniye fren
                    time.sleep(12) 
            
            if "KOTA_DOLDU" in locals().get('ai_summary', ''):
                break
                
        except Exception as e:
            continue

    if new_entries_count > 0:
        save_history(history)

if __name__ == "__main__":
    main()
