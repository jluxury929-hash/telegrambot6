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

# Polymarket SDK Imports
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
from py_clob_client.order_builder.builder import OrderBuilder 

# --- 0. UTILITY ---
class Map(dict):
    def __getattr__(self, name): return self.get(name)

# --- 1. CONFIGURATION ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")
NEG_RISK_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

ERC20_ABI = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}, {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}]

LOGO = """<pre>
█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗     ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝      ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-STABLE</pre>"""

# --- 2. BLOCKCHAIN ---
def get_hydra_w3():
    raw_url = os.getenv("RPC_URL", "").strip()
    endpoints = [raw_url, "https://polygon-rpc.com", "https://1rpc.io/matic"]
    for url in endpoints:
        if not url: continue
        try:
            _w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 15}))
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                return _w3
        except: continue
    return None

w3 = get_hydra_w3()
if not w3: print("FATAL: RPC Failure."); import sys; sys.exit(1)
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

# --- 3. VAULT & AUTH ---
def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try: return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except: return None

vault = get_vault()

def init_clob():
    try:
        if not vault: return None
        client = ClobClient(host="https://clob.polymarket.com", key=vault.key.hex(), chain_id=137, signature_type=int(os.getenv("SIGNATURE_TYPE", 1)), funder=os.getenv("FUNDER_ADDRESS", vault.address))
        
        # KEY FIX: Strictly check credentials
        creds = client.create_or_derive_api_creds()
        if creds:
            client.set_api_creds(creds)
            return client
        return None 
    except: return None

# --- 4. ENGINE ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0: return None
    s_y, s_n = (p_no / combined_prob) * total_capital, (p_yes / combined_prob) * total_capital
    if s_y < 1.0 or s_n < 1.0: return None
    roi = (((s_y / p_yes) - total_capital) / total_capital) * 100
    return {"stake_yes": round(s_y, 2), "stake_no": round(s_n, 2), "roi": round(roi, 2), "eff": round(combined_prob, 4)}

async def fetch_full_market(cond_id):
    try:
        r = await asyncio.to_thread(requests.get, f"https://clob.polymarket.com/markets/{cond_id}", timeout=5)
        d = r.json()
        return {"tokens": {t['outcome'].upper(): {"id": t['token_id'], "price": float(t['price'])} for t in d.get('tokens', [])}, "neg_risk": d.get("neg_risk", False)}
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    for tag in [1, 10, 100, 4, 6, 237]:
        try:
            resp = await asyncio.to_thread(requests.get, f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=40&tag_id={tag}", timeout=5)
            for e in resp.json():
                m = e.get('markets', [{}])[0]
                if not m.get('conditionId'): continue
                m_data = await fetch_full_market(m['conditionId'])
                if m_data and 'YES' in m_data['tokens']:
                    arb = calculate_arbitrage_guaranteed(m_data['tokens']['YES']['price'], m_data['tokens']['NO']['price'], 100.0)
                    if arb: ARBI_CACHE.append({"title": e.get('title')[:25], "condition_id": m['conditionId'], "yes_id": m_data['tokens']['YES']['id'], "no_id": m_data['tokens']['NO']['id'], "p_y": m_data['tokens']['YES']['price'], "p_n": m_data['tokens']['NO']['price'], "roi": arb['roi'], "eff": arb['eff'], "ends": m['endDate'], "neg_risk": m_data['neg_risk']})
        except: continue
    ARBI_CACHE.sort(key=lambda x: x['eff'])
    return len(ARBI_CACHE) > 0

# --- 5. TELEGRAM ---
async def start(update, context):
    btns = [['🚀 START ARBI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>HYDRA ARBITRAGE SYSTEM ONLINE</b>", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    cmd = update.message.text
    if 'START ARBI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"🟢 {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:10])]
            await m.edit_text("<b>SHORT-TERM OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await m.edit_text("🛰 <b>NO ARBS DETECTED.</b>")
    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT</b>\n<code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')
    elif 'CALIBRATE' in cmd:
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [5, 10, 50, 100, 250, 500]]]
        await update.message.reply_text("🎯 <b>CALIBRATE STRIKE CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    elif 'FIX APPROVAL' in cmd:
        tx = usdc_e_contract.functions.approve(CTF_EXCHANGE, 2**256 - 1).build_transaction({'from': vault.address, 'nonce': w3.eth.get_transaction_count(vault.address), 'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137})
        w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, vault.key).raw_transaction)
        await update.message.reply_text("✅ <b>USDC APPROVED</b>")

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 5))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>CAPITAL LOADED: ${context.user_data['stake']}</b>")
    elif "ARB_" in q.data:
        idx = int(q.data.split("_")[1]); target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        await q.edit_message_text(f"<b>PLAN:</b> {target['title']}\n✅ YES: ${calc['stake_yes']}\n❌ NO: ${calc['stake_no']}\n💰 ROI: {calc['roi']}%", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{idx}")]]), parse_mode='HTML')
    elif "EXE_" in q.data:
        idx = int(q.data.split("_")[1]); target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        err_msg = ""
        try:
            client = init_clob()
            # FIX: Stop execution if Auth is None
            if not client: raise Exception("Auth Failed: API credentials returned None.")
            
            raw_m = client.get_market(target['condition_id'])
            if not raw_m: raise Exception("Market data unreachable.")
            
            m_meta = Map(raw_m)
            ob = OrderBuilder(client.get_address(), 137, int(os.getenv("SIGNATURE_TYPE", 1)))
            ob.funder = os.getenv("FUNDER_ADDRESS", vault.address)
            ob.contract_address = NEG_RISK_EXCHANGE if target['neg_risk'] else CTF_EXCHANGE

            for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
                signed = ob.create_order(OrderArgs(token_id=str(t_id), price=0.99, size=float(amt), side=BUY), m_meta)
                resp = client.post_order(signed, OrderType.GTC)
                if not resp.get("success"):
                    err_msg = resp.get("errorMsg") or "Order failed."
                    break
        except Exception as e: err_msg = str(e)
        await context.bot.send_message(q.message.chat_id, "✅ <b>ARBITRAGE SECURED</b>" if not err_msg else f"⚠️ <b>EXE ERROR</b>\n<code>{err_msg}</code>", parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    app.run_polling(drop_pending_updates=True)









































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































