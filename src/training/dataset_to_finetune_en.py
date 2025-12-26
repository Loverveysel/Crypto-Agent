import json
import asyncio
import sys
import os
import random
import aiofiles
import re
from groq import AsyncGroq

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GROQCLOUD_API_KEY

INSTRUCTION = """
You are a Lead Event-Driven Quantitative Trader.
Decisions are based strictly on pre-event information.
Risk preference or narrative style must not affect decisions.

Core Mission:
Identify asymmetric trading edges using strictly pre-event information.
Default stance is NO_TRADE.
Capital preservation overrides opportunity seeking.

Evaluation Protocol:

1) Catalyst DNA:
Determine whether the news is structurally capable of moving an asset of this Market Cap and Category.
Price movement alone is never evidence.

2) Contextual Synthesis:
Evaluate RSI, Funding, and Momentum for signs of positioning imbalance,
liquidity absorption, squeeze risk, or mean reversion pressure.
Momentum without structural support is NOT an edge.

3) Edge Detection:
If and only if an asymmetric edge exists, classify the primary driver as:
Momentum, Liquidity, MeanReversion, NewsDecay, or VolatilityExpansion.

Hard Consistency Rules:

- NO_TRADE -> Edge: None, Horizon: None, Risk Posture: Avoid.
- VALID_TRADE -> Risk Posture must be Moderate or Aggressive.
- Momentum / VolatilityExpansion -> Horizon: Immediate or Short.
- MeanReversion / NewsDecay -> Horizon: Short only.

If the news catalyst is structurally or fundamentally insufficient to trigger the observed move (e.g., minor social media news followed by a major price expansion in a large-cap asset), you MUST state clearly that the move was likely driven by pre-existing technical momentum, BTC beta, or organic order flow rather than the news itself. Identify the news as 'Incidental' rather than 'Causal'
"""

# Config
MODEL_NAME = "llama-3.3-70b-versatile" 
INPUT_FILE = "data/raw_market_outcomes_v1_5.jsonl"
OUTPUT_FILE = "data/synthetic_finetune_data_v2_5.jsonl"

client = AsyncGroq(api_key=GROQCLOUD_API_KEY)


def get_sampling_params(phase, persona):
    if phase == "canonical":
        return {
            "temperature": 0.35 if persona == "risk-averse" else 0.45,
            "top_p": 0.70,
            "frequency_penalty": 1.05,
            "presence_penalty": 0.0
        }
    else:  # stress
        return {
            "temperature": 0.45 if persona != "aggressive" else 0.55,
            "top_p": 0.80,
            "frequency_penalty": 1.08,
            "presence_penalty": 0.15
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
    prompt = f"""Role: Market Microstructure Analyst. Explain the MECHANISM of a KNOWN price move. 
Context: Trade is finished. Facts are Ground Truth. 

DATA:
News: {news}
Asset: {symbol} | {category} | MCAP: {market_cap}
Pre-Event: RSI: {rsi} | Fund: {funding} | Mom: {momentum} | BTC: {btc_trend}
Outcome: {direction} | {peak_pct}% | in {peak_min} minutes

OBJECTIVE: Explain WHY the move happened via:
1. Catalyst:
- Structural = fundamentals / protocol / regulation
- Positioning Shock = liquidations, short-covering, leverage imbalance
- Sentiment = attention or narrative without structural change
- Noise = non-causal coincidence
2. Friction/Fuel: How metrics (RSI/Fund/Mom) hindered or accelerated transmission. Metrics are NOT reasons; they are environment.
3. Transmission: Explicitly trace
News -> which participants reacted -> how liquidity/order flow shifted -> why price expanded

STRICT RULES:
- causal_link = true ONLY if news initiated the move.
- causal_link = false if move is BTC Beta, Technical Drift, or Noise.
- Respect Scale Inertia: Large caps (100B+) need massive catalysts; otherwise, Causal=False.
- No vague language (may/might). No trading advice. 

JSON OUTPUT:
{
  "reasoning": "90-130 words. Mechanistic flow analysis. Focus on news-to-price transmission.",
  "causal_link": true/false,
  "confidence_score": 0-100
}"""
    
    params = get_sampling_params(phase, persona)
    retries = 0
    max_retries = 3
    while retries < max_retries:
        try:
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

            casual_link = res.get("casual_link", True)
            if casual_link == "True":
                casual_link = True
            elif casual_link == "False":
                casual_link = False
            else:
                casual_link = True

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
                    "confidence": random.randint(0, 50),
                    "action": "HOLD"
                }
            
            entry = {
                "instruction": INSTRUCTION,
                "input": f"News: {row['news']}\nContext: {d['symbol']} | {d['category']} | {d['market_cap']}\nMetrics: RSI {d['rsi']} | BTC {d['btc_trend']}% | Funding: {d['funding']}% | Momentum(1 hour): {d['momentum']['1h']}% ",
                "output": {
                    "analysis": res["reasoning"],
                    **execution
                }
            }
            await out_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
            sys.stdout.write(f"\r🚀 {i+1}/{len(lines)}")
            sys.stdout.flush()

if __name__ == "__main__":
    asyncio.run(process_distillation())