import logging
import asyncio
import os
import sys
from telethon import TelegramClient
from dotenv import load_dotenv
from services import send_telegram_alert

# 1. LOGLARI FULLE (DEBUG MODU)
# Bu sayede "Connect" derken arka planda ne döndüğünü göreceğiz.
logging.basicConfig(
    format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
    level=logging.DEBUG  # <--- KRİTİK AYAR
)

load_dotenv()

API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
SESSION_NAME = 'crypto_agent_session' # Geçici bir session kullanalım

# Klasör ayarları (Standart prosedür)
path = os.path.realpath(__file__)
dir = os.path.dirname(path)
dir = dir.replace('src', 'data')
os.chdir(dir)
SESSION_PATH = os.path.join(dir, SESSION_NAME)

class Context:
    pass
ctx = Context()
ctx.telegram_client = None

async def main():
    print(f"--- 🕵️‍♂️ DERİN ANALİZ BAŞLIYOR ---")
    print(f"Python Sürümü: {sys.version}")
    print(f"Session Yolu: {SESSION_PATH}")
    
    # 2. İSTEMCİ AYARLARI (IPv6'yı Kapatıyoruz)
    # use_ipv6=False parametresi bazen hayat kurtarır.
    client = TelegramClient(
        SESSION_PATH, 
        int(API_ID), 
        API_HASH,
        use_ipv6=False,    # <--- IPv4 ZORLAMASI
        timeout=10         # <--- 10 SANİYE SONRA HATA VERSİN (Beklemesin)
    )

    print("⏳ client.connect() çağrılıyor... (Logları izle)")
    
    try:
        # Bağlantı denemesi
        await client.connect()
        ctx.telegram_client = client
        
        await send_telegram_alert(ctx, "Telegram Debug")
        if client.is_connected():
            print("\n✅ BAĞLANTI BAŞARILI! (Sorun IPv6 veya Timeout olabilirmiş)")
            me = await client.get_me()
            await client.send_message('me', 'Merhaba')
            if me:
                print(f"👤 Kimlik: {me.username}")
            else:
                print("❓ Bağlı ama kimlik yok (Yetkisiz Session).")
        else:
            print("\n❌ Bağlantı kurulamadı (is_connected=False)")
            
    except Exception as e:
        print(f"\n💥 HATA YAKALANDI: {e}")
    
    finally:
        await client.disconnect()
        print("--- ANALİZ BİTTİ ---")

if __name__ == '__main__':
    asyncio.run(main())