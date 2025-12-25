import json
import re
import os

# --- AYARLAR ---
# Dosya yollarını tam kontrol et!
INPUT_FILE = "data/synthetic_finetune_data.jsonl" 
OUTPUT_FILE = "data/final_finetune_ready.json"

FINAL_INSTRUCTION = """Acting as a Lead Event-Driven Quantitative Strategist, your task is to synthesize unstructured crypto news with multi-dimensional market metrics.      

Evaluation Protocol:
1) Catalyst DNA: Classify news as 'High-Impact Catalyst', 'Lagging' (Priced-in), or 'Noise'.
2) Sentiment-Technical Confluence: Cross-examine news sentiment with Funding Rates and RSI to identify exhaustion or overextension.
3) Size-Adjusted Impact: Scale volatility expectations based on Market Cap and Category.
4) Reasoning: Provide a 2-3 sentence logic bridge focusing on liquidity grab, 'Sell the News' or trend continuation.

Output Format:
Analysis: [Your Synthesis]
Action: [LONG/SHORT/HOLD]
Expected Volatility: [Low/Medium/High]"""

def get_volatility_category(peak_pct):
    try:
        val = abs(float(peak_pct))
        if val >= 2.5: return "High"
        if val >= 1.0: return "Medium"
        return "Low"
    except:
        return "Low"

def transform_data():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ HATA: {INPUT_FILE} bulunamadı! Lütfen yolu kontrol et.")
        return

    final_list = []
    processed_count = 0

    print(f"🔄 {INPUT_FILE} okunuyor ve dönüştürülüyor...")

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line: continue
            
            try:
                entry = json.loads(line)
                
                # 1. Mevcut output'u parçala (Hatırla: Teacher'dan gelen format)
                # Output genelde "Analysis: ... \nAction: ... \nPeak: ..." şeklindeydi.
                output_text = entry.get('output', '')
                lines = output_text.split("\n")
                
                analysis_line = next((l for l in lines if l.startswith("Analysis:")), "Analysis: N/A")
                action_line = next((l for l in lines if l.startswith("Action:")), "Action: HOLD")
                
                # 2. Peak değerini REGEX ile çek
                peak_line = next((l for l in lines if l.startswith("Peak:")), "")
                match = re.search(r"Peak:\s*(-?[\d.]+)", peak_line)
                
                if match:
                    peak_numeric = match.group(1)
                    vol_cat = get_volatility_category(peak_numeric)
                else:
                    vol_cat = "Low"

                # 3. Formata uygun yeni entry oluştur
                new_entry = {
                    "instruction": FINAL_INSTRUCTION,
                    "input": entry.get('input', ''), # Mevcut inputu koru
                    "output": f"{analysis_line}\n{action_line}\nExpected Volatility: {vol_cat}"
                }
                
                final_list.append(new_entry)
                processed_count += 1
                
            except json.JSONDecodeError as e:
                print(f"⚠️ Satır {line_num} atlandı (Hatalı JSON): {e}")
                continue

    # Eğitim için standart JSON (List) formatında kaydet
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_list, f, indent=4, ensure_ascii=False)

    print(f"✅ Başarılı! {processed_count} satır işlendi ve {OUTPUT_FILE} dosyasına kaydedildi.")

if __name__ == "__main__":
    transform_data()