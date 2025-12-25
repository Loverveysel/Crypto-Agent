import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
import aiofiles

# Proje Modülleri
from config import TARGET_CHANNELS, API_ID, API_HASH
from binance_client import BinanceExecutionEngine
from utils import find_coins, get_top_100_map, coin_categories
from price_buffer import PriceBuffer

# --- AYARLAR ---
LOOKBACK_DAYS = 365 
OBSERVATION_WINDOW = 20
MIN_ROI_THRESHOLD = 3.0 # %3
STOP_LOSS_LIMIT = 0.8
OUTPUT_FILE = "raw_market_outcomes.jsonl"
COIN_MAP = get_top_100_map()

async def get_market_outcome(ctx, pair, msg_ts, btc_trend): # btc_trend dışarıdan geliyor
    """Haber anındaki verileri toplar. BTC sorgusu içermez, hızlıdır."""
    try:
        start_ms = int(msg_ts * 1000)
        
        # 1. FUNDING RATE
        funding = await ctx.real_exchange.client.futures_funding_rate(symbol=pair.upper(), limit=1)
        funding_rate = float(funding[0]['fundingRate']) if funding else 0.01

        # 2. TEKNİK ANALİZ (100 dk geri)
        klines_hist = await ctx.real_exchange.client.futures_klines(
            symbol=pair.upper(), interval='1m', startTime=start_ms - 6000000, endTime=start_ms, limit=100
        )
        if not klines_hist: return None
        
        pb = PriceBuffer()
        for k in klines_hist: pb.update_candle(float(k[4]), k[0]/1000, True)
        rsi_val = pb.calculate_rsi(14)
        entry_price = float(klines_hist[-1][4])
        
        # Değişimler
        price_1h_ago = float(klines_hist[-60][4]) if len(klines_hist) >= 60 else float(klines_hist[0][4])
        change_1h = ((entry_price - price_1h_ago) / price_1h_ago) * 100

        # 3. MARKET CAP & KATEGORİ
        clean_symbol = pair.upper().replace("USDT", "")
        coin_info = COIN_MAP.get(clean_symbol.lower(), {})
        mcap = coin_info.get("cap", 0)
        category = coin_category.get(clean_symbol.upper(), "Unknown")

        # 4. HABER SONRASI (20 dk)
        after_klines = await ctx.real_exchange.client.futures_klines(
            symbol=pair.upper(), interval='1m', startTime=start_ms, limit=OBSERVATION_WINDOW + 1
        )
        max_high, min_low = 0.0, 0.0
        p_high_min, p_low_min = 0, 0
        
        for i, k in enumerate(after_klines):
            h_move = ((float(k[2]) - entry_price) / entry_price) * 100
            l_move = ((float(k[3]) - entry_price) / entry_price) * 100
            if h_move > max_high: max_high, p_high_min = h_move, i
            if l_move < min_low: min_low, p_low_min = l_move, i

        data_template = {
            "symbol": pair.upper(), "mcap": f"{mcap/1e9:.2f}B", "cat": category,
            "rsi": round(rsi_val, 2), "funding": funding_rate, "btc_trend": btc_trend,
            "mom": {"1h": round(change_1h, 2)}
        }

        if max_high >= MIN_ROI_THRESHOLD and abs(min_low) < STOP_LOSS_LIMIT:
            return {**data_template, "action": "LONG", "peak_pct": round(max_high, 2), "peak_min": p_high_min}
        elif abs(min_low) >= MIN_ROI_THRESHOLD and max_high < STOP_LOSS_LIMIT:
            return {**data_template, "action": "SHORT", "peak_pct": round(min_low, 2), "peak_min": p_low_min}
            
        return None
    except: return None

async def get_btc_trend(ctx, msg_ts):
    """Haber anındaki BTC 1 saatlik trendini tek seferde hesaplar."""
    try:
        start_ms = int(msg_ts * 1000)
        klines = await ctx.real_exchange.client.futures_klines(
            symbol="BTCUSDT", interval='1m', startTime=start_ms - 3600000, endTime=start_ms, limit=61
        )
        if not klines: return 0.0
        start_p, end_p = float(klines[0][4]), float(klines[-1][4])
        return round(((end_p - start_p) / start_p) * 100, 2)
    except: return 0.0

async def main():
    ctx = type('obj', (object,), {'real_exchange': BinanceExecutionEngine("", "")})
    await ctx.real_exchange.connect()

    client = TelegramClient(os.path.join("data", "crypto_agent_session"), API_ID, API_HASH)
    await client.connect()

    start_date = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    processed, found = 0, 0

    try:
        async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as f:
            for channel in TARGET_CHANNELS:
                print(f"\n📡 {channel} için mesajlar sayılıyor...")
                # 1. TUR: İlerleme için sayım
                all_msgs = await client.get_messages(channel, offset_date=start_date, limit=None)
                chan_total = len(all_msgs)
                print(f"📈 Kanalda işlenecek {chan_total} mesaj bulundu.")

                # 2. TUR: İşleme
                for i, message in enumerate(reversed(all_msgs)): # Eskiden yeniye
                    processed += 1
                    percent = ((i + 1) / chan_total) * 100
                    sys.stdout.write(f"\r🚀 %{percent:.2f} | İncelenen: {processed} | Elmas: {found} | Kanal: {channel}")
                    sys.stdout.flush()

                    if not message.text or len(message.text) < 20: continue
                    detected = find_coins(message.text, COIN_MAP)
                    if not detected: continue
                    
                    # BTC Trendini haber bazında BİR KERE hesapla
                    btc_trend = await get_btc_trend(ctx, message.date.timestamp())
                    
                    for pair in detected:
                        res = await get_market_outcome(ctx, pair, message.date.timestamp(), btc_trend)
                        if res:
                            entry = {"ts": message.date.isoformat(), "news": message.text, "data": res}
                            await f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                            await f.flush()
                            os.fsync(f.fileno())
                            found += 1
                            print(f"\n💎 [{res['action']}] {pair} | %{res['peak_pct']} | {message.date.strftime('%Y-%m-%d %H:%M')}")
                        await asyncio.sleep(0.01)

    finally:
        print("\n🧹 Oturumlar kapatılıyor...")
        await client.disconnect()
        if hasattr(ctx.real_exchange, 'client'): await ctx.real_exchange.client.close_connection()
        print("✅ Tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())