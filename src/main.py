
import asyncio
from collections import defaultdict
import time
import os
from nicegui import ui, app
from telethon import TelegramClient

# Modules
from config import (
    USE_GROQCLOUD, GROQCLOUD_API_KEY, GROQCLOUD_MODEL,
    USE_MAINNET, REAL_TRADING_ENABLED, API_KEY, API_SECRET, IS_TESTNET,
    TARGET_PAIRS, TARGET_CHANNELS, RSS_FEEDS,
    API_ID, API_HASH, TELETHON_SESSION_NAME,
    STARTING_BALANCE, LEVERAGE, FIXED_TRADE_AMOUNT
)
from exchange import PaperExchange
from brain import AgentBrain
from price_buffer import PriceBuffer
from binance_client import BinanceExecutionEngine
from data_collector import TrainingDataCollector
from dataset_manager import DatasetManager
from news_memory import NewsMemory
from dashboard import create_dashboard
import services

# --- GLOBAL STATE CONTAINER ---
class BotContext:
    def __init__(self):
        self.is_running = True
        self.log_container = None

ctx = BotContext()

# --- INITIALIZATION ---
# 1. Objects
class SharedState:
    def __init__(self): self.is_running = True

ctx.app_state = SharedState()
ctx.market_memory = defaultdict(PriceBuffer)
ctx.exchange = PaperExchange(STARTING_BALANCE)
ctx.brain = AgentBrain(
    use_groqcloud=USE_GROQCLOUD, 
    api_key=GROQCLOUD_API_KEY, 
    groqcloud_model=GROQCLOUD_MODEL
)
ctx.real_exchange = BinanceExecutionEngine(API_KEY, API_SECRET, testnet=IS_TESTNET)
ctx.collector = TrainingDataCollector()
ctx.dataset_manager = DatasetManager()
ctx.telegram_client = TelegramClient(TELETHON_SESSION_NAME, API_ID, API_HASH, use_ipv6=False, timeout=10)
ctx.stream_command_queue = None
ctx.news_memory = NewsMemory()

# 2. Logger Wrapper
def log_ui_wrapper(message, type="info"):
    timestamp = time.strftime("%H:%M:%S")
    icon = "📝"
    if type == "success": icon = "✅"
    elif type == "error": icon = "❌"
    elif type == "warning": icon = "⚠️"
    
    full_msg = f"[{timestamp}] {icon} {message}"
    print(full_msg) 
    
    try:
        if ctx.log_container is not None:
            ctx.log_container.push(full_msg)
    except Exception:
        pass

ctx.log_ui = log_ui_wrapper

# --- STARTUP TASKS ---
async def start_tasks():
    ctx.stream_command_queue = asyncio.Queue()
    # 1. API Connection & Sync
    if REAL_TRADING_ENABLED:
        await ctx.real_exchange.connect()
        
        real_total, real_available = await ctx.real_exchange.get_usdt_balance()
        
        if real_total > 0:
            ctx.exchange.balance = real_total
            ctx.exchange.initial_balance = real_total
            # Note: STARTING_BALANCE is a constant, so we update the instance only
            
            ctx.log_ui(f"✅ Bakiye Eşitlendi: {real_total:.2f} USDT (Kullanılabilir: {real_available:.2f})", "success")
        else:
            ctx.log_ui("⚠️ Gerçek bakiye çekilemedi veya 0. Varsayılan kullanılıyor.", "warning")
    else:
        ctx.log_ui("⚠️ Gerçek İşlem Kapalı (Paper Trading Modu)", "warning")
    
    # 2. Launch Loops
    #asyncio.create_task(services.rss_loop(ctx)) # RSS Loopü devre dışı bırakıldı
    asyncio.create_task(services.websocket_loop(ctx))
    asyncio.create_task(services.telegram_loop(ctx))
    asyncio.create_task(services.collector_loop(ctx))

# --- UI ENTRY POINT ---
@ui.page('/') 
def index():
    async def manual_news_handler(text, source="MANUAL"):
        await services.process_news(text, source, ctx)

    # Create Dashboard and capture the log container
    ctx.log_container = create_dashboard(
        app_state=ctx.app_state,
        exchange=ctx.exchange,
        on_manual_submit=manual_news_handler
    )

app.on_startup(start_tasks)
ui.run(title="Crypto AI", host="0.0.0.0", dark=True, port=8080, reload=False)