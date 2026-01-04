import feedparser
import requests
import json
import os
from groq import Groq
from datetime import datetime
from difflib import SequenceMatcher
import time
import re
import html  # <-- YENI: HTML kodlarini temizlemek icin

# --- AYARLAR ---
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # <-- KANAL ADINI BURAYA YAZ!

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 300 
SIMILARITY_THRESHOLD = 0.65 

BLOCKED_KEYWORDS = [
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç", "astroloji", "survivor", "masterchef",
    "hava durumu", "gelin evi", "kim milyoner",
    "football match", "celebrity", "horoscope", "gossip", "royal family", 
    "kim kardashian", "premier league", "nba results", "lottery"
]

RSS_SOURCES = [
    # --- GLOBAL DEVLER ---
    {"name": "Reuters World", "url": "http://feeds.reuters.com/reuters/worldNews"},
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "Sky News", "url": "https://feeds.skynews.com/feeds/rss/world.xml"},
    
    # --- TURKCE KAYNAKLAR (KARAKTER DUZELTMELI) ---
    # Baslikta sorun cikmamasi icin Turkce karakterleri kaldirdik
    {"name": "BBC Turkce", "url": "https://feeds.bbci.co.uk/turkce/rss.xml"},
    {"name": "DW Turkce", "url": "https://rss.dw.com/xml/rss-tr-all"},     
    {"name": "Euronews TR", "url": "https://tr.euronews.com/rss"},            
    {"name": "VOA Turkce", "url": "https://www.voaturkce.com/api/zqyqyepqqt"},
    {"name": "Independent TR", "url": "https://www.independentturkish.com/rss.xml"}
]

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)

def clean_html(raw_html):
    if not raw_html: return ""
    # 1. HTML etiketlerini temizle (<br>, <p> vs)
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    # 2. HTML kodlarini duzelt (&amp; -> & gibi)
    cleantext = html.unescape(cleantext)
    return cleantext[:2500] 

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: return []
    return []

def save_history(history_data):
    trimmed_data = history_data[-MAX_HISTORY_ITEMS:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed_data, f, ensure_ascii=False, indent=2)

def is_spam_or_blocked(title):
    title_lower = title.lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword in title_lower: return True
    return False

def is_duplicate(entry, history):
    for item in history:
        if item['link'] == entry.link: return True
    for item in history:
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > SIMILARITY_THRESHOLD: return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''): return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''): return link['href']
    return None

def summarize_news_groq(title, summary, source_name):
    if not client: return "API_KEY_YOK"
    
    clean_summary = clean_html(summary)
    
    # Eger ozet bossa, basligi ozet niyetine kullan (Guvenlik Onlemi)
    if len(clean_summary) < 10:
        clean_summary = title

    prompt = f"""
    Sen Global bir Haber İstihbarat Servisisin.
    
    GÖREVİN:
    1. Haberi oku (İngilizce/Almanca olabilir).
    2. Çıktıyı MUTLAKA VE SADECE TÜRKÇE ver.
    3. Eğer haber Magazin, Spor, Burç, Yerel Kaza ise SADECE "SKIP" YAZ.

    4. Eğer haber ÖNEMLİ ise:
       - Başa olayı anlatan EMOJİ koy.
       - Haberi TÜRKÇE olarak, en fazla 15 kelimeyle, SONUÇ ODAKLI özetle.
       - Asla "Haberde..." deme. Direkt olayı yaz.

    Kaynak: {source_name}
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        # Llama 3.3 - En guclu ve guncel model
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Sen Türkçe haber özetleyen bir asistansın."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.3, 
        )
        text = chat_completion.choices[0].message.content.strip()
        
        if "SKIP" in text: return "SKIP"
        return text

    except Exception as e:
        return f"⚠️ Groq Hatası: {str(e)[:50]}..."

def send_push_notification(message, link, source_name, image_url=None):
    # Baslikta Turkce karakter sorunu olmamasi icin source_name artik ASCII
    headers = {"Title": f"Kaynak: {source_name}", "Priority": "default", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    new_entries_count = 0
    print("Operasyon: Groq (Llama 3.3) & Temizlik Modu Devrede...")
    
    for source in RSS_SOURCES:
        url = source["url"]
        name = source["name"]
        
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:1]: 
                if is_spam_or_blocked(entry.title): continue
                if not is_duplicate(entry, history):
                    
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    ai_result = summarize_news_groq(entry.title, content, name)
                    
                    if ai_result == "SKIP":
                        print(f"Elenen: {entry.title}")
                        history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                        continue
                    
                    if ai_result == "API_KEY_YOK":
                        send_push_notification("⚠️ Groq API Key Eksik", "", "Sistem")
                        break

                    image_url = find_image_url(entry)
                    send_push_notification(ai_result, entry.link, name, image_url)
                    
                    history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                    new_entries_count += 1
                    
                    print(f"Gonderildi: {name}. Bekleniyor (15sn)...")
                    time.sleep(15) 
            
        except Exception as e: 
            continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
