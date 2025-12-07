import asyncio
from collections import defaultdict
import json
import time
import datetime
import re
import os
from dotenv import load_dotenv
from telethon import TelegramClient, events
import websockets
from nicegui import ui, app

# Modüller
from exchange import PaperExchange
from brain import AgentBrain
from price_buffer import PriceBuffer
from binance_client import BinanceExecutionEngine
from data_collector import TrainingDataCollector
from dataset_manager import DatasetManager
from utils import get_top_pairs, get_top_100_map, perform_research

# --- AYARLAR ---
load_dotenv()

# GÜVENLİK AYARLARI
USE_MAINNET = False # True = Gerçek Para, False = Testnet
REAL_TRADING_ENABLED = True # API'ye emir gitsin mi?

if USE_MAINNET:
    API_KEY = os.getenv('BINANCE_API_KEY')
    API_SECRET = os.getenv('BINANCE_API_SECRET')
    IS_TESTNET = False
    #raise ValueError("GÜVENLİK: Mainnet şu an kodda kapalı. Açmak için yorum satırlarını kaldır.")
else:
    API_KEY = os.getenv('BINANCE_API_KEY_TESTNET')
    API_SECRET = os.getenv('BINANCE_API_SECRET_TESTNET')
    IS_TESTNET = True

if not API_KEY: raise ValueError("API Key Eksik!")

# DİĞER AYARLAR
TARGET_CHANNELS = ['cointelegraph', 'wublockchainenglish', 'CryptoRankNews', 'TheBlockNewsLite', 'coindesk', 'arkhamintelligence', 'glassnode'] 
TARGET_PAIRS = get_top_pairs(50)
BASE_URL = os.getenv('BASE_URL', "wss://stream.binance.com:9443/stream?streams=")
STREAM_PARAMS = "/".join([f"{pair}@kline_1m" for pair in TARGET_PAIRS] + ["!miniTicker@arr"])
WEBSOCKET_URL = f"{BASE_URL}{STREAM_PARAMS}"

# Telegram
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
TELETHON_SESSION_NAME = os.getenv('TELETHON_SESSION_NAME')

# Simülasyon
STARTING_BALANCE = 22
LEVERAGE = 10 
FIXED_TRADE_AMOUNT = 11 # USDT

# GLOBAL NESNELER
class State:
    def __init__(self): self.is_running = True

app_state = State()
market_memory = defaultdict(PriceBuffer)
exchange = PaperExchange(STARTING_BALANCE)
brain = AgentBrain() 
real_exchange = BinanceExecutionEngine(API_KEY, API_SECRET, testnet=IS_TESTNET)
collector = TrainingDataCollector()
dataset_manager = DatasetManager()
telegram_client = TelegramClient(TELETHON_SESSION_NAME, API_ID, API_HASH)
log_container = None # UI referansı

# --- YARDIMCILAR ---
def log_ui(message, type="info"):
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

def log_txt(message, filename="trade_logs.txt"):
    path = os.path.realpath(__file__)

    # gives the directory where demo.py 
    # exists
    dir = os.path.dirname(path)

    # replaces folder name of Sibling_1 to 
    # Sibling_2 in directory
    dir = dir.replace('src', 'data')

    # changes the current directory to 
    # Sibling_2 folder
    os.chdir(dir)

    filename = filename
    with open(filename, 'a', encoding='utf-8') as f:
        f.write(f"\n### {datetime.datetime.now()} ###\n{message}\n##################\n")

async def send_telegram_alert(message):
    try:
        if telegram_client.is_connected():
            await telegram_client.send_message('me', f"🤖 **BOT ALERT**\n{message}")
    except: pass

# --- İŞ MANTIĞI ---
IGNORE_KEYWORDS = ['daily', 'digest', 'recap', 'summary', 'analysis', 'price analysis', 'prediction', 'overview', 'roundup']

async def process_news(msg, source="TELEGRAM"):
    if not app_state.is_running: return

    clean_msg = msg.replace("— link", "").replace("Link:", "")
    msg_lower = clean_msg.lower()
    
    for word in IGNORE_KEYWORDS:
        if word in msg_lower:
            log_ui(f"🛑 [FİLTRE] Bayat haber: '{word}'", "warning")
            return

    log_ui(f"[{source}] Taranıyor: {msg[:40]}...", "info")
    
    # 1. Regex & Mapping ile Coin Bul
    name_map = get_top_100_map()
    search_text = msg_lower
    for name, ticker in name_map.items():
        if name in msg_lower: search_text += f" {ticker} "

    detected_pairs = []
    for pair in TARGET_PAIRS:
        symbol = pair.replace('usdt', '')
        if re.search(r'\b' + symbol + r'\b', search_text):
            detected_pairs.append(pair)

    # 2. Fallback (Ajan Tespiti)
    if not detected_pairs:
        log_ui("⚠️ Regex bulamadı, Ajan'a soruluyor...", "warning")
        found_symbol = await brain.detect_symbol(msg, TARGET_PAIRS)
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            if pot_pair in TARGET_PAIRS:
                log_ui(f"🕵️ AJAN BULDU: {found_symbol}", "success")
                detected_pairs.append(pot_pair)

    # 3. Analiz Döngüsü
    for pair in detected_pairs:
        stats = market_memory[pair]
        
        # Backfill
        if stats.current_price == 0:
            log_ui(f"⚠️ {pair} Backfill yapılıyor...", "warning")
            hist_data, chg_24h = await real_exchange.fetch_missing_data(pair)
            if hist_data:
                for c, t in hist_data: stats.update_candle(c, t, True)
                stats.set_24h_change(chg_24h)
            else: continue

        # Araştırma
        smart_query = await brain.generate_search_query(msg, pair.replace('usdt',''))
        log_ui(f"🌍 Araştırılıyor: '{smart_query}'", "info")
        search_res = await perform_research(smart_query)

        # Karar
        changes = stats.get_all_changes()
        dec = await brain.analyze_specific(msg, pair, stats.current_price, changes, search_res)

        # Loglama
        collector.log_decision(msg, pair, stats.current_price, str(changes), dec)
        
        if dec['confidence'] > 75 and dec['action'] in ['LONG', 'SHORT']:
            log, color = exchange.open_position(
                pair, dec['action'], stats.current_price, 
                FIXED_TRADE_AMOUNT, LEVERAGE, dec['tp_pct'], dec['sl_pct'], 
                app_state, dec.get('validity_minutes', 15)
            )
            
            print("Top and Stop Price:", dec['tp_pct'], " | ", dec['sl_pct'])

            full_log = f"{log}\nSrc: {source}\nReason: {dec.get('reason')}"
            log_ui(full_log, color)
            log_txt(full_log)
            asyncio.create_task(send_telegram_alert(full_log))
            
            dataset_manager.log_trade_entry(pair, msg, str(changes), dec, search_res)

            if REAL_TRADING_ENABLED:
                env_lbl = "TESTNET" if IS_TESTNET else "MAINNET"
                log_ui(f"🚀 {env_lbl} API Emri: {pair}", "error")
                try:
                    asyncio.create_task(real_exchange.execute_trade(
                        pair, dec['action'], FIXED_TRADE_AMOUNT, LEVERAGE, 
                        dec['tp_pct'], dec['sl_pct']
                    ))
                except Exception as e:
                    log_ui(f"API Emri Hatası: {e}", "error")
                    exchange.close_position(pair.replace('usdt', ''), "API ERROR", 0.0)
        else:
            log_ui(f"🛑 Pas: {pair} {dec['action']} (G: %{dec['confidence']}) - {dec.get('reason')}", "warning")

# --- LOOPLAR ---
async def websocket_loop():
    print("[SİSTEM] Websocket Başlatılıyor...")
    while True:
        try:
            async for ws in websockets.connect(WEBSOCKET_URL, ping_interval=None):
                log_ui("Websocket Bağlandı ✅ (Kline + Ticker)", "success")
                try:
                    while True:
                        msg = await ws.recv()
                        raw_data = json.loads(msg)

                        # --- KRİTİK DÜZELTME: Zarfı Açma ---
                        # Binance Combined Stream verisi {"stream": "...", "data": {...}} formatında gelir.
                        if 'data' in raw_data:
                            data = raw_data['data']
                        else:
                            data = raw_data # Tekli yayınsa direkt al
                        # -----------------------------------

                        # 1. KLINE (Mum Verisi)
                        if isinstance(data, dict) and 'e' in data and data['e'] == 'kline':
                            pair = data['s'].lower()
                            k = data['k']
                            price = float(k['c'])
                            is_closed = k['x']
                            ts = k['t'] / 1000
                            
                            # Hafızayı Güncelle
                            market_memory[pair].update_candle(price, ts, is_closed)
                            
                            # --- BURASI ARTIK ÇALIŞACAK ---
                            log, color, closed_sym, pnl = exchange.check_positions(pair, price)
                            
                            if log:
                                log_ui(log, color)
                                log_txt(log, "data/trade_logs.txt")
                                asyncio.create_task(send_telegram_alert(log))
                                if closed_sym:
                                    dataset_manager.log_trade_exit(closed_sym, pnl, "Closed")
                                    if REAL_TRADING_ENABLED:
                                        asyncio.create_task(real_exchange.close_position_market(closed_sym))

                        # 2. MINITICKER (24s Değişim Listesi)
                        elif isinstance(data, list): 
                            for item in data:
                                pair = item['s'].lower()
                                if pair in market_memory:
                                    # "P" = Price change percent
                                    change_24h = float(item['P'])
                                    market_memory[pair].set_24h_change(change_24h)

                except Exception as e:
                    # Hata basıp durmasın diye pass geçiyoruz, debug için print açabilirsin
                    # print(f"WS Parse Hatası: {e}") 
                    pass
        except Exception as e:
            log_ui(f"WS Koptu (5sn): {e}", "error")
            await asyncio.sleep(5)

async def telegram_loop():
    await telegram_client.start()
    log_ui("Telegram Dinleniyor 📡", "success")
    @telegram_client.on(events.NewMessage(chats=TARGET_CHANNELS))
    async def handler(event):
        if event.message.message: await process_news(event.message.message, "TELEGRAM")

async def collector_loop():
    log_ui("Data Collector Aktif 💾", "success")
    while True:
        await asyncio.sleep(60)
        curr_prices = {p: market_memory[p].current_price for p in TARGET_PAIRS if market_memory[p].current_price > 0}
        if curr_prices: await collector.check_outcomes(curr_prices)

async def start_tasks():
    await real_exchange.connect()
    asyncio.create_task(websocket_loop())
    asyncio.create_task(telegram_loop())
    asyncio.create_task(collector_loop())

# --- UI ---
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
app.on_startup(start_tasks)
ui.run(title="Crypto AI", dark=True, port=8080, reload=False)