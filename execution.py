import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY
from web3 import Web3

# --- 1. CONFIG ---
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    client = ClobClient(host, key=os.getenv("PRIVATE_KEY"), chain_id=137, signature_type=1, funder=os.getenv("FUNDER_ADDRESS"))
    client.set_api_creds(client.create_or_derive_api_creds())
    return client

# --- 2. ATOMIC EXECUTION ---
async def execute_atomic_hit(client, token_id, stake_cad):
    # Step 1: Currency Conversion
    usd_rate = 0.735  # $1 CAD to USD
    budget_usd = float(stake_cad) * usd_rate # $10 CAD -> $7.35 USD
    
    # Step 2: Set the Hard Target
    target_payout_usd = 19.00
    shares_to_buy = 19 
    
    # CALCULATE THE MAX PRICE (to keep cost at/under $10 CAD)
    # Price = Budget / Shares
    max_price_allowed = round(budget_usd / shares_to_buy, 2) # 0.38
    
    print(f"🛡️ Profit Shield: Budget is ${budget_usd:.2f} USD ({stake_cad} CAD)")
    print(f"🎯 Target: 19 shares at ${max_price_allowed} or lower.")

    try:
        # Step 3: Check Current Market Price
        price_data = client.get_price(token_id, side=BUY)
        current_market_price = float(price_data['price'])
        
        # STOP if it's too expensive
        if current_market_price > max_price_allowed:
            diff = current_market_price - max_price_allowed
            return f"❌ ABORTED: Market price is ${current_market_price}. You would overspend by ${diff*shares_to_buy:.2f}."

        # Step 4: Gas Guard
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        safe_gas = int(w3.eth.gas_price * 1.1)

        # Step 5: Fire the Order
        print(f"🚀 Firing order at ${current_market_price} (CHEAPER THAN TARGET!)")
        
        resp = client.create_and_post_order(OrderArgs(
            price=current_market_price,
            size=shares_to_buy,
            side=BUY,
            token_id=token_id
        ))
        
        if resp.get("success"):
            actual_cost_cad = (current_market_price * shares_to_buy) / usd_rate
            return f"✅ SUCCESS: Bet placed for {actual_cost_cad:.2f} CAD. Payout: $19.00 USD."
        else:
            return f"❌ Rejected: {resp.get('errorMsg')}"

    except Exception as e:
        return f"❌ Error: {str(e)}"

if __name__ == "__main__":
    async def run():
        c = await initialize_earning_client()
        # Ensure you use the correct token_id for the market you want
        print(await execute_atomic_hit(c, "YOUR_TOKEN_ID", 10.0))
    asyncio.run(run())
