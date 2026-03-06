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

# --- 1. CONFIGURATION ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")
NEG_RISK_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]

# --- 2. BLOCKCHAIN CONNECTION ---
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
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try: return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except: return None

vault = get_vault()

# --- 3. AUTHENTICATION (DOCS COMPLIANT) ---
def init_clob():
    try:
        host = "https://clob.polymarket.com"
        chain_id = 137
        # Level 1: Initial client to derive credentials
        temp_client = ClobClient(host, key=vault.key.hex(), chain_id=chain_id)
        creds = temp_client.create_or_derive_api_creds()
        
        # Level 2: Full trading client
        sig_type = int(os.getenv("SIGNATURE_TYPE", 0)) 
        funder = os.getenv("FUNDER_ADDRESS", vault.address)
        
        return ClobClient(
            host, 
            key=vault.key.hex(), 
            chain_id=chain_id, 
            creds=creds, 
            signature_type=sig_type, 
            funder=funder
        )
    except Exception as e:
        print(f"Auth Error: {e}")
        return None

# --- 4. ENGINE LOGIC ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob <= 0: return None
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    profit = (stake_yes / p_yes) - total_capital
    return {
        "stake_yes": round(stake_yes, 2), 
        "stake_no": round(stake_no, 2), 
        "profit": round(profit, 2), 
        "roi": round((profit / total_capital) * 100, 2), 
        "eff": round(combined_prob, 4)
    }

async def fetch_full_market(cond_id):
    try:
        r = await asyncio.to_thread(requests.get, f"https://clob.polymarket.com/markets/{cond_id}", timeout=5)
        d = r.json()
        return {
            "tokens": {t['outcome'].upper(): {"id": t['token_id'], "price": float(t['price'])} for t in d.get('tokens', [])}, 
            "neg_risk": d.get("neg_risk", False)
        }
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    for tag in [1, 10]:
        try:
            resp = await asyncio.to_thread(requests.get, f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=10&tag_id={tag}", timeout=5)
            for e in resp.json():
                if not e.get('markets'): continue
                m = e['markets'][0]
                m_data = await fetch_full_market(m['conditionId'])
                if m_data:
                    arb = calculate_arbitrage_guaranteed(m_data['tokens']['YES']['price'], m_data['tokens']['NO']['price'], 100.0)
                    if arb: ARBI_CACHE.append({
                        "title": e['title'][:25], 
                        "condition_id": m['conditionId'], 
                        "yes_id": m_data['tokens']['YES']['id'], 
                        "no_id": m_data['tokens']['NO']['id'], 
                        "p_y": m_data['tokens']['YES']['price'], 
                        "p_n": m_data['tokens']['NO']['price'], 
                        "roi": arb['roi'], "eff": arb['eff'], 
                        "ends": m['endDate'], "neg_risk": m_data['neg_risk']
                    })
        except: continue
    return len(ARBI_CACHE) > 0

# --- 5. TELEGRAM INTERFACE ---
async def start(update, context):
    await update.message.reply_text("<b>HYDRA SYSTEM ONLINE</b>", parse_mode='HTML')

async def main_handler(update, context):
    if 'START ARBI-SCAN' in update.message.text:
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"{a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:5])]
            await update.message.reply_text("<b>OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 5))
    
    if "ARB_" in q.data:
        idx = int(q.data.split("_")[1]); target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        await q.edit_message_text(f"<b>PLAN:</b> {target['title']}\nROI: {calc['roi']}%", 
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{idx}")]]), 
                                  parse_mode='HTML')
        
    elif "EXE_" in q.data:
        idx = int(q.data.split("_")[1]); target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        err_msg = ""
        try:
            # Sync time with server
            requests.get("https://clob.polymarket.com/time", timeout=5) 
            client = init_clob()
            if not client: raise Exception("Auth Initialization Failed")
            
            # Fetch fresh market data for tick_size
            raw_market = client.get_market(target['condition_id'])
            
            # DOCS REQUIREMENT: Strict options dictionary
            order_options = {
                "tick_size": str(raw_market.get("minimum_tick_size") or "0.001"),
                "neg_risk": target['neg_risk']
            }
            
            sig_type = int(os.getenv("SIGNATURE_TYPE", 0))
            ob = OrderBuilder(client.get_address(), 137, sig_type)
            ob.funder = os.getenv("FUNDER_ADDRESS", vault.address)
            ob.contract_address = NEG_RISK_EXCHANGE if target['neg_risk'] else CTF_EXCHANGE

            for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
                order_args = OrderArgs(token_id=str(t_id), price=0.99, size=float(amt), side=BUY)
                
                # Create and sign order with DOCS compliant options
                signed_order = ob.create_order(order_args, order_options)
                
                if signed_order is None:
                    raise Exception("Signer Error: Order creation returned None.")
                
                resp = client.post_order(signed_order, OrderType.FOK)
                
                if resp is None:
                    raise Exception("API Reject: Polymarket returned None (check balance/allowance).")
                
                if isinstance(resp, dict) and not (resp.get("success") or resp.get("orderID")):
                    err_msg = resp.get("errorMsg") or "Order placement failed."
                    break

        except Exception as e: err_msg = str(e)
        
        status = "✅ <b>ARBITRAGE SECURED</b>" if not err_msg else f"⚠️ <b>EXE ERROR</b>\n<code>{err_msg}</code>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("Hydra Bot Active...")
    app.run_polling()




















































































































































































































































































































































































































































































































































