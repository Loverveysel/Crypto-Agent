import time 

class PaperExchange:
    def __init__(self, balance):
        self.balance = balance
        self.positions = {} 
        self.total_pnl = 0.0
        self.history = []


    def open_position(self, symbol, side, price, amount_usdt, leverage, tp_pct, sl_pct, app_state, validity):
        if not app_state.is_running: return 

        expiry_time = time.time() + (validity * 60)
        if symbol in self.positions:
            return "⚠️ Pozisyon Zaten Açık", "warning"

        if self.balance < amount_usdt:
            return "❌ Bakiye Yetersiz!", "error"

        tp_price = price * (1 + tp_pct/100) if side == 'LONG' else price * (1 - tp_pct/100)
        sl_price = price * (1 - sl_pct/100) if side == 'LONG' else price * (1 + sl_pct/100)
        
        self.balance -= amount_usdt
        self.positions[symbol] = {
            'entry': price, 'qty': (amount_usdt * leverage) / price,
            'side': side, 'lev': leverage, 'margin': amount_usdt,
            'tp': tp_price, 'sl': sl_price, 'current_price': price,
            'pnl': 0.0,
            'expiry_time': expiry_time,
            'validity': validity,
            'highest_price': price, # Long için en yüksek görülen
            'lowest_price': price   # Short için en düşük görülen
        }
        return f"🔵 POZİSYON AÇILDI: {symbol.upper()} {side} | Giriş: {price} | Top Point : {tp_pct} | Stop Loss : {sl_pct} | VM : {validity} minutes", "info"

    def check_positions(self, symbol, current_price):
        if symbol not in self.positions:
            return None, None, None, 0.0, 0.0 # <-- 5 Değer Dönmeli (Peak Price eklendi)

        pos = self.positions[symbol]
        side = pos['side']
        entry = pos['entry'] # Senin yapında 'entry_price' değil 'entry'
        
        # --- 1. REKOR TAKİBİ (YENİ) ---
        # Long ise en yükseği, Short ise en düşüğü takip et
        # 'highest_price' ve 'lowest_price' anahtarlarını open_position'da eklediğini varsayıyorum.
        # Eğer eklemediysen, hata almamak için .get() ile güvenli çekip güncelliyoruz.
        
        peak_price = entry # Varsayılan olarak giriş fiyatı
        
        if side == 'LONG':
            # Mevcut en yükseği al, yoksa entry kabul et
            current_high = pos.get('highest_price', entry)
            if current_price > current_high:
                pos['highest_price'] = current_price
                current_high = current_price
            peak_price = current_high
            
        else: # SHORT
            # Mevcut en düşüğü al, yoksa entry kabul et
            current_low = pos.get('lowest_price', entry)
            if current_price < current_low:
                pos['lowest_price'] = current_price
                current_low = current_price
            peak_price = current_low
        # -----------------------------

        # PnL Hesaplama (Senin yapına uygun)
        # Formül: (Fiyat Farkı) * Miktar
        # Not: Senin 'qty' dediğin şey aslında (Margin * Kaldıraç / Fiyat) yani Coin Adedi.
        if side == 'LONG':
            pnl = (current_price - entry) * pos['qty']
        else:
            pnl = (entry - current_price) * pos['qty']

        # -----------------------------------------------------------
        # MENTÖR GÜNCELLEMESİ: TRAILING STOP (AKILLI TAKİP)
        # -----------------------------------------------------------
        roi = 0.0
        
        if side == 'LONG':
            roi = (current_price - entry) / entry * 100
            
            # 1. ADIM: ZARARSIZ MOD (Breakeven)
            # Eğer kar %0.8'i geçerse, Stop'u girişin azıcık üstüne çek (Komisyon çıkar)
            if roi > 0.8 and pos['sl'] < entry:
                pos['sl'] = entry * 1.0015 
            
            # 2. ADIM: KARI KİLİTLE (Trailing)
            # Eğer kar %1.5'u geçerse, Stop'u %1.0 kara sabitle.
            # Fiyat daha da artarsa (%2, %3), burayı dinamik yapabilirsin ama şimdilik bu yeter.
            if roi > 1.5:
                new_sl = entry * 1.01 
                if pos['sl'] < new_sl: # Sadece yukarı taşı, asla aşağı indirme!
                    pos['sl'] = new_sl

        elif side == 'SHORT':
            roi = (entry - current_price) / entry * 100
            
            # 1. ADIM: ZARARSIZ MOD
            if roi > 0.8 and pos['sl'] > entry:
                pos['sl'] = entry * 0.9985
                
            # 2. ADIM: KARI KİLİTLE
            if roi > 1.5:
                new_sl = entry * 0.99
                if pos['sl'] > new_sl: # Sadece aşağı taşı, asla yukarı çıkarma!
                    pos['sl'] = new_sl

        # Çıkış Kontrolleri
        close_reason = None
        
        # TP/SL Kontrolü
        if side == 'LONG':
            if current_price >= pos['tp']: close_reason = "TAKE PROFIT 💰"
            elif current_price <= pos['sl']: close_reason = "STOP LOSS 🛑"
        else:
            if current_price <= pos['tp']: close_reason = "TAKE PROFIT 💰"
            elif current_price >= pos['sl']: close_reason = "STOP LOSS 🛑"

        # Süre Kontrolü (Expiry Time ile)
        # Senin yapında 'expiry_time' (timestamp) var, 'validity' (dakika) var.
        # expiry_time'ı kontrol ediyoruz.
        if time.time() > pos['expiry_time']:
            close_reason = "TIME LIMIT ⏳"

        if close_reason:
            # Pozisyonu Kapat ve Sil
            del self.positions[symbol]
            
            log_msg = f"🏁 KAPANDI: {symbol.upper()} ({close_reason}) | PnL: {pnl:.2f} USDT | Enter: {entry} | Close: {current_price} | Peak Seen: {peak_price}"
            color = "success" if pnl > 0 else "error"
            
            # --- 5 DEĞER DÖNDÜRÜYORUZ ---
            # peak_price'ı en sona ekledik
            return log_msg, color, symbol, pnl, peak_price 

        return None, None, None, 0.0, 0.0
    
    def close_position(self, symbol, reason, pnl):
        pos = self.positions[symbol]
        
        # Bakiye güncelle
        self.balance += self.positions[symbol]['margin'] + pnl
        self.total_pnl += pnl
        
        # --- YENİ: GEÇMİŞE KAYDET ---
        trade_record = {
            'symbol': symbol,
            'side': pos['side'],
            'entry': pos['entry'],
            'exit': pos['current_price'],
            'pnl': pnl,
            'reason': reason,
            'time': time.strftime("%H:%M:%S")
        }
        self.history.append(trade_record)
        # -----------------------------

        del self.positions[symbol]
        
        color = "success" if pnl > 0 else "error"
        # Peak price hesaplama (Safety check ile)
        peak = pos.get('highest_price', pos['entry']) if pos['side'] == 'LONG' else pos.get('lowest_price', pos['entry'])
        
        return f"🏁 KAPANDI: {symbol.upper()} ({reason}) | PnL: {pnl:.2f} USDT | Enter: {pos['entry']} | Close: {pos['current_price']}", color