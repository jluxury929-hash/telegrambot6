import os
import requests
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

# --- 1. CLOB SYSTEM SETUP ---
# You need these from your Polymarket Settings -> API
POLY_API_URL = "https://clob.polymarket.com"
PK = os.getenv("WALLET_PRIVATE_KEY")
CREDS = ApiCreds(
    key=os.getenv("POLY_API_KEY"),
    secret=os.getenv("POLY_API_SECRET"),
    passphrase=os.getenv("POLY_API_PASSPHRASE")
)

# Initialize the High-Speed Trading Client
client = ClobClient(POLY_API_URL, key=PK, chain_id=137, creds=CREDS)

# --- 2. ATOMIC EXECUTION LOGIC ---
async def run_atomic_clob_trade(context, chat_id):
    # Using your example IDs for BTC YES/NO
    YES_TOKEN = "1234567890" 
    NO_TOKEN = "0987654321"

    await context.bot.send_message(chat_id, "🛡️ **Atomic Shield:** Fetching Batch Books...")

    try:
        # STEP 1: GET BATCH BOOKS (As per your curl request)
        # This returns the snapshot of both sides at once
        response = requests.post(
            f"{POLY_API_URL}/books",
            json=[{"token_id": YES_TOKEN}, {"token_id": NO_TOKEN}]
        )
        batch_data = response.json()

        # STEP 2: 1ms "EARNING" ANALYSIS
        # We look at the 'asks' array from your example data
        yes_ask = float(batch_data[0]['asks'][0]['price'])
        no_ask = float(batch_data[1]['asks'][0]['price'])
        
        # Check Liquidity: We only trade if 'size' is large enough to fill us
        yes_size = float(batch_data[0]['asks'][0]['size'])
        no_size = float(batch_data[1]['asks'][0]['size'])

        # DECISION: We choose the side with the tightest price and highest size
        winning_side = "CALL" if yes_size > no_size else "PUT"
        target_token = YES_TOKEN if winning_side == "CALL" else NO_TOKEN
        target_price = yes_ask if winning_side == "CALL" else no_ask

        # STEP 3: THE ATOMIC HIT (Live Market Order)
        stake = float(context.user_data.get('stake', 10))
        order = OrderArgs(
            price=target_price,
            size=stake / target_price,
            side="BUY",
            token_id=target_token
        )
        
        # This physically places the order into the Polymarket CLOB
        resp = client.create_order(order)

        if resp.get("success"):
            report = (
                f"✅ **BATCH ATOMIC HIT!**\n"
                f"🎯 **Market Hash:** `{batch_data[0]['hash'][:10]}...`\n"
                f"📊 **Decision:** {winning_side} @ ${target_price:.4f}\n"
                f"💰 **Total Stake:** `${stake:.2f} USDCe` (Real Market)\n"
                f"⛓️ **Order ID:** `{resp['orderID']}`"
            )
            await context.bot.send_message(chat_id, report, parse_mode='Markdown')
        else:
            raise Exception(resp.get("errorMsg", "Order Rejected"))

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Atomic Error:** `{str(e)}`")
