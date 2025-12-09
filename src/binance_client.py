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
            await self._place_tp_sl(sym, side, entry_price, tp_pct, sl_pct)
            print(f"🚀 [API] {sym} {side} @ {entry_price} (Miktar: {qty})")
            
        except Exception as e: 
            print(f"❌ [API HATA] {e}")

    async def _place_tp_sl(self, symbol, side, entry, tp_pct, sl_pct):
        try:
            tick = self.symbol_info[symbol.lower()]['tickSize']
            
            if side == 'LONG':
                tp_raw = entry * (1 + tp_pct/100)
                sl_raw = entry * (1 - sl_pct/100)
                close_side = SIDE_SELL
            else:
                tp_raw = entry * (1 - tp_pct/100)
                sl_raw = entry * (1 + sl_pct/100)
                close_side = SIDE_BUY

            # Negatif fiyat koruması
            if tp_raw <= tick: tp_raw = entry + (tick * 10) if side=='LONG' else entry - (tick * 10)
            if sl_raw <= tick: sl_raw = entry - (tick * 10) if side=='LONG' else entry + (tick * 10)

            tp = self._round_price(tp_raw, tick)
            sl = self._round_price(sl_raw, tick)
            
            print(f"🛡️ TP/SL Ayarlanıyor: TP={tp} | SL={sl}")

            await self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=sl, closePosition=True)
            await self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, stopPrice=tp, closePosition=True)

        except Exception as e: print(f"⚠️ [TP/SL HATASI] {e}")

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