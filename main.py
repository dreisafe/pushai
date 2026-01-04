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
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # <-- KENDI KANAL ADINI YAZMAYI UNUTMA!

HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200
SIMILARITY_THRESHOLD = 0.70 

# GENISLETILMIS YASAKLI KELIMELER (İstemediğin her şeyi buraya ekle)
BLOCKED_KEYWORDS = [
    # Spor & Magazin
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç", "astroloji", "survivor", "masterchef",
    # Gereksiz 3. Sayfa Haberleri
    "yangın", "kaza", "trafik kazası", "yaralı", "ceset", "cinayet", "kavga",
    "hava durumu", "sağanak", "kar yağışı", "baraj doluluk",
    # Analiz ve Clickbait
    "kimdir?", "nedir?", "analiz:", "tarihçesi", "hayatı", "fiyatı ne kadar?"
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

# --- AKILLI MODEL SECICISI ---
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
    return cleantext[:1500]

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
    for item in history:
        if item['link'] == entry.link: return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''): return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''): return link['href']
    return None

def summarize_news(title, summary):
    clean_summary = clean_html(summary)
    
    # YENI PROMPT: ACIMASIZ EDITOR MODU
    prompt = f"""
    Sen dünyanın en titiz haber editörüsün. Görevin gereksiz haberleri elemek.

    KURALLAR:
    1. Eğer haber şu kategorilerdense SADECE "SKIP" YAZ (Başka hiçbir şey yazma):
       - Geçmiş tarihli analizler (Örn: "Bitcoin 17 yılda nasıl yükseldi?")
       - Genel kültür yazıları, Biyografiler ("Maduro kimdir?")
       - Yerel, küçük çaplı kazalar veya yangınlar.
       - Sadece görüş bildiren köşe yazıları.

    2. Eğer haber ÖNEMLİ ve SICAK BİR GELİŞME ise:
       - Net bir EMOJİ ile başla.
       - Haberi "X Kişisi, Y Kişisini TUTUKLADI/İADE ETTİ/VURDU" şeklinde FİİL kullanarak anlat.
       - "Götürdü", "Getirdi" gibi muğlak kelimeler kullanma. Net ol.
       - Maksimum 15 kelime kullan.

    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        model = genai.GenerativeModel(ACTIVE_MODEL_NAME)
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # AI "SKIP" dediyse biz de kod icinde bunu yakalayacagiz
        if "SKIP" in text:
            return "SKIP"
            
        return text
            
    except Exception as e:
        if "429" in str(e): return "KOTA_DOLDU"
        return f"⚠️ Hata: {str(e)[:30]}..."

def send_push_notification(message, link, image_url=None):
    headers = {"Title": "Gundem Ozeti", "Priority": "default", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    new_entries_count = 0
    print(f"Acimasiz Editor Modu Aktif... Model: {ACTIVE_MODEL_NAME}")
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:1]: # Sadece en guncel habere bak
                if is_spam_or_blocked(entry.title):
                    continue
                    
                if not is_duplicate(entry, history):
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    
                    # AI Karar Veriyor
                    ai_result = summarize_news(entry.title, content)
                    
                    # Eger AI "Bu haber cop" dediyse pas gec
                    if ai_result == "SKIP":
                        print(f"Cop haber atlandi: {entry.title}")
                        # Cop olsa bile history'ye ekleyelim ki surekli karsimiza cikmasin
                        history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                        continue

                    if ai_result == "KOTA_DOLDU":
                        send_push_notification("⚠️ Kota limitine takıldı.", "https://google.com")
                        break 

                    # Kaliteli haber bulundu!
                    image_url = find_image_url(entry)
                    send_push_notification(ai_result, entry.link, image_url)
                    
                    history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                    new_entries_count += 1
                    time.sleep(12) 
            
            if "KOTA_DOLDU" in locals().get('ai_result', ''): break
        except: continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
