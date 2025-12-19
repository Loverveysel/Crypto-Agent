import time 

class PaperExchange:
    def __init__(self, balance):
        self.balance = balance
        self.positions = {} 
        self.total_pnl = 0.0
        self.history = []


    def open_position(self, symbol, side, price, tp_pct, sl_pct, amount_usdt, leverage, validity, app_state, decision_id):
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
            'validity': validity,
            'decision_id': decision_id  # Decision ID
            
        }
        
        self.balance -= margin
        return f"🔵 POZİSYON AÇILDI: {symbol.upper()} {side} | Giriş: {price} | TP: {tp_pct} | SL: {sl_pct} | VM: {validity}", "info"
    
    def check_positions(self, symbol, current_price):
        if symbol not in self.positions:
            return None, None, None, 0.0, 0.0, None

        pos = self.positions[symbol]
        side = pos['side']
        entry = pos['entry']
        
        # --- 1. REKOR TAKİBİ (PEAK PRICE) ---
        peak_price = entry
        if side == 'LONG':
            current_high = pos.get('highest_price', entry)
            if current_price > current_high:
                pos['highest_price'] = current_price
            peak_price = pos['highest_price']
        else:
            current_low = pos.get('lowest_price', entry)
            if pos.get('lowest_price', 0) == 0 or current_price < current_low:
                pos['lowest_price'] = current_price
            peak_price = pos['lowest_price']

        # --- 2. PNL HESAPLAMA ---
        if side == 'LONG':
            pnl = (current_price - entry) * pos['qty']
        else:
            pnl = (entry - current_price) * pos['qty']
            
        pos['pnl'] = pnl # UI görsün diye kaydet

        # --- 3. TRAILING STOP ---
        roi = 0.0
        if side == 'LONG':
            roi = (current_price - entry) / entry * 100
            if roi > 0.8 and pos['sl'] < entry: pos['sl'] = entry * 1.0015 
            if roi > 1.5:
                new_sl = entry * 1.01 
                if pos['sl'] < new_sl: pos['sl'] = new_sl
        elif side == 'SHORT':
            roi = (entry - current_price) / entry * 100
            if roi > 0.8 and pos['sl'] > entry: pos['sl'] = entry * 0.9985
            if roi > 1.5:
                new_sl = entry * 0.99
                if pos['sl'] > new_sl: pos['sl'] = new_sl

        # --- 4. ÇIKIŞ NEDENLERİ (TIME LIMIT DAHİL) ---
        close_reason = None
        
        # TP/SL Kontrolü
        if side == 'LONG':
            if current_price >= pos['tp']: close_reason = "TAKE PROFIT 💰"
            elif current_price <= pos['sl']: close_reason = "STOP LOSS 🛑"
        else:
            if current_price <= pos['tp']: close_reason = "TAKE PROFIT 💰"
            elif current_price >= pos['sl']: close_reason = "STOP LOSS 🛑"

        # SÜRE KONTROLÜ (SENİN İSTEDİĞİN EXPIRY MANTIĞI)
        # Eğer expiry_time anahtarı yoksa hata vermesin diye .get kullanıyoruz
        if time.time() > pos.get('expiry_time', time.time() + 999999):
            close_reason = "TIME LIMIT ⏳"

        if close_reason:
            # Pozisyonu Kapatmadan önce log verilerini hazırla

            decision_id = pos.get('decision_id') # <--- ID'Yİ ÇEK

            log_msg = f"🏁 KAPANDI: {symbol.upper()} ({close_reason}) | PnL: {pnl:.2f} USDT | Enter: {entry} | Close: {current_price} | Peak Seen: {peak_price}"
            color = "success" if pnl > 0 else "error"
            
            # Kapatma işlemini çağır (Geçmişe kaydeder ve siler)
            self.close_position(symbol, close_reason, pnl)
            
            return log_msg, color, symbol, pnl, peak_price, decision_id

        return None, None, None, 0.0, 0.0, None
    
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