import os
import asyncio
import random
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- IMPORT YOUR ORIGINAL CODE WITHOUT CHANGING IT ---
import main  # This loads your variables: vault, usdc_contract, etc.

# Configuration for the autonomous loop
AUTO_RUNNING = {}

async def autonomous_loop(chat_id, context):
    """The background engine using your main.py execution logic."""
    print(f"🤖 APEX Auto Mode: ACTIVATED for {chat_id}")
    
    markets = ["BTC/CAD", "ETH/CAD", "SOL/CAD", "BVIV", "EVIV"]
    
    while AUTO_RUNNING.get(chat_id, False):
        # 1. Setup the analysis
        target = random.choice(markets)
        side = random.choice(["HIGHER 📈", "LOWER 📉"])
        
        # 2. Mimic the user data your main.py expects
        context.user_data['pair'] = target
        # Ensure a default stake exists if not set
        if 'stake' not in context.user_data:
            context.user_data['stake'] = 50 

        await context.bot.send_message(chat_id, f"📡 **Auto Pilot Scanning:** `{target}`...")
        await asyncio.sleep(random.randint(5, 10)) 
        
        if not AUTO_RUNNING.get(chat_id, False): break

        # 3. Call your original function from main.py exactly as it is
        try:
            await main.run_atomic_execution(context, chat_id, side)
        except Exception as e:
            print(f"⚠️ Auto-Execution Skip: {e}")

        # 4. Wait for next signal
        wait_time = random.randint(60, 120)
        await asyncio.sleep(wait_time)

# --- OVERRIDE UI HANDLERS ---

async def auto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the bot with the modified 5-choice keyboard."""
    # Choice 1 & 2: Start/Settings | Choice 3 & 4: Wallet/Withdraw | Choice 5: Auto Mode
    keyboard = [
        ['🚀 Start Trading', '⚙️ Settings'],
        ['💰 Wallet', '📤 Withdraw'],
        ['🤖 START AUTO']  # The 5th choice at the bottom
    ]
    
    pol_bal = main.w3.from_wei(main.w3.eth.get_balance(main.vault.address), 'ether')
    welcome = (
        f"🕴️ **APEX Autonomous Terminal**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol_bal:.4f}`\n"
        f"🤖 **Auto Mode:** {'ACTIVE' if AUTO_RUNNING.get(update.message.chat_id) else 'OFF'}\n\n"
        f"📥 **Vault:** `{main.vault.address}`"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def auto_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id

    if text == '🤖 START AUTO':
        if not AUTO_RUNNING.get(chat_id, False):
            AUTO_RUNNING[chat_id] = True
            await update.message.reply_text("✅ **Auto Mode: ENABLED**\nRobot is now scanning 2026 volatility.")
            asyncio.create_task(autonomous_loop(chat_id, context))
        else:
            AUTO_RUNNING[chat_id] = False
            await update.message.reply_text("🛑 **Auto Mode: DISABLED**")
    
    # Fallback to your main.py handler for all other buttons
    else:
        await main.main_chat_handler(update, context)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        # Create the app using your token but our custom UI
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Priority handlers
        app.add_handler(CommandHandler("start", auto_start))
        app.add_handler(MessageHandler(filters.Regex('^🤖 START AUTO$'), auto_chat_handler))
        
        # Route everything else to your original main.py logic
        app.add_handler(CallbackQueryHandler(main.handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), auto_chat_handler))
        
        print("🤖 APEX Auto-Pilot Wrapper Online...")
        app.run_polling(drop_pending_updates=True)
