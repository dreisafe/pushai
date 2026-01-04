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
CONTEXT_WINDOW_SIZE = 15

# Python tarafındaki filtreyi (Blacklist) genişletiyoruz
BLOCKED_KEYWORDS = [
    # Spor & Magazin
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç", "astroloji", "survivor", "masterchef",
    "gelin evi", "kim milyoner", "royal family", "gossip", "kim kardashian", 
    "premier league", "piyango", "çekiliş", "yerel seçim", "muhtar", 
    # Teknoloji Çöplüğü
    "yeni telefon", "tanıttı", "lansman", "özellikleri sızdı", "fiyatı belli oldu", 
    "iphone", "android", "samsung", "inceleme",
    # Arkeoloji / Tarih / Gereksiz Bilim (Kremasyon Pyresi vb.)
    "arkeolojik", "kazı", "bulundu", "yıllık", "yıl önce", "antik", "fosil", "kemik", 
    "mezar", "lahit", "müze", "restorasyon", "keşfedildi", "tarihi eser", "dinozor"
]

RSS_SOURCES = [
    {"name": "Reuters World", "url": "http://feeds.reuters.com/reuters/worldNews"},
    {"name": "AP News", "url": "https://apnews.com/hub/world-news/feed"},
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss"},
    {"name": "CNBC World", "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html"}, 
    {"name": "BBC Turkce", "url": "https://feeds.bbci.co.uk/turkce/rss.xml"},
    {"name": "DW Turkce", "url": "https://rss.dw.com/xml/rss-tr-all"},     
    {"name": "Euronews TR", "url": "https://tr.euronews.com/rss"},            
    {"name": "VOA Turkce", "url": "https://www.voaturkce.com/api/zqyqyepqqt"},
]

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)

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
        if similarity > 0.75: return True
    return False

def find_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'jpg' in media.get('url', ''): return media['url']
    if 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''): return link['href']
    return None

def analyze_news_groq(title, summary, source_name, recent_history_titles):
    if not client: return "API_KEY_YOK"
    
    clean_summary = clean_html(summary)
    if len(clean_summary) < 10: clean_summary = title
    history_context = "\n".join([f"- {h}" for h in recent_history_titles])

    # PROMPT V3: KATI FİLTRELER VE BAĞLAM ZORUNLULUĞU
    prompt = f"""
    Rol: Askeri İstihbarat Analisti.

    KATİ YASAKLAR (BUNLARI GÖRÜRSEN SADECE "SKIP" YAZ):
    1. ARKEOLOJİ/TARİH: Eski mezar, kemik, antik kent, 5000 yıllık keşif, dinozor vb.
    2. SÖZLÜ EYLEMLER: Kınadı, endişeli, çağrı yaptı, sert çıktı, atıştı.
    3. MAGAZİN/SPOR/TEKNOLOJİ ÜRÜNLERİ.
    4. YEREL KAZALAR: Trafik kazası, küçük yangınlar (orman yangını veya stratejik sabotaj değilse).

    ÖZETLEME KURALLARI:
    1. "GÖTÜRÜLDÜ" YASAK: Bir devlet adamı veya kişi yer değiştiriyorsa STATÜSÜNÜ yazacaksın. (Örn: "Maduro New York'a götürüldü" DEME -> "Maduro TUTUKLANARAK New York'a iade edildi" DE).
    2. NEDEN SONUÇ İLİŞKİSİ: Olayın sebebini en kısa şekilde ekle.
    3. EMOJİ YOK. Sadece saf, ciddi Türkçe.
    4. Max 140 karakter.

    GÖREV:
    Haberi analiz et. Eğer stratejik, küresel veya ciddi bir kriz değilse SKIP yaz.
    Eğer önemliyse yukarıdaki kurallara göre özetle.

    --- VERİLER ---
    KAYNAK: {source_name}
    GEÇMİŞ (Tekrarı önle):
    {history_context}
    
    YENİ HABER:
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Sen sadece çok önemli stratejik gelişmeleri raporlayan bir botsun."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1, 
        )
        text = chat_completion.choices[0].message.content.strip()
        
        if "SKIP" in text or "skip" in text.lower():
            if len(text) < 15: return "SKIP"
        
        text = text.replace('"', '').replace("'", "")
        return text

    except Exception as e:
        return f"Hata: {str(e)[:20]}"

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
    print(f"--- Haber Taraması (V3 - General Mod): {datetime.now().strftime('%H:%M')} ---")
    
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries[:3]: 
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
                
                history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                recent_titles.append(entry.title)
                new_entries_count += 1
                
                print(f"✅ GÖNDERİLDİ: {ai_result}")
                time.sleep(5)
            
        except Exception: continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
