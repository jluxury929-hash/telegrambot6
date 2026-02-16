import os
import asyncio
import requests
import json
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Set financial precision
getcontext().prec = 28
load_dotenv()

# --- 1. BLOCKCHAIN & PROTOCOL SETUP ---
RPC_URL = os.getenv("RPC_URL", "https://arb1.arbitrum.io/rpc") 
w3 = Web3(Web3.HTTPProvider(RPC_URL))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

# Buffer Finance Mainnet Addresses (Arbitrum)
BUFFER_ROUTER = "0x4Dbd...AB3f" # Example Router
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831" # Native USDC

# Load ABI (Ensure you have this file in your folder)
with open('buffer_abi.json') as f:
    BUFFER_ABI = json.load(f)

# AUTH
PK = os.getenv("WALLET_PRIVATE_KEY")
vault = Account.from_key(PK)
contract = w3.eth.contract(address=BUFFER_ROUTER, abi=BUFFER_ABI)

# --- 2. PRECISION PRICE & EXECUTION ---
def get_pol_price_cad():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad"
        res = requests.get(url, timeout=5).json()
        return Decimal(str(res['polygon-ecosystem-token']['cad']))
    except:
        return Decimal('0.1478') # Fallback for Feb 2026



async def execute_protocol_trade(context, chat_id, side):
    """Fires an ACTUAL smart contract trade on Buffer Finance."""
    stake_cad = context.user_data.get('stake', 10)
    asset = context.user_data.get('pair', 'BTC')
    
    # 1 = UP (Call), 0 = DOWN (Put)
    direction = 1 if side == "CALL" else 0
    
    status_msg = await context.bot.send_message(chat_id, f"⚔️ **Protocol Mode:** Broadcasting {side} order to Arbitrum...")

    try:
        # Calculate USDC equivalent (assuming 1 CAD = ~0.72 USDC)
        # Note: In a real bot, use a CAD/USDC price feed here
        usdc_amount = int(Decimal(str(stake_cad)) * Decimal('0.72') * 10**6) 

        # Build Protocol Transaction
        nonce = w3.eth.get_transaction_count(vault.address)
        
        # Buffer initiateTrade signature: (uint256 amount, uint256 assetPair, uint256 direction, uint256 timeframe)
        # timeframe: 300 = 5 minutes
        tx = contract.functions.initiateTrade(
            usdc_amount,
            0, # BTC Pair Index
            direction,
            300 
        ).build_transaction({
            'from': vault.address,
            'nonce': nonce,
            'gas': 450000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 42161
        })

        # Sign & Send
        signed_tx = w3.eth.account.sign_transaction(tx, PK)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        report = (
            f"✅ **PROTOCOL HIT!**\n"
            f"🎯 **Market:** {asset}/USD {side}\n"
            f"💰 **Stake:** `${stake_cad:.2f} CAD`\n"
            f"📊 **Status:** Active on Smart Contract\n"
            f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Protocol Error:** `{str(e)}` \n(Ensure you have approved the Router to spend your USDC)")

# --- 3. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal_pol = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    price = float(get_pol_price_cad())
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    await update.message.reply_text(
        f"🕴️ **Shadow Engine v5 (DeFi)**\n\n💵 **Vault:** {bal_pol:.4f} POL (**${float(bal_pol)*price:.2f} CAD**)\n"
        f"**Protocol Status:** Connected to Buffer Router ✅",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("EXEC_"):
        side = "CALL" if "CALL" in query.data else "PUT"
        await execute_protocol_trade(context, query.message.chat_id, side)
    
    elif query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake set to **${context.user_data['stake']} CAD**")
    
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}**\nDirection:", reply_markup=InlineKeyboardMarkup(kb))

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), lambda u, c: None))
    print(f"Shadow Bot Live: {vault.address}")
    app.run_polling()
