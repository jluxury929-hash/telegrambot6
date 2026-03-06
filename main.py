import os
import asyncio
import json
import time
import requests
import numpy as np
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType

# --- 1. CORE CONFIG ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

# Polygon Addresses
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")

LOGO = """<code>██╗  ██╗██╗   ██╗██████╗ ██████╗  █████╗ 
██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██╔══██╗
███████║ ╚████╔╝ ██║  ██║██████╔╝███████║
██╔══██║  ╚██╔╝  ██║  ██║██╔══██╗██╔══██║
██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║ v4.1-OMNI</code>"""

# --- 2. BLOCKCHAIN & AUTH ---
def get_hydra_w3():
    endpoints = [os.getenv("RPC_URL"), "https://polygon-rpc.com", "https://1rpc.io/matic"]
    for url in endpoints:
        if not url: continue
        try:
            _w3 = Web3(Web3.HTTPProvider(url.strip(), request_kwargs={'timeout': 10}))
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                return _w3
        except: continue
    return None

w3 = get_hydra_w3()
if not w3:
    print("FATAL: RPC Failure."); import sys; sys.exit(1)

vault = Account.from_key(os.getenv("WALLET_SEED")) if os.getenv("WALLET_SEED") else None

def init_clob():
    try:
        client = ClobClient("https://clob.polymarket.com", key=vault.key.hex(), chain_id=137)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except: return None

clob_client = init_clob()
usdc_contract = w3.eth.contract(address=USDC_E, abi=[{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}])

# --- 3. THE OMNI-ARBITRAGE ENGINE ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    # Only proceed if there is a mathematical discrepancy (Combined < 1.00)
    if combined_prob >= 1.0 or combined_prob <= 0: return None
    
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    
    # Calculate profit from the guaranteed payout
    expected_payout = (stake_yes / p_yes) if p_yes > 0 else 0
    profit = expected_payout - total_capital
    roi = (profit / total_capital) * 100
    
    return {
        "stake_yes": round(stake_yes, 2),
        "stake_no": round(stake_no, 2),
        "roi": round(roi, 2)
    }

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24) # SAME-DAY ENFORCEMENT

    # Crawl global Gamma API for all active events
    base_url = "https://gamma-api.polymarket.com/events"
    try:
        for page in range(5): # Scans top 500 events across all categories
            params = {"active": "true", "closed": "false", "limit": 100, "offset": page * 100}
            resp = await asyncio.to_thread(requests.get, base_url, params=params, timeout=10)
            events = resp.json()
            if not events: break

            for e in events:
                # Same-Day Filter Logic
                end_str = e.get('endDate')
                if not end_str: continue
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                if end_dt > cutoff: continue 

                markets = e.get('markets', [])
                if not markets: continue
                m = markets[0] # Focus on the primary market of the event
                
                # Extract live pricing
                prices = m.get('outcomePrices')
                if not prices or len(prices) < 2: continue
                p_y, p_n = float(prices[0]), float(prices[1])
                
                arb = calculate_arbitrage_guaranteed(p_y, p_n, 100.0)
                if arb and arb['roi'] > 0:
                    hours_left = round((end_dt - now).total_seconds() / 3600, 1)
                    ARBI_CACHE.append({
                        "title": f"⏱{hours_left}h | {e.get('title')[:25]}",
                        "category": e.get('category', 'General'),
                        "yes_id": m['clobTokenIds'][0],
                        "no_id": m['clobTokenIds'][1],
                        "p_y": p_y, "p_n": p_n, "roi": arb['roi']
                    })
    except Exception as ex:
        print(f"Global Scour Failure: {ex}")

    # Sort by absolute ROI efficiency
    ARBI_CACHE.sort(key=lambda x: x['roi'], reverse=True)
    return len(ARBI_CACHE) > 0

# --- 4. TELEGRAM BOT HANDLERS ---
async def start(update, context):
    btns = [['🚀 GLOBAL OMNI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 STATUS']]
    await update.message.reply_text(
        f"{LOGO}\n<b>HYDRA OMNI-SCANNER ACTIVE</b>\nMarkets: All Categories\nExpiry: Same-Day Only",
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML'
    )

async def main_handler(update, context):
    cmd = update.message.text
    if 'GLOBAL OMNI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING EVERY CATEGORY...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"💰 {a['roi']}% | {a['title']}", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:12])]
            await m.edit_text("<b>SAME-DAY GLOBAL ARBITRAGE:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("🛰 <b>NO SAME-DAY ARBITRAGE DETECTED.</b>")

    elif 'CALIBRATE' in cmd:
        # Integrated $5 calibration option
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [5, 10, 50, 100, 250, 500]]]
        await update.message.reply_text("🎯 <b>CALIBRATE STRIKE CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif 'VAULT' in cmd:
        bal = usdc_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT INFO</b>\n<b>Address:</b> <code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>CAPITAL SET TO: ${context.user_data['stake']}</b>")
    
    elif "ARB_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        msg = f"<b>PLAN:</b> {target['title']}\n<b>CAT:</b> {target['category']}\n\n✅ YES: ${calc['stake_yes']}\n❌ NO: ${calc['stake_no']}\n💰 ROI: {calc['roi']}%"
        kb = [[InlineKeyboardButton("🔥 EXECUTE TRADE", callback_data=f"EXE_{q.data.split('_')[1]}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        success = True
        for (tid, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                order = MarketOrderArgs(token_id=str(tid), amount=float(amt), side="BUY")
                signed = clob_client.create_order(order)
                resp = clob_client.post_order(signed, OrderType.FOK)
                if not resp.get("success") and "order_id" not in resp: success = False
            except: success = False
        
        status = "✅ <b>ARBITRAGE SECURED</b>" if success else "⚠️ <b>EXECUTION ERROR</b>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    app.run_polling()



















