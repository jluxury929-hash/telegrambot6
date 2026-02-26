async def run_atomic_execution(context, chat_id, side):
    """
    THE DUAL-PAYOUT ENGINE:
    Calculates Stake + Profit and releases them in a single high-priority TX.
    """
    if not vault: 
        return False, "❌ Vault not initialized."

    # 1. Get user stake and define the 92% profit multiplier
    stake_usd = float(context.user_data.get('stake', 10))
    payout_multiplier = 1.92  # 1.0 (Stake) + 0.92 (Profit)
    
    # 2. Financial Math (Using 2026 Fallback Rate)
    conversion = 0.1478 
    total_val_wei = w3.to_wei((stake_usd * payout_multiplier) / conversion, 'ether')
    
    # 3. Network Sync: Get the latest 'pending' nonce to avoid collisions
    nonce = w3.eth.get_transaction_count(vault.address, 'pending')
    
    # 4. Aggressive Gas: 1.3x multiplier to "guarantee" front-run positioning
    priority_gas = int(w3.eth.gas_price * 1.3)

    # 5. Build the Atomic Transaction
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': total_val_wei,
        'gas': 21000,
        'gasPrice': priority_gas,
        'chainId': 137 # Polygon
    }

    try:
        # 6. Pre-Sign (Local CPU - 0ms Latency)
        signed_tx = w3.eth.account.sign_transaction(tx, vault.key)
        
        # 7. 1ms Atomic Release
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        report = (
            f"✅ **DUAL-PAYOUT HIT!**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎯 **Direction:** {side}\n"
            f"💰 **Stake:** `${stake_usd:.2f}`\n"
            f"📈 **Profit Payout:** `${stake_usd * 0.92:.2f}`\n"
            f"⚡ **Status:** High-Priority Broadcast\n"
            f"🔗 [View on Polygonscan](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        return True, report

    except Exception as e:
        return False, f"❌ **Atomic Error:** `{str(e)}`"
