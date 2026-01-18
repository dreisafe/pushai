import feedparser
import requests
import json
import os
from groq import Groq
from datetime import datetime
from difflib import SequenceMatcher
import time
import re
import html

# --- AYARLAR ---
NTFY_TOPIC = "haber_akis_gizli_xyz_123" 
HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 300 
CONTEXT_WINDOW_SIZE = 20

# --- KATILAŞTIRILMIŞ FİLTRE LİSTESİ (V6) ---
BLOCKED_KEYWORDS = [
    # Spor & Magazin
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "premier league", "nba", "gol", "transfer", "kupa", "madalya",
    "magazin", "ünlü", "aşk", "sevgili", "boşanma", "nafaka", "gelin evi", 
    "kim milyoner", "masterchef", "survivor", "gossip", "royal family", "kardashian",
    # Teknoloji Çöplüğü & Ürün Tanıtımı
    "yeni telefon", "tanıttı", "lansman", "özellikleri sızdı", "fiyatı", "iphone", 
    "android", "samsung", "inceleme", "kutu açılışı", "güncelleme geldi",
    # Arkeoloji / Tarih / Bilimsel "Soft" Haberler
    "arkeolojik", "kazı", "bulundu", "yıllık", "yıl önce", "antik", "fosil", "kemik", 
    "mezar", "lahit", "müze", "restorasyon", "keşfedildi", "tarihi eser", "dinozor", 
    "kremasyon", "pyre", "sunak", "tapınak", "mozaik", "kabile", "amazon ormanları",
    # "Soft" İçerik: Röportaj, Tavsiye, Belgesel, Analiz (Buffett/Fridman Engeli)
    "röportaj", "açıklamalarda bulundu", "tavsiye", "öneri", "ipucu", "analiz etti",
    "belgesel", "görüntüledi", "yayınladı", "sohbet", "podcast", "yorumladı",
    "neden", "niçin", "nasıl yapılır", "ebeveynlik", "bahis", "at yarışı", "borsa yorumu"
]

RSS_SOURCES = [
    {"name": "Reuters", "url": "http://feeds.reuters.com/reuters/worldNews"},
    {"name": "AP News", "url": "https://apnews.com/hub/world-news/feed"},
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    # The Guardian ve CNBC cok fazla "feature" (yumusak) haber giriyor, onlari eledim veya dikkatli olmali
    # {"name": "Guardian", "url": "https://www.theguardian.com/world/rss"}, 
    {"name": "BBC TR", "url": "https://feeds.bbci.co.uk/turkce/rss.xml"},
    {"name": "DW TR", "url": "https://rss.dw.com/xml/rss-tr-all"},     
    {"name": "Euronews", "url": "https://tr.euronews.com/rss"},            
    {"name": "VOA", "url": "https://www.voaturkce.com/api/zqyqyepqqt"},
]

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)

session_sent_summaries = []

def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = html.unescape(cleantext)
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

def is_duplicate_basic(entry, history):
    for item in history:
        if item['link'] == entry.link: return True
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > 0.70: return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''): return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''): return link['href']
    return None

# --- V6: KIRMIZI ALARM ANALİZ MODU ---
def analyze_news_groq(title, summary, source_name, recent_history_titles):
    if not client: return "API_KEY_YOK"
    
    clean_summary = clean_html(summary)
    if len(clean_summary) < 10: clean_summary = title
    
    context_list = recent_history_titles + session_sent_summaries
    history_context = "\n".join([f"- {h}" for h in context_list[-20:]])

    prompt = f"""
    Sen bir "SON DAKİKA MASASI" Şefisin. Görevin gereksiz her şeyi elemek.
    
    FİLTRE (BUNLARI GÖRÜRSEN DİREKT "SKIP" YAZ):
    1. RÖPORTAJ/GÖRÜŞ: "X kişi şunun hakkında konuştu", "Analiz etti", "Tavsiye verdi". (Örn: Buffett, Lex Fridman, Elon Musk konuştu -> SKIP).
    2. BELGESEL/BİLİM: "Amazon kabilesi görüntülendi", "Yeni araştırma yapıldı".
    3. RUTİN SİYASET: "Seçim çalışmaları başladı", "Meclis toplandı" (Sonuç yoksa SKIP).
    4. YEREL/KÜÇÜK: Sadece Dünyayı veya Türkiye'yi sarsacak olaylar kalsın.
    
    KABUL EDİLENLER (SADECE BUNLAR):
    - Savaş ilanı, Ateşkes, Büyük Saldırı.
    - Büyük Deprem (>6.0), Tsunami, Felaket.
    - Ekonomik Çöküş, İflas, Dev Şirket Satışı.
    - Suikast, Darbe, Tutuklama (Devlet Başkanı seviyesi).
    
    YAZIM TARZI (TELGRAF STİLİ):
    - ASLA uzun cümle kurma. Bağlaçları at.
    - Max 10-12 kelime.
    - Örnek: "İtalya Başbakanı Meloni ile Trump görüştü..." DEME -> "Meloni ve Trump, gümrük vergilerini görüştü." DE.
    - Örnek: "ABD Başkanı Biden Ukrayna'ya yardımı onayladı" -> "Biden, Ukrayna'ya 2 milyar dolarlık yardımı onayladı."
    
    --- GEÇMİŞ KONTROLÜ (TEKRARSA SKIP YAZ) ---
    {history_context}
    
    --- HABER ---
    Kaynak: {source_name}
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "Sen sadece son dakika eylemlerini bildiren, gevezelik etmeyen bir botsun."},
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.1, # Cok dusuk yaraticilik = Sadece gercekler
            )
            text = chat_completion.choices[0].message.content.strip()
            
            if "SKIP" in text or "skip" in text.lower():
                if len(text) < 15: return "SKIP"
            
            if text.isupper(): text = text.capitalize()
            # Gereksiz "Son Dakika:" gibi on ekleri de temizle
            text = text.replace("Son dakika:", "").replace("SON DAKİKA:", "").strip()
            
            return text.replace('"', '').replace("'", "")

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                print(f"⚠️ Hız Sınırı (429). {60} saniye bekleniyor...")
                time.sleep(60) 
                continue 
            else:
                return "SKIP"
    
    return "SKIP"

def send_push_notification(message, link, source_name, image_url=None):
    headers = {"Title": source_name, "Priority": "high", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    recent_titles = [item['title'] for item in history[-CONTEXT_WINDOW_SIZE:]]
    new_entries_count = 0
    print(f"--- Haber Taraması (V6 - Kırmızı Alarm): {datetime.now().strftime('%H:%M')} ---")
    
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:2]: 
                
                if is_spam_or_blocked(entry.title): continue
                if is_duplicate_basic(entry, history): continue
                
                content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                ai_result = analyze_news_groq(entry.title, content, source["name"], recent_titles)
                
                if ai_result == "SKIP":
                    print(f"Elenen ({source['name']}): {entry.title[:30]}...")
                    continue
                
                if ai_result == "API_KEY_YOK": break

                image_url = find_image_url(entry)
                send_push_notification(ai_result, entry.link, source["name"], image_url)
                
                timestamp = datetime.now().isoformat()
                history.append({"title": entry.title, "link": entry.link, "date": timestamp})
                recent_titles.append(entry.title) 
                session_sent_summaries.append(ai_result) 
                new_entries_count += 1
                
                print(f"✅ {ai_result}")
                time.sleep(3)
            
        except Exception: continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
