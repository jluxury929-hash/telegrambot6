import os
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

# Initialize Client
PK = os.getenv("WALLET_PRIVATE_KEY")
creds = ApiCreds(
    key=os.getenv("POLY_API_KEY"),
    secret=os.getenv("POLY_API_SECRET"),
    passphrase=os.getenv("POLY_API_PASSPHRASE")
)
client = ClobClient("https://clob.polymarket.com", key=PK, chain_id=137, creds=creds)

async def execute_real_market_bet(context, chat_id, side):
    stake_cad = context.user_data.get('stake', 10)
    
    # BTC 15m Token Placeholder IDs
    YES_TOKEN = "88613172803544318200496156596909968959424174365708473463931555296257475886634"
    NO_TOKEN = "93025177978745967226369398316375153283719303181694312089956059680730874301533"
    target_token = YES_TOKEN if side == "CALL" else NO_TOKEN
    
    try:
        order_args = OrderArgs(
            price=0.50,
            size=float(stake_cad) / 0.50,
            side="BUY",
            token_id=target_token
        )
        resp = client.create_order(order_args)
        if resp.get("success"):
            return True, f"✅ **Order Placed:** `{resp['orderID']}`"
        else:
            raise Exception("Order Rejected")
    except Exception as e:
        return False, f"❌ **Error:** {str(e)}"
