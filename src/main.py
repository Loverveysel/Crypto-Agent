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
BASE_URL = os.getenv('BASE_URL', "wss://stream.binance.com:9443/ws")
WEBSOCKET_URL = BASE_URL # Parametre yok, saf bağlantı.STREAM_PARAMS = "/".join([f"{pair}@kline_1m" for pair in TARGET_PAIRS] + ["!miniTicker@arr"])

# Telegram
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
TELETHON_SESSION_NAME = os.getenv('TELETHON_SESSION_NAME')

# Simülasyon
STARTING_BALANCE = 19.73
LEVERAGE = 10 
FIXED_TRADE_AMOUNT = 9 # USDT

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
# ... (Diğer global nesneler) ...
stream_command_queue = asyncio.Queue() # Websocket'e emir gönderme kanalı
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

async def update_system_balance(last_pnl=0.0):
    """
    İşlem sonrası bakiyeyi günceller.
    - Real Trading Açıksa: Binance'ten en güncel veriyi çeker.
    - Kapalıysa: Simülasyon bakiyesine PnL'i ekler.
    """
    if REAL_TRADING_ENABLED:
        # Binance'ten gerçek bakiyeyi sor (Hafif gecikme ekleyelim ki borsa işlemi işlesin)
        await asyncio.sleep(1) 
        total, available = await real_exchange.get_usdt_balance()
        
        if total > 0:
            old_balance = exchange.balance
            exchange.balance = total # Simülasyonu gerçekle eşitle
            
            diff = total - old_balance
            icon = "📈" if diff >= 0 else "📉"
            log_ui(f"{icon} Bakiye Güncellendi: {total:.2f} USDT (Fark: {diff:+.2f})", "info", save_file=True)
            
    else:
        # Sadece Kağıt Üzerinde (Matematiksel Ekleme)
        exchange.balance += last_pnl
        log_ui(f"📝 Simülasyon Bakiyesi: {exchange.balance:.2f} USDT (PnL: {last_pnl:+.2f})", "info")

async def send_telegram_alert(message):
    try:
        if telegram_client.is_connected():
            await telegram_client.send_message('me', f"🤖 **BOT ALERT**\n{message}")
    except: pass

# --- İŞ MANTIĞI ---
IGNORE_KEYWORDS = ['daily', 'digest', 'recap', 'summary', 'analysis', 'price analysis', 'prediction', 'overview', 'roundup']

async def process_news(msg, source="TELEGRAM"):
    start_time = time.time()
    if not app_state.is_running: return

    clean_msg = msg.replace("— link", "").replace("Link:", "")
    msg_lower = clean_msg.lower()
    
    log_txt(f"[{source}] Gelen Haber: {clean_msg}")
    for word in IGNORE_KEYWORDS:
        if word in msg_lower:
            log_ui(f"🛑 [FİLTRE] Bayat haber: '{word}'", "warning")
            log_txt(f"🛑 [FİLTRE] Bayat haber: '{word}'")
            return

    log_ui(f"[{source}] Taranıyor: {msg[:40]}...", "info")    
    # 1. Regex & Mapping ile Coin Bul (KELİME SINIRI KORUMALI)
    name_map = get_top_100_map()
    
    # search_text'i msg_lower ile başlatıyoruz
    search_text = msg_lower 
    
    for name, ticker in name_map.items():
        # name stringi içinde özel karakterler varsa (. gibi) regex'i bozmasın diye escape ediyoruz
        safe_name = re.escape(name)
        
        # \b : Word Boundary (Kelime Sınırı) demektir.
        # Yani "aster" kelimesinin başında ve sonunda harf olmamalı (boşluk, nokta, virgül veya cümle başı/sonu olabilir).
        # Bu sayede "forecaster", "master", "disaster" kelimelerinde tetiklenmez.
        pattern = r'\b' + safe_name + r'\b'
        
        if re.search(pattern, msg_lower):
            search_text += f" {ticker.lower()} "

    detected_pairs = []
    # Yasaklı/Tehlikeli Kelimeler (Ticker ile karışanlar)
    DANGEROUS_TICKERS = {
        'S', 'THE', 'A', 'I', 'IS', 'TO', 'IT', 'BY', 'ON', 'IN', 'AT', 'OF', 
        'ME', 'MY', 'UP', 'DO', 'GO', 'OR', 'IF', 'BE', 'AS', 'WE', 'SO',
        'NEAR', 'ONE', 'SUN', 'GAS', 'POL', 'BOND', 'OM', 'ELF', 'MEME', 'AI'
    }
    
    for pair in TARGET_PAIRS:
        symbol = pair.replace('usdt', '').upper()
        
        # SENARYO 1: TEHLİKELİ COIN (S, THE, NEAR...)
        if symbol in DANGEROUS_TICKERS:
            # KURAL: 
            # 1. $S (Güvenli)
            # 2. S Token (Solunda ne harf ne de ' işareti olabilir!)
            
            # (?<![\w']) : Negative Lookbehind. 
            # Anlamı: "Eşleşmenin hemen solunda harf, rakam (\w) veya tırnak (') YOKSA kabul et."
            # Bu sayede "User's Token" veya "Permits Token" asla eşleşmez.
            
            # Açıklayıcı kelimeler
            suffixes = r'(Coin|Token|Network|Protocol|Chain|Foundation|DAO|Swap|Finance)'
            
            pattern = rf"(\${symbol}\b)|((?<![\w'])\b{symbol}\s+{suffixes}\b)"
            
            if re.search(pattern, msg, re.IGNORECASE):
                log_ui(f"🕵️ Hassas Ticker Tespit Edildi: {symbol}", "warning", save_file=True)
                detected_pairs.append(pair)
        
        # SENARYO 2: GÜVENLİ COIN (BTC, ETH, SOL...)
        else:
            # search_text içinde arıyoruz (mapping eklenmiş hali)
            if re.search(r'\b' + symbol.lower() + r'\b', search_text):
                detected_pairs.append(pair)

    # 2. Fallback (Ajan Tespiti)
    if not detected_pairs:
        log_ui("⚠️ Regex bulamadı, Ajan'a soruluyor...", "warning")
        found_symbol = await brain.detect_symbol(msg, TARGET_PAIRS)
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            if pot_pair in TARGET_PAIRS:
                log_ui(f"🕵️ AJAN BULDU: {found_symbol}", "success")
                log_txt(f"🕵️ AJAN BULDU: {found_symbol}")
                detected_pairs.append(pot_pair)

    # 3. Analiz Döngüsü
    for pair in detected_pairs:
        stats = market_memory[pair]
        
        
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
        log_txt(f"🌍 Smart Query: '{smart_query}'")
        search_res = await perform_research(smart_query)

        # Karar
        changes = stats.get_all_changes()
        symbol_map = get_top_100_map()
        coin_full_name = symbol_map.get(pair.replace('usdt',''), 'Unknown').title()
        dec = await brain.analyze_specific(msg, pair, stats.current_price, changes, search_res, coin_full_name)
        
        #for testing
        """dec = {
            "action": "LONG",
            "confidence": 80,
            "tp_pct": 2.0,
            "sl_pct": 1.0,
            "reason": "Demo karar",
            "validity_minutes": 0
        }"""
        # Loglama
        collector.log_decision(msg, pair, stats.current_price, str(changes), dec)
        
        if dec['confidence'] >= 75 and dec['action'] in ['LONG', 'SHORT']:
            
            # Değişkenleri hazırla
            trade_amount = FIXED_TRADE_AMOUNT
            leverage = LEVERAGE
            # TP/SL oranlarını karardan al (yoksa varsayılanı kullan)
            tp_pct = dec.get('tp_pct', 2.0)
            sl_pct = dec.get('sl_pct', 1.0)
            validity = dec.get('validity_minutes', 15)

            # --- SAVAŞ PLANI: ÖNCE BINANCE ---
            can_open_paper_trade = False # Kapı kapalı
            
            if REAL_TRADING_ENABLED:
                # 1. GERÇEK İŞLEMİ DENE (Await ile bekle!)
                api_result = await real_exchange.execute_trade(
                    pair, dec['action'], trade_amount, leverage, tp_pct, sl_pct
                )
                
                # 2. SONUCU KONTROL ET
                if api_result == "Pozisyon Açma Hatası":
                    # Kritik hata: Binance reddetti. Simülasyonu da açma!
                    log_ui(f"❌ Binance işlemi reddetti: {pair.upper()}. Simülasyon iptal.", "error", save_file=True)
                    can_open_paper_trade = False
                    
                elif api_result == "TP/SL Yerleştirme Hatası":
                    # Yarı başarılı: Pozisyon açık ama TP/SL yok.
                    # Simülasyonu aç, bot zaten fiyatı takip edip kapatacak.
                    log_ui(f"⚠️ Binance TP/SL hatası: {pair.upper()}. Bot manuel takip edecek.", "warning", save_file=True)
                    can_open_paper_trade = True
                    
                elif api_result == "Pozisyon açıldı":
                    # Başarılı
                    can_open_paper_trade = True
                    
                elif api_result == "Bağlantı Yok":
                     log_ui("⚠️ API Bağlı değil. Sadece Paper Trading yapılıyor.", "warning")
                     can_open_paper_trade = True # API yoksa test için açsın mı? Karar senin. (Burada açsın dedim)

            else:
                # Gerçek işlem kapalıysa direkt simülasyonu aç
                can_open_paper_trade = True

            # --- 3. SİMÜLASYON (KAYIT) İŞLEMİ ---
            if can_open_paper_trade:
                # Paper Trading motorunda pozisyonu aç (Loglama ve takip için şart)
                log, color = exchange.open_position(
                    symbol=pair, 
                    side=dec['action'], 
                    entry_price=stats.current_price, # API'den dönen gerçek fiyatı buraya verebiliriz aslında ama şimdilik böyle kalsın
                    tp_pct=tp_pct, 
                    sl_pct=sl_pct, 
                    amount=trade_amount, 
                    leverage=leverage, 
                    validity_minutes=validity,
                    reason=dec.get('reason', 'N/A'),
                    confidence=dec['confidence']
                )
                
                # Detaylı Log
                full_log = log + f'\nSrc: {source}\nReason: {dec.get("reason")}\nNews: {msg}'
                log_ui(full_log, color, save_file=True)
                
                # Dataset'e kaydet (Eğitim için)
                dataset_manager.log_trade_entry(
                    symbol=pair, 
                    news=msg, 
                    price_data=str(changes), 
                    ai_decision=dec, 
                    search_context= search_text,
                    entry_price=stats.current_price
                )
                
                # Telegram Bildirimi
                asyncio.create_task(send_telegram_alert(full_log))

                # Websocket Stream'i Başlat (Fiyat takibi için)
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": [f"{pair.lower()}@kline_1m"],
                    "id": int(time.time())
                }
                await stream_command_queue.put(subscribe_msg)
        
        else:
            # Pas geçilen işlem (Aynı kalacak)
            log = f"🛑 Pas: {pair.upper()} ({coin_full_name}) | {dec['action']} | (G: %{dec['confidence']}) | Reason : {dec.get('reason')}\nNews: {msg}"
            log_ui(log, "warning", save_file=True)

    end_time = time.time()
    print(f"[{source}] Haber İşleme Süresi: {end_time - start_time:.2f} saniye.")
    log_ui(f"[{source}] Haber İşleme Süresi: {end_time - start_time:.2f} saniye.", "info")

# --- LOOPLAR ---
async def websocket_loop():
    print("[SİSTEM] Websocket Başlatılıyor (Sniper Modu)...")
    
    while True:
        try:
            async with websockets.connect(WEBSOCKET_URL) as ws:
                log_ui("Websocket Bağlandı ✅ (Beklemede)", "success")
                
                # --- İÇ GÖREVLER ---
                # 1. Gönderici (Sender): Kuyruktan emir bekler
                async def sender():
                    while True:
                        command = await stream_command_queue.get()
                        await ws.send(json.dumps(command))
                        log_ui(f"📡 Stream Güncellendi: {command['params']}", "info")

                # 2. Alıcı (Receiver): Sadece abone olunan veriyi işler
                async def receiver():
                    async for msg in ws:
                        try:
                            raw_data = json.loads(msg)
                            
                            # Zarf Açma
                            if 'data' in raw_data:
                                data = raw_data['data']
                            else:
                                data = raw_data

                            # SADECE KLINE VERİSİ (Açık Pozisyonlar İçin)
                            if isinstance(data, dict) and data.get('e') == 'kline':
                                pair = data['s'].lower()
                                k = data['k']
                                price = float(k['c'])
                                is_closed = k['x']
                                ts = k['t'] / 1000
                                
                                # Hafızayı güncelle
                                market_memory[pair].update_candle(price, ts, is_closed)
                                
                                # POZİSYON KONTROLÜ (5 Değer Dönüyor Artık)
                                log, color, closed_sym, pnl, peak_price = exchange.check_positions(pair, price)
                                
                                if log:
                                    # Logu kaydet
                                    log_ui(log, color)
                                    asyncio.create_task(send_telegram_alert(log))
                                    
                                    if closed_sym:
                                        # Dataset Manager'a Peak Price'ı da gönderiyoruz
                                        dataset_manager.log_trade_exit(closed_sym, pnl, "Closed", peak_price)
                                        
                                        if REAL_TRADING_ENABLED:
                                            asyncio.create_task(real_exchange.close_position_market(closed_sym))
                                            
                                        # Yayını kapat
                                        unsubscribe_msg = {
                                            "method": "UNSUBSCRIBE",
                                            "params": [f"{closed_sym.lower()}@kline_1m"],
                                            "id": int(time.time())
                                        }
                                        await stream_command_queue.put(unsubscribe_msg)

                                        asyncio.create_task(update_system_balance(last_pnl=pnl))

                            # BURADA ARTIK 'elif list' YOK.
                            # 'P' hatası veren kısım çöpe atıldı.

                        except Exception as e:
                            # Hata olursa sadece konsola bas, UI'yı kirletme
                            print(f"WS Hata: {e}")
                            log_ui(f"WS İşleme Hatası: {e}", "error")
                            pass

                await asyncio.gather(sender(), receiver())

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
    # 1. API Bağlantısı
    if REAL_TRADING_ENABLED:
        await real_exchange.connect()
        
        # --- YENİ: GERÇEK BAKİYEYİ ÇEK VE SİSTEMİ GÜNCELLE ---
        real_total, real_available = await real_exchange.get_usdt_balance()
        
        if real_total > 0:
            # Simülasyon bakiyesini gerçek bakiye ile eşitle
            exchange.balance = real_total
            exchange.initial_balance = real_total
            
            # Global ayarı da güncelle (Opsiyonel ama iyi olur)
            STARTING_BALANCE = real_total
            
            log_ui(f"✅ Bakiye Eşitlendi: {real_total:.2f} USDT (Kullanılabilir: {real_available:.2f})", "success", save_file=True)
        else:
            log_ui("⚠️ Gerçek bakiye çekilemedi veya 0. Varsayılan kullanılıyor.", "warning")
        # -----------------------------------------------------

    else:
        log_ui("⚠️ Gerçek İşlem Kapalı (Paper Trading Modu)", "warning")
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