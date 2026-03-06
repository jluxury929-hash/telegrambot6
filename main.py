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

# --- 1. CONFIGURATION & ABIs ---
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
███████║██████╔╝█████╗    ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝     ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-STABLE</pre>"""

# --- 2. BLOCKCHAIN CONNECTION ---
def get_hydra_w3():
    endpoints = [os.getenv("RPC_URL"), "https://polygon-rpc.com", "https://1rpc.io/matic"]
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

usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

# --- 3. VAULT & AUTHENTICATION ---
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

# --- 4. ENGINE LOGIC ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0: return None
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    if stake_yes < 1.0 or stake_no < 1.0: return None
    expected_payout = (stake_yes / p_yes)
    profit = expected_payout - total_capital
    roi = (profit / total_capital) * 100
    return {"stake_yes": round(stake_yes, 2), "stake_no": round(stake_no, 2), "profit": round(profit, 2), "roi": round(roi, 2), "eff": round(combined_prob, 4)}

async def fetch_full_market(cond_id):
    try:
        r = await asyncio.to_thread(requests.get, f"https://clob.polymarket.com/markets/{cond_id}", timeout=5)
        d = r.json()
        if not d or 'tokens' not in d: return None
        return {
            "tokens": {t['outcome'].upper(): {"id": t['token_id'], "price": float(t['price'])} for t in d.get('tokens', [])},
            "neg_risk": d.get("neg_risk", False)
        }
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    limit_ts = time.time() + (3 * 24 * 3600)
    for tag in [1, 10, 100, 4, 6, 237]:
        try:
            resp = await asyncio.to_thread(requests.get, f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=40&tag_id={tag}", timeout=5)
            data = resp.json()
            if not isinstance(data, list): continue
            for e in data:
                m_list = e.get('markets', [])
                if not m_list: continue
                m = m_list[0]
                if not m.get('conditionId'): continue
                end_dt = datetime.fromisoformat(m['endDate'].replace('Z', '+00:00'))
                if end_dt.timestamp() > limit_ts: continue
                m_data = await fetch_full_market(m['conditionId'])
                if m_data and 'YES' in m_data['tokens'] and 'NO' in m_data['tokens']:
                    arb = calculate_arbitrage_guaranteed(m_data['tokens']['YES']['price'], m_data['tokens']['NO']['price'], 100.0)
                    if arb:
                        ARBI_CACHE.append({
                            "title": f"[{round((end_dt.timestamp()-time.time())/86400, 1)}d] " + e.get('title')[:25], 
                            "condition_id": m['conditionId'], 
                            "yes_id": m_data['tokens']['YES']['id'], 
                            "no_id": m_data['tokens']['NO']['id'], 
                            "p_y": m_data['tokens']['YES']['price'], 
                            "p_n": m_data['tokens']['NO']['price'], 
                            "roi": arb['roi'], "eff": arb['eff'], "ends": m['endDate'],
                            "neg_risk": m_data['neg_risk']
                        })
        except: continue
    ARBI_CACHE.sort(key=lambda x: x['eff'])
    return len(ARBI_CACHE) > 0

# --- 5. TELEGRAM INTERFACE ---
async def start(update, context):
    btns = [['🚀 START ARBI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>HYDRA ARBITRAGE SYSTEM ONLINE</b>", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    cmd = update.message.text
    if 'START ARBI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"{'🟢' if a['roi'] > 0 else '🟡'} {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:10])]
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
        signed = w3.eth.account.sign_transaction(tx, vault.key)
        w3.eth.send_raw_transaction(signed.raw_transaction)
        await update.message.reply_text("✅ <b>USDC APPROVED</b>")

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 5))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>CAPITAL LOADED: ${context.user_data['stake']}</b>")
        
    elif "ARB_" in q.data:
        idx = int(q.data.split("_")[1])
        if idx >= len(ARBI_CACHE):
            await q.edit_message_text("⚠️ Data expired. Please re-scan.")
            return
        target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        msg = f"<b>PLAN:</b> {target['title']}\n📅 <b>Ends:</b> {target['ends']}\n\n✅ YES: ${calc['stake_yes']}\n❌ NO: ${calc['stake_no']}\n💰 ROI: {calc['roi']}%"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{idx}")]]), parse_mode='HTML')
        
    elif "EXE_" in q.data:
        idx = int(q.data.split("_")[1])
        if idx >= len(ARBI_CACHE):
            await context.bot.send_message(q.message.chat_id, "⚠️ Error: Index out of range.")
            return
            
        target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        err_msg = ""
        try:
            requests.get("https://clob.polymarket.com/time", timeout=5)
            client = init_clob()
            sig_type = int(os.getenv("SIGNATURE_TYPE", 1))
            funder_addr = os.getenv("FUNDER_ADDRESS", vault.address)
            
            # Use exactly 4 positional arguments to satisfy the SDK's constructor
            ob = OrderBuilder(client.get_address(), 137, sig_type, funder_addr)
            
            # Manually switch the exchange address if market is Negative Risk
            # This avoids adding a 5th positional argument that crashes the code
            if target['neg_risk']:
                ob.contract_address = NEG_RISK_EXCHANGE
            else:
                ob.contract_address = CTF_EXCHANGE

            for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
                order_args = OrderArgs(token_id=str(t_id), price=0.99, size=float(amt), side=BUY)
                signed_order = ob.create_order(order_args)
                resp = client.post_order(signed_order, OrderType.FOK)
                
                if isinstance(resp, int):
                    if resp not in [200, 201]:
                        err_msg = f"HTTP {resp}: Check balance/allowance."
                        break
                elif isinstance(resp, dict):
                    if not (resp.get("success") or resp.get("orderID")):
                        err_msg = resp.get("errorMsg") or "Order placement failed."
                        break

        except Exception as e: err_msg = str(e)
        
        status = "✅ <b>ARBITRAGE SECURED</b>" if not err_msg else f"⚠️ <b>EXE ERROR</b>\n<code>{err_msg}</code>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

# --- 6. START BOT ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("Hydra Bot Active...")
    app.run_polling(drop_pending_updates=True)









































































































































