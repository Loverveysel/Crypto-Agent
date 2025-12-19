import time 

class PaperExchange:
    def __init__(self, balance):
        self.balance = balance
        self.positions = {} 
        self.total_pnl = 0.0
        self.history = []


    def open_position(self, symbol, side, price, tp_pct, sl_pct, amount_usdt, leverage, validity, app_state):
        if not app_state.is_running:
            return "Bot duraklatıldı.", "warning"

        # --- DÜZELTME: ZORUNLU KÜÇÜK HARF ---
        symbol = symbol.lower() 
        # ------------------------------------

        if symbol in self.positions:
            return f"{symbol.upper()} zaten açık!", "warning"

        if self.balance < amount_usdt:
            return "Yetersiz Bakiye!", "error"

        margin = amount_usdt
        qty = (amount_usdt * leverage) / price
        
        # Hedef Fiyatlar
        if side == 'LONG':
            tp = price * (1 + tp_pct/100)
            sl = price * (1 - sl_pct/100)
        else:
            tp = price * (1 - tp_pct/100)
            sl = price * (1 + sl_pct/100)

        self.positions[symbol] = {
            'entry': price,
            'current_price': price, # Başlangıçta aynı
            'highest_price': price, # Peak takibi için
            'lowest_price': price,  # Peak takibi için
            'side': side,
            'margin': margin,
            'qty': qty,
            'lev': leverage,
            'tp': tp,
            'sl': sl,
            'pnl': 0.0,             # Başlangıçta 0
            'start_time': time.time(),
            'validity': validity
        }
        
        self.balance -= margin
        return f"🔵 POZİSYON AÇILDI: {symbol.upper()} {side} | Giriş: {price} | TP: {tp_pct} | SL: {sl_pct}", "info"
    
    def check_positions(self, symbol, current_price):
        # --- DÜZELTME: ZORUNLU KÜÇÜK HARF ---
        symbol = symbol.lower()
        # ------------------------------------

        if symbol not in self.positions:
            return None, None, None, 0.0, 0.0
            
        pos = self.positions[symbol]
        entry = pos['entry']
        side = pos['side']
        
        # 1. FİYAT GÜNCELLEMESİ (UI BUNU OKUR!)
        pos['current_price'] = current_price
        
        # 2. PEAK (ZİRVE) TAKİBİ
        if side == 'LONG':
            if current_price > pos.get('highest_price', entry):
                pos['highest_price'] = current_price
            peak_price = pos['highest_price']
        else:
            if pos.get('lowest_price', 0) == 0 or current_price < pos['lowest_price']:
                pos['lowest_price'] = current_price
            peak_price = pos['lowest_price']

        # 3. PNL HESAPLAMA & KAYDETME (KRİTİK!)
        if side == 'LONG':
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry
            
        pnl = pnl_pct * pos['margin'] * pos['lev']
        pos['pnl'] = pnl  # <--- İşte UI'ın güncellenmesi için gereken satır bu!

        # 4. TRAILING STOP
        roi = pnl_pct * 100
        if side == 'LONG':
            if roi > 0.8 and pos['sl'] < entry: pos['sl'] = entry * 1.0015 
            if roi > 1.5:
                new_sl = entry * 1.01
                if pos['sl'] < new_sl: pos['sl'] = new_sl
        elif side == 'SHORT':
            if roi > 0.8 and pos['sl'] > entry: pos['sl'] = entry * 0.9985
            if roi > 1.5:
                new_sl = entry * 0.99
                if pos['sl'] > new_sl: pos['sl'] = new_sl

        # 5. ÇIKIŞ KONTROLÜ
        close_reason = None
        elapsed_min = (time.time() - pos['start_time']) / 60
        
        if elapsed_min >= pos['validity']: close_reason = "Time Limit"
        elif side == 'LONG' and (current_price >= pos['tp']): close_reason = "Take Profit"
        elif side == 'LONG' and (current_price <= pos['sl']): close_reason = "Stop Loss"
        elif side == 'SHORT' and (current_price <= pos['tp']): close_reason = "Take Profit"
        elif side == 'SHORT' and (current_price >= pos['sl']): close_reason = "Stop Loss"
            
        if close_reason:
            log_msg, color = self.close_position(symbol, close_reason, pnl)
            return log_msg, color, symbol, pnl, peak_price
            
        return None, None, None, 0.0, 0.0
    
    def close_position(self, symbol, reason, pnl):
        # --- DÜZELTME: ZORUNLU KÜÇÜK HARF ---
        symbol = symbol.lower()
        # ------------------------------------

        if symbol not in self.positions: 
            return "Hata: Pozisyon bulunamadı", "error"
        
        pos = self.positions[symbol]
        
        # Bakiyeyi güncelle
        self.balance += pos['margin'] + pnl
        self.total_pnl += pnl
        
        # GEÇMİŞ KAYDI (Burası sende vardı ama PnL 0 geliyordu, artık düzelecek)
        record = {
            'time': time.strftime("%H:%M:%S"),
            'symbol': symbol.upper(),
            'side': pos['side'],
            'pnl': pnl,
            'reason': reason,
            'entry': pos['entry'],
            'exit': pos['current_price'] # Check_positions çalıştığı için bu artık güncel olacak
        }
        self.history.append(record)
        
        del self.positions[symbol]
        
        color = "success" if pnl > 0 else "error"
        return f"🏁 KAPANDI: {symbol.upper()} ({reason}) | PnL: {pnl:.2f} USDT", color