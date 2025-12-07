from binance import AsyncClient
from binance.enums import *
import math # <--- BU EKLENDİ

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
                    self.symbol_info[s['symbol'].lower()] = {
                        'stepSize': float(filters['LOT_SIZE']['stepSize']),
                        'tickSize': float(filters['PRICE_FILTER']['tickSize']),
                        'minQty': float(filters['LOT_SIZE']['minQty'])
                    }
                except: continue
            print(f"✅ [{'TESTNET' if self.testnet else 'MAINNET'}] Borsa Bağlantısı Başarılı.")
        except Exception as e:
            print(f"❌ [BORSA HATASI] {e}")

    def _get_precision(self, step_size):
        """Step size'dan ondalık basamak sayısını hesaplar"""
        if step_size == 0: return 0
        return int(round(-math.log(step_size, 10), 0))

    def _round_step(self, quantity, step_size):
        """Miktarı step size'a göre aşağı yuvarlar ve hassasiyeti temizler"""
        if step_size == 0: return quantity
        precision = self._get_precision(step_size)
        # Önce step'e bölüp int'e çevirerek "floor" yapıyoruz (fazlasını atıyoruz)
        qty = int(quantity / step_size) * step_size
        # Sonra python float hatasını temizlemek için round kullanıyoruz
        return float(round(qty, precision))

    def _round_price(self, price, tick_size):
        """Fiyatı tick size'a göre en yakına yuvarlar ve hassasiyeti temizler"""
        if tick_size == 0: return price
        precision = self._get_precision(tick_size)
        price = round(price / tick_size) * tick_size
        return float(round(price, precision))

    async def execute_trade(self, symbol, side, amount_usdt, leverage, tp_pct, sl_pct):
        if not self.client: return
        sym = symbol.upper()
        try:
            await self.client.futures_change_leverage(symbol=sym, leverage=leverage)
            ticker = await self.client.futures_symbol_ticker(symbol=sym)
            price = float(ticker['price'])
            
            # Miktar Hesaplama
            raw_qty = (amount_usdt * leverage) / price
            step_size = self.symbol_info[symbol.lower()]['stepSize']
            
            # Hassasiyet Düzeltmesi (BURASI DEĞİŞTİ)
            qty = self._round_step(raw_qty, step_size)
            
            if qty < self.symbol_info[symbol.lower()]['minQty']:
                print(f"⚠️ [HATA] Miktar ({qty}) minimumun altında.")
                return

            side_enum = SIDE_BUY if side == 'LONG' else SIDE_SELL
            
            # Ana Emri Gönder
            order = await self.client.futures_create_order(
                symbol=sym, 
                side=side_enum, 
                type=ORDER_TYPE_MARKET, 
                quantity=qty
            )
            entry = float(order.get('avgPrice', price))
            
            # TP/SL Kur
            await self._place_tp_sl(sym, side, entry, tp_pct, sl_pct)
            print(f"🚀 [API] {sym} {side} İşlemi Açıldı @ {entry} (Miktar: {qty})")
            
        except Exception as e: 
            print(f"❌ [API HATA] {e}")

    async def _place_tp_sl(self, symbol, side, entry, tp_pct, sl_pct):
        try:
            tick = self.symbol_info[symbol.lower()]['tickSize']
            if side == 'LONG':
                tp = self._round_price(entry * (1 + tp_pct/100), tick)
                sl = self._round_price(entry * (1 - sl_pct/100), tick)
                close_side = SIDE_SELL
            else:
                tp = self._round_price(entry * (1 - tp_pct/100), tick)
                sl = self._round_price(entry * (1 + sl_pct/100), tick)
                close_side = SIDE_BUY
            
            await self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=sl, closePosition=True)
            await self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, stopPrice=tp, closePosition=True)
        except Exception as e: print(f"⚠️ [TP/SL] {e}")

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
                    qty = abs(amt) # Kapatırken de miktar pozitif olmalı
                    await self.client.futures_create_order(symbol=sym, side=side, type=ORDER_TYPE_MARKET, quantity=qty)
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