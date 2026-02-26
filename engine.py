async def run_atomic_execution(context, chat_id, side):
    """
    COMBINED PAYOUT ENGINE:
    Calculates [Stake + Profit] and sends it as one single 'Value' hit.
    """
    if not vault: return False, "❌ Vault Error"

    # 1. Configuration
    stake_usd = float(context.user_data.get('stake', 10))
    # SET PAYOUT: 1.92 means you get 100% of stake + 92% profit back in one go
    payout_multiplier = 1.92  
    
    # 2. Financial Math (2026 Conversion: 0.1478 POL/USD)
    conversion = 0.1478 
    total_to_send_usd = stake_usd * payout_multiplier
    
    # This is the line that was missing: Combined Total in Wei
    total_val_wei = w3.to_wei(total_to_send_usd / conversion, 'ether')
    
    # 3. Sync & Gas
    nonce = w3.eth.get_transaction_count(vault.address, 'pending')
    priority_gas = int(w3.eth.gas_price * 1.5) # High priority for 2026 congestion

    # 4. The Atomic Transaction
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': total_val_wei, # THIS is the full $19.20 / $96.00 amount
        'gas': 21000,
        'gasPrice': priority_gas,
        'chainId': 137
    }

    try:
        signed_tx = w3.eth.account.sign_transaction(tx, vault.key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        report = (
            f"✅ **ATOMIC PAYOUT SENT!**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎯 **Direction:** {side}\n"
            f"💰 **Total Sent:** `${total_to_send_usd:.2f}`\n"
            f"   *(Stake: `${stake_usd:.2f}` + Profit: `${stake_usd * 0.92:.2f}`)*\n"
            f"🔗 [Transaction Receipt](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        return True, report

    except Exception as e:
        return False, f"❌ **Error:** `{str(e)}`"
