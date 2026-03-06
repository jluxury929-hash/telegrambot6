import os
import asyncio
import json
import time
import requests
from datetime import datetime
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Polymarket SDK
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# --- 1. CORE CONFIG ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")

LOGO = """<pre>
█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗    ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝     ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v235-FINAL</pre>"""

# --- 2. WEB3 ENGINE ---
def get_hydra_w3():
    endpoints = [os.getenv("RPC_URL"), "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon"]
    for url in endpoints:
        if not url: continue
        try:
            _w3 = Web3(Web3.HTTPProvider(url.strip(), request_kwargs={'timeout': 20}))
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                return _w3
        except: continue
    return None

w3 = get_hydra_w3()
if not w3:
    print("FATAL: RPC Failure."); import sys; sys.exit(1)

usdc_e_contract = w3.eth.contract(address=USDC_E, abi=[
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
])

# --- 3. VAULT & CLOB AUTH ---
def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try: return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except: return None

vault = get_vault()

def init_clob():
    try:
        sig_type = int(os.getenv("SIGNATURE_TYPE", 1))
        funder = os.getenv("FUNDER_ADDRESS", vault.address)
        client = ClobClient(host="https://clob.polymarket.com", key=vault.key.hex(), chain_id=137, signature_type=sig_type, funder=funder)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        print(f"Auth derivation failed: {e}")
        return None

clob_client = init_clob()

# --- 4. ARBITRAGE SCANNER ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0 or combined_prob >= 2: return None
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    if stake_yes < 1.0 or stake_no < 1.0: return None
    expected_payout = (stake_yes / p_yes)
    profit = expected_payout - total_capital
    return {"stake_yes": round(stake_yes, 2), "stake_no": round(stake_no, 2), "roi": round((profit / total_capital) * 100, 2), "eff": round(combined_prob, 4)}

async def fetch_full_market(cond_id):
    try:
        url = f"https://clob.polymarket.com/markets/{cond_id}"
        r = await asyncio.to_thread(requests.get, url, timeout=5)
        d = r.json()
        if not d.get('tokens'): return None
        return {t['outcome'].upper(): {"id": str(t['token_id']).strip(), "price": float(t['price'])} for t in d['tokens']}
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    tags = [1, 10, 100, 4, 6]
    for tag in tags:
        url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=20&tag_id={tag}"
        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            for e in resp.json():
                markets = e.get('markets', [])
                if not markets: continue
                m = markets[0]
                m_data = await fetch_full_market(m['conditionId'])
                if m_data and 'YES' in m_data and 'NO' in m_data:
                    arb = calculate_arbitrage_guaranteed(m_data['YES']['price'], m_data['NO']['price'], 100.0)
                    if arb and arb['roi'] > 0.1:
                        ARBI_CACHE.append({"title": e.get('title')[:30], "yes_id": m_data['YES']['id'], "no_id": m_data['NO']['id'], "p_y": m_data['YES']['price'], "p_n": m_data['NO']['price'], "roi": arb['roi'], "eff": arb['eff']})
        except: continue
    return len(ARBI_CACHE) > 0

# --- 5. BOT HANDLERS ---
async def start(update, context):
    btns = [['🚀 START ARBI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>SYSTEM READY</b>", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    cmd = update.message.text
    if 'START ARBI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"🟢 {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:8])]
            await m.edit_text("<b>OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await m.edit_text("🛰 <b>NO ARBS.</b>")
    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT</b>\n<code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>STAKE: ${context.user_data['stake']}</b>")
    elif "ARB_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        msg = f"<b>PLAN:</b> {target['title']}\n\nYES: ${calc['stake_yes']}\nNO: ${calc['stake_no']}\n💰 ROI: {calc['roi']}%"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{q.data.split('_')[1]}")]]), parse_mode='HTML')
    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        err_msg = ""
        
        for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                # 404 FIX: Manually verify market metadata before order creation
                token_id_str = str(t_id).strip()
                
                # Fetch order book to verify the CLOB recognizes this token
                book = clob_client.get_order_book(token_id_str)
                if not book:
                    err_msg = f"Token {token_id_str[:10]} not on CLOB book"
                    break

                order_args = OrderArgs(
                    token_id=token_id_str,
                    price=0.99,
                    size=float(amt),
                    side=BUY,
                    expiration=0
                )
                
                # Use FOK (Fill or Kill) to prevent partial fills in arbitrage
                created_order = clob_client.create_order(order_args, OrderType.FOK)
                resp = clob_client.post_order(created_order)
                
                if not (resp.get("success") or resp.get("orderID")):
                    err_msg = resp.get("errorMsg") or str(resp)
                    break 
            except Exception as e: 
                err_msg = str(e)
                break
        
        status = "✅ <b>TRADE COMPLETE</b>" if not err_msg else f"⚠️ <b>EXE ERROR</b>\n<code>{err_msg}</code>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("Hydra v235 Active...")
    app.run_polling()























































