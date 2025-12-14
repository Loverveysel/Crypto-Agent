
import os
from dotenv import load_dotenv
from utils import get_top_pairs
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
TARGET_PAIRS = get_top_pairs(100)

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
IGNORE_KEYWORDS = ['daily', 'digest', 'recap', 'summary', 'analysis', 'price analysis', 'prediction', 'overview', 'roundup']

DANGEROUS_TICKERS = {
    'S', 'THE', 'A', 'I', 'IS', 'TO', 'IT', 'BY', 'ON', 'IN', 'AT', 'OF', 
    'ME', 'MY', 'UP', 'DO', 'GO', 'OR', 'IF', 'BE', 'AS', 'WE', 'SO',
    'NEAR', 'ONE', 'SUN', 'GAS', 'POL', 'BOND', 'OM', 'ELF', 'MEME', 'AI', 'MOVE'
}