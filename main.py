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
CONTEXT_WINDOW_SIZE = 20 # YZ'nin hatırlayacağı son haber sayısı

# --- KATILAŞTIRILMIŞ FİLTRE LİSTESİ ---
BLOCKED_KEYWORDS = [
    # Spor
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "premier league", "nba", "gol", "transfer",
    # Magazin / Boş İçerik
    "magazin", "ünlü oyuncu", "aşk", "sevgili", "boşanma", "nafaka", "gelin evi", 
    "kim milyoner", "masterchef", "survivor", "gossip", "royal family", "kardashian",
    # Teknoloji Çöplüğü
    "yeni telefon", "tanıttı", "lansman", "özellikleri sızdı", "fiyatı", "iphone", 
    "android", "samsung", "inceleme", "kutu açılışı",
    # Arkeoloji / Tarih (SENİN İÇİN ÖZEL)
    "arkeolojik", "kazı", "bulundu", "yıllık", "yıl önce", "antik", "fosil", "kemik", 
    "mezar", "lahit", "müze", "restorasyon", "keşfedildi", "tarihi eser", "dinozor", 
    "kremasyon", "pyre", "sunak", "tapınak", "mozaik"
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

# Bu oturumda gönderilen haberlerin özeti (Anlık Spam Koruması)
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
    # Link kontrolü
    for item in history:
        if item['link'] == entry.link: return True
        # Başlık benzerliği (%70 üzeri benzerse at)
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

def analyze_news_groq(title, summary, source_name, recent_history_titles):
    if not client: return "API_KEY_YOK"
    
    clean_summary = clean_html(summary)
    if len(clean_summary) < 10: clean_summary = title
    
    # Geçmiş bağlamına hem veritabanını hem de bu oturumda az önce gönderilenleri ekle
    # Bu, "Maduro" spam'ini engelleyen kilit nokta
    context_list = recent_history_titles + session_sent_summaries
    history_context = "\n".join([f"- {h}" for h in context_list[-20:]])

    prompt = f"""
    Rol: Kıdemli İstihbarat Subayı.
    
    GÖREV: Gelen istihbaratı analiz et ve SADECE STRATEJİK ÖNEMİ varsa raporla.
    
    KURAL 1: TEKRAR ENGELLEME (CRITICAL)
    Aşağıdaki "GEÇMİŞ RAPORLAR" listesine bak. Eğer yeni haber, listedeki bir olayla AYNIYSA (farklı kaynak olsa bile, örn: BBC yazdı şimdi Reuters yazıyor), KESİNLİKLE "SKIP" YAZ.
    
    KURAL 2: FİLTRELEME
    - Arkeoloji, Tarih, Magazin, Spor -> SKIP.
    - Siyasi laf dalaşı (Kınadı, Çağrı yaptı) -> SKIP.
    - Sadece EYLEM odaklı ol (Saldırı, İflas, Tutuklama, Anlaşma).
    
    KURAL 3: RAPOR DİLİ
    - "Götürüldü", "Alındı" gibi pasif diller YASAK. Askeri/Resmi dil kullan: "TUTUKLANDI", "İADE EDİLDİ", "ELE GEÇİRİLDİ".
    - EMOJİ KESİNLİKLE YOK.
    - Konuyu max 140 karaktere sığdır.
    
    --- VERİLER ---
    KAYNAK: {source_name}
    GEÇMİŞ RAPORLAR (Bunların aynısıysa SKIP ver):
    {history_context}
    
    YENİ İSTİHBARAT:
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Sen tekrarı sevmeyen, sadece eylem odaklı, ciddi bir botsun."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1, # Yaratıcılık minimumda olsun ki halüsinasyon görmesin
        )
        text = chat_completion.choices[0].message.content.strip()
        
        if "SKIP" in text or "skip" in text.lower():
            if len(text) < 15: return "SKIP"
        
        return text.replace('"', '').replace("'", "")

    except Exception as e:
        return f"Hata: {str(e)[:20]}"

def send_push_notification(message, link, source_name, image_url=None):
    # Başlıkta sadece Kaynak var. İçerik tamamen mesajda.
    headers = {"Title": source_name, "Priority": "high", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    # Son 20 haberin sadece özet metnini/başlığını al
    # Burada 'title' yerine kaydedilmiş YZ özetlerini kullanmak daha iyi olurdu ama
    # basitlik adına title kullanıyoruz, YZ bunu anlayacaktır.
    recent_titles = [item['title'] for item in history[-CONTEXT_WINDOW_SIZE:]]
    
    new_entries_count = 0
    print(f"--- Haber Taraması (V4 - Anti-Spam Modu): {datetime.now().strftime('%H:%M')} ---")
    
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            # Sadece son 2 habere bak, 3 çok fazla tekrar riski yaratıyor
            for entry in feed.entries[:2]: 
                
                # 1. Aşama: Python Filtreleri (Hızlı Eleme)
                if is_spam_or_blocked(entry.title): 
                    # print(f"Bloklandı: {entry.title}") 
                    continue
                if is_duplicate_basic(entry, history): continue
                
                # 2. Aşama: YZ Analizi (Pahalı ve Akıllı Eleme)
                content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                
                # recent_titles'a session_sent_summaries'i de dahil ediyoruz
                ai_result = analyze_news_groq(entry.title, content, source["name"], recent_titles)
                
                if ai_result == "SKIP":
                    print(f"Elenen ({source['name']}): {entry.title[:40]}...")
                    continue
                
                if ai_result == "API_KEY_YOK": break

                # 3. Aşama: Gönderim ve Kayıt
                image_url = find_image_url(entry)
                send_push_notification(ai_result, entry.link, source["name"], image_url)
                
                # Kayıtlar
                timestamp = datetime.now().isoformat()
                history.append({"title": entry.title, "link": entry.link, "date": timestamp})
                recent_titles.append(entry.title) # Döngü içi güncelleme
                session_sent_summaries.append(ai_result) # Anlık oturum hafızası (Spam önleyici)
                new_entries_count += 1
                
                print(f"✅ GÖNDERİLDİ: {ai_result}")
                time.sleep(3) # Kaynaklar arası kısa bekleme
            
        except Exception as e:
            # print(f"Hata: {e}")
            continue

    if new_entries_count > 0: save_history(history)

if __name__ == "__main__":
    main()
