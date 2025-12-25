import os
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
from unsloth import FastLanguageModel
import torch
from trl import SFTTrainer
from transformers import TrainingArguments
from datasets import load_dataset
# 1. MODEL AYARLARI
max_seq_length = 2048 # HFT analizi için fazlasıyla yeterli
dtype = None # GPU mimarisine göre otomatik seçer (bfloat16 veya float16)
load_in_4bit = True # VRAM dostu 4-bit kuantizasyon

model, tokenizer = FastLanguageModel.from_pretrained(
    "unsloth/Ministral-3-3B-Instruct-2512", # Veya "unsloth/gemma-2-9b-bnb-4bit"
    max_seq_length = max_seq_length,
    dtype = dtype,
    load_in_4bit = load_in_4bit,
    use_gradient_checkpointing = "unsloth", # True or "unsloth" for long context
)

# 2. LoRA (Düşük Dereceli Adaptasyon) PARAMETRELERİ
model = FastLanguageModel.get_peft_model(
    model,
    r = 32, # Modelin esnekliği (1300 satır için 32 ideal)
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj",],
    lora_alpha = 32,
    lora_dropout = 0, # Eğitim hızı için 0 kalsın
    bias = "none",    # Eğitim stabilitesi için
    use_gradient_checkpointing = "unsloth", # VRAM tasarrufu
    random_state = 3407,
)

# 3. VERİ SETİ FORMATLAMA
def formatting_prompts_func(examples):
    instructions = examples["instruction"]
    inputs       = examples["input"]
    outputs      = examples["output"]
    texts = []
    for instruction, input, output in zip(instructions, inputs, outputs):
        # Modelin 'Reasoning' ve 'Action' arasındaki bağı kurduğu yapı
        text = f"### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:\n{output} <|end_of_text|>"
        texts.append(text)
    return { "text" : texts, }

# 'data/synthetic_finetune_data.json' dosyanın yolunu ver
dataset = load_dataset("json", data_files="data/final_finetune_ready.json", split="train")
dataset = dataset.map(formatting_prompts_func, batched = True,)

# 4. EĞİTİM (TRAINING) ARGÜMANLARI
trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = max_seq_length,
    dataset_num_proc = 2,
    args = TrainingArguments(
        per_device_train_batch_size = 2, # VRAM yetmezse 1 yap
        gradient_accumulation_steps = 4, # Batch size'ı sanal olarak artırır
        warmup_steps = 5,
        max_steps = -1, # Epoch bazlı gitmek için -1
        num_train_epochs = 3, # 1300 satır için 3 tur (Overfitting riskine dikkat!)
        learning_rate = 2e-4,
        fp16 = not torch.cuda.is_bf16_supported(),
        bf16 = torch.cuda.is_bf16_supported(),
        logging_steps = 1,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 3407,
        output_dir = "outputs",
    ),
)

# 5. ATEŞLE!
trainer_stats = trainer.train()

# 6. MODELİ KAYDET (GGUF veya LoRA olarak)
model.save_pretrained("crypto_trader_lora") # Sadece adaptörü kaydeder
tokenizer.save_pretrained("crypto_trader_lora")
model.save_pretrained_gguf("model_gguf", tokenizer, quantization_method = "q4_k_m") # Ollama için GGUF