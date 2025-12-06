from binance import AsyncClient
from binance.enums import *
from dotenv import load_dotenv


class BinanceExecutionEngine:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.client = None
        self.symbol_info = {} 

    async def connect(self):
        """API'ye bağlanır ve parite kurallarını çeker"""
        try:
            # Tek bir client oluşturuyoruz. Testnet ayrımı burada yapılıyor.
            self.client = await AsyncClient.create(self.api_key, self.api_secret, testnet=self.testnet)
            
            # Exchange Info'yu çek
            info = await self.client.futures_exchange_info()
            
            for symbol_data in info['symbols']:
                symbol = symbol_data['symbol'].lower()
                # Filtreleri güvenli çekmek için kontrol
                filters = {f['filterType']: f for f in symbol_data['filters']}
                
                # Bazen API'den eksik veri gelebilir, try-except ile koru
                try:
                    self.symbol_info[symbol] = {
                        'stepSize': float(filters['LOT_SIZE']['stepSize']),
                        'tickSize': float(filters['PRICE_FILTER']['tickSize']),
                        'minQty': float(filters['LOT_SIZE']['minQty'])
                    }
                except KeyError:
                    continue

            env_name = "TESTNET (DEMO)" if self.testnet else "MAINNET (REAL)"
            print(f"✅ [{env_name}] Bağlantı başarılı. {len(self.symbol_info)} parite kuralı yüklendi.")
            
        except Exception as e:
            print(f"❌ [BORSA HATASI] Bağlanamadı: {e}")

    def _round_step(self, quantity, step_size):
        if step_size == 0: return quantity
        return float(int(quantity / step_size) * step_size)

    def _round_price(self, price, tick_size):
        if tick_size == 0: return price
        return float(round(price / tick_size) * tick_size)

    async def execute_trade(self, symbol, side, amount_usdt, leverage, tp_pct, sl_pct):
        symbol = symbol.upper()
        symbol_lower = symbol.lower()
        
        if not self.client:
            print("⚠️ API Bağlı değil!")
            return

        try:
            # 1. Kaldıraç Ayarla
            await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

            # 2. Anlık Fiyatı Al
            ticker = await self.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])

            # 3. Miktarı Hesapla
            raw_qty = (amount_usdt * leverage) / current_price
            
            step_size = self.symbol_info[symbol_lower]['stepSize']
            qty = self._round_step(raw_qty, step_size)
            
            min_qty = self.symbol_info[symbol_lower]['minQty']
            if qty < min_qty:
                print(f"⚠️ [HATA] Miktar çok düşük: {qty} (Min: {min_qty})")
                return

            print(f"🚀 [İŞLEM BAŞLIYOR] {symbol} {side} | Lev: {leverage}x | Fiyat: {current_price}")

            # 4. Ana Market Emri
            order_side = SIDE_BUY if side == 'LONG' else SIDE_SELL
            
            order = await self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            
            entry_price = float(order['avgPrice']) if 'avgPrice' in order and float(order['avgPrice']) > 0 else current_price
            print(f"✅ GİRİŞ BAŞARILI: Ort. Fiyat {entry_price}")

            # 5. TP/SL Emirleri
            await self._place_tp_sl(symbol, side, qty, entry_price, tp_pct, sl_pct)
            
            return order

        except Exception as e:
            print(f"❌ [KRİTİK İŞLEM HATASI] {e}")

    async def _place_tp_sl(self, symbol, side, qty, entry_price, tp_pct, sl_pct):
        try:
            tick_size = self.symbol_info[symbol.lower()]['tickSize']
            
            if side == 'LONG':
                tp_price = self._round_price(entry_price * (1 + tp_pct/100), tick_size)
                sl_price = self._round_price(entry_price * (1 - sl_pct/100), tick_size)
                close_side = SIDE_SELL
            else: 
                tp_price = self._round_price(entry_price * (1 - tp_pct/100), tick_size)
                sl_price = self._round_price(entry_price * (1 + sl_pct/100), tick_size)
                close_side = SIDE_BUY

            # STOP LOSS
            await self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=sl_price,
                closePosition=True
            )
            
            # TAKE PROFIT
            await self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                stopPrice=tp_price,
                closePosition=True
            )
            print(f"🛡️ TP/SL Kuruldu: {tp_price} / {sl_price}")

        except Exception as e:
            print(f"⚠️ [TP/SL HATASI] {e}")

    async def close(self):
        if self.client:
            await self.client.close_connection()

    async def close_position_market(self, symbol):
        """
        Açık olan tüm pozisyonu ve emirleri kapatır (Acil Çıkış).
        """
        symbol = symbol.upper()
        if not self.client: return

        try:
            # 1. Açık Emirleri İptal Et (TP/SL emirleri askıda kalmasın)
            await self.client.futures_cancel_all_open_orders(symbol=symbol)
            print(f"🧹 [API] {symbol} Açık emirler iptal edildi.")

            # 2. Mevcut Pozisyonun Yönünü ve Miktarını Bul
            # (Long isek Short açmalıyız, Short isek Long açmalıyız kapatmak için)
            positions = await self.client.futures_position_information(symbol=symbol)
            # Hedge modu kapalıysa liste döner, biz ilkine bakarız
            target_pos = None
            for p in positions:
                if float(p['positionAmt']) != 0:
                    target_pos = p
                    break
            
            if not target_pos:
                print(f"⚠️ [API] {symbol} Kapatılacak açık pozisyon bulunamadı.")
                return

            amt = float(target_pos['positionAmt'])
            side = SIDE_SELL if amt > 0 else SIDE_BUY # Pozisyonun tersine işlem
            qty = abs(amt)

            # 3. Kapatma Emri (Market)
            await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"🚨 [API] {symbol} Pozisyonu piyasa fiyatından kapatıldı (TIME LIMIT).")

        except Exception as e:
            print(f"❌ [API KAPATMA HATASI] {e}")
