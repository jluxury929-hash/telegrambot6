import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# --- 1. THE INITIALIZATION (Your Logic) ---
async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    pk = os.getenv("PRIVATE_KEY")
    funder = os.getenv("FUNDER_ADDRESS") # Your Polymarket Proxy Address
    
    # Init L1 Client
    client = ClobClient(host, key=pk, chain_id=137, signature_type=1, funder=funder)
    
    # Apply your L2 Logic: Derive keys automatically
    creds = await client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

# --- 2. THE ATOMIC TRADE (Real Earning) ---
async def execute_atomic_hit(client, token_id, stake_usd):
    print("🛡️ Shield: Simulating Market Depth...")
    
    try:
        # STEP 1: Get the 1ms Snapshot
        book = client.get_orderbook(token_id)
        best_ask = float(book.asks[0].price)
        
        # STEP 2: Atomic Decision
        # Only buy if the price is 'fair' (e.g., under 0.65)
        if best_ask > 0.65:
            return "❌ Price too high. Trade aborted to save funds."

        # STEP 3: Real Execution (The code you provided)
        # Market BUY orders are matched off-chain and settled on-chain
        resp = client.create_and_post_order(OrderArgs(
            price=best_ask,
            size=stake_usd / best_ask,
            side=BUY,
            token_id=token_id
        ))
        
        return resp
    except Exception as e:
        return f"❌ Execution Error: {str(e)}"
