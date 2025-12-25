import json
import asyncio
import re
import sys
import os
import aiofiles # 'pip install aiofiles' şart
from groq import AsyncGroq

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GROQCLOUD_API_KEY

# GROQ MODEL ADI (Groq'daki güncel adı kontrol et, genelde llama-3.3-70b-versatile)
MODEL_NAME = "meta-llama/llama-guard-4-12b" 
OUTPUT_FILE = "data/synthetic_finetune_data.jsonl" # JSONL hayat kurtarır

client = AsyncGroq(api_key=GROQCLOUD_API_KEY)

async def ask_teacher_llm(news, symbol, rsi, btc_trend, momentum, action, peak_pct, funding, market_cap, category, peak_min):
    prompt = f"""
    You are a Senior Crypto Quantitative Trader. 
    Analyze the following event and provide a 2-3 sentence 'Reasoning' for the outcome.
    
    NEWS: {news}
    SYMBOL: {symbol}
    MARKET DATA: RSI is {rsi}, BTC Trend is {btc_trend}%, 1h Momentum is {momentum}%, funding rate is {funding}%, market cap is {market_cap}, category is {category}
    ACTUAL OUTCOME: The price moved {peak_pct}% in {peak_min} minutes causing a {action} action.
    
    CRITICAL: Analyze the impact based on the MARKET CAP and CATEGORY. 
    Small caps pump harder, Large caps (like {symbol}) require massive liquidity. 
    Explain the logic linkage between news and the outcome. If it's a 'Sell the News' or 'Short Squeeze', state it.
    Professional English. High-stakes trader journal tone.
    
    Reasoning:"""

    retries = 0
    max_retries = 5
    while retries < max_retries:
        try:
            response = await client.chat.completions.create(
                model=MODEL_NAME, 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7 # Yaratıcılık için ideal
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                retries += 1
                wait_time = float(re.search(r"(\d+\.?\d*)s", error_msg).group(1)) if "s" in error_msg else 2.0
                print(f"⏳ [RATE LIMIT] {wait_time}s bekleniyor...")
                await asyncio.sleep(wait_time + 0.5)
            else:
                print(f"❌ [ERROR]: {e}")
                return None
    return None

async def process_distillation(input_file):
    if not os.path.exists(os.path.dirname(OUTPUT_FILE)):
        os.makedirs(os.path.dirname(OUTPUT_FILE))

    async with aiofiles.open(input_file, mode='r', encoding='utf-8') as f:
        lines = await f.readlines()

    total = len(lines)
    print(f"🧠 {total} satır için işlem başladı. Veriler {OUTPUT_FILE} dosyasına anlık yazılacak.")

    startsfrom = 1161

    # JSONL formatında yazıyoruz: Her satır bağımsız bir JSON objesi
    async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as out_f:
        for i, line in enumerate(lines):
            if i < startsfrom:
                continue
            row = json.loads(line)
            d = row['data']
            
            reasoning = await ask_teacher_llm(
                row['news'], d['symbol'], d['rsi'], d['btc_trend'], 
                d['momentum']['1h'], d['action'], d['peak_pct'], 
                d['funding'], d['market_cap'], d['category'], d['peak_min']
            )
            
            if reasoning:
                entry = {
                    "instruction": "Analyze news and metrics for a trading decision.",
                    "input": f"News: {row['news']}\nSymbol: {d['symbol']}\nRSI: {d['rsi']}\nBTC: {d['btc_trend']}%\nFunding: {d['funding']}%\nMarket Cap: {d['market_cap']}\nCategory: {d['category']}\nMomentum: 1h: {d['momentum']['1h']}%",
                    "output": f"Analysis: {reasoning}\nAction: {d['action']}\nPeak: {d['peak_pct']}% in {d['peak_min']}m"
                }
                # Her satırı anında diske kazıyoruz
                await out_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                await out_f.flush()
                
            sys.stdout.write(f"\r🚀 İlerleme: {i+1}/{total} | Sonuçlar kaydediliyor...")
            sys.stdout.flush()

if __name__ == "__main__":
    asyncio.run(process_distillation("data/raw_market_outcomes_v1_5.jsonl"))