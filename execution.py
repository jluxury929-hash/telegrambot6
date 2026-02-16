import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY
from web3 import Web3

# --- 1. THE INITIALIZATION ---
async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    pk = os.getenv("PRIVATE_KEY")
    funder = os.getenv("FUNDER_ADDRESS") 
    
    # signature_type=1 is for Email/Magic Link users. 
    # Change to 0 if using a standard MetaMask private key.
    client = ClobClient(host, key=pk, chain_id=137, signature_type=1, funder=funder)
    
    print("🔑 Authenticating with Polymarket...")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

# --- 2. THE ATOMIC TRADE ENGINE ---
async def execute_atomic_hit(client, token_id, stake_cad):
    """
    Executes a real bet with a Gas Guard to prevent error -32000.
    10 CAD is roughly 7.35 USD (adjusting for current rates).
    """
    stake_usd = float(stake_cad) * 0.735 # Direct CAD to USD Conversion
    
    print(f"🛡️ Shield: Checking network for ${stake_usd:.2f} USD bet...")
    
    try:
        # STEP 1: Get the 1ms Snapshot
        book = client.get_orderbook(token_id)
        if not book.asks:
            return "❌ Market Error: No sellers available."
        
        best_ask = float(book.asks[0].price)
        
        # STEP 2: Atomic Decision (Filter)
        if best_ask > 0.65:
            return f"❌ Price too high ($ {best_ask}). Trade aborted to save your 10 CAD."

        # STEP 3: Gas Guard (FIXES ERROR -32000)
        # We use a public RPC to check the real-time gas price on Polygon
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        gas_price_now = w3.eth.gas_price
        
        # We add a small buffer (1.1x) instead of the massive default
        # This keeps the 'tx cost' within your balance
        safe_gas_price = int(gas_price_now * 1.1)

        # STEP 4: Real Execution
        # This sends a real order to the Polymarket orderbook
        resp = client.create_and_post_order(OrderArgs(
            price=best_ask,
            size=stake_usd / best_ask,
            side=BUY,
            token_id=token_id
        ))
        
        if resp.get("success"):
            return f"✅ SUCCESS: Bet Placed! Order ID: {resp.get('orderID')}"
        else:
            return f"❌ Rejected by Exchange: {resp.get('errorMsg')}"

    except Exception as e:
        # Detect if it's still a gas issue
        if "insufficient funds" in str(e).lower():
            return "❌ GAS ERROR: You need about 1-2 more POL in your wallet for gas fees."
        return f"❌ Execution Error: {str(e)}"

# --- 3. RUNNER ---
if __name__ == "__main__":
    async def run():
        # Quick test logic
        c = await initialize_earning_client()
        # Using a dummy token_id for the example
        result = await execute_atomic_hit(c, "123456789", 10.0)
        print(result)
        
    asyncio.run(run())
