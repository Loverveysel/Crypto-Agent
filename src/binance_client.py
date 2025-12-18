from binance import AsyncClient
from binance.enums import *
import math

class BinanceExecutionEngine:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.client = None
        self.symbol_info = {} 

    async def connect(self):
        try:
            self.client = await AsyncClient.create(self.api_key, self.api_secret, testnet=self.testnet)
            info = await self.client.futures_exchange_info()
            for s in info['symbols']:
                filters = {f['filterType']: f for f in s['filters']}
                try:
                    # MIN_NOTIONAL filtresini de çekiyoruz
                    min_notional = 5.0 # Varsayılan (Altcoinler için genelde 5$)
                    if 'MIN_NOTIONAL' in filters:
                        min_notional = float(filters['MIN_NOTIONAL']['notional'])
                    
                    self.symbol_info[s['symbol'].lower()] = {
                        'stepSize': float(filters['LOT_SIZE']['stepSize']),
                        'tickSize': float(filters['PRICE_FILTER']['tickSize']),
                        'minQty': float(filters['LOT_SIZE']['minQty']),
                        'minNotional': min_notional # <--- YENİ EKLENDİ
                    }
                except: continue
            env = "TESTNET" if self.testnet else "MAINNET"
            print(f"✅ [{env}] Borsa Bağlantısı Başarılı.")
        except Exception as e:
            print(f"❌ [BORSA HATASI] {e}")

    def _get_precision(self, size):
        if size == 0: return 0
        return int(round(-math.log(size, 10), 0))

    def _round_step(self, quantity, step_size):
        """Miktarı step size'a göre aşağı yuvarlar (Floor)"""
        if step_size == 0: return quantity
        precision = self._get_precision(step_size)
        qty = int(quantity / step_size) * step_size
        return float(f"{qty:.{precision}f}")

    def _ceil_step(self, quantity, step_size):
        """Miktarı step size'a göre YUKARI yuvarlar (Ceiling) - Notional için gerekli"""
        if step_size == 0: return quantity
        precision = self._get_precision(step_size)
        qty = math.ceil(quantity / step_size) * step_size
        return float(f"{qty:.{precision}f}")

    def _round_price(self, price, tick_size):
        """Fiyatı tick size'a göre en yakına yuvarlar"""
        if tick_size == 0: return price
        precision = self._get_precision(tick_size)
        price = round(price / tick_size) * tick_size
        return float(f"{price:.{precision}f}")

    async def execute_trade(self, symbol, side, amount_usdt, leverage, tp_pct, sl_pct):
        if not self.client: return
        sym = symbol.upper()
        sym_lower = symbol.lower()
        
        try:
            # 1. Kaldıraç ve Fiyat
            await self.client.futures_change_leverage(symbol=sym, leverage=leverage)
            ticker = await self.client.futures_symbol_ticker(symbol=sym)
            current_market_price = float(ticker['price'])
            
            # 2. Temel Miktar Hesapla
            raw_qty = (amount_usdt * leverage) / current_market_price
            
            step_size = self.symbol_info[sym_lower]['stepSize']
            min_qty = self.symbol_info[sym_lower]['minQty']
            min_notional = self.symbol_info[sym_lower]['minNotional'] # 100 USDT vb.
            
            # Yuvarla
            qty = self._round_step(raw_qty, step_size)
            
            # --- KONTROL 1: ADET SINIRI ---
            if qty < min_qty:
                print(f"⚠️ Miktar ({qty}) min_qty ({min_qty}) altında. Yükseltiliyor.")
                qty = min_qty
            
            # --- KONTROL 2: TUTAR SINIRI (YENİ) ---
            current_notional_value = qty * current_market_price
            
            if current_notional_value < min_notional:
                print(f"⚠️ Tutar ({current_notional_value:.2f}$) min_notional ({min_notional}$) altında. Zorlanıyor...")
                
                # Hedef tutara ulaşmak için gereken miktar
                required_qty = min_notional / current_market_price
                
                # Yukarı yuvarla ki sınırın biraz üstünde olsun (100.01 gibi)
                qty = self._ceil_step(required_qty * 1.01, step_size) # %1 güvenli pay ekle
                
                print(f"✅ Yeni Miktar: {qty} (Tahmini Tutar: {qty * current_market_price:.2f}$)")

            # 3. İşlemi Aç
            side_enum = SIDE_BUY if side == 'LONG' else SIDE_SELL
            order = await self.client.futures_create_order(
                symbol=sym, side=side_enum, type=ORDER_TYPE_MARKET, quantity=qty
            )
            
            # Gerçekleşen fiyatı al
            filled_price = float(order.get('avgPrice', 0.0))
            entry_price = filled_price if filled_price > 0 else current_market_price
            
            # 4. TP/SL Yerleştir
            try:
                await self._place_tp_sl(sym, side, entry_price, tp_pct, sl_pct)
                print(f"🚀 [API] {sym} {side} @ {entry_price} (Miktar: {qty})")
            except Exception as e:
                return "TP/SL Yerleştirme Hatası"
            
            return "Pozisyon açıldı" # Her şey mükemmel        
        except Exception as e: 
            print(f"❌ [API HATA] {e}")
            return "Pozisyon Açma Hatası"


    async def _place_tp_sl(self, symbol, side, entry, tp_pct, sl_pct):
        try:
            tick = self.symbol_info[symbol.lower()]['tickSize']
            
            # Yön Belirleme
            if side == 'LONG':
                tp_raw = entry * (1 + tp_pct/100)
                sl_raw = entry * (1 - sl_pct/100)
                close_side = 'SELL' # String olarak gönderiyoruz
            else: # SHORT
                tp_raw = entry * (1 - tp_pct/100)
                sl_raw = entry * (1 + sl_pct/100)
                close_side = 'BUY' # String olarak gönderiyoruz

            # Negatif fiyat koruması (Matematiksel Güvenlik)
            if tp_raw <= tick: tp_raw = entry + (tick * 10) if side=='LONG' else entry - (tick * 10)
            if sl_raw <= tick: sl_raw = entry - (tick * 10) if side=='LONG' else entry + (tick * 10)

            # Yuvarlama
            tp = self._round_price(tp_raw, tick)
            sl = self._round_price(sl_raw, tick)
            
            print(f"🛡️ TP/SL Hesaplanıyor: TP={tp} | SL={sl}")

            # --- STOP LOSS EMRI (STOP_MARKET) ---
            # closePosition=True dediğimiz için miktar (quantity) göndermiyoruz.
            # workingType='MARK_PRICE' iğnelerden korur.
            await self.client.futures_create_algo_order(
                symbol=symbol, 
                side=close_side, 
                type='STOP_MARKET', 
                triggerPrice=sl, # <--- BURASI DEĞİŞTİ
                closePosition=True, 
                workingType='MARK_PRICE',
                algoType='CONDITIONAL'
            )
            
            # --- TAKE PROFIT EMRI (ALGO ENDPOINT) ---
            # DÜZELTME: stopPrice -> triggerPrice
            await self.client.futures_create_algo_order(
                symbol=symbol, 
                side=close_side, 
                type='TAKE_PROFIT_MARKET', 
                triggerPrice=tp, # <--- BURASI DEĞİŞTİ
                closePosition=True, 
                workingType='MARK_PRICE',
                algoType='CONDITIONAL'
            )
            
            print(f"✅ [API] TP/SL Yerleştirildi ({symbol})")

        except Exception as e: 
            print(f"⚠️ [TP/SL HATASI] {e}")
            # Hata detayını görmek için (Opsiyonel):
            # print(f"Hata Detayı: {e.message if hasattr(e, 'message') else e}")

    async def close(self):
        if self.client: await self.client.close_connection()
    
    async def close_position_market(self, symbol):
        if not self.client: return
        sym = symbol.upper()
        try:
            await self.client.futures_cancel_all_open_orders(symbol=sym)
            positions = await self.client.futures_position_information(symbol=sym)
            for p in positions:
                amt = float(p['positionAmt'])
                if amt != 0:
                    side = SIDE_SELL if amt > 0 else SIDE_BUY
                    await self.client.futures_create_order(symbol=sym, side=side, type=ORDER_TYPE_MARKET, quantity=abs(amt))
                    print(f"🚨 [API] {sym} Pozisyon Kapatıldı.")
        except Exception as e: print(f"❌ [KAPATMA HATA] {e}")

    async def fetch_missing_data(self, symbol):
        if not self.client: return None, 0.0
        try:
            klines = await self.client.futures_klines(symbol=symbol.upper(), interval=KLINE_INTERVAL_1MINUTE, limit=60)
            data = [(float(k[4]), int(k[0])/1000) for k in klines]
            ticker = await self.client.futures_ticker(symbol=symbol.upper())
            return data, float(ticker['priceChangePercent'])
        except: return None, 0.0
    
    async def get_usdt_balance(self):
        """
        Binance Futures hesabındaki güncel USDT bakiyesini çeker.
        Dönüş: (Toplam Bakiye, Kullanılabilir Bakiye)
        """
        if not self.client:
            print("⚠️ [BAKİYE] API bağlı değil, bakiye çekilemedi.")
            return 0.0, 0.0
            
        try:
            # Futures hesabındaki tüm varlıkları çek
            balances = await self.client.futures_account_balance()
            
            for asset in balances:
                if asset['asset'] == 'USDT':
                    # balance: Toplam Varlık (Pozisyonlar dahil)
                    # withdrawAvailable: İşlem açılabilir boş bakiye
                    print(f"🧾 [BAKİYE VERİSİ] {asset}")

                    total_balance = float(asset['balance'])
                    available_balance = float(asset.get('availableBalance', 0.0))                    
                    print(f"💰 [CÜZDAN] Toplam: {total_balance:.2f} USDT | Boşta: {available_balance:.2f} USDT")
                    return total_balance, available_balance
            
            print("⚠️ [BAKİYE] USDT varlığı bulunamadı.")
            return 0.0, 0.0
            
        except Exception as e:
            print(f"❌ [BAKİYE HATASI] {e}")
            return 0.0, 0.0

    async def get_extended_metrics(self, symbol):
        """
        Gelişmiş analiz için 24h Hacim ve Fonlama Oranını çeker.
        """
        if not self.client: return "Unknown", 0.0

        try:
            # 1. 24 Saatlik Veriler (Hacim için)
            # quoteVolume = USDT cinsinden hacim
            ticker_stats = await self.client.futures_ticker(symbol=symbol.upper())
            volume_usdt = float(ticker_stats.get('quoteVolume', 0))
            
            # Formatla (Milyar/Milyon)
            if volume_usdt > 1_000_000_000:
                vol_str = f"${volume_usdt / 1_000_000_000:.2f}B"
            else:
                vol_str = f"${volume_usdt / 1_000_000:.2f}M"

            # 2. Fonlama Oranı (Funding Rate)
            # premiumIndex endpoint'i anlık fonlamayı verir
            premium_index = await self.client.futures_mark_price(symbol=symbol.upper())
            funding_rate = float(premium_index.get('lastFundingRate', 0)) * 100 # Yüzdeye çevir
            
            return vol_str, funding_rate

        except Exception as e:
            print(f"⚠️ [METRİK HATA] {symbol}: {e}")
            return "Unknown", 0.0
        
    async def get_order_book_imbalance(self, symbol, limit=100):
        """
        Tahtadaki Alıcı/Satıcı dengesizliğini ölçer.
        Dönüş: (Oran, Yorum)
        Oran > 0 ise Alıcılar Güçlü (Bullish)
        Oran < 0 ise Satıcılar Güçlü (Bearish)
        """
        if not self.client: return 0.0, "No Connection"
        
        try:
            # Derinlik verisini çek (Limit 100 yeterli, fazlası yavaşlatır)
            depth = await self.client.futures_depth(symbol=symbol.upper(), limit=limit)
            
            # Bids (Alışlar) ve Asks (Satışlar) toplam hacmini hesapla
            # Liste formatı: [[Fiyat, Miktar], [Fiyat, Miktar]...]
            total_bids = sum([float(x[1]) for x in depth['bids']])
            total_asks = sum([float(x[1]) for x in depth['asks']])
            
            if total_bids + total_asks == 0: return 0.0, "No Volume"

            # Dengesizlik Oranı (-1 ile +1 arası)
            # +0.5 demek: Alıcılar %75, Satıcılar %25 (Çok Güçlü Alış)
            # -0.5 demek: Satıcılar %75, Alıcılar %25 (Çok Güçlü Satış)
            imbalance = (total_bids - total_asks) / (total_bids + total_asks)
            
            return imbalance, f"Bids: {total_bids:.2f} | Asks: {total_asks:.2f}"
            
        except Exception as e:
            print(f"⚠️ [DEPTH HATA] {e}")
            return 0.0, "Error"