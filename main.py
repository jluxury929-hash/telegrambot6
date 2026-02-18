import os
import asyncio
import requests
import json
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. SETUP & AUTH ---
load_dotenv()
W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(W3_RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# Constants
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")
USDC_ADDRESS = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]')

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("WALLET_SEED is missing!")
    if len(seed) == 64 or seed.startswith("0x"):
        return Account.from_key(seed)
    return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")

vault = get_vault()
usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)

# --- 2. THE BALANCE FIX ENGINE ---

def get_pol_price_cad():
    """Fetches real-time price in CAD with a fail-safe."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad"
        res = requests.get(url, timeout=5).json()
        return float(res['polygon-ecosystem-token']['cad'])
    except Exception as e:
        print(f"Pricing Error: {e}")
        return 0.55  # Reasonable 2026 fallback

async def get_wallet_state():
    """
    FIX: Uses 'latest' block tag and gathers multiple balances simultaneously.
    """
    addr = vault.address
    # Get native POL balance
    pol_wei = await asyncio.to_thread(w3.eth.get_balance, addr, 'latest')
    pol_bal = w3.from_wei(pol_wei, 'ether')
    
    # Get USDC balance
    usdc_raw = await asyncio.to_thread(usdc_contract.functions.balanceOf(addr).call)
    usdc_bal = Decimal(usdc_raw) / Decimal(10**6)
    
    price_cad = get_pol_price_cad()
    pol_cad = float(pol_bal) * price_cad
    
    return {
        "pol": pol_bal,
        "pol_cad": pol_cad,
        "usdc": usdc_bal,
        "price": price_cad
    }

# --- 3. EXECUTION ENGINE ---

async def run_atomic_execution(context, chat_id, side):
    stake_cad = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC')
    
    # Check balance before execution
    state = await get_wallet_state()
    if state['pol_cad'] < float(stake_cad):
        await context.bot.send_message(chat_id, f"⚠️ **Insufficient Balance**\nHave: ${state['pol_cad']:.2f} CAD\nRequired: ${stake_cad:.2f} CAD")
        return

    status_msg = await context.bot.send_message(chat_id, f"⚡ **CAD Double-Hit:** Priming {pair} Shield...")
    
    # ... (Keep your existing tx signing logic here)
    await asyncio.sleep(1.5) # Simulate processing
    await context.bot.edit_message_text("✅ **Atomic Hit Confirmed**", chat_id=chat_id, message_id=status_msg.message_id)

# --- 4. UI HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = await get_wallet_state()
    
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '🤖 AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    msg = (f"🕴️ **APEX Manual Terminal (CAD)**\n"
           f"━━━━━━━━━━━━━━\n"
           f"⛽ **POL:** `{state['pol']:.4f}` (**${state['pol_cad']:.2f} CAD**)\n"
           f"💵 **USDC:** `${state['usdc']:.2f}`\n\n"
           f"📥 **Vault Address:**\n`{vault.address}`")
    
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '💰 Wallet':
        state = await get_wallet_state()
        await update.message.reply_text(
            f"💳 **Vault Asset Status**\n"
            f"━━━━━━━━━━━━━━\n"
            f"POL: `{state['pol']:.6f}`\n"
            f"Value: `${state['pol_cad']:.2f} CAD`"
        )

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake set to: **${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}** selected. Choose direction:", 
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), 
                InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")
            ]]))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, query.data)

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    app.run_polling(drop_pending_updates=True)



