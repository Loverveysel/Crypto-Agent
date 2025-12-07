import asyncio
from collections import defaultdict
import time
import json
from scipy import stats
from telethon import TelegramClient, events
import websockets
from nicegui import ui, app # GUI Kütüphanesi
from exchange import PaperExchange
from brain import AgentBrain
from price_buffer import PriceBuffer
from utils import get_top_pairs
from binance_client import BinanceExecutionEngine # Dosya adın neyse
from data_collector import TrainingDataCollector
from dotenv import load_dotenv
import os 
import datetime
from utils import get_top_100_map, perform_research
import re 
from dataset_manager import DatasetManager

# AYARLAR
REAL_TRADING_ENABLED = True # <--- DİKKAT DÜĞMESİ! False yaparsan sadece simülasyon çalışır.

# İzlenecek Telegram kanallarının/gruplarının ID'leri (veya kullanıcı adları)
TARGET_CHANNELS = ['cointelegraph', 'wublockchainenglish', 'CryptoRankNews', 'TheBlockNewsLite', 'coindesk', 'arkhamintelligence', 'glassnode',  ] 
name_map = get_top_100_map()
# İzlenecek pariteler (küçük harf)
TARGET_PAIRS = get_top_pairs(100)  # Otomatik en çok işlem gören 100 pariteyi al
# --- Environments --- 
load_dotenv()
BASE_URL = os.getenv('BASE_URL')
kline_streams = [f"{pair}@kline_1m" for pair in TARGET_PAIRS]
ticker_stream = ["!miniTicker@arr"] # Tüm marketin 24s değişimini tek kanaldan verir

# Hepsini birleştir
STREAM_PARAMS = "/".join(kline_streams + ticker_stream)
WEBSOCKET_URL = f"{BASE_URL}{STREAM_PARAMS}"
# Telethon
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
TELETHON_SESSION_NAME = os.getenv('TELETHON_SESSION_NAME')
MODEL = os.getenv('MODEL')
# Binance
# BU ŞALTERE DİKKAT ET!
# True  = MAINNET (Gerçek Para Gider)
# False = TESTNET (Binance Kum Havuzu)
USE_MAINNET = False 

if USE_MAINNET:
    API_KEY = os.getenv('BINANCE_API_KEY')
    API_SECRET = os.getenv('BINANCE_API_SECRET')
    IS_TESTNET = False
    print("\n" + "#"*40)
    print("🚨 DİKKAT: MAINNET MODU AKTİF!")
    print("🚨 GERÇEK PARA RİSK ALTINDA!")
    print("#"*40 + "\n")
else:
    API_KEY = os.getenv('BINANCE_API_KEY_TESTNET')
    API_SECRET = os.getenv('BINANCE_API_SECRET_TESTNET')
    IS_TESTNET = True
    print("\n" + "-"*40)
    print("🧪 TESTNET MODU AKTİF")
    print("🧪 İşlemler Binance Test Sunucularında Yapılacak")
    print("-"*40 + "\n")

# --- SİMÜLASYON AYARLARI ---
STARTING_BALANCE = 20 # 20 USDT ile başlıyoruz
LEVERAGE = 5             # 5x Kaldıraç (Acımasız olsun)
FIXED_TRADE_AMOUNT = 10  # Her işleme 10 USDT (Margin) basıyoruz (Total size = 100 USDT)

class State:
    def __init__(self):
        self.is_running = True

# --- GLOBAL NESNELER ---
app_state = State()
market_memory = defaultdict(PriceBuffer)
exchange = PaperExchange(STARTING_BALANCE)
brain = AgentBrain() 
real_exchange = BinanceExecutionEngine(API_KEY, API_SECRET, testnet=IS_TESTNET)
collector = TrainingDataCollector()
telegram_client = TelegramClient(TELETHON_SESSION_NAME, API_ID, API_HASH)
dataset_manager = DatasetManager()

# ---------------------------------------------------------
# UI FONKSİYONLARI (GÜVENLİ HALE GETİRİLDİ)
# ---------------------------------------------------------
def log_txt(message, filename):
    message = f"\n######################\nTime : {datetime.datetime.now()}\n" + message
    message += "\n######################\n"
    open(file=filename, mode='a', encoding='utf-8').write(message)

def log_ui(message, type="info"):
    """Güvenli Loglama"""
    timestamp = time.strftime("%H:%M:%S")
    icon = "📝"
    if type == "success": icon = "✅"
    elif type == "error": icon = "❌"
    elif type == "warning": icon = "⚠️"
    
    full_msg = f"[{timestamp}] {icon} {message}"
    print(full_msg) 
    
    # Try-Except ile "Client deleted" hatasını engelliyoruz
    try:
        if log_container is not None:
            log_container.push(full_msg)
    except Exception:
        pass # UI ölü ise sadece konsola bas ve geç

async def send_telegram_alert(message):
    """
    Kritik olayları Telegram'dan 'Kayıtlı Mesajlar'a gönderir.
    """
    try:
        # Client bağlı mı kontrol et
        if telegram_client.is_connected():
            # 'me' = Kendine (Saved Messages) mesaj at demektir.
            await telegram_client.send_message('me', f"🤖 **CRYPTO AGENT ALERT**\n\n{message}")
    except Exception as e:
        print(f"Telegram Bildirim Hatası: {e}")

# ---------------------------------------------------------
# ANA SAYFA TASARIMI
# ---------------------------------------------------------
@ui.page('/') 
def index():
    global log_container
    
    ui.colors(primary='#5898d4', secondary='#26a69a', accent='#9c27b0', dark='#1d1d1d')
    
    # --- HEADER ---
    with ui.header().classes(replace='row items-center') as header:
        ui.icon('smart_toy', size='32px')
        ui.label('CRYPTO AI AGENT DASHBOARD').classes('text-h6 font-bold')
        ui.space()
        
        # Cüzdan Bilgileri
        with ui.row().classes("gap-4"):
            with ui.column():
                ui.label("CÜZDAN").classes("text-xs text-gray-300")
                balance_label = ui.label(f"${exchange.balance:.2f}").classes("text-xl font-mono font-bold")
            with ui.column():
                ui.label("TOPLAM K/Z").classes("text-xs text-gray-300")
                pnl_label = ui.label("$0.00").classes("text-xl font-mono font-bold text-green-500")
        
        # Durdurma Butonu
        def toggle_bot():
            app_state.is_running = not app_state.is_running
            status_badge.set_text("ÇALIŞIYOR" if app_state.is_running else "DURDURULDU")
            status_badge.classes(replace=f"text-white {'bg-green-600' if app_state.is_running else 'bg-red-600'} px-2 rounded")
            
        status_badge = ui.label("ÇALIŞIYOR").classes("bg-green-600 text-white px-2 rounded font-bold cursor-pointer")
        status_badge.on('click', toggle_bot)

    # --- MANUEL HABER GİRİŞ ALANI (YENİ) ---
    with ui.row().classes('w-full p-4 bg-gray-900 border-b border-gray-700 items-center gap-2'):
        ui.icon('edit_note', size='24px').classes('text-blue-400')
        news_input = ui.input(placeholder="Manuel Haber Simülasyonu: 'Bitcoin ETF approved by SEC...'").classes('w-3/5 text-white').props('dark')
        
        async def manual_submit():
            text = news_input.value
            if text:
                news_input.value = "" # Kutuyu temizle
                # Ortak fonksiyonu çağırıyoruz
                await process_news(text, source="MANUAL")
        
        ui.button('ANALİZ ET & İŞLEME SOK', on_click=manual_submit).classes('bg-blue-600 text-white')

    # --- CONTENT GRID ---
    with ui.grid(columns=2).classes("w-full h-full gap-4 p-4"):
        with ui.column().classes("w-full"):
            ui.label("AÇIK POZİSYONLAR").classes("text-lg font-bold mb-2 text-blue-400")
            positions_container = ui.column().classes("w-full gap-2")
            
        with ui.column().classes("w-full h-screen"):
            ui.label("CANLI LOG AKIŞI").classes("text-lg font-bold mb-2 text-yellow-400")
            log_container = ui.log(max_lines=100).classes("w-full h-96 bg-gray-900 text-green-400 font-mono text-sm p-2 border border-gray-700 rounded")

    # --- LOKAL REFRESH ---
    def refresh_local_ui():
        # (Burası aynı kalacak, önceki kodundaki refresh_local_ui içeriği)
        try:
            balance_label.set_text(f"${exchange.balance:.2f}")
            pnl_label.set_text(f"${exchange.total_pnl:.2f}")
            pnl_label.style(f"color: {'green' if exchange.total_pnl >= 0 else 'red'}")
            
            positions_container.clear()
            with positions_container:
                if not exchange.positions:
                    ui.label("Açık pozisyon yok...").classes("text-gray-500 italic")
                for sym, pos in exchange.positions.items():
                    pnl_color = "text-green-500" if pos['pnl'] >= 0 else "text-red-500"
                    with ui.card().classes("w-full p-2 bg-gray-800 border border-gray-700"):
                        with ui.row().classes("w-full justify-between"):
                            ui.label(f"{sym.upper()} {pos['side']} {pos['lev']}x").classes("font-bold text-lg")
                            ui.label(f"${pos['pnl']:.2f}").classes(f"font-bold text-xl {pnl_color}")
                        with ui.row().classes("text-xs text-gray-400 gap-4"):
                            ui.label(f"Giriş: {pos['entry']}")
                            ui.label(f"Anlık: {pos['current_price']}")
                            ui.label(f"TP: {pos['tp']:.2f}")
                            ui.label(f"SL: {pos['sl']:.2f}")
        except Exception: pass

    ui.timer(1.0, refresh_local_ui)
    
# ---------------------------------------------------------
# ARKA PLAN GÖREVLERİ
# ---------------------------------------------------------
async def start_background_tasks():
    log_ui("Sistem Başlatılıyor...")
    
    # ARTIK HER DURUMDA BAĞLANIYORUZ
    # Çünkü Testnet de olsa Mainnet de olsa bir API bağlantısı şart.
    target_env = "MAINNET 🚨" if USE_MAINNET else "TESTNET 🧪"
    log_ui(f"Borsa Bağlantısı Başlatılıyor ({target_env})...", "warning")
    
    await real_exchange.connect()
    
    asyncio.create_task(websocket_loop())
    asyncio.create_task(telegram_loop())
    asyncio.create_task(collector_loop())

async def websocket_loop():
    print(f"[SİSTEM] Websocket Bağlanıyor...")
    while True:
        try:
            async for ws in websockets.connect(WEBSOCKET_URL, ping_interval=None):
                log_ui("Websocket Bağlandı ✅ (Kline + Ticker)", "success")
                try:
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        
                        # VERİ TİPİ 1: KLINE (Mum Verisi) -> 1m, 10m, 1h hesaplamak için
                        # Format: {"e":"kline", "s":"BTCUSDT", "k":{...}}
                        if 'e' in data and data['e'] == 'kline':
                            kline = data['k']
                            pair = data['s'].lower()
                            close_price = float(kline['c'])
                            is_closed = kline['x'] # Mum kapandı mı?
                            ts = kline['t'] / 1000 # Saniye cinsinden
                            
                            # Hafızayı Güncelle
                            market_memory[pair].update_candle(close_price, ts, is_closed)
                            
                            # Simülasyon Kontrolü (Sadece mum kapandığında veya anlık yapılabilir)
                            # Her saniye yapmamak için sadece is_closed veya belirli aralıkla yapılabilir
                            # Ama senin bot hızlı olsun istiyoruz, her güncellemede yapalım:
                            log, color, closed_symbol, pnl = exchange.check_positions(pair, close_price)
                            if log:
                                log_ui(log, color)
                                log_txt(log, "trade_logs.txt")
                                asyncio.create_task(send_telegram_alert(log))
                                if closed_symbol:
                                    dataset_manager.log_trade_exit(closed_symbol, pnl, "Closed")
                                if closed_symbol and REAL_TRADING_ENABLED:
                                    asyncio.create_task(real_exchange.close_position_market(closed_symbol))

                        # VERİ TİPİ 2: 24 SAAT TİCKER (Toplu gelir)
                        # Format: [{"s":"BTCUSDT", "P":"5.40"...}, ...]
                        elif isinstance(data, list): 
                            for item in data:
                                # Sadece bizim izlediğimiz coinlerse işle
                                pair = item['s'].lower()
                                if pair in market_memory:
                                    # "P" = Price change percent
                                    change_24h = float(item['P'])
                                    market_memory[pair].set_24h_change(change_24h)

                except Exception as e:
                    # log_ui(f"WS Okuma Hatası: {e}", "error")
                    pass
        except Exception as e:
            log_ui(f"WS Koptu (5sn): {e}", "error")
            await asyncio.sleep(5)

IGNORE_KEYWORDS = ['daily', 'digest', 'recap', 'summary', 'analysis', 'price analysis', 'prediction', 'overview', 'roundup', 'market wrap']

async def process_news(msg, source="TELEGRAM"):
    if not app_state.is_running: return

    # 1. TEMİZLİK VE FİLTRELEME (Aynı)
    clean_msg = msg.replace("— link", "").replace("Link:", "")
    msg_lower = clean_msg.lower()
    for word in IGNORE_KEYWORDS:
        if word in msg_lower:
            log_ui(f"🛑 [FİLTRE] Bayat haber: '{word}'", "warning")
            asyncio.create_task(send_telegram_alert(f"🛑 [FİLTRE] Bayat haber: '{word}'"))
            return

    log_ui(f"[{source}] Taranıyor: {msg[:40]}...", "info")
    asyncio.create_task(send_telegram_alert(f"[{source}] Yeni Haber: {msg}"))

    # 2. REGEX İLE PARİTE BULMA (Aynı)
    # ... (Mapping kodların burada kalsın) ...
    name_map = get_top_100_map()
    search_text = msg_lower
    for name, ticker in name_map.items():
        if name in msg_lower: search_text += f" {ticker} "

    detected_pairs = []
    for pair in TARGET_PAIRS:
        symbol = pair.replace('usdt', '')
        if re.search(r'\b' + symbol + r'\b', search_text):
            detected_pairs.append(pair)

    # --- YENİ KISIM: FALLBACK MEKANİZMASI ---
    if not detected_pairs:
        log_ui(f"⚠️ Regex bulamadı, Ajan devreye giriyor...", "warning")
        log_txt(f"[{source}] Regex bulamadı, Ajan devreye giriyor...\nHaber: {msg}", "debug_logs.txt")
        
        # Agent'a sor: "Burada hangi coin var?"
        found_symbol = await brain.detect_symbol(msg, TARGET_PAIRS)
        
        if found_symbol:
            # LLM "BTC" dedi, biz bunu "btcusdt"ye çevirip listemizde var mı bakalım
            potential_pair = f"{found_symbol.lower()}usdt"
            
            if potential_pair in TARGET_PAIRS:
                log_ui(f"🕵️ AJAN BULDU: {found_symbol.upper()} (Regex kaçırmıştı)", "success")
                log_txt(f"[{source}] Ajan buldu: {found_symbol.upper()} (Regex kaçırmıştı)\nHaber: {msg}", "debug_logs.txt")
                asyncio.create_task(send_telegram_alert(f"🕵️ AJAN BULDU: {found_symbol.upper()} (Regex kaçırmıştı)"))
                detected_pairs.append(potential_pair)
            else:
                log_ui(f"⚠️ Ajan '{found_symbol}' buldu ama izleme listemizde yok.", "info")
                log_txt(f"[{source}] Ajan '{found_symbol}' buldu ama izleme listemizde yok.\nHaber: {msg}", "debug_logs.txt")
                asyncio.create_task(send_telegram_alert(f"⚠️ Ajan '{found_symbol}' buldu ama izleme listemizde yok."))
        else:
            # Ajan da bulamadıysa gerçekten yoktur
            log_ui(f"[{source}] İlgili coin bulunamadı.", "info")
            asyncio.create_task(send_telegram_alert(f"[{source}] İlgili coin bulunamadı."))
            return

    # 4. BULUNAN HER COİN İÇİN LLM ANALİZİ
    # Genelde tek coin çıkar ama bazen "BTC and ETH" haberleri olur.
    for pair in detected_pairs:
        stats = market_memory[pair]
        
        # Fiyat verisi yoksa (Websocket daha veri atmadıysa)
        if stats.current_price == 0:
            log_ui(f"⚠️ {pair.upper()} RAM'de yok, API'den 'Backfill' yapılıyor...", "warning")
            
            # Binance Client üzerinden geçmiş veriyi çek
            history_data, change_24h = await real_exchange.fetch_missing_data(pair)
            if history_data:
                # Veriyi hafızaya doldur (Backfill)
                for close_price, ts in history_data:
                    # is_closed=True diyoruz ki geçmiş listesine eklesin
                    stats.update_candle(close_price, ts, is_closed=True)
                
                # 24s Değişimi de ayarla
                stats.set_24h_change(change_24h)
                
                log_ui(f"✅ {pair.upper()} verisi kurtarıldı. Analize devam ediliyor.", "success")
            else:
                # API'den de çekemediysek yapacak bir şey yok, şimdi hata ver
                log_ui(f"❌ {pair.upper()} verisi API'den de alınamadı. Atlanıyor.", "error")
                continue
        
        all_changes = stats.get_all_changes()
        log_ui(f"🔍 Analiz: {pair.upper()} | 1m: {all_changes['1m']:.2f}% | 24h: {all_changes['24h']:.2f}%", "info")
        log_txt(f"[{source}] {pair.upper()} tespit edildi. Fiyat: {stats.current_price}, 1dk Değişim: %{stats.get_change(60):.2f}\nHaber: {msg}", "debug_logs.txt")
        asyncio.create_task(send_telegram_alert(f"🔍 TESPİT: {pair.upper()} | Değişim: %{stats.get_change(60):.2f} | LLM'e Soruluyor..."))

        # --- YENİ KISIM: AKILLI ARAŞTIRMA ---
        log_ui(f"🧠 {pair.upper()} için arama stratejisi oluşturuluyor...", "info")
        
        # 1. Ajan ne arayacağına karar verir
        smart_query = await brain.generate_search_query(msg, pair.replace('usdt',''))
        
        log_ui(f"🌍 Botun Araması: '{smart_query}'", "info")
        
        # 2. Arama yapılır
        search_results = await perform_research(smart_query)
        
        log_ui(f"🔍 Analiz Ediliyor: {pair.upper()} | Değişim: %{stats.get_change(60):.2f}", "info")
        all_changes = stats.get_all_changes()
        # 3. Sonuçlarla birlikte analiz edilir
        dec = await brain.analyze_specific(
            news=msg, 
            symbol=pair, 
            price=stats.current_price, 
            changes=all_changes, # <--- ARTIK SÖZLÜK GÖNDERİYORUZ
            search_context=search_results
        )
        # 5. DATA COLLECTOR (Eğitim için kaydet)
        collector.log_decision(msg, pair, stats.current_price, stats.get_change(60), dec)

        # 6. SONUÇ VE İŞLEM
        if dec['confidence'] > 75 and dec['action'] in ['LONG', 'SHORT']:
            
            # --- NOT: Artık Python tarafında Momentum Check yok ---
            # --- LLM, verdiğimiz % değişim verisine göre buna kendi karar verdi ---
            
            validity = dec.get('validity_minutes', 15)
            
            # A. Paper Trading
            log, color = exchange.open_position(
                symbol=pair,
                side=dec['action'],
                price=stats.current_price,
                amount_usdt=FIXED_TRADE_AMOUNT,
                leverage=LEVERAGE,
                tp_pct=dec['tp_pct'],
                sl_pct=dec['sl_pct'],
                validity=validity,
                app_state=app_state
            )
            
            full_log = log + f'\nSrc: {source}\nReason: {dec.get("reason")}\nNews: {msg}\nConfidence: %{dec["confidence"]}\n'
            log_ui(full_log, color)
            log_txt(full_log, "trade_logs.txt")
            # --- YENİ: TELEGRAM BİLDİRİMİ ---
            # İşlem açıldığı an cebine mesaj gelsin
            asyncio.create_task(send_telegram_alert(full_log))

            dataset_manager.log_trade_entry(
                symbol=pair,
                news=msg,
                price_data=f"Price: {stats.current_price}, 1m Chg: {stats.get_change(60):.2f}%",
                ai_decision=dec,
                search_context=search_results if 'search_results' in locals() else ""
            )

            # B. Real Trading
            if REAL_TRADING_ENABLED:
                env_label = "MAINNET" if USE_MAINNET else "TESTNET"
                log_ui(f"🚀 {env_label} EMRİ: {pair.upper()}", "error")


                asyncio.create_task(real_exchange.execute_trade(
                    symbol=pair,
                    side=dec['action'],
                    amount_usdt=FIXED_TRADE_AMOUNT,
                    leverage=LEVERAGE,
                    tp_pct=dec['tp_pct'],
                    sl_pct=dec['sl_pct']
                ))
        else:
            log = f"[{source}] {pair.upper()} HOLD. Reason: {dec.get('reason')} (Güven: %{dec['confidence']})"
            log_ui(log, "warning")
            log_txt(f"Pas Geçildi: {pair.upper()} {dec['action']} (Güven: %{dec['confidence']})\nHaber: {msg}", "trade_logs.txt")
            asyncio.create_task(send_telegram_alert(log))



async def telegram_loop():
    await telegram_client.start()
    log_ui(f"Telegram {len(TARGET_CHANNELS)} Kanalı Dinliyor 📡", "success")
    
    @telegram_client.on(events.NewMessage(chats=TARGET_CHANNELS))
    async def handler(event):
        msg = event.message.message
        if msg:
            # Tüm mantığı process_news'e devrettik
            await process_news(msg, source="TELEGRAM")

async def collector_loop():
    """Eğitim verilerini kontrol eden düşük öncelikli döngü"""
    log_ui("Data Collector Başlatıldı 💾", "success")
    while True:
        try:
            await asyncio.sleep(60) # Her 60 saniyede bir kontrol et (PC'yi yormaz)
            
            if not market_memory: continue
            
            # Anlık fiyatları çek
            current_prices_dict = {p: market_memory[p].current_price for p in TARGET_PAIRS if market_memory[p].current_price > 0}
            
            if current_prices_dict:
                await collector.check_outcomes(current_prices_dict)
                
        except Exception as e:
            print(f"Collector Hatası: {e}")

# UYGULAMAYI BAŞLAT
app.on_startup(start_background_tasks)
ui.run(title="Crypto AI Agent", dark=True, port=8080, reload=False)