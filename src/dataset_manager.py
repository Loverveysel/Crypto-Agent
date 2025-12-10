import json
import os

class DatasetManager:
    def __init__(self, filename="training_dataset.jsonl"):
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
        self.filename = filename
        # Açık işlemleri burada tutacağız: { 'BTCUSDT': { 'news': '...', 'input_data': '...', 'ai_response': ... } }
        self.open_trades = {}

    def log_trade_entry(self, symbol, news, price_data, ai_decision, search_context="", entry_price=0.0): # <-- entry_price eklendi
        """
        İşlem açıldığında verileri hafızaya atar.
        """
        self.open_trades[symbol] = {
            "news": news,
            "price_data": price_data, 
            "search_context": search_context,
            "original_decision": ai_decision,
            "entry_price": entry_price # <-- YENİ KAYIT
        }
        
    def log_trade_exit(self, symbol, pnl, exit_reason, peak_price=0.0): # <-- peak_price eklendi
        """
        İşlem kapandığında sonucu analiz eder ve Gelişmiş Eğitim Verisi oluşturur.
        """
        if symbol not in self.open_trades:
            return

        trade_data = self.open_trades.pop(symbol)
        
        # Giriş fiyatını hafızadan al (Varsayılan 0.0)
        entry_price = trade_data.get('entry_price', 0.0)
        original_decision = trade_data['original_decision']
        original_action = original_decision.get('action')
        
        # --- EĞİTİM MANTIĞI (HINDSIGHT LABELING) ---
        ideal_response = {}
        
        # SENARYO 1: KAZANDIK (PnL > 0)
        if pnl > 0:
            ideal_response = original_decision
            ideal_response['reason'] += f" [VALIDATED: Trade made profit: {pnl:.2f} USDT]"
        
        # SENARYO 2: KAYBETTİK (PnL < 0)
        else:
            # "Kaçan Balık Büyük müydü?" Analizi
            max_favorable_move_pct = 0.0
            
            if entry_price > 0 and peak_price > 0:
                if original_action == 'LONG':
                    # Long'da: (Zirve - Giriş) / Giriş
                    max_favorable_move_pct = (peak_price - entry_price) / entry_price * 100
                elif original_action == 'SHORT':
                    # Short'ta: (Giriş - Dip) / Giriş
                    max_favorable_move_pct = (entry_price - peak_price) / entry_price * 100
            
            # --- DÜZELTME MANTIĞI ---
            
            # A) Yön Doğruydu ama TP Çok Yüksekti (Almost Won)
            # Eğer fiyat %0.5'ten fazla lehimize gittiyse ama biz kar almadan kapattıysak...
            if max_favorable_move_pct > 0.5:
                ideal_response = original_decision.copy()
                
                # TP'yi düşür (Zirvenin %80'ine)
                new_tp = round(max_favorable_move_pct * 0.8, 2)
                if new_tp < 0.2: new_tp = 0.5 # Minimum koruması
                
                ideal_response['tp_pct'] = new_tp
                ideal_response['reason'] = f"Correction: Direction was correct (Moved {max_favorable_move_pct:.2f}%), but TP was too high. Lower TP to {new_tp}%."
                
            # B) Tamamen Yanlış Karar (Wrong Direction)
            else:
                ideal_response = {
                    "action": "HOLD",
                    "confidence": 100,
                    "reason": f"Correction: The original trade ({original_action}) resulted in a loss of {pnl:.2f} USDT. Safer to wait."
                }

        # --- VERİYİ FORMATLA (Alpaca / Instruction Format) ---
        system_prompt = "You are a crypto trading AI. Analyze the news and market data to decide direction."
        
        user_input = f"""
        DETECTED COIN: {symbol}
        MARKET DATA: {trade_data['price_data']}
        NEWS: "{trade_data['news']}"
        RESEARCH: "{trade_data['search_context']}"
        """
        
        entry = {
            "instruction": system_prompt,
            "input": user_input.strip(),
            "output": json.dumps(ideal_response)
        }

        # Dosyaya ekle
        with open(self.filename, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
        print(f"🎓 [EĞİTİM] Veri Kaydedildi: {symbol} (Peak: {peak_price} | PnL: {pnl:.2f})")