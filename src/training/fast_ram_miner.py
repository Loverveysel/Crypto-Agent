import asyncio
import json
import os
import sys
import glob
import pandas as pd
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
import aiofiles

# Proje Modülleri
from config import TARGET_CHANNELS, API_ID, API_HASH
from utils import find_coins, get_top_100_map, coin_categories
from binance_client import BinanceExecutionEngine
from main import BotContext
import random
# --- AYARLAR ---
LOOKBACK_DAYS = 150
OBSERVATION_WINDOW = 40
MIN_ROI_THRESHOLD = 0.5
STOP_LOSS_LIMIT = 0.5
OUTPUT_FILE = "hold_data.jsonl"
CACHE_PATH = "market_cache/klines"
COIN_MAP = get_top_100_map()

class RAMDataCenter:
    def __init__(self, path):
        self.path = path
        self.klines = {} # { 'BTCUSDT': DataFrame }
        self.btc_df = None
        self.passed = 0
        self.passedCoins = []

    def load_all_to_ram(self):
        print("🧠 Veriler RAM'e yükleniyor, kemerleri bağla...")
        files = glob.glob(f"{self.path}/*.pkl")
        for i, file in enumerate(files):
            symbol = os.path.basename(file).split("_")[0]
            try:
                df = pd.read_pickle(file)
                df.set_index('ts', inplace=True)
                df.sort_index(inplace=True)
                self.klines[symbol] = df
                if symbol == "BTCUSDT":
                    self.btc_df = df
                
                # Yükleme İlerlemesi
                if i % 10 == 0:
                    sys.stdout.write(f"\r📦 RAM Yüklemesi: %{(i/len(files))*100:.1f}")
                    sys.stdout.flush()
            except Exception as e:
                print(f"\n⚠️ {symbol} yüklenemedi: {e}")
        
        print(f"\n✅ {len(self.klines)} Coin RAM'e alındı. Madencilik hazır!")

    async def get_fast_outcome(self, ctx, symbol, msg_ts, btc_trend):
        """RAM üzerinden teknik analiz yapar. RSI ve çoklu momentum eklenmiştir."""
        if symbol not in self.klines.keys(): 
            self.passed += 1
            self.passedCoins.append(symbol)
            return None
        
        df = self.klines[symbol]
        target_ts = (int(msg_ts) // 60) * 60 * 1000 
        
        try:
            # 1. Haber Anı İndeksi
            idx = df.index.get_indexer([target_ts], method='pad')[0]
            if idx < 60 or idx + OBSERVATION_WINDOW >= len(df): return None
            
            # 2. Teknik Metrikler (Momentum)
            entry_price = df.iloc[idx]['c']
            ch_1m = ((entry_price - df.iloc[idx-1]['c']) / df.iloc[idx-1]['c']) * 100
            ch_10m = ((entry_price - df.iloc[idx-10]['c']) / df.iloc[idx-10]['c']) * 100
            ch_1h = ((entry_price - df.iloc[idx-60]['c']) / df.iloc[idx-60]['c']) * 100
            
            # 3. RSI Hesaplama (Son 14 periyot üzerinden manuel/hızlı)
            # Daha verimli olması için Pandas rolling de kullanılabilir ama RAM miner için bu yeterli
            delta = df.iloc[idx-20 : idx+1]['c'].diff()
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            ema_up = up.rolling(window=14).mean()
            ema_down = down.rolling(window=14).mean()
            rs = ema_up / ema_down
            rsi_val = 100 - (100 / (1 + rs.iloc[-1]))

            # 4. Performans Analizi (Gelecek 20 dk)
            future_df = df.iloc[idx+1 : idx + OBSERVATION_WINDOW + 1]
            max_h = ((future_df['h'].max() - entry_price) / entry_price) * 100
            min_l = ((future_df['l'].min() - entry_price) / entry_price) * 100
            
            # 5. Karar Mekanizması
            action = None
            peak_pct = 0
            peak_min = 0

            if max_h >= MIN_ROI_THRESHOLD and abs(min_l) < STOP_LOSS_LIMIT:
                funding = await ctx.real_exchange.client.futures_funding_rate(symbol=symbol.upper(), limit=1)
                funding_rate = float(funding[0]['fundingRate']) if funding else 0.01
                action = "LONG"
                peak_pct = round(max_h, 2)
                peak_min = int(future_df['h'].argmax()) + 1
            elif abs(min_l) >= MIN_ROI_THRESHOLD and max_h < STOP_LOSS_LIMIT:
                funding = await ctx.real_exchange.client.futures_funding_rate(symbol=symbol.upper(), limit=1)
                funding_rate = float(funding[0]['fundingRate']) if funding else 0.01
                action = "SHORT"
                peak_pct = round(min_l, 2)
                peak_min = int(future_df['l'].argmin()) + 1

            if action: return None

            # 6. Sembol Temizliği (1000PEPE -> PEPE)
            clean_symbol = symbol.replace("USDT", "")
            lookup_symbol = clean_symbol[4:] if clean_symbol.startswith("1000") else clean_symbol
            
            coin_info = COIN_MAP.get(lookup_symbol.lower(), {})
            funding = await ctx.real_exchange.client.futures_funding_rate(symbol=symbol.upper(), limit=1)
            funding_rate = float(funding[0]['fundingRate']) if funding else 0.01
            return {
                "symbol": symbol,
                "price": round(entry_price, 6),
                "market_cap": f"{coin_info.get('cap', 0)/1e9:.2f}B",
                "category": coin_categories.get(lookup_symbol, "Unknown"),
                "rsi": round(rsi_val, 2) if not pd.isna(rsi_val) else 50.0,
                "btc_trend": btc_trend,
                "funding": funding_rate,
                "momentum": {
                    "1m": round(ch_1m, 2),
                    "10m": round(ch_10m, 2),
                    "1h": round(ch_1h, 2)
                },
                "action": "HOLD",
                "peak_pct": peak_pct,
                "peak_min": peak_min,
                "time": datetime.fromtimestamp(msg_ts).strftime('%H:%M')
            }
        except Exception as e:
            print("Error in get_fast_outcome:", e)
            return None
    
    def get_btc_trend_ram(self, msg_ts):
        """BTC trendini RAM'den çeker."""
        if self.btc_df is None: return 0.0
        target_ts = (int(msg_ts) // 60) * 60 * 1000
        try:
            idx = self.btc_df.index.get_indexer([target_ts], method='pad')[0]
            if idx < 60: return 0.0
            start_p = self.btc_df.iloc[idx - 60]['c']
            end_p = self.btc_df.iloc[idx]['c']
            return round(((end_p - start_p) / start_p) * 100, 2)
        except: return 0.0

async def main():
    # 1. RAM Hazırlığı
    ram = RAMDataCenter(CACHE_PATH)
    ram.load_all_to_ram()
    print("RAM Hazırlığı Bitti")
    ctx = BotContext()
    ctx.real_exchange = BinanceExecutionEngine("", "")
    await ctx.real_exchange.connect()

    # 2. Telegram Hazırlığı
    client = TelegramClient("crypto_agent_session", API_ID, API_HASH)
    await client.connect()

    start_date = datetime.now(timezone.utc) - timedelta(hours=12)
    processed, found = 0, 0

    try:
        async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as f:
            for channel in TARGET_CHANNELS:
                print(f"\n📡 {channel} Kazılıyor...")
                # Tüm mesajları bir kerede çek (Hızlı tur)
                all_msgs = await client.get_messages(channel, offset_date=start_date, limit=10000)
                
                random.shuffle(all_msgs)
                
                for i, message in enumerate(all_msgs):
                    processed += 1
                    if not message.text or len(message.text) < 20: continue
                    
                    detected = find_coins(message.text, COIN_MAP)
                    if not detected: continue
                    
                    # Trendi ve Outcome'ı RAM'den al (Neredeyse anlık)
                    btc_trend = ram.get_btc_trend_ram(message.date.timestamp())
                    
                    for pair in detected:
                        res = await ram.get_fast_outcome(ctx, pair.upper(), message.date.timestamp(), btc_trend)
                        if res:
                            entry = {"ts": message.date.isoformat(), "news": message.text, "data": res}
                            await f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                            found += 1
                            print(f"\n💎 [{res['action']}] {pair} | %{res['peak_pct']} | {message.date.strftime('%H:%M')}")
                    
                    # Progress log
                    if i % 100 == 0:
                        sys.stdout.write(f"\r🚀 Kanal: {channel} | %{((i+1)/len(all_msgs))*100:.1f} | Toplam Bulunan: {found}")
                        sys.stdout.flush()

    finally:
        await client.disconnect()
        print(f"\n\nDikkat! {ram.passed} coin pas geçildiw")
        print("Pas geçilen coinler : ", list(set(ram.passedCoins)))
        print(f"\n✅ Madencilik bitti. {found} elmas {OUTPUT_FILE} dosyasına yazıldı.")

if __name__ == "__main__":
    asyncio.run(main())