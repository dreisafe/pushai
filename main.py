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
MAX_HISTORY_ITEMS = 300 # Daha fazla kaynak = Daha fazla hafiza lazim
SIMILARITY_THRESHOLD = 0.65 

# YASAKLI KELIMELER (Hem Turkce Hem Ingilizce)
BLOCKED_KEYWORDS = [
    # TR
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç", "astroloji", "survivor", "masterchef",
    "hava durumu", "gelin evi", "kim milyoner",
    # EN (Global copleri de engellemek lazim)
    "football match", "celebrity", "horoscope", "gossip", "royal family", 
    "kim kardashian", "premier league", "nba results"
]

# --- GLOBAL ISTIHBARAT LISTESI ---
# Dünyanin en hizli ajanslari + Turkce kaynaklar
RSS_URLS = [
    # --- GLOBAL DEVLER (Haber buraya once duser) ---
    "http://feeds.reuters.com/reuters/worldNews",       # Reuters Dunya (Cok Hizli)
    "http://feeds.bbci.co.uk/news/world/rss.xml",       # BBC World
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", # NY Times
    "https://www.aljazeera.com/xml/rss/all.xml",        # Al Jazeera (Ortadogu uzmani)
    "https://feeds.skynews.com/feeds/rss/world.xml",    # Sky News
    "https://www.cnbc.com/id/100727362/device/rss/rss.html", # Dunya Ekonomisi/Borsa
    
    # --- TURKCE KAYNAKLAR ---
    "https://feeds.bbci.co.uk/turkce/rss.xml",
    "https://rss.dw.com/xml/rss-tr-all",     
    "https://tr.euronews.com/rss",            
    "https://www.voaturkce.com/api/zqyqyepqqt",
    "https://www.independentturkish.com/rss.xml"
]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- AKILLI MODEL SECICISI ---
def get_best_model_name():
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # Once 1.5 Flash (Hizli/Ucuz)
        for model in available_models:
            if "flash" in model.lower() and "1.5" in model: return model 
        
        # Sonra Pro
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
    return cleantext[:2000] # Global haberler uzun olabilir

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
        if keyword in title_lower:
            return True
    return False

def is_duplicate(entry, history):
    # 1. Link Kontrolu (Kesin)
    for item in history:
        if item['link'] == entry.link: return True
        
    # 2. Baslik Benzerligi (Fuzzy Match)
    # Ingilizce vs Turkce basliklari yakalamak zordur ama 
    # ayni ajanstan gelen tekrarlari engeller.
    for item in history:
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > SIMILARITY_THRESHOLD:
            return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''): return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''): return link['href']
    return None

def summarize_news(title, summary, source_url):
    clean_summary = clean_html(summary)
    
    # PROMPT: GLOBAL MÜTERCİM TERCÜMAN & ACIMASIZ EDİTÖR
    prompt = f"""
    Sen Global bir Haber İstihbarat Servisisin.
    Gelen haber İngilizce, Almanca veya Fransızca olabilir.
    
    GÖREVİN:
    1. Haberi oku ve anla.
    2. Çıktıyı MUTLAKA VE SADECE TÜRKÇE olarak ver.
    3. Eğer haber şu kategorilerdense SADECE "SKIP" YAZ:
       - Magazin, Kraliyet ailesi dedikoduları, Spor skorları.
       - "Nedir?", "Kimdir?" tarzı ansiklopedik bilgiler.
       - Yerel küçük trafik kazaları veya 3. sayfa haberleri.
       - Reklam veya ürün tanıtımı.

    4. Eğer haber ÖNEMLİ (Savaş, Kriz, Ekonomi, Teknoloji, Siyaset) ise:
       - Başa olayı anlatan EMOJİ koy.
       - Haberi TÜRKÇE olarak, en fazla 15 kelimeyle, SONUÇ ODAKLI özetle.
       - Asla "Haberde...", "Reuters'ın bildirdiğine göre..." deme. Direkt olayı yaz.
       - Örnek: "🚨 İsrail ve Lübnan arasında ateşkes antlaşması imzalandı."

    Haber Kaynağı: {source_url}
    Başlık (Orijinal): {title}
    İçerik (Orijinal): {clean_summary}
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

def send_push_notification(message, link, image_url=None):
    headers = {"Title": "Global Istihbarat", "Priority": "default", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    new_entries_count = 0
    print(f"Global Tarama Basliyor... Model: {ACTIVE_MODEL_NAME}")
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            # Her ajanstan sadece EN GUNCEL 1 haberi al (Kota dostu)
            for entry in feed.entries[:1]: 
                if is_spam_or_blocked(entry.title):
                    continue
                    
                if not is_duplicate(entry, history):
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    # AI Karar Veriyor + Turkceye ceviriyor
                    ai_result = summarize_news(entry.title, content, url)
                    
                    if ai_result == "SKIP":
                        print(f"Elenen Haber: {entry.title}")
                        history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                        continue

                    if ai_result == "KOTA_DOLDU":
                        send_push_notification("⚠️ Kota limitine takıldı.", "https://google.com")
                        break 

                    image_url = find_image_url(entry)
                    send_push_notification(ai_result, entry.link, image_url)
                    
                    history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                    new_entries_count += 1
                    
                    # Cok fazla kaynak var, API'yi yormamak icin beklemeyi artirdik
                    print("Diger kaynağa geçiliyor (10sn)...")
                    time.sleep(10) 
            
            if "KOTA_DOLDU" in locals().get('ai_result', ''): break
        except: continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
