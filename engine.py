import redeemer # Import the file we just made

async def run_atomic_execution(context, chat_id, side):
    """
    DUAL RECEIPT SYSTEM:
    Receipt 1: The Bet (Stake goes into the Pool)
    Receipt 2: The Payout (Profit comes out of the Pool)
    """
    # --- RECEIPT 1: THE STAKE ---
    # This sends the money INTO the liquidity pool
    stake_tx_hash = await send_stake_to_pool(context, side)
    
    await context.bot.send_message(
        chat_id, 
        f"📜 **RECEIPT 1 (STAKE):** Bet placed in LP.\n`{stake_tx_hash}`",
        parse_mode='Markdown'
    )

    # --- THE WAIT ---
    # We must wait for the pool to calculate the profit
    await asyncio.sleep(65) # Wait for 1-minute market resolution

    # --- RECEIPT 2: THE PAYOUT ---
    # This pulls the money OUT of the liquidity pool
    try:
        payout_tx_hash = await redeemer.claim_payout("0xCONTRACT_ADDRESS_HERE")
        
        report = (
            f"📜 **RECEIPT 2 (PAYOUT):** Profit + Stake Claimed!\n"
            f"💰 **Status:** Funds returned to Vault\n"
            f"🔗 [View Payout](https://polygonscan.com/tx/{payout_tx_hash})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ **Payout Delayed:** Oracle still resolving...")
        return False
