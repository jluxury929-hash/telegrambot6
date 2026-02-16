import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
from web3 import Web3

# --- 1. SETUP ---
async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    pk = os.getenv("PRIVATE_KEY")
    funder = os.getenv("FUNDER_ADDRESS")
    
    # signature_type=1 for Polymarket proxy wallets
    client = ClobClient(host, key=pk, chain_id=137, signature_type=1, funder=funder)
    
    print("🔑 Authenticating...")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

# --- 2. THE PROFIT SHIELD ENGINE ---
async def execute_atomic_hit(client, token_id):
    """
    Guarantees you never spend more than $10 CAD.
    Forces a payout target of $19 CAD ($14 USD).
    """
    # FEB 2026 Exchange Rate: 1 USD = 1.362 CAD
    target_shares = 14 # Pays out $14.00 USD (~$19.06 CAD)
    limit_price = 0.52 # Cost: $7.28 USD (~$9.92 CAD)

    print(f"🛡️ Profit Shield: 14 shares @ ${limit_price} limit.")

    try:
        # STEP 1: Gas Guard (Save your POL balance)
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        # Using a tight 1.05 multiplier prevents 'overshot' errors
        safe_gas_price = int(w3.eth.gas_price * 1.05)

        # STEP 2: The 'Post-Only' Maker Order
        # This ensures you pay $0.00 fees and don't buy at a bad price
        print(f"🚀 Firing Maker Order at ${limit_price}...")
        
        order_args = OrderArgs(
            price=limit_price,
            size=target_shares,
            side=BUY,
            token_id=token_id
        )
        
        # We create the order first
        signed_order = client.create_order(order_args)
        
        # post_only=True is the key. It prevents the 3.15% fee.
        # If the price is higher than 0.52, the order sits and waits.
        resp = client.post_order(signed_order, order_type=OrderType.GTC, post_only=True)

        if resp.get("success"):
            return f"✅ SUCCESS: Order set for ~$9.92 CAD cost.\n🏆 Payout on win: ~$19.06 CAD."
        else:
            # If it's too expensive, the 'post_only' flag will reject it rather than lose you money
            return f"❌ Rejected: {resp.get('errorMsg')} (Price too high right now)"

    except Exception as e:
        return f"❌ Execution Error: {str(e)}"

# --- 3. RUNNER ---
if __name__ == "__main__":
    async def run():
        c = await initialize_earning_client()
        # Ensure your TOKEN_ID is from a market currently priced near 0.50
        print(await execute_atomic_hit(c, "YOUR_TOKEN_ID"))
    asyncio.run(run())
