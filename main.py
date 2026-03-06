import os
import asyncio
import json
import time
import requests
import numpy as np
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# --- 1. CORE CONFIG ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

# ADDRESSES
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")

# Using <pre> and avoiding bare '<' symbols to prevent Telegram HTML parsing errors
LOGO = """<pre>
█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-3DAY-STABLE</pre>"""

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
        except:
            continue
    return None

w3 = get_hydra_w3()
if not w3:
    print("FATAL: RPC Failure."); import sys; sys.exit(1)

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

# --- 3. VAULT & CLOB AUTH ---
def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try:
        return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except:
        return None

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

# --- 4. ARBITRAGE MATH & 3-DAY FILTER ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0: return None
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    if stake_yes < 1.0 or stake_no < 1.0: return None
    expected_payout = (stake_yes / p_yes)
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
    except:
        return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    
    # Define the 3-day (72 hour) window
    cutoff_sec = 3 * 24 * 60 * 60
    now_ts = time.time()
    limit_ts = now_ts + cutoff_sec
    
    tags = [1, 10, 100, 4, 6, 237]
    for tag in tags:
        url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=40&tag_id={tag}"
        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            for e in resp.json():
                markets = e.get('markets', [])
                if not markets: continue
                
                m = markets[0]
                end_date_str = m.get('endDate')
                if not end_date_str: continue
                
                # Parse the ISO date and check against our 3-day cutoff
                end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                end_ts = end_dt.timestamp()

                if end_ts > limit_ts:
                    continue # Skip markets that take longer than 3 days

                m_data = await fetch_full_market(m['conditionId'])
                if m_data and 'YES' in m_data and 'NO' in m_data:
                    arb = calculate_arbitrage_guaranteed(m_data['YES']['price'], m_data['NO']['price'], 100.0)
                    if arb:
                        days_left = round((end_ts - now_ts) / (24 * 3600), 1)
                        ARBI_CACHE.append({
                            "title": f"[{max(0, days_left)}d] " + e.get('title')[:25],
                            "yes_id": m_data['YES']['id'],
                            "no_id": m_data['NO']['id'],
                            "p_y": m_data['YES']['price'],
                            "p_n": m_data['NO']['price'],
                            "roi": arb['roi'],
                            "eff": arb['eff'],
                            "ends": end_date_str
                        })
        except:
            continue
            
    ARBI_CACHE.sort(key=lambda x: x['eff'])
    return len(ARBI_CACHE) > 0

# --- 5. BOT HANDLERS ---
async def start(update, context):
    btns = [['🚀 START ARBI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    # We use &lt; instead of < to prevent HTML parsing errors
    welcome_text = (
        f"{LOGO}\n"
        f"<b>HYDRA ARBITRAGE SYSTEM ONLINE</b>\n"
        f"<i>Filtering for &lt; 3-day settlements.</i>"
    )
    await update.message.reply_text(
        welcome_text, 
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), 
        parse_mode='HTML'
    )

async def main_handler(update, context):
    cmd = update.message.text
    if 'START ARBI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING SHORT-TERM ARBS...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"{'🟢' if a['roi'] > 0 else '🟡'} {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:10])]
            await m.edit_text("<b>SHORT-TERM OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("🛰 <b>NO &lt; 3-DAY ARBS DETECTED.</b>")
    
    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT AUDIT</b>\n━━━━━━━━━━━━━━\n<b>Signer:</b> <code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')
    
    elif 'CALIBRATE' in cmd:
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [5, 10, 50, 100, 250, 500]]]
        await update.message.reply_text("🎯 <b>CALIBRATE STRIKE CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif 'FIX APPROVAL' in cmd:
        try:
            msg = await update.message.reply_text("⌛ <b>SENDING APPROVAL...</b>", parse_mode='HTML')
            tx = usdc_e_contract.functions.approve(CTF_EXCHANGE, 2**256 - 1).build_transaction({
                'from': vault.address,
                'nonce': w3.eth.get_transaction_count(vault.address),
                'gasPrice': int(w3.eth.gas_price * 1.2),
                'chainId': 137
            })
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            raw_tx = getattr(signed, 'raw_transaction', getattr(signed, 'rawTransaction', None))
            w3.eth.send_raw_transaction(raw_tx)
            await msg.edit_text("✅ <b>USDC APPROVED</b> for the CTF Exchange.")
        except Exception as e:
            await update.message.reply_text(f"❌ <b>APPROVAL FAILED</b>: {e}", parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>CAPITAL LOADED: ${context.user_data['stake']}</b>")
    
    elif "ARB_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        msg = (f"<b>PLAN:</b> {target['title']}\n"
               f"📅 <b>Ends:</b> {target['ends']}\n\n"
               f"✅ YES: ${calc['stake_yes']}\n"
               f"❌ NO: ${calc['stake_no']}\n"
               f"💰 ROI: {calc['roi']}%")
        kb = [[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{q.data.split('_')[1]}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        results = []
        for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                order = MarketOrderArgs(token_id=str(t_id), amount=float(amt), side="BUY")
                signed_order = clob_client.create_order(order)
                resp = clob_client.post_order(signed_order, OrderType.FOK)
                if resp.get("success") or "order_id" in resp:
                    results.append(True)
                else:
                    results.append(False)
            except:
                results.append(False)
        
        status = "✅ <b>ARBITRAGE SECURED</b>" if all(results) else "⚠️ <b>EXECUTION ERROR</b>\nVerify balance or order limits."
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("Hydra Bot (3-Day Limit) Active...")
    app.run_polling()






























































