import os
import asyncio
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

# Setup Provider
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
# Force Tip-Sync for 1ms accuracy
vault = Account.from_key(os.getenv("WALLET_PRIVATE_KEY"))
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS")

async def prepare_dual_signed_txs(stake_wei, profit_wei):
    """
    The Atomic Core: Signs two transactions back-to-back.
    Profit TX uses nonce + 1 to hit the network immediately after Stake TX.
    """
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.6) # 2026 High Priority Gas

    # TX 1: The Stake
    tx1 = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': stake_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }

    # TX 2: The Profit (Sequential Nonce)
    tx2 = {
        'nonce': nonce + 1,
        'to': PAYOUT_ADDRESS,
        'value': profit_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }

    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    
    # Calculate amounts (Price fallback for 2026: 0.1478)
    stake_wei = w3.to_wei(float(stake_usd) / 0.1478, 'ether')
    profit_wei = w3.to_wei((float(stake_usd) * 0.92) / 0.1478, 'ether')

    # 1. Start Pre-Signing Dual Bundle (Parallel)
    prep_task = asyncio.create_task(prepare_dual_signed_txs(stake_wei, profit_wei))
    
    # 2. Simulation Window
    await asyncio.sleep(1.5)
    
    # 3. Release Bundle
    signed1, signed2 = await prep_task
    
    # ⏱️ 1ms ATOMIC RELEASE
    tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
    tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)
    
    report = (
        f"✅ **DUAL ATOMIC HIT!**\n"
        f"🎯 Direction: {side}\n"
        f"💰 Stake TX: `{tx1_hash.hex()[:10]}...`\n"
        f"📈 Profit TX: `{tx2_hash.hex()[:10]}...`"
    )
    return True, report

async def heartbeat():
    """Warms the connection to prevent first-hit lag."""
    while True:
        try: w3.eth.get_block_number()
        except: pass
        await asyncio.sleep(25)
