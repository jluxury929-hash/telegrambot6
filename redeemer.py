import os
import asyncio
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
vault = w3.eth.account.from_key(os.getenv("WALLET_SEED"))

# --- BUFFER FINANCE / POLYMARKET REDEMPTION LOGIC ---
# This ABI allows the bot to 'Claim' or 'Redeem' from the Pool
MINIMAL_ABI = '[{"inputs":[],"name":"claimWinnings","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

async def claim_payout(contract_address):
    """
    Triggers the second transaction receipt: The Payout.
    This pulls the Profit + Stake from the Liquidity Pool back to your vault.
    """
    contract = w3.eth.contract(address=contract_address, abi=MINIMAL_ABI)
    
    # 1. Prepare the 'Claim' Transaction
    nonce = w3.eth.get_transaction_count(vault.address)
    tx = contract.functions.claimWinnings().build_transaction({
        'from': vault.address,
        'nonce': nonce,
        'gas': 120000,
        'gasPrice': int(w3.eth.gas_price * 1.5),
        'chainId': 137 # Polygon
    })

    # 2. Sign and Send
    signed_tx = w3.eth.account.sign_transaction(tx, vault.key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    return tx_hash.hex()
