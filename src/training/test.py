import asyncio
import time
import os
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

# Proje Modülleri
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TARGET_CHANNELS, API_ID, API_HASH, TELETHON_SESSION_NAME, STARTING_BALANCE
from main import BotContext, SharedState
from binance_client import BinanceExecutionEngine
from services import process_news, ensure_fresh_data
from utils import find_coins, get_top_100_map
from price_buffer import PriceBuffer
from exchange import PaperExchange
from brain import AgentBrain
from config import GROQCLOUD_API_KEY, GROQCLOUD_MODEL

# 1. DATABASE'İ DEVRE DIŞI BIRAKAN MOCK
class MockMemory:
    def is_duplicate(self, text): return False, 0.0
    def add_news(self, source, content): pass
    def log_decision(self, record): return 999 # Fake ID
    def log_trade(self, record, decision_id=None): pass

async def get_historical_technicals(ctx, pair, msg_ts):
    """Haber anındaki teknik metrikleri hesaplar."""
    # 1. Hedef Coin için geçmiş 100 dakikayı çek (RSI ve Changes için)
    # 100 dakika çekiyoruz ki RSI (14) sağlıklı hesaplansın
    klines = await ctx.real_exchange.client.futures_klines(
        symbol=pair.upper(),
        interval='1m',
        endTime=int(msg_ts * 1000),
        limit=100
    )
    
    if not klines:
        return None

    # Buffer oluştur ve doldur
    temp_buffer = PriceBuffer()
    for k in klines:
        # (price, timestamp, is_closed)
        temp_buffer.update_candle(float(k[4]), k[0]/1000, True)
    
    # Anlık fiyatı son kapanışa eşitle
    temp_buffer.current_price = float(klines[-1][4])
    
    # 2. BTC Trendi için aynı işlemi yap
    btc_klines = await ctx.real_exchange.client.futures_klines(
        symbol="BTCUSDT",
        interval='1m',
        endTime=int(msg_ts * 1000),
        limit=60
    )
    
    btc_trend = 0.0
    if btc_klines:
        btc_start = float(btc_klines[0][4])
        btc_end = float(btc_klines[-1][4])
        btc_trend = ((btc_end - btc_start) / btc_start) * 100

    return {
        'price': temp_buffer.current_price,
        'rsi': temp_buffer.calculate_rsi(),
        'changes': temp_buffer.get_all_changes(),
        'btc_trend': btc_trend,
    }

coin_map = get_top_100_map()
async def simulate_process_news(message, ctx, f_log):
    """
    services.py -> process_news() fonksiyonunun simülasyon versiyonu.
    Araştırma (Research) kısmını atlar, teknik verileri geçmişten çeker.
    """
    msg_text = message.text
    msg_ts = message.date.timestamp()
    msg_dt = message.date.strftime("%Y-%m-%d %H:%M:%S")

    # --- 1. FİLTRELEME (is_duplicate benzeri) ---
    # Simülasyonda aynı haberi tekrar işlememek için basit bir kontrol
    # (MockMemory zaten False dönecek şekilde ayarlandı)
    is_dup, _ = ctx.memory.is_duplicate(msg_text)
    if is_dup: return

    # --- 2. COIN TESPİTİ (Regex + AI Fallback) ---
    # services.py'daki mantığın aynısı
    detected_pairs = find_coins(msg_text, coin_map=coin_map)
    
    if not detected_pairs:
        # Regex bulamazsa AI'ya sor (detect_symbol)
        found_symbol = await ctx.brain.detect_symbol(msg_text, coin_map)
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            detected_pairs.append(pot_pair)

    if detected_pairs == None:
        return # Hiç coin yoksa geç

    # --- 3. ANALİZ DÖNGÜSÜ ---
    for pair in detected_pairs:
        try:
            # A) Geçmiş Veri Çekme (Haber anındaki 1 saatlik veri)
            # ensure_fresh_data'nın simülasyon karşılığı
            klines = await ctx.real_exchange.client.futures_klines(
                symbol=pair.upper(),
                interval='1m',
                startTime=int(msg_ts * 1000),
                limit=61 # Analiz + 60dk takip
            )
            if not klines: continue

            # Haber anındaki fiyat (Entry)
            entry_price = float(klines[0][4]) # Close
            
            # Analiz için gereken değişimler (Haber anında hepsi 0 varsayılıyor veya geçmiş kline ile hesaplanabilir)
            # A) Teknik Verileri "Haber Anına" Göre Hazırla
            tech = await get_historical_technicals(ctx, pair, msg_ts)
            if not tech: continue

            print(f"📊 Teknik Veriler Alındı ({pair}): RSI: {tech['rsi']:.2f} | BTC 1h: {tech['btc_trend']:.2f}%")

            #Get coin full name
            # Güvenli Sözlük Erişimi
            clean_symbol = pair.lower().replace("usdt", "")
            c_data = coin_map.get(clean_symbol)
            if isinstance(c_data, dict):
                coin_full_name = c_data.get("name", "Unknown").title()
                m_cap = c_data.get("cap", 0)
            else:
                coin_full_name = "Unknown"
                m_cap = 0

            # Market Cap Formatlama
            if m_cap > 1_000_000_000:
                cap_str = f"${m_cap / 1_000_000_000:.2f} BILLION"
            elif m_cap > 1_000_000:
                cap_str = f"${m_cap / 1_000_000:.2f} MILLION"
            else:
                cap_str = "UNKNOWN/SMALL"
            # B) AI Kararını Gerçek Teknik Verilerle Al
            dec = await ctx.brain.analyze_specific_no_research(
                news=msg_text,
                symbol=pair,
                price=tech['price'],
                changes=tech['changes'], # Artık gerçek geçmiş değişimler
                coin_full_name=coin_full_name,
                market_cap_str=cap_str,
                rsi_val=tech['rsi'],     # Gerçek RSI
                btc_trend=tech['btc_trend'], # Gerçek BTC Trendi
                volume_24h="UNKNOWN", # Geçmiş hacmi çekmek zordur, opsiyonel
                funding_rate=0.01      # Sabit veya anlık verilebilir
            )
            print(f"🧠 AI Karar: symbol: {pair}, action: {dec['action']}, confidence: {dec['confidence']}")

            # C) Karar Uygulama (Confidence >= 65 Check)
            if dec.get("confidence", 0) >= 65 and dec.get("action") in ["LONG", "SHORT"]:
                
                # --- SİMÜLASYON İŞLEM AÇILIŞI ---
                # execute_trade_logic yerine test versiyonu
                trade_amount = ctx.exchange.balance * 0.40 # Sabit test tutarı
                leverage = 10
                
                if dec.get("confidence", 0) >= 75:
                    trade_amount = ctx.exchange.balance * 0.60 # Sabit test tutarı
                    leverage = 15
                
                if dec.get("confidence", 0) >= 90:
                    trade_amount = ctx.exchange.balance * 0.80 # Sabit test tutarı
                    leverage = 20
                
                report_entry = (
                    f"\n{'='*60}\n"
                    f"🔔 YENİ İŞLEM TESPİTİ | {msg_dt}\n"
                    f"{'-'*60}\n"
                    f"📰 HABER: {msg_text[:150]}...\n"
                    f"🎯 HEDEF: {pair.upper()} ({coin_full_name})\n"
                    f"📊 ANALİZ VERİLERİ:\n"
                    f"   - Giriş Fiyatı: {entry_price}\n"
                    f"   - RSI: {tech['rsi']:.2f}\n"
                    f"   - BTC Trend (1h): %{tech['btc_trend']:.2f}\n"
                    f"   - Market Cap: {cap_str}\n"
                    f"🧠 AI KARARI:\n"
                    f"   - Aksiyon: {dec['action']} (Güven: %{dec['confidence']})\n"
                    f"   - TP/SL: %{dec.get('tp_pct')}/%{dec.get('sl_pct')}\n"
                    f"   - Sebep: {dec.get('reason')}\n"
                    f"{'-'*60}\n"
                )
                
                # İşlemi aç
                open_log, _ = ctx.exchange.open_position_test(
                    symbol=pair, side=dec["action"], price=entry_price,
                    tp_pct=dec.get("tp_pct", 1.5), sl_pct=dec.get("sl_pct", 1.0),
                    amount_usdt=100, leverage=10, validity=dec.get("validity_minutes", 15),
                    app_state=ctx.app_state, decision_id=999, now_ts=msg_ts
                )
                
                print(f"🚀 İşlem Açıldı: {pair} | {dec['action']}")

                # --- 4. POZİSYON TAKİBİ (15sn Ticks) ---
                # services.py'daki websocket_loop ve monitor_loop'un simülasyonu
                for k in klines:
                    minute_ts = k[0] / 1000
                    # OHLC Verileriyle 15 saniyelik tick simülasyonu
                    ticks = [float(k[1]), float(k[2]), float(k[3]), float(k[4])]
                    
                    for i, tick_price in enumerate(ticks):
                        current_ts = minute_ts + (i * 15)
                        # Pozisyonu kontrol et (Test versiyonu)
                        res_log, _, sym, pnl, peak, _ = ctx.exchange.check_positions_test(
                            pair, tick_price, now_ts=current_ts
                        )
                        
                        if res_log:
                            close_dt = datetime.fromtimestamp(current_ts).strftime("%Y-%m-%d %H:%M:%S")
                            report_exit = (
                                f"🏁 İŞLEM SONUCU ({close_dt}):\n"
                                f"   - Durum: {res_log}\n"
                                f"   - Kar/Zarar: {pnl:.2f} USDT\n"
                                f"   - Görülen En İyi Fiyat (Peak): {peak}\n"
                                f"{'='*60}\n"
                            )
                            
                            f_log.write(report_entry + report_exit)
                            f_log.flush() # Dosyaya anında yaz
                            print(f"✅ İşlem Tamamlandı: {pair} | PnL: {pnl:.2f}")
                            return # Pozisyon kapandı, bir sonraki habere geç

        except Exception as e:
            print(f"⚠️ Simülasyon Hatası ({pair}): {e}")

async def run_simulation(model = "LlamaTrader"):
    print("🚀 NEXUS BACKTEST SİMÜLASYONU BAŞLIYOR...")
    
    # Context Hazırlığı
    ctx = BotContext()
    ctx.app_state = SharedState()
    ctx.memory = MockMemory() # DB susturuldu
    ctx.exchange = PaperExchange(1000.0)
    ctx.brain = AgentBrain(
        use_groqcloud=False,
        api_key=GROQCLOUD_API_KEY,
        groqcloud_model=GROQCLOUD_MODEL,
    )
    ctx.brain.ollama_model = model
    # Borsa bağlantısı (Sadece geçmiş veri çekmek için)
    ctx.real_exchange = BinanceExecutionEngine("", "") 
    await ctx.real_exchange.connect()
    
    # Telegram İstemcisi
    path = os.path.realpath(__file__)
    dir = os.path.dirname(path)
    dir = dir.replace("src", "data")
    dir = dir.replace("training", "")
    SESSION_PATH = os.path.join(dir, "crypto_agent_session")
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()

    # Zaman Aralığı (Son 3 Gün)
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    
    results_file = "backtest_results_" + model + ".txt"
    if not os.path.exists(results_file):
        os.makedirs(os.path.dirname(results_file), exist_ok=True)
        
    with open(results_file, "a", encoding="utf-8") as f:
        f.write(f"\n--- SIMULATION RUN: {datetime.now()} ---\n")

        for channel in TARGET_CHANNELS:
            print(f"📡 {channel} kanalı taranıyor...")
            async for message in client.iter_messages(channel, offset_date=three_days_ago, reverse=True):
                
                await simulate_process_news(message, ctx, f)
    
    print(f"--- ✅ SİMÜLASYON BİTTİ. Sonuçlar: {results_file} ---")
    await client.disconnect()

if __name__ == "__main__":
    # Run the simulation for 2 different models
    asyncio.run(run_simulation("LlamaTrader"))