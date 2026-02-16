import os
import asyncio
from decimal import Decimal
from web3 import Web3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. SETUP ---
w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
PK = os.getenv("WALLET_PRIVATE_KEY")
account = w3.eth.account.from_key(PK)

# Buffer Finance Arbitrum Mainnet Addresses
ROUTER_ADDRESS = "0x311334883921Fb1b813826E585dF1C2be4358615" # Official Router
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"   # Native USDC

# Minimal ABI for 'initiateTrade'
ROUTER_ABI = '[{"inputs":[{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"uint256","name":"assetPair","type":"uint256"},{"internalType":"uint256","name":"direction","type":"uint256"},{"internalType":"uint256","name":"timeframe","type":"uint256"}],"name":"initiateTrade","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

# --- 2. EXECUTION ENGINE ---
async def place_real_bet(context, chat_id, side):
    stake_cad = context.user_data.get('stake', 10)
    
    # Logic: 1 = UP (Higher), 0 = DOWN (Lower)
    direction = 1 if side == "CALL" else 0
    
    # Convert CAD to USDC (Example rate: 1 CAD = 0.72 USDC)
    # USDC has 6 decimals
    usdc_amount = int(Decimal(str(stake_cad)) * Decimal('0.72') * 10**6)

    status_msg = await context.bot.send_message(chat_id, f"⚔️ **Broadcasting REAL trade to Buffer Finance...**")

    try:
        contract = w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
        nonce = w3.eth.get_transaction_count(account.address)

        # Build 'initiateTrade' transaction
        # assetPair 0 = BTC/USD, timeframe 300 = 5 Minutes
        tx = contract.functions.initiateTrade(
            usdc_amount, 0, direction, 300
        ).build_transaction({
            'from': account.address,
            'nonce': nonce,
            'gas': 500000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 42161
        })

        # Sign and Send
        signed_tx = w3.eth.account.sign_transaction(tx, PK)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        report = (
            f"✅ **REAL BET PLACED!**\n"
            f"🎯 **Market:** BTC/USD {side}\n"
            f"💰 **Stake:** `${stake_cad:.2f} CAD` ({usdc_amount/10**6:.2f} USDC)\n"
            f"📊 **Settlement:** Automatic in 5 minutes\n"
            f"⛓️ **TX Hash:** `{tx_hash.hex()}`"
        )
        await context.bot.send_message(chat_id, report, parse_mode='Markdown')

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Trade Failed:** `{str(e)}` \n(Check your USDC balance and approvals)")

# --- 3. THE ONE-TIME APPROVAL SCRIPT ---
async def approve_usdc(update, context):
    """Allows the Buffer contract to spend your USDC."""
    usdc_abi = '[{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]'
    usdc_contract = w3.eth.contract(address=USDC_ADDRESS, abi=usdc_abi)
    
    tx = usdc_contract.functions.approve(
        ROUTER_ADDRESS, 2**256 - 1 # Infinite approval for convenience
    ).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 100000,
        'gasPrice': w3.eth.gas_price,
        'chainId': 42161
    })
    
    signed = w3.eth.account.sign_transaction(tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    await update.message.reply_text(f"🚀 **Approval Sent!** \nHash: `{tx_hash.hex()}`")
