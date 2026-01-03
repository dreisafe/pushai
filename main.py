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
# BURAYA TELEFONUNDA KULLANDIGIN GIZLI TOPIC ISMINI YAZ!
NTFY_TOPIC = "haber_akis_gizli_xyz_123" 

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200
SIMILARITY_THRESHOLD = 0.70 

# Engellenecek Kelimeler (Spor, Magazin, Yarisma)
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

# API Key Kontrolu
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("HATA: API KEY Bulunamadi!")

genai.configure(api_key=GEMINI_API_KEY)

# HTML Temizleyici
def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:1500] # Gemini 2.0 daha uzun metin isleyebilir

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
    # İŞTE BURASI: 2026 Standartlarina uygun model
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
    except:
        # Eger kutuphane cok eskiyse 'gemini-pro'ya dussun (Fallback)
        model = genai.GenerativeModel('gemini-pro')
    
    clean_summary = clean_html(summary)
    
    prompt = f"""
    Görevin: Aşağıdaki haberi bir haber ajansı editörü gibi okumak ve kullanıcıya bildirim olarak gidecek şekilde özetlemek.
    
    Kurallar:
    1. Haberin ana duygusunu veya konusunu en iyi anlatan TEK BİR EMOJİ ile başla (Örn: 🚨, 📉, 🏛️, 🌍).
    2. Sadece TEK BİR CÜMLE kur.
    3. Asla "Haberde...", "Metinde..." gibi girişler yapma. Direkt olayı anlat.
    4. Siyasi veya hassas içerik olsa bile tarafsızca özetle, sansürleme.
    
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        # Guvenlik filtresi olmadan cagri yapiyoruz (Default ayarlar)
        # Gemini 2.0 genellikle daha esnek oldugu icin extra ayara gerek yok
        response = model.generate_content(prompt)
        
        if response.text:
            return response.text.strip()
        else:
            return f"⚠️ AI Bos Dondu: {title}"
            
    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg:
             return f"⚠️ Model Bulunamadı: Kütüphane güncellenmeli."
        elif "429" in error_msg:
            return f"⚠️ Kota Doldu: Biraz bekle."
        else:
            # Hata mesajini kisaltip gonderelim ki gorelim
            return f"⚠️ Hata: {error_msg[:60]}..." 

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
    
    print("Haberler taraniyor (Gemini 2.0)...")
    
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
    else:
        print("Yeni haber yok.")

if __name__ == "__main__":
    main()
