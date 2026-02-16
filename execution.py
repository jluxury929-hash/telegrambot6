import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY
from web3 import Web3

# --- 1. THE ADDRESSES (2026 Standards) ---
# Most exchanges like Coinbase now send Native USDC
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

async def initialize_earning_client():
    host = "https://clob.polymarket.com"
    pk = os.getenv("PRIVATE_KEY")
    funder = os.getenv("FUNDER_ADDRESS")
    
    # signature_type=1 is for Email/Magic Link. Use 0 if using a MetaMask PK.
    client = ClobClient(host, key=pk, chain_id=137, signature_type=1, funder=funder)
    
    print("🔑 Authenticating with Polymarket...")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

# --- 2. THE UNIVERSAL BALANCE CHECK ---
def check_usdc_liquidity(funder):
    w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
    abi = '[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]'
    
    native = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=abi)
    bridged = w3.eth.contract(address=Web3.to_checksum_address(USDC_BRIDGED), abi=abi)
    
    bal_native = native.functions.balanceOf(funder).call() / 1e6
    bal_bridged = bridged.functions.balanceOf(funder).call() / 1e6
    
    print(f"💰 USDC Native: ${bal_native:.2f} | USDC.e: ${bal_bridged:.2f}")
    return bal_native + bal_bridged

# --- 3. ATOMIC TARGET ENGINE ---
async def execute_atomic_hit(client, token_id, stake_cad):
    funder = os.getenv("FUNDER_ADDRESS")
    
    # Check total USDC power
    total_usdc = check_usdc_liquidity(funder)
    
    usd_rate = 0.735 
    required_usd = float(stake_cad) * usd_rate # $10 CAD -> ~$7.35 USD
    
    if total_usdc < required_usd:
        return f"❌ Insufficient Funds: You have ${total_usdc:.2f} USD, but need ${required_usd:.2f} ($10 CAD)."

    # Step 2: Target $19 payout (19 shares)
    target_shares = 19  
    limit_price = round(required_usd / target_shares, 2) # ~$0.38

    try:
        # Step 3: Gas Guard (Manual cap to avoid -32000)
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        safe_gas_price = int(w3.eth.gas_price * 1.1)

        # Step 4: Fire the Limit Order
        print(f"🚀 Firing Limit Order: 19 shares @ ${limit_price}...")
        
        resp = client.create_and_post_order(OrderArgs(
            price=limit_price,
            size=target_shares,
            side=BUY,
            token_id=token_id
        ))
        
        if resp.get("success"):
            return f"✅ SUCCESS: Limit Order placed. ID: {resp.get('orderID')}\n💰 Cost: ~{stake_cad} CAD | 🏆 Payout: $19.00 USD"
        else:
            # Handle the 'Activation' issue
            if "allowance" in str(resp).lower():
                return "❌ ALLOWANCE ERROR: Go to Polymarket.com and click 'Enable USDC' to activate your funds."
            return f"❌ Rejected: {resp.get('errorMsg')}"

    except Exception as e:
        return f"❌ Execution Error: {str(e)}"

if __name__ == "__main__":
    async def run():
        c = await initialize_earning_client()
        print(await execute_atomic_hit(c, "YOUR_TOKEN_ID", 10.0))
    asyncio.run(run())
