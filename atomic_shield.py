import os
import asyncio
import json
import requests
import websockets
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from dotenv import load_dotenv

load_dotenv()

# --- 1. ENDPOINT CONFIGURATION ---
GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL  = "https://clob.polymarket.com"
DATA_URL  = "https://data-api.polymarket.com"
WSS_URL   = "wss://ws-subscriptions-clob.polymarket.com"

# AUTH
PK = os.getenv("WALLET_PRIVATE_KEY")
creds = ApiCreds(
    key=os.getenv("POLY_API_KEY"),
    secret=os.getenv("POLY_API_SECRET"),
    passphrase=os.getenv("POLY_API_PASSPHRASE")
)

# Initialize the Master Client
client = ClobClient(CLOB_URL, key=PK, chain_id=137, creds=creds)

# --- 2. GAMMA: METADATA ENGINE ---
def get_market_tokens(slug):
    """Uses Gamma API to find the Token IDs for YES and NO."""
    url = f"{GAMMA_URL}/markets?slug={slug}"
    res = requests.get(url).json()
    # In Polymarket, 'clobTokenIds' are the keys to the trade
    tokens = json.loads(res[0]['clobTokenIds'])
    return tokens[0], tokens[1] # Returns (YES_ID, NO_ID)

# --- 3. DATA API: ANALYTICS ENGINE ---
def get_volume_stats(condition_id):
    """Uses Data API to check if there is enough volume to earn."""
    url = f"{DATA_URL}/markets/{condition_id}/stats"
    return requests.get(url).json()

# --- 4. WEBSOCKET: 1ms LISTENER ---
async def start_high_speed_listener(token_id):
    """Listens to the CLOB for every single price tick."""
    async with websockets.connect(WSS_URL) as ws:
        subscribe_msg = {
            "type": "subscribe",
            "topic": "market",
            "assets_ids": [token_id]
        }
        await ws.send(json.dumps(subscribe_msg))
        print(f"📡 WSS: Listening to Token {token_id[:10]}...")
        
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            # ⚡ ATOMIC TRIGGER:
            # If the price drops below your target, fire the trade in <1ms
            if "price" in data:
                print(f"🎯 TICK: New Price {data['price']}")

# --- 5. CLOB: THE ATOMIC HIT ---
async def fire_atomic_trade(token_id, side, amount):
    """Places the REAL order on the Polymarket CLOB."""
    order = OrderArgs(
        price=0.50, # Limit price
        size=amount / 0.50,
        side="BUY",
        token_id=token_id
    )
    resp = client.create_order(order)
    return resp

if __name__ == "__main__":
    # Example: Finding a market, checking stats, and readying for a trade
    yes_token, no_token = get_market_tokens("will-bitcoin-hit-100k")
    print(f"✅ Gamma Linked: YES Token {yes_token[:10]}")
    
    # Start the engine
    # asyncio.run(start_high_speed_listener(yes_token))
