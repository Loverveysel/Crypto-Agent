import sqlite3
import time
import json
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re

class MemoryManager:
    def __init__(self, db_path="nexus_db.sqlite"):
        self.db_path = db_path
        self._init_db()
        self.vectorizer = TfidfVectorizer(stop_words='english')

    def _init_db(self):
        """Veritabanı tablolarını oluşturur."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. TABLO: HABERLER (Eskisi gibi)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                content TEXT,
                timestamp REAL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_timestamp ON news (timestamp)')

        # 2. TABLO: AI KARARLARI (GÜNLÜK)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                action TEXT,
                confidence INTEGER,
                reason TEXT,
                price REAL,
                news_snippet TEXT,
                raw_data TEXT
            )
        ''')

        # 3. TABLO: İŞLEM GEÇMİŞİ (TRADE HISTORY)
        # decision_id: Karar tablosuna bağlayan anahtar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER, 
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                reason TEXT,
                peak_price REAL,
                FOREIGN KEY(decision_id) REFERENCES decisions(id)
            )
        ''')
        
        conn.commit()
        conn.close()

    # --- ESKİ HABER FONKSİYONLARI (Aynen Korundu) ---
    def clean_text(self, text):
        text = text.lower()
        text = re.sub(r'http\S+', '', text)
        text = re.sub(r'[^\w\s]', '', text)
        return text

    def is_duplicate(self, new_text, threshold=0.75):
        clean_new = self.clean_text(new_text)
        if not clean_new.strip(): return True, 1.0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        limit_time = time.time() - (24 * 60 * 60)
        cursor.execute('SELECT content FROM news WHERE timestamp > ? ORDER BY id DESC LIMIT 100', (limit_time,))
        rows = cursor.fetchall()
        conn.close()

        if not rows: return False, 0.0

        past_news = [self.clean_text(row[0]) for row in rows]
        try:
            corpus = past_news + [clean_new]
            tfidf_matrix = self.vectorizer.fit_transform(corpus)
            similarities = cosine_similarity(tfidf_matrix[-1], tfidf_matrix[:-1])
            max_sim = similarities.flatten().max() if similarities.size > 0 else 0.0
            
            if max_sim >= threshold:
                print(f"🛑 [BENZERLİK] Tespit edildi: {max_sim:.2f}")
                return True, max_sim
            return False, max_sim
        except:
            return False, 0.0

    def add_news(self, source, content):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO news (source, content, timestamp) VALUES (?, ?, ?)', 
                          (source, content, time.time()))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ DB Yazma Hatası: {e}")

    # --- YENİ: KARAR VE TRADE KAYIT FONKSİYONLARI ---

    def log_decision(self, record):
        """
        AI Kararını DB'ye kaydeder ve ID'sini döner.
        record: dict
        """
        decision_id = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO decisions (timestamp, symbol, action, confidence, reason, price, news_snippet, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record['time'], record['symbol'], record['action'], record['confidence'], 
                record['reason'], record['price'], record['news_snippet'], json.dumps(record)
            ))
            decision_id = cursor.lastrowid # Bu ID'yi Trade açarken kullanacağız
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ DB Decision Log Hatası: {e}")
        return decision_id

    def log_trade(self, record, decision_id=None):
        """
        Kapanan işlemi DB'ye kaydeder.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (decision_id, timestamp, symbol, side, entry_price, exit_price, pnl, reason, peak_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                decision_id, record['time'], record['symbol'], record['side'],
                record['entry'], record['exit'], record['pnl'], record['reason'], record.get('peak_price', 0)
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ DB Trade Log Hatası: {e}")

    # --- YENİ: YÜKLEME VE RAPORLAMA ---

    def load_recent_history(self, ctx):
        """
        Program açılışında son 100 kararı ve işlemi hafızaya yükler.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # Dict gibi erişmek için
        cursor = conn.cursor()
        
        # 1. Kararları Yükle
        cursor.execute('SELECT * FROM decisions ORDER BY id DESC LIMIT 100')
        decisions = cursor.fetchall()
        for d in reversed(decisions): # Eskiden yeniye ekle (Deque yapısı için)
            rec = {
                "time": d['timestamp'], "symbol": d['symbol'], "action": d['action'],
                "confidence": d['confidence'], "reason": d['reason'], "price": d['price'],
                "news_snippet": d['news_snippet']
            }
            ctx.ai_decisions.append(rec)

        # 2. İşlemleri Yükle
        cursor.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 50')
        trades = cursor.fetchall()
        for t in reversed(trades):
            rec = {
                'time': t['timestamp'], 'symbol': t['symbol'], 'side': t['side'],
                'pnl': t['pnl'], 'reason': t['reason'], 'entry': t['entry_price'],
                'exit': t['exit_price']
            }
            ctx.exchange.history.append(rec)
            
        conn.close()
        print(f"♻️ Hafıza Tazelendi: {len(decisions)} Karar, {len(trades)} İşlem yüklendi.")

    def get_full_trade_story(self):
        """
        Senin istediğin 'Combined Table' verisini çeker.
        Hangi Karar -> Hangi İşleme -> Hangi Sonuca yol açtı?
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # LEFT JOIN: İşlem açılmamış kararları da getir
        query = '''
            SELECT 
                d.timestamp as time, d.symbol, d.action, d.confidence, d.reason as ai_reason,
                t.entry_price, t.exit_price, t.pnl, t.reason as close_reason
            FROM decisions d
            LEFT JOIN trades t ON t.decision_id = d.id
            WHERE d.action IN ('LONG', 'SHORT') -- Sadece aksiyon alınanları gösterelim
            ORDER BY d.id DESC
            LIMIT 100
        '''
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]