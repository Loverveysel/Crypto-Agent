
import asyncio
import time
import json
import re
import datetime
import os
import websockets
from telethon import events

from rss_listener import RSSMonitor
from utils import get_top_100_map, perform_research
from config import (
    TARGET_PAIRS, TARGET_CHANNELS, RSS_FEEDS, WEBSOCKET_URL,
    REAL_TRADING_ENABLED, IGNORE_KEYWORDS, DANGEROUS_TICKERS,
    FIXED_TRADE_AMOUNT, LEVERAGE
)

def log_txt(message, filename="trade_logs.txt"):
    # Get the directory of this file (src) and go up one level, then to data
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')
    
    # Ensure data dir exists
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        
    filepath = os.path.join(data_dir, filename)

    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(f"\n### {datetime.datetime.now()} ###\n{message}\n##################\n")

async def update_system_balance(ctx, last_pnl=0.0):
    if REAL_TRADING_ENABLED:
        await asyncio.sleep(1) 
        total, available = await ctx.real_exchange.get_usdt_balance()
        
        if total > 0:
            old_balance = ctx.exchange.balance
            ctx.exchange.balance = total
            
            diff = total - old_balance
            icon = "📈" if diff >= 0 else "📉"
            ctx.log_ui(f"{icon} Bakiye Güncellendi: {total:.2f} USDT (Fark: {diff:+.2f})", "info")
            
    else:
        ctx.exchange.balance += last_pnl
        ctx.log_ui(f"📝 Simülasyon Bakiyesi: {ctx.exchange.balance:.2f} USDT (PnL: {last_pnl:+.2f})", "info")

async def send_telegram_alert(ctx, message):
    try:
        if ctx.telegram_client.is_connected():
            await ctx.telegram_client.send_message('me', f"🤖 **BOT ALERT**\n{message}")
    except: pass

async def process_news(msg, source, ctx):
    start_time = time.time()
    if not ctx.app_state.is_running: return

    is_dup, score = ctx.news_memory.is_duplicate(msg)

    if is_dup:
        ctx.log_ui(f"♻️ [TEKRAR] Haber engellendi (Benzerlik: {score:.2f})", "warning")
        return

    ctx.news_memory.add_news(source, msg)
    
    clean_msg = msg.replace("— link", "").replace("Link:", "")
    msg_lower = clean_msg.lower()
    
    log_txt(f"[{source}] Gelen Haber: {clean_msg}")
    for word in IGNORE_KEYWORDS:
        if word in msg_lower:
            ctx.log_ui(f"🛑 [FİLTRE] Bayat haber: '{word}'", "warning")
            # log_txt(...) is called above already for full msg
            return

    ctx.log_ui(f"[{source}] Taranıyor: {msg[:40]}...", "info")    
    
    name_map = get_top_100_map()
    search_text = msg_lower 
    
    for name, ticker in name_map.items():
        safe_name = re.escape(name)
        pattern = r'\b' + safe_name + r'\b'
        if re.search(pattern, msg_lower):
            search_text += f" {ticker.lower()} "

    detected_pairs = []
    
    for pair in TARGET_PAIRS:
        symbol = pair.replace('usdt', '').upper()
        
        if symbol in DANGEROUS_TICKERS:
            suffixes = r'(Coin|Token|Network|Protocol|Chain|Foundation|DAO|Swap|Finance)'
            pattern = rf"(\${symbol}\b)|((?<![\w'])\b{symbol}\s+{suffixes}\b)"
            
            if re.search(pattern, msg, re.IGNORECASE):
                ctx.log_ui(f"🕵️ Hassas Ticker Tespit Edildi: {symbol}", "warning")
                detected_pairs.append(pair)
        else:
            if re.search(r'\b' + symbol.lower() + r'\b', search_text):
                detected_pairs.append(pair)

    if not detected_pairs:
        ctx.log_ui("⚠️ Regex bulamadı, Ajan'a soruluyor...", "warning")
        found_symbol = await ctx.brain.detect_symbol(msg, TARGET_PAIRS)
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            if pot_pair in TARGET_PAIRS:
                ctx.log_ui(f"🕵️ AJAN BULDU: {found_symbol}", "success")
                log_txt(f"🕵️ AJAN BULDU: {found_symbol}")
                detected_pairs.append(pot_pair)

    for pair in detected_pairs:
        stats = ctx.market_memory[pair]
        
        if stats.current_price == 0:
            ctx.log_ui(f"⚠️ {pair} Backfill yapılıyor...", "warning")
            hist_data, chg_24h = await ctx.real_exchange.fetch_missing_data(pair)
            if hist_data:
                for c, t in hist_data: stats.update_candle(c, t, True)
                stats.set_24h_change(chg_24h)
            else: continue

        smart_query = await ctx.brain.generate_search_query(msg, pair.replace('usdt',''))
        ctx.log_ui(f"🌍 Araştırılıyor: '{smart_query}'", "info")
        log_txt(f"🌍 Smart Query: '{smart_query}'")
        search_res = await perform_research(smart_query)

        changes = stats.get_all_changes()
        symbol_map = get_top_100_map()
        coin_full_name = symbol_map.get(pair.replace('usdt',''), 'Unknown').title()
        
        dec = await ctx.brain.analyze_specific(msg, pair, stats.current_price, changes, search_res, coin_full_name)
        
        ctx.collector.log_decision(msg, pair, stats.current_price, str(changes), dec)
        
        if dec['confidence'] >= 75 and dec['action'] in ['LONG', 'SHORT']:
            
            trade_amount = FIXED_TRADE_AMOUNT
            leverage = LEVERAGE
            tp_pct = dec.get('tp_pct', 2.0)
            sl_pct = dec.get('sl_pct', 1.0)
            validity = dec.get('validity_minutes', 15)

            can_open_paper_trade = False
            
            if REAL_TRADING_ENABLED:
                api_result = await ctx.real_exchange.execute_trade(
                    pair, dec['action'], trade_amount, leverage, tp_pct, sl_pct
                )
                
                if api_result == "Pozisyon Açma Hatası":
                    ctx.log_ui(f"❌ Binance işlemi reddetti: {pair.upper()}. Simülasyon iptal.", "error")
                    can_open_paper_trade = False
                    
                elif api_result == "TP/SL Yerleştirme Hatası":
                    ctx.log_ui(f"⚠️ Binance TP/SL hatası: {pair.upper()}. Bot manuel takip edecek.", "warning")
                    can_open_paper_trade = True
                    
                elif api_result == "Pozisyon açıldı":
                    can_open_paper_trade = True
                    
                elif api_result == "Bağlantı Yok":
                     ctx.log_ui("⚠️ API Bağlı değil. Sadece Paper Trading yapılıyor.", "warning")
                     can_open_paper_trade = True

            else:
                can_open_paper_trade = True

            if can_open_paper_trade:
                log, color = ctx.exchange.open_position(
                    symbol=pair, 
                    side=dec['action'], 
                    price=stats.current_price, 
                    tp_pct=tp_pct, 
                    sl_pct=sl_pct, 
                    amount_usdt=trade_amount, 
                    leverage=leverage, 
                    validity=validity,
                    app_state=ctx.app_state,
                )
                
                full_log = log + f'\nSrc: {source}\nReason: {dec.get("reason")}\nNews: {msg}'
                ctx.log_ui(full_log, color)
                log_txt(full_log)
                
                ctx.dataset_manager.log_trade_entry(
                    symbol=pair, 
                    news=msg, 
                    price_data=str(changes), 
                    ai_decision=dec, 
                    search_context= search_text,
                    entry_price=stats.current_price
                )
                
                asyncio.create_task(send_telegram_alert(ctx, full_log))

                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": [f"{pair.lower()}@kline_1m"],
                    "id": int(time.time())
                }
                await ctx.stream_command_queue.put(subscribe_msg)
        
        else:
            log = f"🛑 Pas: {pair.upper()} ({coin_full_name}) | {dec['action']} | (G: %{dec['confidence']}) | Reason : {dec.get('reason')}\nNews: {msg}"
            ctx.log_ui(log, "warning")

    end_time = time.time()
    print(f"[{source}] Haber İşleme Süresi: {end_time - start_time:.2f} saniye.")
    ctx.log_ui(f"[{source}] Haber İşleme Süresi: {end_time - start_time:.2f} saniye.", "info")

# --- LOOPS ---

async def websocket_loop(ctx):
    print("[SYSTEM] Websocket Starting (Sniper Mode)...")
    
    while True:
        try:
            async with websockets.connect(WEBSOCKET_URL) as ws:
                ctx.log_ui("Websocket Connected ✅ (Standing By)", "success")
                
                async def sender():
                    while True:
                        command = await ctx.stream_command_queue.get()
                        await ws.send(json.dumps(command))
                        ctx.log_ui(f"📡 Stream Updated: {command['params']}", "info")

                async def receiver():
                    async for msg in ws:
                        try:
                            raw_data = json.loads(msg)
                            
                            if 'data' in raw_data:
                                data = raw_data['data']
                            else:
                                data = raw_data

                            if isinstance(data, dict) and data.get('e') == 'kline':
                                pair = data['s'].lower()
                                k = data['k']
                                price = float(k['c'])
                                is_closed = k['x']
                                ts = k['t'] / 1000
                                
                                ctx.market_memory[pair].update_candle(price, ts, is_closed)
                                
                                log, color, closed_sym, pnl, peak_price = ctx.exchange.check_positions(pair, price)
                                
                                if log:
                                    ctx.log_ui(log, color)
                                    asyncio.create_task(send_telegram_alert(ctx, log))
                                    
                                    if closed_sym:
                                        ctx.dataset_manager.log_trade_exit(closed_sym, pnl, "Closed", peak_price)
                                        
                                        if REAL_TRADING_ENABLED:
                                            asyncio.create_task(ctx.real_exchange.close_position_market(closed_sym))
                                            
                                        unsubscribe_msg = {
                                            "method": "UNSUBSCRIBE",
                                            "params": [f"{closed_sym.lower()}@kline_1m"],
                                            "id": int(time.time())
                                        }
                                        await ctx.stream_command_queue.put(unsubscribe_msg)

                                        asyncio.create_task(update_system_balance(ctx, last_pnl=pnl))

                        except Exception as e:
                            print(f"WS Error: {e}")
                            ctx.log_ui(f"WS Processing Error: {e}", "error")
                            pass

                await asyncio.gather(sender(), receiver())

        except Exception as e:
            ctx.log_ui(f"WS Disconnected (5s): {e}", "error")
            await asyncio.sleep(5)

async def telegram_loop(ctx):
    await ctx.telegram_client.start()
    ctx.log_ui("Telegram Listening 📡", "success")
    @ctx.telegram_client.on(events.NewMessage(chats=TARGET_CHANNELS))
    async def handler(event):
        if event.message.message: 
            await process_news(event.message.message, "TELEGRAM", ctx)

async def collector_loop(ctx):
    ctx.log_ui("Data Collector Active 💾", "success")
    while True:
        await asyncio.sleep(60)
        curr_prices = {p: ctx.market_memory[p].current_price for p in TARGET_PAIRS if ctx.market_memory[p].current_price > 0}
        if curr_prices: await ctx.collector.check_outcomes(curr_prices)

async def rss_loop(ctx):
    rss_bot = RSSMonitor(callback_func=lambda msg, src: asyncio.create_task(process_news(msg, src, ctx)))
    await rss_bot.start_loop()
