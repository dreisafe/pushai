import feedparser
import requests
import json
import os
import google.generativeai as genai
from datetime import datetime
from difflib import SequenceMatcher
import time

# --- AYARLAR ---
# BURAYA TELEFONDAKI TOPIC ISMINI YAZMAYI UNUTMA!
NTFY_TOPIC = "haber_akis_gizli_xyz_123" 

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200
SIMILARITY_THRESHOLD = 0.70 

# Engellenecek Kelimeler (Kucuk harfle yazin)
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
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY bulunamadi!")

genai.configure(api_key=GEMINI_API_KEY)

def summarize_news(title, summary):
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Görevin: Aşağıdaki haberi okuyup, kullanıcıya bildirim olarak gidecek şekilde özetlemek.
    Kurallar:
    1. Haberin duygusunu en iyi anlatan TEK BİR EMOJİ ile başla (Örn: 🚨, 📉, ⚽, 🏛️).
    2. Sadece TEK BİR CÜMLE kur.
    3. Asla "Haberde...", "Metinde..." gibi ifadeler kullanma, direkt konuya gir.
    
    Başlık: {title}
    İçerik: {summary}
    """
    
    try:
        # safety_settings kismini kaldirdik, varsayilan ayarlarla calissin
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini Hata: {e}")
        return f"📰 {title}" 


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
            print(f"Engellenen icerik atlandi: {title}")
            return True
    return False

def is_duplicate(entry, history):
    for item in history:
        if item['link'] == entry.link:
            return True
    
    for item in history:
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > SIMILARITY_THRESHOLD:
            print(f"Benzer haber elendi ({similarity:.2f}): {entry.title}")
            return True
    return False

def find_image_url(entry):
    # 1. Media Content kontrolu (Genellikle buradadir)
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''):
                return media['url']
    
    # 2. Links kontrolu
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''):
                return link['href']
                
    # 3. Enclosure kontrolu
    if 'enclosures' in entry:
        for enclosure in entry.enclosures:
            if 'image' in enclosure.get('type', ''):
                return enclosure['href']
                
    return None

def summarize_news(title, summary):
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Görevin: Aşağıdaki haberi okuyup, kullanıcıya bildirim olarak gidecek şekilde özetlemek.
    Kurallar:
    1. Haberin duygusunu en iyi anlatan TEK BİR EMOJİ ile başla (Örn: 🚨, 📉, ⚽, 🏛️).
    2. Sadece TEK BİR CÜMLE kur.
    3. Asla "Haberde...", "Metinde..." gibi ifadeler kullanma, direkt konuya gir.
    4. İlgi çekici ve vurucu olsun.
    
    Başlık: {title}
    İçerik: {summary}
    """
    
    try:
        # safety_settings parametresini buraya ekledik
        response = model.generate_content(prompt, safety_settings=safety_settings)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini Hata: {e}")
        return f"📰 {title}" # Hata olursa emoji + baslik don

def send_push_notification(message, link, image_url=None):
    headers = {
        "Title": "Gundem Ozeti", # Turkce karakter sorunu icin duzeltildi
        "Priority": "default",
        "Click": link,
    }
    
    # Eger resim bulunduysa header'a ekle
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
    
    print("RSS Kaynaklari taraniyor...")
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]: 
                # Spam ve Duplicate Kontrolu
                if is_spam_or_blocked(entry.title):
                    continue
                    
                if not is_duplicate(entry, history):
                    print(f"İşleniyor: {entry.title}")
                    
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    # AI Ozetleme
                    ai_summary = summarize_news(entry.title, content)
                    
                    # Resim Bulma
                    image_url = find_image_url(entry)
                    
                    # Bildirim Gonder
                    send_push_notification(ai_summary, entry.link, image_url)
                    
                    history.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": datetime.now().isoformat()
                    })
                    new_entries_count += 1
                    
                    time.sleep(2) 
                    
        except Exception as e:
            print(f"RSS Hatasi ({url}): {e}")
            continue

    if new_entries_count > 0:
        save_history(history)
        print(f"{new_entries_count} yeni haber islendi.")
    else:
        print("Yeni haber yok.")

if __name__ == "__main__":
    main()
