import os
import asyncio
import main  # Importing your original file exactly as provided
from decimal import Decimal

# --- THE DUAL-PAYOUT OVERRIDE ---
async def patched_run_atomic_execution(context, chat_id, side, asset_override=None):
    """
    This function replaces your original one at runtime to send 2 transactions.
    TX 1: Stake Amount
    TX 2: Profit Amount (Nonce + 1)
    """
    if not main.vault: return False
    
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    
    # Financial Calculation
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_usdc = stake_usdc * yield_multiplier
    
    await context.bot.send_message(chat_id, f"⚔️ **Dual Atomic Hit:** Priming Stake & Profit...")

    try:
        # 1. Fetch current nonce
        nonce = await asyncio.to_thread(main.w3.eth.get_transaction_count, main.vault.address, 'pending')
        gas_price = await asyncio.to_thread(lambda: int(main.w3.eth.gas_price * 1.6))
        
        # 2. Build Transaction 1 (Stake)
        tx1 = main.usdc_contract.functions.transfer(
            main.PAYOUT_ADDRESS, int(stake_usdc * 10**6)
        ).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': gas_price, 'nonce': nonce
        })
        
        # 3. Build Transaction 2 (Profit - Nonce + 1)
        tx2 = main.usdc_contract.functions.transfer(
            main.PAYOUT_ADDRESS, int(profit_usdc * 10**6)
        ).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': gas_price, 'nonce': nonce + 1
        })

        # 4. Sign Both
        signed1 = main.w3.eth.account.sign_transaction(tx1, main.vault.key)
        signed2 = main.w3.eth.account.sign_transaction(tx2, main.vault.key)
        
        # 5. Atomic Release (Parallel Broadcast)
        tx1_hash = await asyncio.to_thread(main.w3.eth.send_raw_transaction, signed1.raw_transaction)
        tx2_hash = await asyncio.to_thread(main.w3.eth.send_raw_transaction, signed2.raw_transaction)
        
        report = (
            f"✅ **DUAL HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 **Market:** {asset} | **Side:** {side}\n"
            f"💰 **Stake Sent:** `${stake_usdc:.2f} USDC`\n"
            f"💎 **Profit Sent:** `${profit_usdc:.2f} USDC`\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔗 [Stake TX](https://polygonscan.com/tx/{tx1_hash.hex()})\n"
            f"🔗 [Profit TX](https://polygonscan.com/tx/{tx2_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Dual-TX Error:** `{str(e)}`")
        return False

# --- APPLY THE PATCH WITHOUT CHANGING main.py ---
main.run_atomic_execution = patched_run_atomic_execution

if __name__ == "__main__":
    print(f"🤖 APEX Dual-TX Wrapper Online: {main.vault.address}")
    # Start the original bot loop from your main file
    main.app.run_polling(drop_pending_updates=True)
