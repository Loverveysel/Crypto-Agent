from collections import deque

class PriceBuffer:
    def __init__(self):
        # Sadece son 60 dakikanın kapanış fiyatlarını tutar.
        # Her eleman: (timestamp_minute, close_price)
        self.candles = deque(maxlen=60) 
        self.current_price = 0.0
        self.change_24h = 0.0 # Binance'den hazır gelecek

    def update_candle(self, price, timestamp, is_closed):
        """
        Websocket'ten gelen mum verisini işler.
        is_closed: Mum kapandı mı? (True ise listeye ekle, False ise sadece anlık fiyatı güncelle)
        """
        self.current_price = price
        
        # Eğer mum kapandıysa listeye kalıcı olarak ekle (Tarihçeyi oluştur)
        if is_closed:
            # Dakikayı yuvarla (Timestamp -> Dakika)
            minute_ts = int(timestamp / 60)
            
            # Eğer son eklenen veri bu dakika değilse ekle (Çift eklemeyi önle)
            if not self.candles or self.candles[-1][0] != minute_ts:
                self.candles.append((minute_ts, price))

    def set_24h_change(self, percent):
        self.change_24h = percent

    def get_change(self, minutes):
        """
        Geçmişe bakıp yüzde değişimini hesaplar.
        minutes: 1, 10, 60 gibi.
        """
        if not self.candles or self.current_price == 0:
            return 0.0
            
        # Hedeflenen eski fiyatı bulmaya çalış
        # Listemiz [(ts, price), (ts, price)...] şeklinde sondan başa doğru
        # Şu anki zamandan 'minutes' kadar geriye gitmemiz lazım ama 
        # en basiti listenin sonundan 'minutes' kadar gerideki elemanı almak.
        
        if len(self.candles) < minutes:
            # Yeterli veri yoksa en eski veriyi kullan
            old_price = self.candles[0][1]
        else:
            # [-1] son dakika, [-minutes] istenen dakika
            old_price = self.candles[-minutes][1]
            
        if old_price == 0: return 0.0
        
        return ((self.current_price - old_price) / old_price) * 100
    
    def get_all_changes(self):
        """Tüm periyotları toplu döndürür"""
        return {
            "1m": self.get_change(1),
            "10m": self.get_change(10),
            "1h": self.get_change(60),
            "24h": self.change_24h
        }