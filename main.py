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

# --- 1. CORE CONFIG & MATH ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")

LOGO = """<code>█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v2.5-DEEP-SCAN</code>"""

# --- 2. BLOCKCHAIN CONNECTION ---
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
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI) if w3 else None

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
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=vault.key.hex(),
            chain_id=137,
            signature_type=int(os.getenv("SIGNATURE_TYPE", 1)),
            funder=os.getenv("FUNDER_ADDRESS", vault.address)
        )
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        print(f"Auth derivation failed: {e}")
        return None

clob_client = init_clob()

# --- 4. ARBITRAGE ENGINE (Deep Scanning) ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    combined_prob = p_yes + p_no
    if combined_prob >= 1.0 or combined_prob <= 0: 
        # Standard Arbi: We only care if Yes + No < 1.00 (Risk Free)
        # If > 1.00, it's just a normal market spread.
        return {"roi": (1.0 - combined_prob) * 100, "eff": combined_prob}
    
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
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

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    
    # SCAN PARAMETERS: Iterating through pages to find high ROI gems
    # Gamma API is faster for discovery than CLOB API
    base_url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,
        "offset": 0,
        "order": "volume", 
        "ascending": "false"
    }

    try:
        # Scans the top 200 markets across all categories
        for _ in range(2): 
            resp = await asyncio.to_thread(requests.get, base_url, params=params, timeout=10)
            markets = resp.json()
            if not markets: break
            
            for m in markets:
                prices = m.get('outcomePrices')
                if not prices or len(prices) < 2: continue
                
                p_y, p_n = float(prices[0]), float(prices[1])
                arb = calculate_arbitrage_guaranteed(p_y, p_n, 100.0)
                
                # We save all markets, but our sorting will push ROI to the top
                ARBI_CACHE.append({
                    "title": f"[{m.get('category', '??')}] {m.get('question')[:25]}",
                    "yes_id": m.get('clobTokenIds', [None])[0],
                    "no_id": m.get('clobTokenIds', [None])[1],
                    "p_y": p_y,
                    "p_n": p_n,
                    "roi": arb.get('roi', -100),
                    "liquidity": float(m.get('liquidity', 0))
                })
            params['offset'] += 100
            
    except Exception as ex:
        print(f"Scour Error: {ex}")

    # SORT BY ROI (Primary) and LIQUIDITY (Secondary)
    ARBI_CACHE.sort(key=lambda x: (x['roi'], x['liquidity']), reverse=True)
    return len(ARBI_CACHE) > 0

# --- 5. TELEGRAM INTERFACE ---
async def start(update, context):
    btns = [['🚀 DEEP SCAN', '📊 STATS'], ['💳 VAULT', '🔧 APPROVE']]
    await update.message.reply_text(
        f"{LOGO}\n<b>HYDRA MULTI-CATEGORY SCANNER</b>\nSearching all markets for ROI > 0%.",
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML'
    )

async def main_handler(update, context):
    cmd = update.message.text
    if 'DEEP SCAN' in cmd:
        m = await update.message.reply_text("🔎 <b>SCANNING EVERY CATEGORY...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            # Show top 10 highest ROI opportunities found
            kb = []
            for i, a in enumerate(ARBI_CACHE[:10]):
                icon = "🔥" if a['roi'] > 0 else "📊"
                kb.append([InlineKeyboardButton(f"{icon} {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")])
            
            await m.edit_text("<b>HIGHEST ROI OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("❌ <b>NO MARKETS FOUND.</b> Check API status.")
            
    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT:</b> <code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')

    elif 'APPROVE' in cmd:
        try:
            tx = usdc_e_contract.functions.approve(CTF_EXCHANGE, 2**256 - 1).build_transaction({
                'from': vault.address, 'nonce': w3.eth.get_transaction_count(vault.address),
                'gasPrice': int(w3.eth.gas_price * 1.2), 'chainId': 137
            })
            signed = w3.eth.account.sign_transaction(tx, vault.key)
            w3.eth.send_raw_transaction(signed.rawTransaction)
            await update.message.reply_text("✅ <b>USDC APPROVED FOR TRADING</b>")
        except Exception as e:
            await update.message.reply_text(f"❌ <b>FAILED:</b> {e}")

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 100))
    
    if "ARB_" in q.data:
        idx = int(q.data.split("_")[1])
        target = ARBI_CACHE[idx]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        
        msg = (f"<b>OPPORTUNITY:</b> {target['title']}\n"
               f"━━━━━━━━━━━━\n"
               f"✅ YES Price: {target['p_y']} | Stake: ${calc['stake_yes']}\n"
               f"❌ NO Price: {target['p_n']} | Stake: ${calc['stake_no']}\n"
               f"━━━━━━━━━━━━\n"
               f"💰 <b>GUARANTEED PROFIT: ${calc['profit']} ({calc['roi']}%)</b>")
        
        kb = [[InlineKeyboardButton("🔥 EXECUTE ARBITRAGE", callback_data=f"EXE_{idx}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        
        # Sequentially place orders
        success_count = 0
        for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                order = MarketOrderArgs(token_id=str(t_id), amount=float(amt), side="BUY")
                signed_order = clob_client.create_order(order)
                resp = clob_client.post_order(signed_order, OrderType.FOK) # Fill or Kill to avoid partials
                if resp.get("success"): success_count += 1
            except: continue
        
        status = "✅ <b>ARBITRAGE SECURED</b>" if success_count == 2 else "⚠️ <b>PARTIAL FILL / ERROR</b>"
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("HYDRA V2.5 DEEP-SCAN ACTIVE...")
    app.run_polling()







