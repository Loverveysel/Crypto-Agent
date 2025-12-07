from telethon import TelegramClient
import asyncio

# BURALARI DOLDUR
API_ID = 33059879
API_HASH = 'aac2748df0bff64aadcdc7692588b75b'
TELETHON_SESSION_NAME = 'crypto_agent_session'

async def main():
    print("Telegram oturumu oluşturuluyor...")
    client = TelegramClient(TELETHON_SESSION_NAME, API_ID, API_HASH)
    
    # Bu komut telefon numaranı ve kodu soracak
    # Başarılı olunca 'trading_bot_session.session' dosyasını oluşturacak
    await client.start()
    
    print("Başarılı! Oturum dosyası oluşturuldu.")
    print("Şimdi main.py dosyasını çalıştırabilirsin.")
    
    me = await client.get_me()
    print(f"Giriş yapılan kullanıcı: {me.username}")
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())