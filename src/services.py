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
    REAL_TRADING_ENABLED, IGNORE_KEYWORDS,
    FIXED_TRADE_AMOUNT, LEVERAGE
)
from price_buffer import PriceBuffer

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

async def ensure_fresh_data(ctx, pair):
    """Verinin güncelliğini kontrol eder ve gerekirse eksikleri tamamlar."""
    stats = ctx.market_memory[pair]
    is_stale = False
    current_minute = int(time.time() / 60)

    # Veri bayat mı kontrolü
    if stats.current_price == 0:
        is_stale = True
    elif stats.candles:
        last_candle_time = stats.candles[-1][0]
        if (current_minute - last_candle_time) > 3:
            is_stale = True
    else:
        is_stale = True

    # Bayatsa çek
    if is_stale:
        ctx.log_ui(f"⚠️ {pair} Verisi Bayat/Yok. Taze veri çekiliyor...", "warning")
        hist_data, chg_24h = await ctx.real_exchange.fetch_missing_data(pair)
        
        if hist_data:
            stats.candles.clear()
            for c, t in hist_data: 
                stats.update_candle(c, t, True)
            stats.set_24h_change(chg_24h)
            stats.current_price = hist_data[-1][0]
            return True # Veri başarıyla güncellendi
        else:
            return False # Veri çekilemedi
            
    return True # Veri zaten taze

async def execute_trade_logic(ctx, pair, dec, stats, source, msg, changes, search_res):
    """Karar onaylandıysa işlemi (Real/Paper) gerçekleştirir."""
# ------------------------------------------------------------------
    # MENTÖR GÜNCELLEMESİ: DİNAMİK KASA & KALDIRAÇ YÖNETİMİ
    # ------------------------------------------------------------------
    confidence = dec.get('confidence', 0)
    balance = ctx.exchange.balance
    
    # SEVİYE 1: ÇIRAK (Güven %65 - %74) -> Düşük Risk
    trade_amount = balance * 0.40  # Bakiyenin %20'si
    leverage = 10                   # 5x Kaldıraç (Güvenli
    # SEVİYE 2: USTA (Güven %75 - %84) -> Orta Risk (Standart)
    if confidence >= 75:
        trade_amount = balance * 0.50  # Bakiyenin %40'ı
        leverage = 15                  # 10x Kaldıra
    # SEVİYE 3: BALİNA (Güven %85+) -> "NUCLEAR" Modu ☢️
    if confidence >= 90:
        trade_amount = balance * 0.60  # Bakiyenin %60'ı
        leverage = 20                  # 20x Kaldıraç (Saldır!)
        
        # Terminalde uyarı verelim ki heyecan olsun
        ctx.log_ui(f"☢️ NUCLEAR MOD AKTİF: {pair} için 20x Kaldıraç ve %60 Kasa basılıyor!", "warning")

    tp_pct = dec.get('tp_pct', 2.0)
    sl_pct = dec.get('sl_pct', 1.0)
    validity = dec.get('validity_minutes', 15)
    
    can_open_paper_trade = False
    
    # --- 1. GERÇEK BORSA ---
    if REAL_TRADING_ENABLED:
        api_result = await ctx.real_exchange.execute_trade(
            pair, dec['action'], trade_amount, leverage, tp_pct, sl_pct
        )
        if api_result == "Pozisyon Açma Hatası":
            ctx.log_ui(f"❌ Binance işlemi reddetti: {pair.upper()}. Simülasyon iptal.", "error")
            can_open_paper_trade = False
        elif api_result == "Bağlantı Yok":
             ctx.log_ui("⚠️ API Bağlı değil. Sadece Paper Trading yapılıyor.", "warning")
             can_open_paper_trade = True
        else:
            # "Pozisyon açıldı" veya "TP/SL Hatası" (Manuel takip gerekir) durumlarında paper trade devam eder
            can_open_paper_trade = True
    else:
        can_open_paper_trade = True

    # --- 2. KAĞIT ÜZERİNDE (SİMÜLASYON) & LOGLAMA ---
    if can_open_paper_trade:
        log, color = ctx.exchange.open_position(
            symbol=pair, side=dec['action'], price=stats.current_price, 
            tp_pct=tp_pct, sl_pct=sl_pct, amount_usdt=trade_amount, 
            leverage=leverage, validity=validity, app_state=ctx.app_state,
        )
        
        full_log = log + f'\nSrc: {source}\nReason: {dec.get("reason")}\nNews: {msg}'
        ctx.log_ui(full_log, color)
        log_txt(full_log)
        
        # Dataset ve Telegram
        ctx.dataset_manager.log_trade_entry(
            symbol=pair, news=msg, price_data=str(changes), 
            ai_decision=dec, search_context=search_res, entry_price=stats.current_price
        )
        asyncio.create_task(send_telegram_alert(ctx, full_log))
        
        # WebSocket Takibi Başlat
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [f"{pair.lower()}@kline_1m"],
            "id": int(time.time())
        }
        await ctx.stream_command_queue.put(subscribe_msg)


async def process_news(msg, source, ctx):
    """Haber akışını yöneten ana orkestra şefi."""
    start_time = time.time()
    if not ctx.app_state.is_running: return

    # --- 1. FİLTRELEME & HAZIRLIK ---
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
    
    # --- 2. COIN TESPİTİ ---
    detected_pairs = find_coins(msg, coin_map=TARGET_PAIRS)

    if not detected_pairs:
        ctx.log_ui("⚠️ Regex bulamadı, Ajan'a soruluyor...", "warning")
        found_symbol = await ctx.brain.detect_symbol(msg, TARGET_PAIRS)
        if found_symbol:
            pot_pair = f"{found_symbol.lower()}usdt"
            if pot_pair in TARGET_PAIRS:
                ctx.log_ui(f"🕵️ AJAN BULDU: {found_symbol}", "success")
                detected_pairs.append(pot_pair)

    # --- 3. ANALİZ DÖNGÜSÜ ---
    coin_map = get_top_100_map() # Global cache olsa iyi olur ama şimdilik burada kalsın.

    for pair in detected_pairs:
        # A) Veri Tazeleme (Yardımcı Fonksiyon Çağrısı)
        data_ready = await ensure_fresh_data(ctx, pair)
        if not data_ready:
            ctx.log_ui(f"❌ {pair} verisi çekilemedi, analiz iptal.", "error")
            continue
            
        stats = ctx.market_memory[pair]
        
        # B) Araştırma
        smart_query = await ctx.brain.generate_search_query(msg, pair.replace('usdt',''))
        ctx.log_ui(f"🌍 Araştırılıyor: '{smart_query}'", "info")
        search_res = await perform_research(smart_query)

        # C) Metadata ve Teknik Veriler
        clean_symbol = pair.replace('usdt', '').lower()
        
        # Güvenli Sözlük Erişimi
        c_data = coin_map.get(clean_symbol)
        if isinstance(c_data, dict):
            coin_full_name = c_data.get('name', 'Unknown').title()
            m_cap = c_data.get('cap', 0)
        else:
            coin_full_name = "Unknown"
            m_cap = 0

        # Market Cap Formatlama
        if m_cap > 1_000_000_000: cap_str = f"${m_cap / 1_000_000_000:.2f} BILLION"
        elif m_cap > 1_000_000: cap_str = f"${m_cap / 1_000_000:.2f} MILLION"
        else: cap_str = "UNKNOWN/SMALL"

        rsi_val = stats.calculate_rsi()
        changes = stats.get_all_changes()
        
        # BTC Trend
        btc_stats = ctx.market_memory.get('btcusdt')
        btc_trend = btc_stats.get_change(60) if btc_stats else 0.0

        ctx.log_ui(f"🔍 Analiz Fiyatı ({pair}): {stats.current_price}", "info")

        # D) Yapay Zeka Kararı
        volume_24h, funding_rate = await ctx.real_exchange.get_extended_metrics(pair)
        dec = await ctx.brain.analyze_specific(
            msg, pair, stats.current_price, changes, search_res, 
            coin_full_name, cap_str, rsi_val, btc_trend, volume_24h, funding_rate
        )
        
        #for testing
        """
        dec = {
            "symbol": pair,
            "action": "LONG",
            "confidence": 100,
            "reason": "Test",
            "validity_minutes": 0,
            "tp_pct": 1.5,
            "sl_pct": 1.5,
        }"""

        # Data Collector Kaydı
        ctx.collector.log_decision(msg, pair, stats.current_price, str(changes), dec)
        
        # Dashboard Karar Günlüğü Kaydı
        decision_record = {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "symbol": pair.upper().replace('USDT', ''),
            "action": dec.get('action', 'HOLD'),
            "confidence": dec.get('confidence', 0),
            "reason": dec.get('reason', 'N/A'),
            "price": stats.current_price,
            "news_snippet": msg[:60] + "..."
        }
        ctx.ai_decisions.append(decision_record)
        # ----------------------------------------------------------------------
        # MENTÖR GÜNCELLEMESİ: DERİNLİK KONTROLÜ (DUVAR KORUMASI)
        # ----------------------------------------------------------------------
        # Sadece LONG veya SHORT kararı varsa tahtaya bak (HOLD için bakmaya gerek yok)
        is_order_book_safe = True
        
        if dec['action'] in ['LONG', 'SHORT'] and REAL_TRADING_ENABLED:
            imbalance, depth_info = await ctx.real_exchange.get_order_book_imbalance(pair)
            ctx.log_ui(f"📊 Derinlik Analizi ({pair}): Oran {imbalance:.2f} | {depth_info}", "info")
            
            # KURAL 1: LONG girmek istiyorsun ama Satıcılar (Asks) çok baskın
            # Eğer imbalance < -0.4 ise (Satıcılar %70'ten fazla), LONG girme!
            if dec['action'] == 'LONG' and imbalance < -0.5:
                ctx.log_ui(f"🛑 DUVAR TESPİT EDİLDİ: Aşırı Satış Baskısı ({imbalance:.2f}). LONG İptal.", "warning")
                dec['action'] = 'HOLD' # Kararı zorla HOLD'a çevir
                dec['reason'] += " [CANCELLED: Sell Wall Detected]"
                is_order_book_safe = False

            # KURAL 2: SHORT girmek istiyorsun ama Alıcılar (Bids) çok baskın
            # Eğer imbalance > 0.4 ise (Alıcılar %70'ten fazla), SHORT girme!
            elif dec['action'] == 'SHORT' and imbalance > 0.5:
                ctx.log_ui(f"🛑 DUVAR TESPİT EDİLDİ: Aşırı Alış Baskısı ({imbalance:.2f}). SHORT İptal.", "warning")
                dec['action'] = 'HOLD' # Kararı zorla HOLD'a çevir
                dec['reason'] += " [CANCELLED: Buy Wall Detected]"
                is_order_book_safe = False

        # ------------------------------------------------------------------
            # ADIM 4: SPREAD KONTROLÜ (GİZLİ MALİYET FİLTRESİ)
            # ------------------------------------------------------------------
            # Spread > %0.3 ise girme. 
            # Çünkü kar etmek için fiyatın Spread + Komisyon kadar gitmesi gerekir.
            try:
                # Anlık Ticker verisini çek (En güncel Bid/Ask)
                ticker = await ctx.real_exchange.client.futures_symbol_ticker(symbol=pair.upper())
                bid = float(ticker['bidPrice'])
                ask = float(ticker['askPrice'])
                
                # Spread Hesapla: (Ask - Bid) / Ask
                spread_pct = ((ask - bid) / ask) * 100
                
                ctx.log_ui(f"📏 Spread Analizi ({pair}): %{spread_pct:.3f}", "info")
    
                if spread_pct > 0.3: # Eşik Değer: %0.3 (Bu HFT için çoktur)
                    ctx.log_ui(f"🛑 SPREAD ÇOK YÜKSEK (%{spread_pct:.2f}). Makas açık, girilmez.", "warning")
                    dec['action'] = 'HOLD' # Kararı iptal et
                    dec['reason'] += f" [CANCELLED: High Spread {spread_pct:.2f}%]"
                    is_order_book_safe = False
                    
            except Exception as e:
                # Veri çekemiyorsak risk almayalım
                ctx.log_ui(f"⚠️ Spread verisi alınamadı: {e}", "warning")
                # is_order_book_safe = False # (İsteğe bağlı: Veri yoksa girme diyebilirsin)

        # ----------------------------------------------------------------------
        # E) Karar Uygulama (Yardımcı Fonksiyon Çağrısı)
        if dec['confidence'] >= 65 and dec['action'] in ['LONG', 'SHORT']:
            await execute_trade_logic(ctx, pair, dec, stats, source, msg, changes, search_res)
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
                        except Exception as e:
                            ctx.log_ui(f"WS Error: {e}", "error")
                await asyncio.gather(sender(), receiver())
        except Exception as e:
            ctx.log_ui(f"WS Disconnected (5s): {e}", "error")
            await asyncio.sleep(5)

async def position_monitor_loop(ctx):
    """
    Bekçi Köpeği: Websocket veri akışından bağımsız olarak,
    her 5 saniyede bir pozisyonların süresini ve durumunu kontrol eder.
    """
    ctx.log_ui("🛡️ Position Monitor (Bekçi) Devrede...", "success")
    
    while True:
        try:
            await asyncio.sleep(1) # 5 Saniyede bir kontrol et
            
            if not ctx.exchange.positions:
                continue

            # Sözlük değişirken hata almamak için listeye çevirip dönüyoruz
            open_symbols = list(ctx.exchange.positions.keys())
            
            for pair in open_symbols:
                # Hafızadaki son fiyatı al
                current_price = ctx.market_memory[pair].current_price
                
                # Eğer fiyat 0 ise (henüz veri gelmediyse) pas geç, yanlış kapatmasın
                if current_price == 0: 
                    continue

                # Mevcut kontrol fonksiyonunu çağır (Bu fonksiyon süreyi de kontrol ediyor)
                log, color, closed_sym, pnl, peak_price = ctx.exchange.check_positions(pair, current_price)
                
                if log:
                    # Eğer bir kapatma kararı çıktıysa (Süre doldu veya TP/SL)
                    ctx.log_ui(log, color)
                    log_txt(log)
                    asyncio.create_task(send_telegram_alert(ctx, log))
                    
                    if closed_sym:
                        # 1. Dataset'e kaydet
                        ctx.dataset_manager.log_trade_exit(closed_sym, pnl, "Closed", peak_price)
                        
                        # 2. Gerçek Borsada Kapat
                        if REAL_TRADING_ENABLED:
                            asyncio.create_task(ctx.real_exchange.close_position_market(closed_sym))
                        
                        # 3. Stream Aboneliğini İptal Et (Trafik yapmasın)
                        unsubscribe_msg = {
                            "method": "UNSUBSCRIBE",
                            "params": [f"{closed_sym.lower()}@kline_1m"],
                            "id": int(time.time())
                        }
                        await ctx.stream_command_queue.put(unsubscribe_msg)
                        
                        # 4. Bakiyeyi Güncelle
                        asyncio.create_task(update_system_balance(ctx, last_pnl=pnl))

        except Exception as e:
            ctx.log_ui(f"⚠️ Monitor Loop Hatası: {e}", "error")
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