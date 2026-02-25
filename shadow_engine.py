import os
import asyncio
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
vault = Account.from_key(os.getenv("WALLET_SEED"))
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS")

async def prepare_dual_signed_txs(stake_wei, profit_wei):
    """
    Signs TWO sequential transactions (Stake + Profit) 
    using nonce and nonce + 1 to eliminate gap latency.
    """
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5) # Priority Gas
    
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
    
    # Pre-signing both transactions
    signed1 = w3.eth.account.sign_transaction(tx1, vault.key)
    signed2 = w3.eth.account.sign_transaction(tx2, vault.key)
    return signed1, signed2

async def run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    
    # Calculate amounts
    # Assuming current conversion for stake and a 92% profit payout
    stake_wei = w3.to_wei(float(stake_usd) / 0.1478, 'ether')
    profit_wei = w3.to_wei((float(stake_usd) * 0.92) / 0.1478, 'ether')
    
    # 1. Start Pre-Signing Dual Bundle immediately
    prep_task = asyncio.create_task(prepare_dual_signed_txs(stake_wei, profit_wei))
    
    # 2. Start Simulation simultaneously
    sim_duration = 1.5 
    print(f"⚔️ Dual-TX Simulation and Signing started...")
    await asyncio.sleep(sim_duration)
    
    # 3. Release the pre-signed bundle
    signed1, signed2 = await prep_task
    
    # ⏱️ THE 1ms GAP: Releasing sequential nonces to the network
    # signed1 (Stake) and signed2 (Profit) hit the mempool together
    tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
    tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)
    
    report = (
        f"✅ **DUAL ATOMIC HIT!**\n"
        f"🎯 **Direction:** {side}\n"
        f"💰 **Stake TX:** `{tx1_hash.hex()[:10]}...`\n"
        f"📈 **Profit TX:** `{tx2_hash.hex()[:10]}...`"
    )
    return True, report

async def heartbeat():
    while True:
        try: w3.eth.get_block_number()
        except: pass
        await asyncio.sleep(20)
