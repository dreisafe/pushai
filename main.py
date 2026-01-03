import feedparser
import requests
import json
import os
import google.generativeai as genai
from datetime import datetime
from difflib import SequenceMatcher
import time

# --- AYARLAR ---
# Kendine ozel, tahmin edilemez bir topic ismi sec (Bunu telefondaki ntfy uygulamasina da gireceksin)
NTFY_TOPIC = "haber_akis_gizli_xyz_123" 
HISTORY_FILE = "history.json"
MAX_HISTORY_ITEMS = 200 # Dosya cok sismesin diye son 200 haberi tutar
SIMILARITY_THRESHOLD = 0.75 # %75 ve uzeri benzerlikte haber reddedilir

# RSS Kaynaklari (Listeyi genislettim)
RSS_URLS = [
    "https://feeds.bbci.co.uk/turkce/rss.xml", # BBC Turkce
    "https://rss.dw.com/xml/rss-tr-all",      # DW Turkce
    "https://tr.euronews.com/rss",             # Euronews
    "https://www.trthaber.com/sinema_xml.php", # TRT (Ornek kategori, ana akis degisebilir)
    "https://www.voaturkce.com/api/zqyqyepqqt", # VOA Turkce
    "https://tr.sputniknews.com/export/rss2/archive/index.xml" # Sputnik
]

# API Key Kontrolu
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY cevre degiskeni bulunamadi!")

genai.configure(api_key=GEMINI_API_KEY)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_history(history_data):
    # Sadece son N kaydi tut
    trimmed_data = history_data[-MAX_HISTORY_ITEMS:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed_data, f, ensure_ascii=False, indent=2)

def is_duplicate(entry, history):
    # 1. URL Kontrolu
    for item in history:
        if item['link'] == entry.link:
            return True
    
    # 2. Baslik Benzerlik Kontrolu (Semantic yakinlik icin basit text similarity)
    for item in history:
        similarity = SequenceMatcher(None, item['title'], entry.title).ratio()
        if similarity > SIMILARITY_THRESHOLD:
            print(f"Benzer haber elendi ({similarity:.2f}): {entry.title}")
            return True
    return False

def summarize_news(title, summary):
    # Gemini Flash Modeli
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Aşağıdaki haberi bir haber spikeri edasıyla, SON DAKİKA formatında, 
    ilgi çekici ve vurucu TEK BİR CÜMLE haline getir. 
    Türkçe karakter kurallarına uy.
    
    Haber Başlığı: {title}
    Haber İçeriği: {summary}
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini Hata: {e}")
        return title # Hata olursa orijinal basligi don

def send_push_notification(message, link):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers={
                "Title": "Gündem Özeti",
                "Priority": "default",
                "Click": link, # Bildirime tiklayinca habere gider
                "Tags": "rotating_light" # Ikon ekler
            }
        )
    except Exception as e:
        print(f"Bildirim Hatasi: {e}")

def main():
    history = load_history()
    new_entries_count = 0
    
    print("RSS Kaynaklari taraniyor...")
    
    # RSS'leri gez
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            # Her feed'den sadece en guncel 3 haberi kontrol et (Performans icin)
            for entry in feed.entries[:3]: 
                if not is_duplicate(entry, history):
                    print(f"Yeni Haber Bulundu: {entry.title}")
                    
                    # Gemini ile ozetle
                    # Bazi RSS'lerde 'summary' yoktur, 'description' vardir
                    content = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    ai_summary = summarize_news(entry.title, content)
                    
                    # Bildirim Gonder
                    send_push_notification(ai_summary, entry.link)
                    
                    # Gecmise kaydet
                    history.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": datetime.now().isoformat()
                    })
                    new_entries_count += 1
                    
                    # API limitlerine takilmamak icin kisa bekleme
                    time.sleep(2) 
                    
        except Exception as e:
            print(f"RSS Hatasi ({url}): {e}")
            continue

    if new_entries_count > 0:
        save_history(history)
        print(f"{new_entries_count} yeni haber islendi ve kaydedildi.")
    else:
        print("Yeni haber yok.")

if __name__ == "__main__":
    main()
