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
NTFY_TOPIC = "haber_akis_gizli_xyz_123"  # <-- KANAL ADINI GUNCELLEMEYI UNUTMA
HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 300 
CONTEXT_WINDOW_SIZE = 15  # YZ'ye gonderilecek "son gonderilen haberler" sayisi

# İstenmeyen kelimeler (Basit filtre)
BLOCKED_KEYWORDS = [
    "süper lig", "maç sonucu", "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
    "magazin", "ünlü oyuncu", "aşk iddiası", "burç", "astroloji", "survivor", "masterchef",
    "gelin evi", "kim milyoner", "royal family", "gossip", "kim kardashian", 
    "premier league", "piyango", "çekiliş", "yerel seçim", "muhtar", "kaza", "yaralı"
]

RSS_SOURCES = [
    # --- GLOBAL DEVLER (EN HIZLI VE GUCLU OLANLAR) ---
    {"name": "Reuters World", "url": "http://feeds.reuters.com/reuters/worldNews"},
    {"name": "AP News", "url": "https://apnews.com/hub/world-news/feed"},
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss"},
    {"name": "CNBC World", "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html"}, # Ekonomi/Finans
    
    # --- TURKCE KAYNAKLAR ---
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

# 1. Aşama: Basit Metin Benzerliği (Hızlı Eleme)
def is_duplicate_basic(entry, history):
    for item in history:
        if item['link'] == entry.link: return True
        # Başlıklar %75 benziyorsa direkt ele (YZ'ye sorma, maliyeti düşür)
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

# 2. Aşama: Yapay Zeka ile Analiz ve Dedublikasyon
def analyze_news_groq(title, summary, source_name, recent_history_titles):
    if not client: return "API_KEY_YOK"
    
    clean_summary = clean_html(summary)
    if len(clean_summary) < 10: clean_summary = title

    # Son gönderilen haberlerin listesini prompt'a ekliyoruz
    history_context = "\n".join([f"- {h}" for h in recent_history_titles])

    prompt = f"""
    Sen Üst Düzey bir Haber İstihbarat Analistisin.
    
    GÖREV 1: TEKRAR KONTROLÜ
    Aşağıdaki "GEÇMİŞ HABERLER" listesine bak. Eğer "YENİ HABER", geçmişteki bir haberin AYNISIYSA (farklı kaynak olsa bile içerik aynıysa), sadece "SKIP" yaz.
    Ancak, eğer olayda YENİ bir gelişme varsa (örneğin: ölü sayısı arttı, yeni bir açıklama geldi), o zaman SKIP yazma, haberi işle.

    GÖREV 2: ÖNEM ANALİZİ
    Eğer haber tekrar değilse, içeriği analiz et.
    SADECE şu kriterlere uyuyorsa çevir:
    - Uluslararası krizler, savaşlar, büyük jeopolitik olaylar.
    - Büyük ekonomik çöküşler veya ani piyasa hareketleri.
    - Büyük çaplı doğal afetler.
    - Teknoloji veya bilimde devrimsel nitelikteki "Breaking" gelişmeler.
    
    Şunları KESİNLİKLE "SKIP" ile ele:
    - Magazin, spor skorları, yerel adli vakalar, köşe yazıları, fikir beyanları.

    ÇIKTI FORMATI:
    Eğer haber önemli ve yeniyse:
    [EMOJİ] [Olayı özetleyen net TÜRKÇE cümle, max 15 kelime]
    
    Eğer önemsiz veya tekrarsa:
    SKIP

    --- VERİLER ---
    KAYNAK: {source_name}
    GEÇMİŞ HABERLER (Bunlara bakarak tekrarı önle):
    {history_context}
    
    YENİ HABER:
    Başlık: {title}
    İçerik: {clean_summary}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Sen sıkı bir filtreye sahip Türkçe haber asistanısın."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1, # Daha tutarlı olması için düşürdük
        )
        text = chat_completion.choices[0].message.content.strip()
        
        if "SKIP" in text: return "SKIP"
        return text

    except Exception as e:
        return f"⚠️ Groq Hatası: {str(e)[:50]}..."

def send_push_notification(message, link, source_name, image_url=None):
    headers = {"Title": f"{source_name}", "Priority": "high", "Click": link}
    if image_url: headers["Attach"] = image_url
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode('utf-8'), headers=headers)
    except: pass

def main():
    history = load_history()
    # YZ'ye bağlam olarak göndermek için son haberlerin sadece başlıklarını alalım
    recent_titles = [item['title'] for item in history[-CONTEXT_WINDOW_SIZE:]]
    
    new_entries_count = 0
    print(f"--- Haber Taraması Başladı: {datetime.now().strftime('%H:%M:%S')} ---")
    
    for source in RSS_SOURCES:
        url = source["url"]
        name = source["name"]
        
        try:
            feed = feedparser.parse(url)
            # Her kaynaktan son 3 habere bak (Belki 2. haber daha önemlidir veya yenidir)
            for entry in feed.entries[:3]: 
                
                # 1. Hızlı Filtreler (Spam ve Basit Tekrar)
                if is_spam_or_blocked(entry.title): continue
                if is_duplicate_basic(entry, history): continue
                
                # İçerik hazırlığı
                content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                
                # 2. Akıllı YZ Analizi (Semantik Tekrar ve Önem Kontrolü)
                ai_result = analyze_news_groq(entry.title, content, name, recent_titles)
                
                if ai_result == "SKIP":
                    # YZ bunun tekrar veya önemsiz olduğuna karar verdi.
                    # Tekrar sormamak için geçmişe eklemiyoruz, çünkü belki ilerde önemli bir gelişme olur.
                    # Ancak "Processed" listesine eklenebilir. Basitlik adına geçiyoruz.
                    print(f"Elenen ({name}): {entry.title[:40]}...")
                    continue
                
                if ai_result == "API_KEY_YOK":
                    send_push_notification("⚠️ Groq API Key Eksik", "", "Sistem")
                    return # Döngüyü kır

                # Haber Onaylandı -> Gönder
                image_url = find_image_url(entry)
                send_push_notification(ai_result, entry.link, name, image_url)
                
                # Geçmişe kaydet
                history.append({"title": entry.title, "link": entry.link, "date": datetime.now().isoformat()})
                recent_titles.append(entry.title) # Anlık döngü için listeyi de güncelle
                new_entries_count += 1
                
                print(f"✅ GÖNDERİLDİ: {ai_result}")
                time.sleep(5) # API rate limit yememek için kısa bekleme
            
        except Exception as e:
            print(f"Hata ({name}): {e}")
            continue

    if new_entries_count > 0: save_history(history)
    print("--- Tarama Bitti ---")

if __name__ == "__main__":
    main()
