import feedparser
import asyncio
import time
from config import RSS_FEEDS

class RSSMonitor:
    def __init__(self, callback_func):
        self.callback = callback_func
        self.seen_links = set() # Oturum süresince linkleri hafızada tut (Hız için)
        self.is_running = False

    async def fetch_feed(self, url):
        try:
            # Feedparser senkron çalışır, bunu thread'e atıp asenkron yapalım
            feed = await asyncio.to_thread(feedparser.parse, url)
            
            for entry in feed.entries[:3]: # Her feed'in sadece en yeni 3 haberine bak
                link = entry.link
                title = entry.title
                summary = getattr(entry, 'summary', '')

                if hasattr(entry, 'published_parsed'):
                    published_time = time.mktime(entry.published_parsed)
                    current_time = time.time()
                    # 2 saatten (7200 sn) eski haberleri direkt çöpe at
                    if current_time - published_time > 60:
                        continue
                
                # Eğer bu linki daha önce görmediysek
                if link not in self.seen_links:
                    self.seen_links.add(link)
                    
                    # İlk açılışta eski haberleri bombardıman yapmasın diye
                    # sadece çok yeni (son 10 dk) haberleri alabiliriz.
                    # Ama şimdilik hepsini işleyelim, Memory modülü zaten eler.
                    
                    full_text = f"{title}. {summary}"
                    
                    # Main.py'daki process_news'i çağır
                    print(f"📡 [RSS] Yeni Haber: {title[:50]}...")
                    await self.callback(full_text, "RSS")
                    
        except Exception as e:
            print(f"⚠️ RSS Hatası ({url}): {e}")

    async def start_loop(self):
        print("📡 RSS Takibi Başlatıldı...")
        self.is_running = True
        
        # İlk açılışta var olanları "görüldü" işaretleyip işlememesi için
        # bir 'warm-up' turu atabilirsin ama duplicate check zaten var.
        
        while self.is_running:
            tasks = [self.fetch_feed(url) for url in RSS_FEEDS]
            await asyncio.gather(*tasks)
            
            # 60 saniye bekle (Çok sık sorma, IP ban yersin)
            await asyncio.sleep(60)