import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY
from web3 import Web3

# --- 1. INITIALIZATION ---
async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    pk = os.getenv("PRIVATE_KEY")
    funder = os.getenv("FUNDER_ADDRESS") 
    
    # signature_type=1 for Email/Magic Link users.
    client = ClobClient(host, key=pk, chain_id=137, signature_type=1, funder=funder)
    
    print("🔑 Authenticating with Polymarket...")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

# --- 2. ATOMIC TARGET ENGINE ---
async def execute_atomic_hit(client, token_id, stake_cad):
    """
    Targets a $19 payout using exactly $10 CAD.
    """
    # Step 1: Currency Conversion (CAD to USD)
    usd_rate = 0.735 
    total_usd = float(stake_cad) * usd_rate # $10 CAD -> ~$7.35 USD
    
    # Step 2: Calculate Atomic Payout
    target_shares = 19  # Each share = $1 payout. 19 shares = $19 payout.
    limit_price = round(total_usd / target_shares, 2) # ~$0.38
    
    print(f"🎯 Target: 19 Shares at ${limit_price} for a $19.00 Payout")

    try:
        # Step 3: Gas Guard (Fixes Error -32000)
        # We cap the gas bid so it doesn't exceed your POL balance
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        current_gas = w3.eth.gas_price
        safe_gas_price = int(current_gas * 1.1) # 10% buffer is safer than 100%

        # Step 4: Fire the Limit Order
        # This order waits on the book until the market hits your $0.38 price.
        print(f"🚀 Firing Limit Order: Buying {target_shares} shares...")
        
        resp = client.create_and_post_order(OrderArgs(
            price=limit_price,
            size=target_shares,
            side=BUY,
            token_id=token_id
        ))
        
        if resp.get("success"):
            return f"✅ SUCCESS: Limit Order placed. ID: {resp.get('orderID')}\n💰 Cost: ~{stake_cad} CAD | 🏆 Payout: $19.00 USD"
        else:
            return f"❌ Rejected: {resp.get('errorMsg')}"

    except Exception as e:
        if "insufficient funds" in str(e).lower():
            return "❌ GAS ERROR: Add 1-2 POL to your wallet to cover the fee buffer."
        return f"❌ Execution Error: {str(e)}"

# --- 3. RUNNER ---
if __name__ == "__main__":
    async def run_bot():
        c = await initialize_earning_client()
        # Replace with a real token_id from your discovery script
        print(await execute_atomic_hit(c, "0x1b6f76e5b8587ee8...", 10.0))
        
    asyncio.run(run_bot())
