from nicegui import ui
import asyncio
import time

def create_dashboard(ctx, on_manual_submit):
    # Renk Paleti (Cyberpunk / Terminal)
    ui.colors(primary='#00B4D8', secondary='#0077B6', accent='#90E0EF', positive='#21BA45', negative='#C10015', dark='#0B0F19')
    
    exchange = ctx.exchange
    app_state = ctx.app_state

    # --- HEADER (Sticky) ---
    with ui.header().classes('bg-dark/90 backdrop-blur-md border-b border-gray-800 p-4 items-center gap-4'):
        with ui.row().classes('items-center gap-2'):
            ui.icon('hub', size='32px').classes('text-primary animate-pulse')
            ui.label('NEXUS AI TERMINAL').classes('text-h6 font-mono font-bold tracking-wider text-white')
        
        ui.space()
        
        # Status Badges
        with ui.row().classes('gap-2'):
            # Bot Durumu
            def toggle_bot():
                app_state.is_running = not app_state.is_running
                status_badge.set_text("SYSTEM: ONLINE" if app_state.is_running else "SYSTEM: PAUSED")
                status_badge.classes(replace=f"text-xs font-bold px-2 py-1 rounded {'bg-positive/20 text-positive' if app_state.is_running else 'bg-negative/20 text-negative'}")
            
            status_badge = ui.label("SYSTEM: ONLINE").classes('text-xs font-bold px-2 py-1 rounded bg-positive/20 text-positive cursor-pointer')
            status_badge.on('click', toggle_bot)
            
            # Bağlantı Durumu (Basit simülasyon)
            ui.label("API: CONNECTED").classes('text-xs font-bold px-2 py-1 rounded bg-blue-500/20 text-blue-400')

    # --- MAIN CONTENT ---
    with ui.tabs().classes('w-full text-gray-400') as tabs:
        dash_tab = ui.tab('KOKPİT', icon='dashboard')
        market_tab = ui.tab('PIYASA', icon='show_chart')
        history_tab = ui.tab('GEÇMİŞ', icon='history')

    with ui.tab_panels(tabs, value=dash_tab).classes('w-full bg-transparent p-0'):
        
        # --- TAB 1: KOKPİT (Ana Ekran) ---
        with ui.tab_panel(dash_tab).classes('p-4 gap-4'):
            
            # 1. KPI İstatistikleri (Üst Bant)
            with ui.grid(columns=4).classes('w-full gap-4 mb-4'):
                def create_kpi(label, value_func, color_func=None, icon='attach_money'):
                    with ui.card().classes('bg-gray-900 border-l-4 border-primary p-3 flex-row gap-3 items-center'):
                        ui.icon(icon, size='md').classes('text-gray-600')
                        with ui.column().classes('gap-0'):
                            ui.label(label).classes('text-xs text-gray-400 uppercase tracking-widest')
                            lbl = ui.label('...').classes('text-xl font-mono font-bold')
                            return lbl
                
                bal_label = create_kpi("Cüzdan", lambda: f"${exchange.balance:.2f}")
                pnl_label = create_kpi("Toplam K/Z", lambda: f"${exchange.total_pnl:.2f}", icon='trending_up')
                win_label = create_kpi("Win Rate", lambda: "%0", icon='pie_chart')
                pos_count_label = create_kpi("Aktif İşlem", lambda: "0", icon='layers')

            # 2. Ana Grid (Pozisyonlar ve Loglar)
            with ui.grid(columns=3).classes('w-full h-[70vh] gap-4'):
                
                # SOL: Aktif Pozisyonlar (2 Kolon Genişliğinde)
                with ui.column().classes('col-span-2 h-full bg-gray-900/50 rounded-lg border border-gray-800 p-4'):
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        ui.label('⚡ AKTİF POZİSYONLAR').classes('text-sm font-bold text-primary')
                        
                        # --- DÜZELTME BURADA YAPILDI ---
                        # size='xs' parametresi silindi, .props('size=xs') eklendi.
                        ui.button('TÜMÜNÜ KAPAT', icon='close', color='negative').props('outline size=xs') 
                    
                    positions_container = ui.column().classes('w-full gap-2 overflow-y-auto pr-2')

                # SAĞ: Live Log Terminali
                with ui.column().classes('col-span-1 h-full bg-black rounded-lg border border-gray-800 p-0 flex flex-col'):
                    ui.label('>_ SYSTEM LOGS').classes('text-xs font-mono text-gray-500 p-2 border-b border-gray-800 bg-gray-900')
                    log_container = ui.log(max_lines=300).classes('w-full h-full p-2 font-mono text-xs text-green-400 leading-tight bg-transparent')
                    
                    # Log Hafıza Nakli
                    if ctx.runtime_logs:
                        for l in ctx.runtime_logs: log_container.push(l)

            # 3. Manuel Input (Alt Bant)
            with ui.row().classes('w-full mt-4 bg-gray-900 p-2 rounded-lg items-center gap-2 border border-gray-800'):
                ui.icon('psychology', size='24px').classes('text-purple-400 ml-2')
                news_input = ui.input(placeholder="Manuel Analiz: 'Bitcoin ETF approved...'").classes('w-full flex-1').props('dark dense borderless')
                async def submit():
                    if news_input.value:
                        await on_manual_submit(news_input.value, "MANUAL")
                        news_input.value = ""
                ui.button(icon='send', on_click=submit).props('flat dense color=primary')

        # --- TAB 2: PİYASA İZLEME ---
        with ui.tab_panel(market_tab).classes('p-4'):
            ui.label('📡 CANLI PİYASA VERİLERİ (MEMORY)').classes('text-lg font-bold mb-4 text-white')
            market_grid = ui.grid(columns=5).classes('w-full gap-3')
            # Burası refresh fonksiyonu ile dolacak

        # --- TAB 3: GEÇMİŞ ---
        with ui.tab_panel(history_tab).classes('p-4'):
            ui.label('📜 İŞLEM GEÇMİŞİ').classes('text-lg font-bold mb-4 text-white')
            history_container = ui.column().classes('w-full gap-2')

    # --- REFRESH LOOP (UI GÜNCELLEME) ---
    def refresh_ui():
        try:
            # 1. KPI Güncelle
            bal_label.set_text(f"${exchange.balance:.2f}")
            pnl_label.set_text(f"${exchange.total_pnl:.2f}")
            pnl_label.classes(replace=f"text-xl font-mono font-bold {'text-positive' if exchange.total_pnl >= 0 else 'text-negative'}")
            
            # Win Rate Hesabı
            total_closed = len(exchange.history)
            wins = len([t for t in exchange.history if t['pnl'] > 0])
            wr = (wins / total_closed * 100) if total_closed > 0 else 0
            win_label.set_text(f"%{wr:.1f} ({wins}/{total_closed})")
            
            pos_count = len(exchange.positions)
            pos_count_label.set_text(str(pos_count))

            # 2. Pozisyon Kartlarını Yenile
            positions_container.clear()
            if not exchange.positions:
                with positions_container:
                    ui.label("Beklemede... İşlem yok.").classes('text-gray-600 italic text-sm w-full text-center mt-10')
            
            for sym, pos in exchange.positions.items():
                pnl = pos['pnl']
                
                pnl_color = "text-positive" if pnl >= 0 else "text-negative"
                border_color = "border-positive" if pnl >= 0 else "border-negative"
                
                with positions_container:
                    with ui.card().classes(f'w-full bg-gray-800 border-l-4 {border_color} p-3 flex flex-row justify-between items-center'):
                        # Sol: Sembol ve Yön
                        with ui.column().classes('gap-0'):
                            with ui.row().classes('gap-2 items-center'):
                                ui.label(sym.upper()).classes('font-bold text-lg text-white')
                                ui.label(f"{pos['side']} {pos['lev']}x").classes(f"text-xs px-1 rounded {'bg-green-900 text-green-300' if pos['side']=='LONG' else 'bg-red-900 text-red-300'}")
                            ui.label(f"Entry: {pos['entry']}").classes('text-xs text-gray-400')
                        
                        # Orta: Anlık Fiyat
                        with ui.column().classes('items-center'):
                            ui.label(f"{pos['current_price']}").classes('font-mono font-bold text-md text-white')
                            ui.label('MARK PRICE').classes('text-[10px] text-gray-500')

                        # Sağ: PnL ve Hedefler
                        with ui.column().classes('items-end'):
                            ui.label(f"${pnl:.2f}").classes(f'font-bold text-xl {pnl_color}')
                            with ui.row().classes('gap-2 text-[10px] text-gray-400'):
                                ui.label(f"TP: {pos['tp']:.2f}")
                                ui.label(f"SL: {pos['sl']:.2f}")

            # 3. Market Tab'ı Yenile (Hafızadaki veriler)
            market_grid.clear()
            with market_grid:
                # Sadece fiyatı 0 olmayanları göster
                active_coins = {k: v for k, v in ctx.market_memory.items() if v.current_price > 0}
                if not active_coins:
                    ui.label("Veri toplanıyor...").classes('col-span-5 text-center text-gray-500')
                
                for pair, buffer in active_coins.items():
                    change_1h = buffer.get_change(60)
                    bg_col = "bg-green-900/30" if change_1h >= 0 else "bg-red-900/30"
                    txt_col = "text-green-400" if change_1h >= 0 else "text-red-400"
                    
                    with ui.card().classes(f'{bg_col} border border-gray-700 p-2 gap-1'):
                        ui.label(pair.upper().replace('USDT','')).classes('font-bold text-xs text-gray-300')
                        ui.label(f"{buffer.current_price:.4f}").classes('font-mono text-sm text-white')
                        ui.label(f"%{change_1h:.2f} (1h)").classes(f'text-xs {txt_col}')

            # 4. Geçmiş Tab'ı Yenile (Son 20 işlem)
            history_container.clear()
            with history_container:
                if not exchange.history:
                    ui.label("Henüz kapanmış işlem yok.").classes('text-gray-500')
                else:
                    # Başlık
                    with ui.row().classes('w-full grid grid-cols-5 text-xs font-bold text-gray-500 border-b border-gray-700 pb-1'):
                        ui.label('ZAMAN')
                        ui.label('SYMBOL')
                        ui.label('YÖN')
                        ui.label('PNL')
                        ui.label('SEBEP')
                    
                    # Satırlar (Ters sırala, en yeni en üstte)
                    for trade in reversed(exchange.history[-20:]):
                        col = "text-green-400" if trade['pnl'] > 0 else "text-red-400"
                        with ui.row().classes('w-full grid grid-cols-5 text-xs py-1 border-b border-gray-800 items-center hover:bg-gray-800/50'):
                            ui.label(trade['time']).classes('text-gray-400')
                            ui.label(trade['symbol']).classes('font-bold text-gray-300')
                            ui.label(trade['side']).classes(f"{'text-green-300' if trade['side']=='LONG' else 'text-red-300'}")
                            ui.label(f"${trade['pnl']:.2f}").classes(f"font-bold {col}")
                            ui.label(trade['reason']).classes('text-gray-500 truncate')

        except Exception as e:
            print(f"UI Refresh Error: {e}")

    ui.timer(1.0, refresh_ui) # 1 saniyede bir güncelle
    return log_container