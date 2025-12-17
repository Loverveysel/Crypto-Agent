import asyncio
import time
import json
import re
import datetime
import os
import websockets
from telethon import events

from rss_listener import RSSMonitor
from utils import get_top_100_map, perform_research, find_coins
from config import (
    TARGET_CHANNELS, RSS_FEEDS, WEBSOCKET_URL,
    REAL_TRADING_ENABLED, IGNORE_KEYWORDS, DANGEROUS_TICKERS,
    FIXED_TRADE_AMOUNT, LEVERAGE, AMBIGUOUS_COINS
)

TARGET_PAIRS = get_top_100_map()

def log_txt(message, filename="trade_logs.txt"):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')
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
        # Önce bağlı mı diye bak, değilse bağlanmayı dene
        if not ctx.telegram_client.is_connected():
            print("❌ TELEGRAM UYARISI: Bağlantı yok, bağlanmayı dene...")
            await ctx.telegram_client.connect()
        
        # Yetki kontrolü (Session dosyası geçerli mi?)
        if not await ctx.telegram_client.is_user_authorized():
            ctx.log_ui("❌ TELEGRAM UYARISI: Oturum yetkisi yok (Session geçersiz).", "error")
            print("❌ TELEGRAM UYARISI: Oturum yetkisi yok (Session geçersiz).")
            return

        # Mesajı gönder
        await ctx.telegram_client.send_message('me', f"🤖 **BOT ALERT**\n{message}")
        print("✅ TELEGRAM UYARISI: Mesaj gönderildi.")

    except Exception as e:
        # Hatayı gizleme, YÜZÜME VUR!
        print(f"❌ [TELEGRAM SEND ERROR]: {e}")
        ctx.log_ui(f"❌ Telegram Gönderme Hatası: {e}", "error")

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
            return

    ctx.log_ui(f"[{source}] Taranıyor: {msg[:40]}...", "info")    
    
    detected_pairs = find_coins(msg, coin_map=TARGET_PAIRS)

    if not detected_pairs:
        ctx.log_ui("⚠️ Regex bulamadı, Ajan'a soruluyor...", "warning")
        found_symbol = await ctx.brain.detect_symbol(msg, )
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            if pot_pair in TARGET_PAIRS:
                ctx.log_ui(f"🕵️ AJAN BULDU: {found_symbol}", "success")
                detected_pairs.append(pot_pair)

    for pair in detected_pairs:
        stats = ctx.market_memory[pair]
        
        # --- ZOMBİ VERİ KONTROLÜ (MENTÖR DÜZELTMESİ) ---
        # Verinin bayat olup olmadığını kontrol et (Son mum 5 dakikadan eskiyse bayattır)
        is_stale = False
        current_minute = int(time.time() / 60)
        
        if stats.current_price == 0:
            is_stale = True
        elif stats.candles:
            last_candle_time = stats.candles[-1][0] # PriceBuffer'da dakika olarak tutuluyor
            # Eğer son veri 3 dakikadan eskiyse (WebSocket dinlemiyor demektir)
            if (current_minute - last_candle_time) > 3:
                is_stale = True
        else:
            is_stale = True

        if is_stale:
            ctx.log_ui(f"⚠️ {pair} Verisi Bayat/Yok. Taze veri çekiliyor...", "warning")
            # missing data -> (candles list, 24h_change)
            hist_data, chg_24h = await ctx.real_exchange.fetch_missing_data(pair)
            
            if hist_data:
                # Hafızayı temizle ve tazele
                stats.candles.clear()
                for c, t in hist_data: 
                    stats.update_candle(c, t, True)
                stats.set_24h_change(chg_24h)
                # Current price'ı da son mumun kapanışına eşitle ki 0 kalmasın
                if hist_data:
                    stats.current_price = hist_data[-1][0]
            else: 
                ctx.log_ui(f"❌ {pair} verisi çekilemedi, analiz iptal.", "error")
                continue

        smart_query = await ctx.brain.generate_search_query(msg, pair.replace('usdt',''))
        ctx.log_ui(f"🌍 Araştırılıyor: '{smart_query}'", "info")
        log_txt(f"🌍 Smart Query: '{smart_query}'")
        search_res = await perform_research(smart_query)

        # Coin Verilerini Çek
        coin_map = get_top_100_map() # Her seferinde çekmek yavaşlatır, bunu global cache yapmak lazım ama şimdilik böyle kalsın
        
        # Hedef coinin Market Cap'ini bul
        clean_symbol = pair.replace('usdt', '').lower()
        coin_info = coin_map.get(clean_symbol, {'cap': 0, 'name': 'Unknown'})
        market_cap = coin_info.get('cap', 0)
        coin_full_name = coin_info.get('name', 'Unknown').title()
        # 1. RSI Hesapla
        rsi_val = stats.calculate_rsi()
        
        # 2. BTC Trendini Çek (Piyasa Yönü)
        btc_stats = ctx.market_memory.get('btcusdt')
        btc_trend = btc_stats.get_change(60) if btc_stats else 0.0
        
        # Güvenli veri çekimi
        if isinstance(coin_map.get(clean_symbol), dict):
            c_info = coin_map[clean_symbol]
            coin_full_name = c_info.get('name', 'Unknown').title()
            m_cap = c_info.get('cap', 0)
        else:
            # Eski utils.py yapısı dönerse diye fallback
            coin_full_name = str(coin_map.get(clean_symbol, 'Unknown')).title()
            m_cap = 0

        # Market Cap String Formatı
        if m_cap > 1_000_000_000:
            cap_str = f"${m_cap / 1_000_000_000:.2f} BILLION"
        elif m_cap > 1_000_000:
            cap_str = f"${m_cap / 1_000_000:.2f} MILLION"
        else:
            cap_str = "UNKNOWN/SMALL"

        changes = stats.get_all_changes()

        log_txt(f"🔍 Analiz Fiyatı ({pair}): {stats.current_price}")
        ctx.log_ui(f"🔍 Analiz Fiyatı ({pair}): {stats.current_price}", "info")


        volume_24h, funding_rate = await ctx.real_exchange.get_extended_metrics(pair)
        dec = await ctx.brain.analyze_specific(msg, pair, stats.current_price, changes, search_res, coin_full_name, cap_str, rsi_val, btc_trend, volume_24h, funding_rate)
        ctx.collector.log_decision(msg, pair, stats.current_price, str(changes), dec)
        
        if dec['confidence'] >= 75 and dec['action'] in ['LONG', 'SHORT']:
            trade_amount = (ctx.exchange.balance / 2)
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
                    symbol=pair, side=dec['action'], price=stats.current_price, 
                    tp_pct=tp_pct, sl_pct=sl_pct, amount_usdt=(ctx.exchange.balance / 2), 
                    leverage=leverage, validity=validity, app_state=ctx.app_state,
                )
                full_log = log + f'\nSrc: {source}\nReason: {dec.get("reason")}\nNews: {msg}'
                ctx.log_ui(full_log, color)
                log_txt(full_log)
                ctx.dataset_manager.log_trade_entry(
                    symbol=pair, news=msg, price_data=str(changes), 
                    ai_decision=dec, search_context=search_text, entry_price=stats.current_price
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
            log_txt(log)
            asyncio.create_task(send_telegram_alert(ctx, log))

    end_time = time.time()
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
                            if 'data' in raw_data: data = raw_data['data']
                            else: data = raw_data
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
                                    log_txt(log)
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
                        except Exception: pass
                await asyncio.gather(sender(), receiver())
        except Exception as e:
            ctx.log_ui(f"WS Disconnected (5s): {e}", "error")
            await asyncio.sleep(5)

async def telegram_loop(ctx):
    ctx.log_ui("Telegram Bağlanıyor...", "info")
    try:
        await ctx.telegram_client.start()

        print("CONNECTED:", ctx.telegram_client.is_connected())
        print("AUTHORIZED:", await ctx.telegram_client.is_user_authorized())
        await send_telegram_alert(ctx, "Telegram Bağlandı ✅")
        if not await ctx.telegram_client.is_user_authorized():
            ctx.log_ui("❌ TELEGRAM OTURUMU YOK!", "error")
            return

        ctx.log_ui("Telegram Listening 📡", "success")

        @ctx.telegram_client.on(events.NewMessage(chats=TARGET_CHANNELS))
        async def handler(event):
            if event.message.message:
                await process_news(event.message.message, "TELEGRAM", ctx)

        # 🔴 BURASI SİLİNDİ
        # await ctx.telegram_client.run_until_disconnected()

    except Exception as e:
        ctx.log_ui(f"❌ Telegram Hatası: {e}", "error")

async def collector_loop(ctx):
    ctx.log_ui("Data Collector Active 💾", "success")
    while True:
        await asyncio.sleep(60)
        curr_prices = {p: ctx.market_memory[p].current_price for p in TARGET_PAIRS if ctx.market_memory[p].current_price > 0}
        if curr_prices: await ctx.collector.check_outcomes(curr_prices)

async def rss_loop(ctx):
    ctx.log_ui("RSS Modülü Başlatılıyor... 📡", "info")
    # RSSMonitor'a bir loglama ekleyemiyoruz ama başlatıldığını buradan logluyoruz.
    rss_bot = RSSMonitor(callback_func=lambda msg, src: asyncio.create_task(process_news(msg, src, ctx)))
    await rss_bot.start_loop()