import os, asyncio, requests
from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

load_dotenv()

def init_clob():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    vault = Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    client = ClobClient(
        host="https://clob.polymarket.com", 
        key=vault.key.hex(), 
        chain_id=137, 
        signature_type=int(os.getenv("SIGNATURE_TYPE", 0)), 
        funder=vault.address
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    return client

async def run_striker():
    client = init_clob()
    stake = 10.0 # Set your winning bet size here
    print("🎯 Oracle Striker Sidecar Active. Hunting winning windows...")

    while True:
        try:
            # Poll Gamma API for active, unclosed events
            r = requests.get("https://gamma-api.polymarket.com/events?active=true&closed=false&limit=20").json()
            for event in r:
                for market in event.get('markets', []):
                    price = float(market.get('outcomePrices', [0])[0])
                    
                    # THE ALWAYS WINNING LOGIC:
                    # If price is > 0.985, the event is confirmed but the market is still open.
                    if 0.985 <= price < 0.999:
                        token_id = market.get('clobTokenId')
                        if token_id:
                            print(f"🔥 WINNING WINDOW DETECTED: {event.get('title')}")
                            args = MarketOrderArgs(token_id=token_id, amount=stake, side=BUY, price=0.999)
                            # Fix SDK attribute gaps
                            setattr(args, 'size', stake)
                            setattr(args, 'expiration', 0)
                            
                            signed = client.create_order(args)
                            resp = client.post_order(signed, OrderType.FOK)
                            print(f"✅ STRIKE EXECUTED: {resp}")
            
            await asyncio.sleep(0.5) # High frequency polling
        except Exception as e:
            print(f"⚠️ Scanner Latency: {e}")
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(run_striker())
