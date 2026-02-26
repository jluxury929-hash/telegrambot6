import os, subprocess, time
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

# --- CONFIG ---
load_dotenv()
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")
USDC_NATIVE = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
RPC_URL = os.getenv("RPC_URL", "https://polygon-rpc.com")
SEED = os.getenv("WALLET_SEED")

# Minimal ABI for silent check
ABI = '[{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","uint256"}],"type":"function"}]'

def silent_approve_and_launch():
    print("🛠️  BOOTING SYSTEM...")
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    Account.enable_unaudited_hdwallet_features()
    
    # Load Vault
    if " " in SEED: vault = Account.from_mnemonic(SEED)
    else: vault = Account.from_key(SEED if SEED.startswith("0x") else "0x"+SEED)
    
    usdc = w3.eth.contract(address=USDC_NATIVE, abi=ABI)
    addr = Web3.to_checksum_address(vault.address)
    
    # Check Allowance
    try:
        allowance = usdc.functions.allowance(addr, CTF_EXCHANGE).call()
        if allowance < 10**12:
            print("⛽ LOW ALLOWANCE: Silently approving USDC...")
            tx = usdc.functions.approve(CTF_EXCHANGE, 2**256 - 1).build_transaction({
                'from': addr, 
                'nonce': w3.eth.get_transaction_count(addr), 
                'gasPrice': w3.eth.gas_price
            })
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            print("✅ APPROVAL SENT. Waiting for confirmation...")
            time.sleep(15) # Brief wait for the blockchain to update
        else:
            print("✅ AUTH OK: Vault already approved.")
    except Exception as e:
        print(f"⚠️  PREFLIGHT ERROR (Skipping): {e}")

    # Launch the main bot
    print("🚀 LAUNCHING CRYPTO SNIPER...")
    subprocess.run(["python", "main.py"])

if __name__ == "__main__":
    silent_approve_and_launch()
