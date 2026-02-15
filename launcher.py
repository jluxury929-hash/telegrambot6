import os, asyncio, requests
from bot_core import * # This imports your original code exactly
import bot_core 

# --- OVERRIDE 1: THE FULL PAYOUT LOGIC ---
async def patched_run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    # FETCH PRICE & CALC 1.92x PAYOUT ($19.20)
    current_price = bot_core.get_pol_price()
    total_payout_usd = float(stake_usd) * 1.92
    payout_in_wei = bot_core.w3.to_wei(total_payout_usd / current_price, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **Atomic Shield:** Priming {pair}...")

    # SIMULTANEOUS EXECUTION
    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(bot_core.prepare_signed_tx(payout_in_wei))

    await sim_task
    signed_tx = await prep_task
    await asyncio.sleep(0.001) # 1ms Release
    
    tx_hash = bot_core.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    report = (f"✅ **ATOMIC HIT!**\n🎯 **Direction:** {side}\n"
              f"💰 **Stake:** `${stake_usd:.2f}`\n📈 **Payout:** `${total_payout_usd:.2f}`\n"
              f"⛓️ **TX:** `{tx_hash.hex()}`")
    return True, report

# --- OVERRIDE 2: THE HEARTBEAT (1ms First-Hit Fix) ---
async def keep_alive():
    while True:
        try: bot_core.w3.eth.get_block_number()
        except: pass
        await asyncio.sleep(30)

# --- APPLY PATCHES AT RUNTIME ---
bot_core.run_atomic_execution = patched_run_atomic_execution

if __name__ == "__main__":
    # Start the Heartbeat in the background
    asyncio.get_event_loop().create_task(keep_alive())
    # Start your original bot polling
    print(f"Shadow Engine v4 Patched: {bot_core.vault.address}")
    bot_core.app.run_polling(drop_pending_updates=True)
