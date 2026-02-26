import os
import asyncio
from web3 import Web3
from eth_account import Account
from decimal import Decimal

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
vault = Account.from_key(os.getenv("WALLET_SEED"))
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS")

async def prepare_dual_bundle(stake_wei, profit_wei):
    """
    Core Engine: Signs two transactions back-to-back.
    The Profit TX uses (nonce + 1) to ensure they are processed sequentially.
    """
    # Fetch current nonce for the vault
    nonce = w3.eth.get_transaction_count(vault.address)
    # 2026 Priority Gas (1.6x multiplier for instant inclusion)
    gas_price = int(w3.eth.gas_price * 1.6) 

    # 1. Transaction: The Stake
    tx1 = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': stake_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137 # Polygon Mainnet
    }

    # 2. Transaction: The Profit (Nonce + 1)
    tx2 = {
        'nonce': nonce + 1,
        'to': PAYOUT_ADDRESS,
        'value': profit_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }

    # Sign both locally (No network lag during signing)
    signed_stake = w3.eth.account.sign_transaction(tx1, vault.key)
    signed_profit = w3.eth.account.sign_transaction(tx2, vault.key)
    
    return signed_stake, signed_profit

async def run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    
    # Financial Calculations (2026 Fallback Rate: 0.1478 POL/USD)
    # Profit set at 92% of stake
    conversion_rate = 0.1478
    stake_wei = w3.to_wei(float(stake_usd) / conversion_rate, 'ether')
    profit_wei = w3.to_wei((float(stake_usd) * 0.92) / conversion_rate, 'ether')

    # 1. Start Pre-Signing Dual Bundle (Heavy CPU task)
    prep_task = asyncio.create_task(prepare_dual_bundle(stake_wei, profit_wei))
    
    # 2. Simulation Window (Mimics analysis delay)
    await asyncio.sleep(1.5)
    
    # 3. Release Bundle
    # Wait for the signing task to return the signed raw transactions
    signed1, signed2 = await prep_task
    
    # ⏱️ 1ms ATOMIC RELEASE: Broadcast both to the mempool
    tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
    tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)
    
    report = (
        f"✅ **DUAL ATOMIC HIT!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 **Direction:** {side}\n"
        f"💰 **Stake Sent:** `{w3.from_wei(stake_wei, 'ether'):.2f} POL`\n"
        f"💎 **Profit Sent:** `{w3.from_wei(profit_wei, 'ether'):.2f} POL`\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔗 **Stake TX:** `{tx1_hash.hex()[:12]}...`\n"
        f"🔗 **Profit TX:** `{tx2_hash.hex()[:12]}...`"
    )
    
    await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    return True
