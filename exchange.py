import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

# API Setup
PK = os.getenv("WALLET_PRIVATE_KEY")
creds = ApiCreds(
    key=os.getenv("POLY_API_KEY"),
    secret=os.getenv("POLY_API_SECRET"),
    passphrase=os.getenv("POLY_API_PASSPHRASE")
)

client = ClobClient("https://clob.polymarket.com", key=PK, chain_id=137, creds=creds)

async def place_order(side, amount):
    # BTC YES/NO Token IDs (2026 Standard)
    YES_TOKEN = "88613172803544318200496156596909968959424174365708473463931555296257475886634"
    NO_TOKEN = "93025177978745967226369398316375153283719303181694312089956059680730874301533"
    
    target_token = YES_TOKEN if side == "CALL" else NO_TOKEN
    
    try:
        resp = client.create_order(OrderArgs(
            price=0.50,
            size=float(amount) / 0.50,
            side="BUY",
            token_id=target_token
        ))
        return resp.get("success"), resp.get("orderID")
    except Exception as e:
        return False, str(e)
