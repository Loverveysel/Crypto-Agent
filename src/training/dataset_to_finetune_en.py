import json
import asyncio
import sys
import os
import random
import aiofiles
import re
from groq import AsyncGroq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GROQCLOUD_API_KEY, GEMINI_MODEL, GOOGLE_API_KEY

class Response(BaseModel):
    reasoning: str = Field(..., description="90-130 words. Mechanistic flow analysis. Focus on news-to-price transmission.")
    causal_link: bool = Field(..., description="true ONLY if news initiated the move.")
    confidence: int = Field(..., description="0-100")

INSTRUCTION = """
## 1. CORE ROLE
You are a Senior Event-Driven Execution Engine. Your sole purpose is to filter out market noise and identify high-conviction directional edges. You do not explain past movements; you calculate the immediate impact of new information.

## 2. PHILOSOPHY: MARKET PHYSICS
Every trade is a calculation of Energy vs. Mass within a Friction-filled environment.

* Energy (News Strength): Classify news as Structural (Policy/Protocol), Positioning Shock (Liquidations/Forced Flows), Sentiment (Hype), or Noise. Only energy that FORCES participant action is valid.
* Mass (Asset Inertia): Energy must be proportional to the asset's Market Cap and Relative Liquidity. High energy on a low-cap is an asymmetric edge; the same energy on BTC is often Noise.
* Friction (Market State): RSI, Funding Rates, and Momentum define resistance. High friction (overbought/high funding) absorbs bullish energy. Friction never creates direction; it only blocks or permits it.

## 3. EXECUTION RULES
* Default Stance: Your default state is HOLD.
* Strict Temporal Blindness: Assume price is flat at t0. Ignore all price action occurring after the news timestamp. No hindsight reasoning is allowed.
* The Forced Behavior Rule: You MUST explicitly identify a specific group (e.g., "Over-leveraged Shorts", "Institutional Mandates") that is forced to trade by this news. If no one is forced, the action is ALWAYS HOLD.
* Causality Check: If the news is insufficient to move the asset’s mass, label it as "Incidental" and stay HOLD, even if the price is volatile.
* Uncertainty: If the Energy/Mass balance is ambiguous, default to HOLD.

## 4. OUTPUT FORMAT (STRICT JSON ONLY)
You must respond ONLY with a JSON object. No prose, no intro, no outro.

{{
  "action": "LONG" | "SHORT" | "HOLD",
  "confidence": <int 0-100>,
  "expected_volatility": "Low" | "Medium" | "High",
  "tp_pct": <float>,
  "reason": "[Ultra-concise summary for the log]",
  "validity_minutes": <int>
}}
"""
# Config
MODEL_NAME = "llama-3.3-70b-versatile" 
INPUT_FILE = "data/raw_market_outcomes_v1_5.jsonl"
OUTPUT_FILE = "data/synthetic_finetune_data_v2_5.jsonl"



client = AsyncGroq(api_key=GROQCLOUD_API_KEY)
gclient = genai.Client(api_key=GOOGLE_API_KEY)
USE_GEMINI = True

def get_sampling_params(phase, persona):
    # Çok daha düşük temperature: Daha kararlı ve daha az "uydurma"
    base_temp = 0.15 if phase == "canonical" else 0.25
    return {
        "temperature": base_temp,
        "top_p": 0.1, # Top_p'yi de düşür ki en yüksek olasılıklı kelimelere odaklansın
        "frequency_penalty": 1.1, # Tekrarları engellemek için hafifçe artır
        "presence_penalty": 0.0
    }

async def ask_teacher_llm(row, phase="canonical", persona="neutral"):
    d = row['data']
    news = row['news']
    category = d['category']
    market_cap = d['market_cap']
    symbol = d['symbol']
    rsi = d['rsi']
    funding = d['funding']
    momentum = d['momentum']
    btc_trend = d['btc_trend']
    direction = d['action']
    peak_pct = d['peak_pct']
    peak_min = d['peak_min']

    # 2️⃣, 3️⃣, 4️⃣, 5️⃣ Düzeltmeler: Edge, Horizon, Consistency ve Risk Posture
    prompt = f"""
Role: Senior Market Microstructure & Order Flow Analyst.
Objective: Conduct a mechanical potentiality analysis of a news event at $t_0$.
Philosophy: Every move has a source, but not every news item is a source. You judge the news solely on its ability to displace 'Mass' against existing 'Friction'.

STRICT TEMPORAL BLINDNESS RULE:
- You are at the exact millisecond of news release ($t_0$). 
- You MUST NOT mention the provided outcome (Peak_Pct/Direction) in your 'reasoning' block. 
- Use ONLY future-tense or conditional language (e.g., 'will force', 'should trigger', 'is likely to').
- Your reasoning must justify a move *theoretically*, as if it hasn't happened yet.

ASSET MASS VS. ENERGY CALCULATION:
- Implied USD Move = (Test_Peak_Pct / 100) * Market_Cap.
- Compare this USD value against the news quality. If a minor NFT launch or tweet is tested against a multi-billion dollar move, the energy is insufficient.

--------------------------------
INPUT DATA FOR EVALUATION:
News: {news}
Context: {symbol} | {category} | MCAP: {market_cap}
Pre-Event State (t0): RSI: {rsi} | Funding: {funding} | Momentum: {momentum} | BTC_Trend: {btc_trend}
Move Under Test: {direction} | {peak_pct}% over {peak_min} mins

--------------------------------
STRICT ANALYSIS ARCHITECTURE:

1. Catalyst DNA & Energy Classification:
Categorize as Structural, Positioning Shock, Sentiment, or Noise.
Does this specific news force a 'Mandatory Response' from any participant group? If not, it is low energy.

2. Friction vs. Fuel Mechanics:
Evaluate RSI and Funding as mechanical resistance.
- Friction (Resistance): RSI > 70, High Positive Funding.
- Fuel (Acceleration): RSI < 30, Neutral/Negative Funding.
Explain if the catalyst has enough raw energy to overcome the observed friction for the 'Move Under Test'.

3. Order Flow Path (Forced Behavior):
Identify the 'Forced Participant'. Who is compelled to trade? (e.g., 'Leveraged shorts forced to cover', 'Institutional rebalancing').
If the 'Move Under Test' direction contradicts news logic (e.g., Bullish News vs. Short Move), only validate it as 'Causal' if you identify a 'Sell the News' / 'Liquidity Grab' mechanic based on positioning extremes.

--------------------------------
STRICT OUTPUT CONSTRAINTS:
- CAUSAL_LINK: Set 'true' ONLY if the news energy is structurally sufficient to drive the 'Move Under Test'. If {btc_trend} or organic technical drift explains it better, return 'false'.
- NO POST-HOC REASONING: Do not say 'Because price moved...'. Say 'Because this news will force...'.
- WORD COUNT: 110-150 words.

JSON OUTPUT FORMAT:
{{
  "reasoning": "Strictly mechanical synthesis at t0. MUST identify the Forced Participant and the Energy-Mass-Friction balance. Use only future/conditional tense.",
  "causal_link": True/False,
  "confidence": 0-100
}}
"""
    
    params = get_sampling_params(phase, persona)
    retries = 0
    max_retries = 3
    while retries < max_retries:
        try:
            if USE_GEMINI:
                res = gclient.models.generate_content(
                    model = GEMINI_MODEL,
                    contents= prompt,
                    config= types.GenerateContentConfig(
                        temperature=params['temperature'],
                    )
                )
                text = res.text.replace("```json", "").replace("```", "")
                json_object = json.loads(text)
                return json_object

            else:
                response = await client.chat.completions.create(
                    model=MODEL_NAME, 
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    **params
                )
                return json.loads(response.choices[0].message.content)
        except Exception as e:
            error_msg = str(e)

            # --- 429 RATE LIMIT AYIKLAMA MANTIĞI ---
            if "429" in error_msg:
                retries += 1
                # Regex ile bekleme süresini bul (ms veya s)
                # Örn: "Please try again in 690ms" veya "try again in 2s"
                ms_match = re.search(r"try again in (\d+)ms", error_msg)
                sec_match = re.search(r"try again in (\d+)s", error_msg)

                wait_time = 1.0 # Default 1 saniye

                if ms_match:
                    wait_time = float(ms_match.group(1)) / 1000.0
                elif sec_match:
                    wait_time = float(sec_match.group(1))

                # Güvenlik payı ekle (0.2 saniye)
                wait_time += 0.2

                print(f"⏳ [RATE LIMIT] 429 Hata! {wait_time:.2f}s bekleniyor... (Deneme {retries}/{max_retries})")
                await asyncio.sleep(wait_time)
                continue # Döngü başına dön ve tekrar dene
            
            else:
                print(f"❌ [ERROR] LLM Request Failed: {e}")
                return None


async def process_distillation():

    startfrom = 0
    async with aiofiles.open(INPUT_FILE, mode='r', encoding='utf-8') as f:
        lines = await f.readlines()

    async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as out_f:
        for i, line in enumerate(lines):
            if i < startfrom:
                continue
            row = json.loads(line)
            d = row['data']
            URL_REGEX = re.compile(
                r"https?://\S+|www\.\S+",
                re.IGNORECASE
            )
            URL_REGEX2 = re.compile(
                r"http?://\S+|www\.\S+",
                re.IGNORECASE
            )
            def remove_urls(text: str) -> str:
                text = URL_REGEX.sub("", text)
                text = URL_REGEX2.sub("", text)
                return text.strip()

            d['news'] = remove_urls(row['news'])
            row['news'] = d['news']            
            # 2️⃣ Düzeltme: Persona olay bazlı atanır, aynı olaya farklı kararlar verilmesi engellenir.
            persona_roll = random.random()
            persona = "risk-averse" if persona_roll < 0.2 else "aggressive" if persona_roll > 0.8 else "neutral"
            
            phase = "stress" if (
                abs(d['funding']) > 0.05 or
                abs(d['momentum']['1h']) > 1.5 or
                abs(d['btc_trend']) > 1.2
            ) else "canonical"            
            res = await ask_teacher_llm(row, phase=phase, persona=persona)

            casual_link = res['causal_link']

            execution = {
                "tp_pct": None,
                "validity_minutes": None
            }
            if casual_link:
                execution = {
                    "tp_pct": d["peak_pct"],
                    "validity_minutes": d["peak_min"],
                    "confidence": res.get("confidence", 0),
                    "action": d["action"],
                }

            else:
                execution = {
                    "tp_pct": None,
                    "validity_minutes": None,
                    "confidence": res.get("confidence", 0),
                    "action": "HOLD"
                }
            
            entry = {
                "instruction": INSTRUCTION,
                "input": f"News: {row['news']}\nContext: {d['symbol']} | {d['category']} | {d['market_cap']}\nMetrics: RSI {d['rsi']} | BTC {d['btc_trend']}% | Funding: {d['funding']}% | Momentum(1 hour): {d['momentum']['1h']}% ",
                "output": {
                    "reason": res["reasoning"],
                    **execution
                }
            }
            await out_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
            sys.stdout.write(f"\r🚀 {i+1}/{len(lines)}")
            sys.stdout.flush()

if __name__ == "__main__":
    asyncio.run(process_distillation())