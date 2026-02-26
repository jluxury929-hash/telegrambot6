async def run_atomic_execution(context, chat_id, side):
    """
    UNIFIED ATOMIC ENGINE:
    Sends Stake + Profit as a single high-priority transaction.
    """
    if not vault:
        return False, "❌ Vault not initialized."

    # 1. Configurable Stake & Profit Multiplier
    stake_usd = float(context.user_data.get('stake', 10))
    payout_multiplier = 1.92  # 1.0 (Stake) + 0.92 (Profit)
    
    # 2. Conversion Logic (2026 Fallback Rate: 0.1478 POL/USD)
    conversion = 0.1478 
    total_usd = stake_usd * payout_multiplier
    total_val_wei = w3.to_wei(total_usd / conversion, 'ether')
    
    # 3. Network Sync: Get 'pending' nonce to jump the queue
    nonce = w3.eth.get_transaction_count(vault.address, 'pending')
    
    # 4. Aggressive Gas: 1.4x multiplier to ensure the hit lands in the next block
    priority_gas = int(w3.eth.gas_price * 1.4)

    # 5. Build the Single Unified Transaction
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': total_val_wei,
        'gas': 21000,
        'gasPrice': priority_gas,
        'chainId': 137 # Polygon Mainnet
    }

    try:
        # 6. Atomic Sign & Release (Sub-1ms overhead)
        signed_tx = w3.eth.account.sign_transaction(tx, vault.key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        report = (
            f"✅ **UNIFIED ATOMIC HIT!**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎯 **Direction:** {side}\n"
            f"💰 **Total Sent:** `${total_usd:.2f}`\n"
            f"   *(Stake: `${stake_usd:.2f}` + Profit: `${stake_usd * 0.92:.2f}`)*\n"
            f"⚡ **Gas Priority:** {w3.from_wei(priority_gas, 'gwei'):.1f} Gwei\n"
            f"🔗 [View Receipt](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        return True, report

    except Exception as e:
        return False, f"❌ **Execution Error:** `{str(e)}`"
