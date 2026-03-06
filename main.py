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

LOGO = """<code>█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v2.6-BINARY-PRO</code>"""

# --- 2. HYDRA ENGINE & ABIs ---
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

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

# --- 3. VAULT & CLOB AUTH ---
def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try:
        return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except: return None

vault = get_vault()

def init_clob():
    try:
        sig_type = int(os.getenv("SIGNATURE_TYPE", 1))
        funder = os.getenv("FUNDER_ADDRESS", vault.address)
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=vault.key.hex(),
            chain_id=137,
            signature_type=sig_type,
            funder=funder
        )
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        print(f"Auth derivation failed: {e}")
        return None

clob_client = init_clob()

# --- 4. ARBITRAGE MATH & BINARY SCOURING ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0 or combined_prob >= 1.0: return None

    # Calculate optimal distribution
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    
    if stake_yes < 1.0 or stake_no < 1.0: return None
    
    expected_payout = (stake_yes / p_yes) if p_yes > 0 else 0
    profit = expected_payout - total_capital
    roi = (profit / total_capital) * 100
    
    return {
        "stake_yes": round(stake_yes, 2),
        "stake_no": round(stake_no, 2),
        "profit": round(profit, 2),
        "roi": round(roi, 2),
        "eff": round(combined_prob, 4)
    }

async def fetch_full_market(cond_id):
    try:
        url = f"https://clob.polymarket.com/markets/{cond_id}"
        r = await asyncio.to_thread(requests.get, url, timeout=5)
        d = r.json()
        return {t['outcome'].upper(): {"id": t['token_id'], "price": float(t['price'])} for t in d.get('tokens', [])}
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24) 
    
    # 1=Crypto, 10=Politics, 100=Sports, 4=Pop, 6=Business, 100004=Science, 100006=Health
    tags = [1, 10, 100, 4, 6, 237, 100004, 100006] 
    
    for tag in tags:
        # Limit increased to 100 to find more deep binary options
        url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&tag_id={tag}"
        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            for e in resp.json():
                end_date_str = e.get('endDate')
                if not end_date_str: continue
                end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                
                # Filter for markets ending within 24h
                if end_dt > cutoff: continue 
                
                m_list = e.get('markets', [])
                if not m_list: continue
                m = m_list[0]

                # STRICT BINARY CHECK: Only Yes/No (2 outcomes)
                outcomes = json.loads(m.get('outcomes', '[]'))
                if len(outcomes) != 2: continue

                m_data = await fetch_full_market(m['conditionId'])
                if m_data and 'YES' in m_data and 'NO' in m_data:
                    p_y, p_n = m_data['YES']['price'], m_data['NO']['price']
                    arb = calculate_arbitrage_guaranteed(p_y, p_n, 100.0)
                    
                    if arb and arb['roi'] > 0.05:
                        hours_left = (end_dt - now).total_seconds() / 3600
                        ARBI_CACHE.append({
                            "title": f"⏱{round(hours_left,1)}h | {e.get('title')[:25]}",
                            "yes_id": m_data['YES']['id'],
                            "no_id": m_data['NO']['id'],
                            "p_y": p_y,
                            "p_n": p_n,
                            "roi": arb['roi'],
                            "eff": arb['eff']
                        })
        except: continue

    ARBI_CACHE.sort(key=lambda x: x['roi'], reverse=True)
    return len(ARBI_CACHE) > 0

# --- 5. BOT LOGIC ---
async def start(update, context):
    btns = [['🚀 BINARY SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 STATUS']]
    await update.message.reply_text(
        f"{LOGO}\n<b>HYDRA BINARY ENGINE</b>\nScanning all sectors for 24h Yes/No Arbitrage.",
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML'
    )

async def main_handler(update, context):
    cmd = update.message.text
    if 'BINARY SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>DEEP SCANNING BINARY MARKETS...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"{'🟢' if a['roi'] > 0.5 else '🟡'} {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:10])]
            await m.edit_text("<b>TOP BINARY SPREADS:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("🛰 <b>NO PROFITABLE BINARY ARBS FOUND.</b>")

    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT</b>\n<b>Address:</b> <code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')

    elif 'CALIBRATE' in cmd:
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [25, 50, 100, 250]]]
        await update.message.reply_text("🎯 <b>CHOOSE STRIKE CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>TARGET STRIKE SET TO: ${context.user_data['stake']}</b>")
    
    elif "ARB_" in q.data:
        idx = int(q.data.split("_")[1])
        target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        msg = f"<b>PLAN:</b> {target['title']}\n\n✅ YES: ${calc['stake_yes']}\n❌ NO: ${calc['stake_no']}\n💰 ROI: {calc['roi']}%"
        kb = [[InlineKeyboardButton("🔥 EXECUTE BOTH SIDES", callback_data=f"EXE_{idx}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        
        results = []
        # Execution loop using Fill-or-Kill (FOK) to ensure atomic-like fills
        for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                order = MarketOrderArgs(token_id=str(t_id), amount=float(amt), side="BUY")
                signed_order = clob_client.create_order(order)
                resp = clob_client.post_order(signed_order, OrderType.FOK)
                results.append(resp.get("success") or "order_id" in resp)
            except Exception as e:
                print(f"Order error: {e}")
                results.append(False)
        
        status = "✅ <b>BINARY ARBITRAGE SECURED</b>" if all(results) else "⚠️ <b>PARTIAL EXECUTION / SLIPPAGE</b>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    app.run_polling()








































