
import os
from dotenv import load_dotenv
# Import prompts into namespace to be used by other modules
from prompts import (
    SYSTEM_PROMPT,
    ANALYZE_SPECIFIC_PROMPT,
    DETECT_SYMBOL_PROMPT,
    GENERATE_SEARCH_QUERY_PROMPT,
    GET_COIN_PROFILE_PROMPT
)

load_dotenv()

# --- LLM Configuration ---
USE_GROQCLOUD = True
GROQCLOUD_API_KEY = os.getenv('GROQCLOUD_API_KEY')
GROQCLOUD_MODEL = os.getenv('GROQCLOUD_MODEL', 'google/gemini-2.0-flash-exp:free')

LLM_CONFIG = {
    "system_prompt": SYSTEM_PROMPT,
    "temperature": 0.0,
    "num_ctx": 4096,
    "max_tokens": 256,
}

# --- Exchange Configuration ---
USE_MAINNET = True
REAL_TRADING_ENABLED = True
    
if USE_MAINNET:
    API_KEY = os.getenv('BINANCE_API_KEY')
    API_SECRET = os.getenv('BINANCE_API_SECRET')
    IS_TESTNET = False
else:
    API_KEY = os.getenv('BINANCE_API_KEY_TESTNET')
    API_SECRET = os.getenv('BINANCE_API_SECRET_TESTNET')
    IS_TESTNET = True

BASE_URL = os.getenv('BASE_URL', "wss://stream.binance.com:9443/ws")
WEBSOCKET_URL = BASE_URL

# --- Target Configuration ---
TARGET_CHANNELS = ['cointelegraph', 'wublockchainenglish', 'CryptoRankNews', 'TheBlockNewsLite', 'coindesk', 'arkhamintelligence', 'glassnode'] 
TARGET_PAIRS = []

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptopotato.com/feed/",
    "https://u.today/rss",
    "https://beincrypto.com/feed/"
]

# --- Telegram Configuration ---
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH')
TELETHON_SESSION_NAME = os.getenv('TELETHON_SESSION_NAME')

# --- Simulation Configuration ---
STARTING_BALANCE = 19.73
LEVERAGE = 10 
FIXED_TRADE_AMOUNT = 8

# --- Filter Constants ---
IGNORE_KEYWORDS = [
    'daily', 'digest', 'recap', 'summary', 'analysis', 'price analysis', 
    'prediction', 'overview', 'roundup', 'market wrap', 'outlook', 
    'forecast', 'top gainer', 'top loser', 'market update',
    'slides', 'declines', 'drops', 'plummet' # <-- Bunlar başlıkta geçiyorsa genelde özettir
]
DANGEROUS_TICKERS = {
    'S', 'THE', 'A', 'I', 'IS', 'TO', 'IT', 'BY', 'ON', 'IN', 'AT', 'OF', 
    'ME', 'MY', 'UP', 'DO', 'GO', 'OR', 'IF', 'BE', 'AS', 'WE', 'SO',
    'NEAR', 'ONE', 'SUN', 'GAS', 'POL', 'BOND', 'OM', 'ELF', 'MEME', 'AI', 'MOVE', 'PUMP'
}

AMBIGUOUS_COINS = {
    'link': 'Chainlink',
    'one': 'Harmony',
    'pol': 'Polygon',  # "Police" veya "Policy" içinde geçebilir
    'gas': 'NeoGas',   # "Gas fees" içinde geçebilir
    'sun': 'Sun',      # "Sunday" veya güneş anlamında geçebilir
    'just': 'Just',    # "Just now" içinde geçebilir
    'omg': 'OMG Network', # "Oh my god" kısaltması
    'meme': 'Memecoin',   # Genel "meme" kelimesi
    'beta': 'Beta Finance', # Yazılım betası
    'iot': 'Helium IOT', # IoT teknolojisi
}