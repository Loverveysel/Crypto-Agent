from google import genai
import json
import ollama 
import os
import asyncio
from dotenv import load_dotenv
from google.genai import types

from utils import search_web_sync


class AgentBrain:
    def __init__(self):
        # Ayarları .env'den çek
        self.use_gemini = False
        self.ollama_model = "crypto-agent:gemma" # Fallback
        
        # ORTAK SYSTEM PROMPT (Hem Gemini hem Ollama için)
        self.system_instruction = """
        You are an elite high-frequency crypto trading AI.
        
        CORE RULES:
        1. PAIR SELECTION: I will provide a list of AVAILABLE_COINS. Pick relevant ones based on the news.
        2. INFERENCE: If news says "Satoshi", imply "BTC". If "Vitalik", imply "ETH".
        3. OUTPUT: Return a JSON object with a "trades" list.
        
        JSON STRUCTURE:
        {
          "trades": [
            {
              "symbol": "BTC",
              "action": "LONG" | "SHORT",
              "confidence": 85,
              "tp_pct": 2.5,
              "sl_pct": 1.0,
              "validity_minutes": 15,
              "reason": "Mining upgrade news"
            }
          ]
        }
        """

        if self.use_gemini:
            load_dotenv()
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                print("❌ [HATA] USE_GEMINI=True ama GOOGLE_API_KEY yok!")
                self.use_gemini = False # Fallback to Ollama
            else:
                # Gemini için yapılandırma
                self.gemini_client = genai.Client(api_key=api_key)
                print(f"🧠 [BEYİN] Mod: GEMINI API ({os.getenv('GEMINI_MODEL')})")
        
        if not self.use_gemini:
            print(f"🧠 [BEYİN] Mod: YEREL OLLAMA ({self.ollama_model})")

    async def analyze(self, news, available_pairs):
        # Coin listesini string'e çevir
        coins_str = ", ".join([p.replace('usdt', '').upper() for p in available_pairs])
        
        # User Prompt (Sadece anlık veriyi içerir)
        user_prompt = f"""
        AVAILABLE_COINS: [{coins_str}]
        NEWS: "{news}"
        
        TASK: Identify impacted coins and decide trades. 
        If no relevant coin found or news is irrelevant, return {{ "trades": [] }}
        """

        try:
            # --- YOL AYRIMI ---
            if self.use_gemini:
                # 1. GEMINI YOLU
                generation_config=types.GenerateContentConfig(
                        response_mime_type="application/json", # JSON zorlama modu
                        temperature=0.1,
                    )
                response = self.gemini_client.models.generate_content(
                    model= os.getenv('GEMINI_MODEL'),
                    contents = [self.system_instruction, user_prompt, news],
                    config = generation_config
                )
                return json.loads(response.text)
            
            else:
                # 2. OLLAMA YOLU
                # Ollama için system prompt'u user prompt'un içine eklememiz gerekebilir 
                # (eğer modelfile kullanmıyorsak). Ama sen modelfile kullandığın için
                # system prompt zaten modelin içinde var.
                res = await asyncio.to_thread(
                    ollama.chat, 
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': user_prompt}],
                    format='json', 
                    options={'temperature': 0.1}
                )
                return json.loads(res['message']['content'])

        except Exception as e:
            print(f"❌ [BEYİN HATASI] Analiz başarısız: {e}")
            return {"trades": []}
        
    async def analyze_specific(self, news, symbol, price, changes, search_context=""):
        # 1. Önce coinin profilini çek (Cache'den veya Web'den)
        coin_category = await self.get_coin_profile(symbol)
    # --- DEBUG LOGU (Bunu konsolda görmek istiyorum) ---
        print(f"🐛 [DEBUG] {symbol} Kategorisi: '{coin_category}'")
        prompt = f"""
        TARGET COIN TO TRADE: {symbol.upper()}
        TARGET COIN CATEGORY: {coin_category} (TRUST THIS CATEGORY!)
        
        MARKET DATA:
        - Price: {price}
        - 1m Change: {changes['1m']:.2f}%
        - 10m Change: {changes['10m']:.2f}%
        - 1h Change: {changes['1h']:.2f}%
        - 24h Change: {changes['24h']:.2f}%
        
        NEWS SNIPPET: "{news}"
        RESEARCH CONTEXT: {search_context}

        ACT AS A CYNICAL TRADER. DO NOT BE AN OPTIMIST.
        
        CRITICAL RULES::
        1. IDENTITY CHECK: The news might mention "USDT" or "Stablecoins". DO NOT confuse them with the TARGET COIN ({symbol.upper()}). 
           - If TARGET COIN is ETH, it is NOT a stablecoin, even if USDT is mentioned.
        2. RELEVANCE: Does this news specifically affect {symbol.upper()} price?
           - "Binance Proof of Reserves" is usually NEUTRAL/HOLD unless huge outflow.
        3. SECTOR HYPE: If CATEGORY is "AI" or "Meme" and news is positive, be more aggressive (Higher Confidence).
        4. MOMENTUM CHECK (MULTI-TIMEFRAME):
           - If 1m and 10m are pumping (>3%) -> FOMO Risk. HOLD.
           - If 24h is DOWN but 1m is UP on Good News -> REVERSAL (Good Long).
           - If 24h is UP (>10%) and 1m is UP -> OVERBOUGHT (Risky).
        
        YOUR MISSION:
        1. VALIDATION: Is the news TRULY about {symbol.upper()}? If it's about "Stablecoins" or "General Market", do NOT trade specific alts like LINK/THE. ACTION: HOLD.
        2. SENTIMENT ANALYSIS:
           - "Hacks", "Delisting", "Regulations", "Delayed Launch", "Execs Leaving" -> SHORT.
           - "Price Crash", "Bear Market", "Outflows" -> SHORT.
           - "Partnership", "Mainnet Launch", "ETF Approval" -> LONG.
        3. MOMENTUM CHECK (CRITICAL):
           - If news is BULLISH but price is DOWN/STABLE -> LONG (Sniper Entry).
           - If news is BULLISH but price already PUMPED (>2%) -> HOLD (FOMO Trap).
           - If news is BEARISH but price is UP -> SHORT (Top Short).
           - If news is BEARISH and price is DUMPING -> HOLD (Panic Sell Trap).
        
        JSON OUTPUT ONLY:
        {{
            "action": "LONG" | "SHORT" | "HOLD",
            "confidence": <int>,
            "tp_pct": <float>,
            "sl_pct": <float>,
            "validity_minutes": <int>,
            "reason": "<Explain logic>"
        }}
        """
        try:
            if self.use_gemini:
                response = await self.gemini_client.generate_content_async(prompt)
                return json.loads(response.text)
            else:
                res = await asyncio.to_thread(
                    ollama.chat, 
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': prompt}],
                    format='json', 
                    options={'temperature': 0.1}
                )
                return json.loads(res['message']['content'])
        except Exception as e:
            print(f"[HATA] LLM Analizi: {e}")
            return {"action": "HOLD", "confidence": 0, "reason": "Error"}
        
    async def detect_symbol(self, news, available_pairs):
        """
        Regex başarısız olduğunda LLM'den sembol bulmasını ister.
        """
        # Sadece coin listesini string yap (USDT olmadan)
        coins_str = ", ".join([p.replace('usdt', '').upper() for p in available_pairs])
        
        prompt = f"""
        TASK: Identify the cryptocurrency symbol in this news.
        NEWS: "{news}"
        ALLOWED SYMBOLS: [{coins_str}]
        
        RULES:
        1. If the news talks about "Satoshi" or "Bitcoin", return "BTC".
        2. If news talks about "Ether", return "ETH".
        3. Only return a symbol if it exists in ALLOWED SYMBOLS list.
        4. If no specific coin is found, return null.
        
        JSON OUTPUT ONLY:
        {{
            "symbol": "BTC" | null
        }}
        """
        try:
            # Gemini veya Ollama kullanımı (Mevcut yapına göre)
            if hasattr(self, 'gemini_client') and self.use_gemini:
                response = await self.gemini_client.generate_content_async(prompt)
                res_json = json.loads(response.text)
            else:
                res = await asyncio.to_thread(
                    ollama.chat, 
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': prompt}],
                    format='json', 
                    options={'temperature': 0.0} # Sıfır yaratıcılık
                )
                res_json = json.loads(res['message']['content'])
            
            return res_json.get('symbol')
            
        except Exception as e:
            print(f"[HATA] Sembol Tespiti: {e}")
            return None
        
    async def generate_search_query(self, news, symbol):
        """
        Haberi analiz eder ve araştırmacı gazeteci gibi sorgu üretir.
        """
        # Papağanlığı kırmak için "Reasoning" (Mantık Yürütme) istiyoruz.
        prompt = f"""
        ACT AS A CRYPTO INVESTIGATOR.
        
        INPUT NEWS: "{news}"
        TARGET COIN: {symbol.upper()}
        
        INSTRUCTIONS:
        1. Identify the "Unknown Entity" or "Event" in the news (e.g. a startup name, a VC firm, a new protocol).
        2. IGNORE the coin name ({symbol.upper()}) in the search query. We know the coin. We need to vet the PARTNER.
        3. Construct a search query to expose scams, low liquidity, or fake news.
        
        BAD QUERY: "{symbol} {news}" (Do NOT do this)
        BAD QUERY: "Mugafi partners with Avalanche" (Too specific)
        
        GOOD QUERY: "Mugafi studio funding valuation" (Investigates the partner)
        GOOD QUERY: "Project XYZ scam allegations" (Investigates risks)
        
        OUTPUT FORMAT: Just the search query string. Nothing else.
        """
        
        try:
            if self.use_gemini:
                # Gemini'nin ayarlarını bu çağrı için özel olarak değiştiriyoruz
                # temperature=0.7 -> Yaratıcılığı artırır, papağanlığı azaltır.
                generation_config = genai.types.GenerationConfig(temperature=0.7) 
                response = await self.gemini_client.generate_content_async(prompt, generation_config=generation_config)
                return response.text.strip().replace('"', '')
            else:
                res = await asyncio.to_thread(
                    ollama.chat, 
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': prompt}],
                    # Ollama için de sıcaklığı artırıyoruz
                    options={'temperature': 0.7} 
                )
                return res['message']['content'].strip().replace('"', '')
        except Exception as e:
            print(f"[HATA] Sorgu Üretme: {e}")
            return f"{news[:20]} scam check"
        
    async def get_coin_profile(self, symbol):
        """
        Coinin ne olduğunu (Meme, L1, AI, Stablecoin) hızlıca öğrenir.
        """
        sym = symbol.upper().replace('USDT', '')
        
        # 1. HIZLI LİSTE (Hardcoded)
        # En popüler coinleri elle yazalım ki LLM saçmalamasın.
        known_coins = {
            'BTC': 'Layer-1 (Store of Value)',
            'ETH': 'Layer-1 (Smart Contract)',
            'SOL': 'Layer-1 (High Speed)',
            'BNB': 'Exchange Token / Layer-1',
            'XRP': 'Payment / Layer-1',
            'DOGE': 'Meme Coin',
            'SHIB': 'Meme Coin',
            'ADA': 'Layer-1',
            'AVAX': 'Layer-1',
            'LINK': 'Oracle (Infrastructure)',
            'MATIC': 'Layer-2',
            'UNI': 'DeFi (DEX)',
            'LDO': 'DeFi (Liquid Staking)',
            'USDT': 'Stablecoin',
            'USDC': 'Stablecoin',
            'FDUSD': 'Stablecoin'
        }
        
        if sym in known_coins:
            return known_coins[sym]

        # 2. BİLİNMEYEN COINLER İÇİN ARAMA (Cache)
        if not hasattr(self, 'coin_cache'):
            self.coin_cache = {}
        
        if sym in self.coin_cache:
            return self.coin_cache[sym]
        
        # Hafıza (Cache) - Her seferinde arama yapmasın, bir kere öğrensin yeter
        if not hasattr(self, 'coin_cache'):
            self.coin_cache = {}
        
        if symbol in self.coin_cache:
            return self.coin_cache[symbol]

        # Bilmiyorsa Ara
        query = f"what is {symbol} crypto category sector utility"
        try:
            # DuckDuckGo araması (zaten import etmiştin)
            if self.use_gemini: # Gemini varsa search tool'u kullanabilir veya DDGS
                 # Hız için yine DDGS kullanalım, Gemini'ye metni verelim
                 pass 
            
            # DDGS senkron olduğu için thread'e atıyoruz
            search_text = await asyncio.to_thread(search_web_sync, query)
            
            # Basit bir özetleme yapalım (LLM ile değil, String işlemiyle hız kazan)
            # Ama LLM ile yapmak daha garantidir.
            profile_prompt = f"""
            DATA: {search_text}
            
            TASK: Classify {symbol} into ONE category.
            OPTIONS: [Layer-1, Layer-2, DeFi, AI, Meme, Gaming, Stablecoin, Exchange Token, Infrastructure]
            
            OUTPUT: Just the category name.
            """
            
            if self.use_gemini:
                resp = await self.gemini_client.generate_content_async(profile_prompt)
                category = resp.text.strip()
            else:
                res = await asyncio.to_thread(
                    ollama.chat, 
                    model=self.ollama_model,
                    messages=[{'role': 'user', 'content': profile_prompt}],
                    options={'temperature': 0.0}
                )
                category = res['message']['content'].strip()
            
            # Cache'e kaydet
            self.coin_cache[symbol] = category
            print(f"🧬 [PROFİL] {symbol} sınıflandırıldı: {category}")
            return category

        except Exception as e:
            print(f"Profil Hatası: {e}")
            return "Unknown"