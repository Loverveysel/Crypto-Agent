import json
import asyncio
from datetime import datetime
from groq import AsyncGroq
import ollama
import time
import re

# Local modules
from config import (
    ANALYZE_SPECIFIC_PROMPT, 
    DETECT_SYMBOL_PROMPT, 
    GENERATE_SEARCH_QUERY_PROMPT, 
    GET_COIN_PROFILE_PROMPT,
    LLM_CONFIG
)
from utils import search_web_sync, coin_categories

class AgentBrain:
    def __init__(self, use_groqcloud=True, api_key=None, groqcloud_model="google/gemini-2.0-flash-exp:free"):
        self.use_groqcloud = use_groqcloud
        self.model = groqcloud_model
        self.ollama_model = "crypto-agent:gemma"  # Fallback
        self.api_key = api_key
        self.coin_cache = {} # Cache
        self.last_request_time = 0
        # 60s for 1 request per minute limit. 62s for safety.
        self.MIN_REQUEST_INTERVAL = 62

        # 1. OpenRouter (GroqCloud) Setup
        if self.use_groqcloud:
            print(f"🧠 [BRAIN] Mode: OPENROUTER ({self.model})")
            self.client = AsyncGroq(
                api_key=self.api_key,
            )
        
        # 2. Local Ollama Setup (Fallback)
        else:
            print(f"🧠 [BRAIN] Mode: LOCAL OLLAMA ({self.ollama_model})")
            print("🔥 [SYSTEM] Loading Model to VRAM (Keep-Alive)...")
            try:
                ollama.chat(model=self.ollama_model, messages=[{'role': 'user', 'content': 'hi'}], keep_alive=-1)
                print("✅ [SYSTEM] Model loaded!")
            except Exception as e:
                print(f"⚠️ Model load warning: {e}")

    async def _wait_for_rate_limit(self):
        """
        Rate limit wait for GroqCloud/OpenRouter.
        """
        if not self.use_groqcloud:
            return

        current_time = time.time()
        time_diff = current_time - self.last_request_time

        if time_diff < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - time_diff
            print(f"⏳ [RATE LIMIT] Waiting {sleep_time:.1f} seconds...")
            await asyncio.sleep(sleep_time)
        
        self.last_request_time = time.time()

    def _clean_thinking(self, text):
        """
        Cleans <think>...</think> blocks.
        """
        if not text:
            return ""
        
        pattern = r"<think>.*?</think>"
        cleaned_text = re.sub(pattern, "", text, flags=re.DOTALL)
        
        return cleaned_text.strip()

    async def _submit_to_llm(self, prompt, temperature=0.1, json_mode=True, max_tokens=1024, use_system_prompt=True, reasoning_mode="none"):
        """
        Central LLM Call Function
        """
        try:
            messages_payload = []
            
            if use_system_prompt:
                messages_payload.append({"role": "system", "content": LLM_CONFIG['system_prompt']})
            
            messages_payload.append({"role": "user", "content": prompt})

            # --- A. OPENROUTER / GROQ ---
            if self.use_groqcloud:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages_payload,
                    response_format={"type": "json_object"} if json_mode else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_mode
                )
                raw_response = completion.choices[0].message.content
                cleaned_response = self._clean_thinking(raw_response)
                return cleaned_response
            # --- B. OLLAMA ---
            else:
                options = {
                    'temperature': temperature,
                    'num_ctx': 512, 
                    'num_predict': 128 if not json_mode else 32
                }
                res = await asyncio.to_thread(
                    ollama.chat,
                    model=self.ollama_model,
                    messages=messages_payload,
                    format='json' if json_mode else '',
                    options=options,
                    keep_alive=-1
                )
                return res['message']['content']

        except Exception as e:
            print(f"❌ [ERROR] LLM Request Failed: {e}")
            return None

    async def analyze_specific(self, news, symbol, price, changes, search_context="", coin_full_name="Unknown", market_cap_str="", rsi_val=0, btc_trend=0, volume_24h="", funding_rate=0):
        # 1. Profile Info
        await self._wait_for_rate_limit()
        coin_category = await self.get_coin_profile(symbol)
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"🐛 [DEBUG] {symbol} Category: '{coin_category}'")
        print(f"🐛 [DEBUG] Price: {price}, Changes: {changes}")

        prompt = ANALYZE_SPECIFIC_PROMPT.format(
            symbol=symbol.upper(),
            coin_full_name=coin_full_name,
            market_cap_str=market_cap_str,
            coin_category=coin_category,
            rsi_val=rsi_val,
            btc_trend=btc_trend,
            volume_24h=volume_24h,
            funding_rate=funding_rate,
            current_time_str=current_time_str,
            price=price,
            change_1m=changes['1m'],
            change_10m=changes['10m'],
            change_1h=changes['1h'],
            change_24h=changes['24h'],
            news=news,
            search_context=search_context
        )

        response_text = await self._submit_to_llm(prompt, temperature=0.1, json_mode=True, max_tokens=2048, use_system_prompt=True, reasoning_mode="default")
        
        try:
            return json.loads(response_text)
        except Exception:
            return {"action": "HOLD", "confidence": 0, "reason": "Error parsing JSON"}

    async def detect_symbol(self, news, available_pairs):
        prompt = DETECT_SYMBOL_PROMPT.format(news=news)
        
        response_text = await self._submit_to_llm(prompt, temperature=0.0, json_mode=True, max_tokens=16, use_system_prompt=False)
        
        try:
            res_json = json.loads(response_text)
            return res_json.get('symbol')
        except Exception as e:
            print(f"[ERROR] Symbol Detect JSON error: {e}")
            return None

    async def generate_search_query(self, news, symbol):
        prompt = GENERATE_SEARCH_QUERY_PROMPT.format(
            news=news,
            symbol=symbol.upper()
        )
        
        # Higher temperature
        response_text = await self._submit_to_llm(prompt, temperature=0.7, json_mode=False, max_tokens=64, use_system_prompt=False, reasoning_mode="none")
        return response_text.strip()

    async def get_coin_profile(self, symbol):
        sym = symbol.upper().replace('USDT', '')
        
        # 1. FAST LIST
        if sym in coin_categories:
            return coin_categories[sym]

        # 2. CACHE CHECK
        if sym in self.coin_cache:
            return self.coin_cache[sym]

        # 3. INTERNET SEARCH & LLM
        print(f"🔍 [BRAIN] {sym} unknown, researching...")
        query = f"what is {sym} crypto category sector utility"
        
        try:
            search_text = await asyncio.to_thread(search_web_sync, query)
            
            profile_prompt = GET_COIN_PROFILE_PROMPT.format(
                search_text=search_text,
                symbol=sym
            )
            
            # JSON mode off
            category = await self._submit_to_llm(profile_prompt, temperature=0.0, json_mode=False, max_tokens=256, use_system_prompt=False)
            category = category.strip()
            
            # Cache
            self.coin_cache[sym] = category
            print(f"🧬 [PROFILE] {symbol} classified: {category}")
            return category

        except Exception as e:
            print(f"Profile Error: {e}")
            return "Unknown"