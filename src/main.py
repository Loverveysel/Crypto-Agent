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
USE_MAINNET = True # True = Gerçek Para, False = Testnet
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
TARGET_PAIRS = get_top_pairs(100)
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
    ts = time.strftime("%H:%M:%S")
    full_msg = f"[{ts}] {message}"
    print(full_msg)
    try:
        if log_container: log_container.push(full_msg)
    except: pass

def log_txt(message, filename="trade_logs.txt"):
    filename = os.path.dirname(__file__) + "/../data/" + filename
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
            
            full_log = f"{log}\nSrc: {source}\nReason: {dec.get('reason')}"
            log_ui(full_log, color)
            log_txt(full_log)
            asyncio.create_task(send_telegram_alert(full_log))
            
            dataset_manager.log_trade_entry(pair, msg, str(changes), dec, search_res)

            if REAL_TRADING_ENABLED:
                env_lbl = "TESTNET" if IS_TESTNET else "MAINNET"
                log_ui(f"🚀 {env_lbl} API Emri: {pair}", "error")
                asyncio.create_task(real_exchange.execute_trade(
                    pair, dec['action'], FIXED_TRADE_AMOUNT, LEVERAGE, 
                    dec['tp_pct'], dec['sl_pct']
                ))
        else:
            log_ui(f"🛑 Pas: {pair} {dec['action']} (G: %{dec['confidence']}) - {dec.get('reason')}", "warning")

# --- LOOPLAR ---
async def websocket_loop():
    print("WS Başlatılıyor...")
    while True:
        try:
            async for ws in websockets.connect(WEBSOCKET_URL, ping_interval=None):
                log_ui("WS Bağlandı ✅", "success")
                try:
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        
                        if 'e' in data and data['e'] == 'kline':
                            pair = data['s'].lower()
                            k = data['k']
                            price = float(k['c'])
                            market_memory[pair].update_candle(price, k['t']/1000, k['x'])
                            
                            log, color, closed_sym, pnl = exchange.check_positions(pair, price)
                            if log:
                                log_ui(log, color)
                                log_txt(log)
                                asyncio.create_task(send_telegram_alert(log))
                                if closed_sym:
                                    dataset_manager.log_trade_exit(closed_sym, pnl, "Closed")
                                    if REAL_TRADING_ENABLED:
                                        asyncio.create_task(real_exchange.close_position_market(closed_sym))
                        
                        elif isinstance(data, list): # MiniTicker
                            for item in data:
                                pair = item['s'].lower()
                                if pair in market_memory:
                                    market_memory[pair].set_24h_change(float(item['P']))
                except Exception: pass
        except Exception: await asyncio.sleep(5)

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
    ui.colors(primary='#5898d4', dark='#1d1d1d')
    with ui.header().classes('items-center'):
        ui.icon('smart_toy', size='32px')
        ui.label('CRYPTO AI AGENT').classes('text-h6 font-bold')
        ui.space()
        
        # Stop Butonu
        def toggle(): 
            app_state.is_running = not app_state.is_running
            btn.set_text("ÇALIŞIYOR" if app_state.is_running else "DURDURULDU")
            btn.classes(replace=f"text-white {'bg-green-600' if app_state.is_running else 'bg-red-600'}")
        btn = ui.button("ÇALIŞIYOR", on_click=toggle).classes('bg-green-600')

    # Manuel Giriş
    with ui.row().classes('w-full p-2 gap-2'):
        inp = ui.input(placeholder="Haber gir...").classes('w-3/5').props('dark')
        async def run_man():
            if inp.value: 
                val = inp.value; inp.value = ""
                await process_news(val, "MANUAL")
        ui.button("ANALİZ ET", on_click=run_man)

    # Loglar
    log_container = ui.log(max_lines=1000).classes('w-full h-screen bg-gray-900 text-green-400 font-mono text-sm p-2')

app.on_startup(start_tasks)
ui.run(title="Crypto AI", dark=True, port=8080, reload=False)