from google import genai
import json
import ollama 
import os
import asyncio
from dotenv import load_dotenv
from google.genai import types

from utils import search_web_sync, coin_categories


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
            
            # --- YENİ: MODEL ISITMA VE KİLİTLEME ---
            print("🔥 [SİSTEM] Model VRAM'e yükleniyor ve kilitleniyor (Keep-Alive)...")
            try:
                # keep_alive=-1 demek "Ben kapatana kadar model hafızada kalsın" demektir.
                ollama.chat(model=self.ollama_model, messages=[{'role': 'user', 'content': 'hi'}], keep_alive=-1)
                print("✅ [SİSTEM] Model yüklendi ve hazır!")
            except Exception as e:
                print(f"⚠️ Model yükleme uyarısı: {e}")

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
                    options={'temperature': 0.1},
                    keep_alive=-1 # 5 dakika açık tut
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
        TARGET COIN: {symbol.upper()}
        OFFICIAL CATEGORY: {coin_category} (TRUST THIS CATEGORY ABSOLUTELY!)
        
        MARKET MOMENTUM:
        - Price: {price}
        - 1m Change: {changes['1m']:.2f}%
        - 10m Change: {changes['10m']:.2f}%
        - 1h Change: {changes['1h']:.2f}%
        - 24h Change: {changes['24h']:.2f}%
        
        NEWS SNIPPET: "{news}"
        RESEARCH CONTEXT: "{search_context}"

        ROLE: You are an AGGRESSIVE SCALPER. Do not hold positions.
        
        CRITICAL RULES (PRIORITY 1):
        1. IDENTITY: If TARGET COIN is 'ETH' (Layer-1), do NOT treat it as 'Stablecoin' even if news mentions USDT.
        2. RELEVANCE: Ensure news is specifically about {symbol.upper()}. Ignore generic market news unless it's a massive crash/pump.
        
        TRADING LOGIC (PRIORITY 2):
        A. SHORT SIGNALS (Don't be afraid to short):
           - News = "Hack", "Exploit", "Delay", "Scam", "Investigation", "Sell-off".
           - News = "Good/Neutral" BUT Price is DROPPING (1m < -0.5%) -> Trend Reversal Short.
           - News = "Bad" AND Price is PUMPING -> Top Short Opportunity.
           
        B. LONG SIGNALS:
           - News = "Major Partnership", "ETF Approval", "Listing", "Mainnet".
           - ONLY if Price is STABLE (-0.5% to +0.5%) or DIPPING. 
           - IF Price > +2.0% (1m/10m) -> HOLD (FOMO Trap).
           
        C. TIME MANAGEMENT:
           - MAX VALIDITY: 30 Minutes. NO EXCEPTIONS.
           - Ideal Validity: 10-15 Minutes.
           
        RULES FOR TIMING:
        - CHECK VERB TENSE: Is the news about something that ALREADY happened ("Sold off", "Dropped", "Plunged")?
          -> IF YES: The move is likely over. ACTION: HOLD (Don't chase ghosts).
        - Is the news about something HAPPENING NOW or COMING ("Launching", "Partnering", "Approving")?
          -> IF YES: ACTION: LONG/SHORT.
        
        JSON OUTPUT ONLY:
        {{
            "action": "LONG" | "SHORT" | "HOLD",
            "confidence": <int 0-100>,
            "tp_pct": <float 1.5-4.0>,
            "sl_pct": <float 0.5-1.5>,
            "validity_minutes": <int 5-30>,
            "reason": "SHORT because bad news and price weakness. Time limited to 15m."
        }}
        """

        #prices debug
        print(f"🐛 [DEBUG] Fiyat: {price}, Değişimler: {changes}")
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
                    options={'temperature': 0.1},
                    keep_alive=-1 # 5 dakika açık tut
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
        
        prompt = f"""
        TASK: Identify which cryptocurrency symbol is most impacted by this news.
        NEWS: "{news}"
        
        RULES:
        1.  **IMPACT ANALYSIS:** Determine which specific cryptocurrency's price or sentiment is most likely to be affected by this news.
        2.  **INFERENCE:**
            * If the news mentions "Satoshi", "Bitcoin", or general crypto market trends led by Bitcoin, return "BTC".
            * If the news mentions "Vitalik", "Ether", or Ethereum ecosystem updates, return "ETH".
            * If the news mentions a project built on a specific chain (e.g., "Jupiter on Solana"), return the chain's token if the project token isn't listed (e.g., "SOL").
        3.  **CONSTRAINT:** Only return a symbol if it exists in the ALLOWED SYMBOLS list.
        4.  **NULL:** If no specific coin from the list is impacted, return null.
        
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
                    options={'temperature': 0.0, 'num_ctx': 512, 'num_predict': 32} ,
                    keep_alive=-1 # Sıfır yaratıcılık
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
                    options={'temperature': 0.7, 'num_ctx': 512, 'num_predict': 32} ,
                    keep_alive=-1 # Sıfır yaratıcılık
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
        
        # 1. HIZLI LİSTE (Hardcoded Memory)
        # coin_categories sözlüğünü buraya veya sınıfın tepesine yapıştır
        # (Yukarıdaki uzun listeyi buraya koy)
        
        if sym in coin_categories:
            return coin_categories[sym]

        # 2. CACHE KONTROLÜ (Daha önce arattık mı?)
        if not hasattr(self, 'coin_cache'):
            self.coin_cache = {}
        
        if sym in self.coin_cache:
            return self.coin_cache[sym]

        # 3. BİLİNMEYEN COINLER İÇİN İNTERNET ARAMASI (Fallback)
        # Burası sadece listede olmayan yeni/küçük coinler için çalışır
        print(f"🔍 [BEYİN] {sym} bilinmiyor, internetten öğreniliyor...")
        query = f"what is {sym} crypto category sector utility"
        try:
            # DuckDuckGo araması (utils.py'dan search_web_sync fonksiyonunu kullan)
            search_text = await asyncio.to_thread(search_web_sync, query)
            
            # LLM'e sorma kısmı (Senin mevcut kodun)
            profile_prompt = f"""
            DATA: {search_text}
            TASK: Classify {sym} into ONE category.
            OPTIONS: [Layer-1, Layer-2, DeFi, AI, Meme, Gaming, Stablecoin, RWA, Oracle]
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
                    options={'temperature': 0.0,  'num_ctx': 128, 'num_predict': 16},
                    keep_alive=-1 # Sıfır yaratıcılık
                )
                category = res['message']['content'].strip()
            
            # Cache'e kaydet
            self.coin_cache[symbol] = category
            print(f"🧬 [PROFİL] {symbol} sınıflandırıldı: {category}")
            return category

        except Exception as e:
            print(f"Profil Hatası: {e}")
            return "Unknown"