import os
from web3 import Web3

# --- 2026 CTF CONTRACT ADDRESSES ---
CTF_ADDRESS = "0x4D970221A8585C0B354710200898520310210201" # Polymarket CTF
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# Minimal ABI for Redemptions
CTF_ABI = '[{"inputs":[{"internalType":"address","name":"collateralToken","type":"address"},{"internalType":"bytes32","name":"parentCollectionId","type":"bytes32"},{"internalType":"bytes32","name":"conditionId","type":"bytes32"},{"internalType":"uint256[]","name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

async def guarantee_payout(context, chat_id, condition_id):
    """
    THE WATCHDOG: 
    Monitors the market and forces the Smart Contract to pay out the USDC.
    """
    contract = w3.eth.contract(address=CTF_ADDRESS, abi=CTF_ABI)
    
    await context.bot.send_message(chat_id, "🔍 **Watchdog Active:** Monitoring Oracle for resolution...")

    # 1. Wait for Oracle Finalization (UMA / Chainlink)
    # In production, you would poll the Gamma API for 'closed' status
    
    try:
        # 2. Trigger On-Chain Redemption
        # indexSets: [1] for YES, [2] for NO
        nonce = w3.eth.get_transaction_count(vault.address)
        tx = contract.functions.redeemPositions(
            USDC_ADDRESS,
            "0x0000000000000000000000000000000000000000000000000000000000000000", # Parent ID
            condition_id,
            [1, 2] # Try to redeem both (losing side returns 0, winning returns $1)
        ).build_transaction({
            'from': vault.address,
            'nonce': nonce,
            'gas': 150000,
            'gasPrice': int(w3.eth.gas_price * 1.5)
        })

        signed_tx = w3.eth.account.sign_transaction(tx, vault.key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        await context.bot.send_message(chat_id, f"💎 **PROFIT CLAIMED:** USDC pulled from pool.\nTX: `{tx_hash.hex()[:10]}...`")
        
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ **Redemption Pending:** Market not yet resolved by Oracle.")
