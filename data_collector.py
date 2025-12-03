# Gerekli import
import aiofiles # pip install aiofiles (Asenkron dosya yazma için şart)
import time 
import json
# ---------------------------------------------------------
# 5. DATA COLLECTOR (GELECEK İÇİN YATIRIM)
# ---------------------------------------------------------
class TrainingDataCollector:
    def __init__(self, filename="fine_tune_dataset.jsonl"):
        self.filename = filename
        self.pending_events = [] # Karar verildi, sonucu bekleniyor

    def log_decision(self, news, pair, initial_price, stats_1m, model_output):
        """
        Bot bir karar verdiğinde bunu bekleme listesine al.
        """
        event = {
            "timestamp": time.time(),
            "news": news,
            "pair": pair,
            "entry_price": initial_price,
            "stats_1m": stats_1m,
            "model_output": model_output, # Botun ürettiği JSON
            "check_time": time.time() + 900 # 15 dakika (900 sn) sonra kontrol et
        }
        self.pending_events.append(event)
        return f"💾 Veri Kaydedildi: Sonuç 15dk sonra kontrol edilecek.", "info"

    async def check_outcomes(self, current_prices):
        """
        Bekleyen olayların süresi doldu mu diye bakar.
        Dolduysa, fiyat hareketine göre 'Ground Truth' oluşturur.
        """
        completed = []
        now = time.time()

        for event in self.pending_events:
            # Henüz zamanı gelmediyse geç
            if now < event['check_time']:
                continue

            pair = event['pair']
            if pair not in current_prices: continue # Fiyat yoksa geç

            exit_price = current_prices[pair]
            entry_price = event['entry_price']
            
            # Gerçekleşen Değişim (%)
            actual_change = ((exit_price - entry_price) / entry_price) * 100
            
            # --- LABELING LOGIC (ETİKETLEME MANTIĞI) ---
            # Burası çok önemli. Hangi hareket "BUY" sinyali olmalıydı?
            
            ideal_action = "HOLD"
            reason = "Price remained stable."
            
            if actual_change > 1.0: # %1'den fazla arttıysa -> BUY olmalıydı
                ideal_action = "LONG"
                reason = f"Price pumped {actual_change:.2f}% in 15m."
            elif actual_change < -1.0: # %1'den fazla düştüyse -> SELL olmalıydı
                ideal_action = "SHORT"
                reason = f"Price dumped {actual_change:.2f}% in 15m."
            
            # Eğitim Verisi Formatı (Alpaca / Chat Format)
            training_entry = {
                "instruction": f"Analyze this crypto news for {pair}. Price is {entry_price}, 1m change is {event['stats_1m']}%. Return JSON.",
                "input": event['news'],
                "output": json.dumps({
                    "action": ideal_action,
                    "confidence": 100,
                    "reason": reason
                })
            }
            
            # Sadece anlamlı hareketleri kaydet (HOLD verisi çok şişirmesin)
            if ideal_action != "HOLD":
                async with aiofiles.open(self.filename, mode='a', encoding='utf-8') as f:
                    await f.write(json.dumps(training_entry) + "\n")
                return f"💎 EĞİTİM VERİSİ KAYDEDİLDİ: {pair.upper()} -> {ideal_action}", "success"
            
            completed.append(event)

        # İşlenenleri listeden sil
        for c in completed:
            self.pending_events.remove(c)

# Global Nesne