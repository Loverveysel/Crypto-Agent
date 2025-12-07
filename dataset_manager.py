import json
import os

class DatasetManager:
    def __init__(self, filename="training_dataset.jsonl"):
        self.filename = filename
        # Açık işlemleri burada tutacağız: { 'BTCUSDT': { 'news': '...', 'input_data': '...', 'ai_response': ... } }
        self.open_trades = {}

    def log_trade_entry(self, symbol, news, price_data, ai_decision, search_context=""):
        """
        İşlem açıldığında verileri hafızaya atar.
        """
        self.open_trades[symbol] = {
            "news": news,
            "price_data": price_data, # Fiyat, değişim vs.
            "search_context": search_context,
            "original_decision": ai_decision
        }

    def log_trade_exit(self, symbol, pnl, exit_reason):
        """
        İşlem kapandığında sonucu analiz eder ve eğitim verisi oluşturur.
        """
        if symbol not in self.open_trades:
            return

        trade_data = self.open_trades.pop(symbol)
        
        # --- EĞİTİM MANTIĞI (HINDSIGHT LABELING) ---
        # Burası sihrin gerçekleştiği yer.
        
        ideal_response = {}
        
        # SENARYO 1: KAZANDIK (PnL > 0)
        # Modelin kararı doğruydu. Olduğu gibi ödüllendir.
        if pnl > 0:
            ideal_response = trade_data['original_decision']
            ideal_response['reason'] += f" [VALIDATED: Trade made profit: {pnl:.2f} USDT]"
        
        # SENARYO 2: KAYBETTİK (PnL < 0)
        # Model yanlış yaptı. Onu düzeltiyoruz.
        # "LONG" dediyse "HOLD" veya "SHORT" demeliydi.
        else:
            bad_action = trade_data['original_decision'].get('action')
            
            # Basit Düzeltme: Kaybettiren işlem yerine "HOLD" öğretelim.
            ideal_response = {
                "action": "HOLD",
                "confidence": 100,
                "reason": f"Correction: The original trade ({bad_action}) resulted in a loss of {pnl:.2f} USDT. Safer to wait."
            }

        # --- VERİYİ FORMATLA (Alpaca / Instruction Format) ---
        # LLM'e vereceğimiz format.
        
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

        # Dosyaya ekle (JSONL formatı: Her satır bir JSON)
        with open(self.filename, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
        print(f"🎓 [EĞİTİM] Veri Kaydedildi: {symbol} ({'BAŞARI' if pnl > 0 else 'DÜZELTME'})")