# auto_pilot.py
import asyncio
import random
import main  # Importing your existing setup and run_atomic_execution logic

# Control flag to allow main.py to stop/start the loop
active_tasks = {}

async def start_engine(chat_id, context):
    """
    The Autonomous Loop:
    Picks markets, simulates analysis, and triggers the 1ms Atomic Engine.
    """
    print(f"🤖 AUTO_PILOT: Initializing for Chat ID {chat_id}")
    
    # 2026 High-Volatility Market Matrix
    markets = ["BTC/USD", "ETH/USD", "SOL/USD", "MATIC/USD", "BVIV", "EVIV"]
    
    try:
        while main.auto_mode_active.get(chat_id, False):
            # 1. Select Asset & Direction
            target_pair = random.choice(markets)
            direction = random.choice(["CALL", "PUT"])
            
            # 2. Inform the user of the robot's intent
            await context.bot.send_message(
                chat_id, 
                f"🤖 **Auto-Mode Scanning:** `{target_pair}`..."
            )
            
            # 3. Simulated Analysis Delay (5-15 seconds)
            # This mimics the time taken for the 'AI' to confirm a hit
            await asyncio.sleep(random.randint(5, 15))
            
            # Check again if mode was disabled during the sleep
            if not main.auto_mode_active.get(chat_id, False):
                break
            
            # 4. Trigger the Simultaneous Engine from main.py
            # We pass the asset_override to bypass the manual pair selection
            success, report = await main.run_atomic_execution(
                context, 
                chat_id, 
                direction, 
                asset_override=target_pair
            )
            
            # 5. Report results back to Telegram
            await context.bot.send_message(chat_id, report, parse_mode='Markdown')
            
            # 6. Post-Trade Cooldown (Preventing RPC spam and rate limits)
            wait_time = random.randint(30, 60)
            print(f"⏳ Trade complete. Resting for {wait_time}s")
            await asyncio.sleep(wait_time)
            
    except Exception as e:
        print(f"⚠️ AUTO_PILOT CRITICAL ERROR: {e}")
        main.auto_mode_active[chat_id] = False
        await context.bot.send_message(chat_id, "🛑 **Auto-Mode Error:** System halted for safety.")

if __name__ == "__main__":
    print("This file is a sidecar module and should be triggered via main.py")
