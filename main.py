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
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # <-- KENDI KANAL ADINI YAZ!

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
    {"name": "CNBC Economy", "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html"},
    
    # --- TURKCE KAYNAKLAR ---
    {"name": "BBC Türkçe", "url": "https://feeds.bbci.co.uk/turkce/rss.xml"},
    {"name": "DW Türkçe", "url": "https://rss.dw.com/xml/rss-tr-all"},     
    {"name": "Euronews TR", "url": "https://tr.euronews.com/rss"},            
    {"name": "VOA Türkçe", "url": "https://www.voaturkce.com/api/zqyqyepqqt"},
    {"name": "Independent TR", "url": "https://www.independentturkish.com/rss.xml"}
]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_best_model_name():
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        for model in available_models:
            if "flash" in model.lower() and "1.5" in model: return model 
        for model in available_models:
            if "pro" in model.lower() and "1.5" in model: return model
        
        if available_models: return available_models[0]
        return "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

ACTIVE_MODEL_NAME = get_best_model_name()

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:2000] 

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

def summarize_news(title, summary, source_name):
    clean_summary = clean_html(summary)
    
    prompt = f"""
    Sen Global bir Haber İstihbarat Servisisin.
    
    GÖREVİN:
    1. Haberi oku ve anla (İngilizce/Almanca olabilir).
    2. Çıktıyı MUTLAKA VE SADECE TÜRKÇE olarak ver.
    3. Eğer haber Magazin, Spor skoru, Ansiklopedik bilgi, Yerel 3. sayfa haberi veya Reklam ise SADECE "SKIP" YAZ.

    4. Eğer haber ÖNEMLİ ise:
       - Başa olayı anlatan EMOJİ koy.
       - Haberi TÜRKÇE olarak, en fazla 15 kelimeyle, SONUÇ ODAKLI özetle.
       - Asla "Haberde..." veya "{source_name}'e göre..." deme. Direkt olayı yaz.

    Haber Kaynağı: {source_name}
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        model = genai.GenerativeModel(ACTIVE_MODEL_NAME)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "SKIP" in text: return "SKIP"
        return text
    except Exception as e:
        if "429" in str(e): return "KOTA_DOLDU"
        return f"⚠️ Hata: {str(e)[:30]}..."

def send_push_notification(message, link, source_name, image_url=None):
    headers = {"Title": f"Kaynak: {source_name}", "Priority": "default", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    new_entries_count = 0
    print(f"Global Tarama Basliyor (Yavas Mod)... Model: {ACTIVE_MODEL_NAME}")
    
    for source in RSS_SOURCES:
        url = source["url"]
        name = source["name"]
        
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:1]: 
                if is_spam_or_blocked(entry.title): continue
                if not is_duplicate(entry, history):
                    
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    ai_result = summarize_news(entry.title, content, name)
                    
                    if ai_result == "SKIP":
                        print(f"Elenen Haber: {entry.title}")
                        history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                        continue

                    if ai_result == "KOTA_DOLDU":
                        # Kota dolduysa sadece log bas, bildirim atip rahatsiz etme artik
                        print("⚠️ Kota doldu. Sonraki calismada devam edecek.")
                        break 

                    image_url = find_image_url(entry)
                    send_push_notification(ai_result, entry.link, name, image_url)
                    
                    history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                    new_entries_count += 1
                    
                    # --- KOTA KALKANI ---
                    # Her isteğin arasina 45 SANIYE koyduk. 
                    # 11 kaynak x 45 sn = ~8 dakika sürer. GitHub icin sorun yok, API icin cok guvenli.
                    print(f"Gonderildi: {name}. Bekleniyor (45sn)...")
                    time.sleep(45) 
            
            if "KOTA_DOLDU" in locals().get('ai_result', ''): break
        except Exception as e: 
            continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
