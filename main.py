import os
import asyncio
import json
import requests
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

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")

LOGO = """<code>█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-STABLE</code>"""

# --- 2. HYDRA ENGINE & ABIs ---
def get_hydra_w3():
    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    try:
        _w3 = Web3(Web3.HTTPProvider(rpc))
        if _w3.is_connected():
            _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            return _w3
    except: return None

w3 = get_hydra_w3()
if not w3:
    print("FATAL: RPC Failure."); import sys; sys.exit(1)

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    try:
        return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except: return None

vault = get_vault()

def init_clob():
    try:
        client = ClobClient("https://clob.polymarket.com", key=vault.key.hex(), chain_id=137)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        print(f"CLOB Auth failed: {e}")
        return None

clob_client = init_clob()

# --- 3. PROFIT MATH & SCANNER ---
def calculate_arbitrage(p_y, p_n, capital):
    combined = p_y + p_n
    if combined >= 1.0 or combined <= 0: return None # Only profitable bets
    
    # Capital split to hedge both sides perfectly
    s_y = (p_n / combined) * capital
    s_n = (p_y / combined) * capital
    
    expected_return = s_y / p_y
    profit = expected_return - capital
    roi = (profit / capital) * 100
    return {"s_y": round(s_y, 2), "s_n": round(s_n, 2), "roi": round(roi, 2), "sum": round(combined, 4)}

async def scour_profitable_same_day():
    global ARBI_CACHE
    ARBI_CACHE = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    
    # Expanded discovery endpoints
    endpoints = [
        "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50&order=volume24hr&ascending=false",
        "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50&tag_id=1", # Crypto
        "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50&tag_id=6"  # Politics
    ]
    
    seen_ids = set()
    for url in endpoints:
        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            for e in resp.json():
                if e['id'] in seen_ids: continue
                seen_ids.add(e['id'])
                
                # Expiry check
                end_str = e.get('endDate')
                if not end_str: continue
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                
                if end_dt > cutoff: continue # Skip if > 24h away
                
                for m in e.get('markets', []):
                    prices = json.loads(m.get('outcomePrices', '[0,0]'))
                    p_y, p_n = float(prices[0]), float(prices[1])
                    
                    # Math Check
                    arb = calculate_arbitrage(p_y, p_n, 100.0)
                    if arb:
                        hours_left = (end_dt - now).total_seconds() / 3600
                        ARBI_CACHE.append({
                            "title": f"{e['title'][:25]}",
                            "y_id": m['clobTokenIds'][0],
                            "n_id": m['clobTokenIds'][1],
                            "p_y": p_y, "p_n": p_n,
                            "roi": arb['roi'],
                            "sum": arb['sum'],
                            "hours": round(hours_left, 1)
                        })
        except: continue

    ARBI_CACHE.sort(key=lambda x: x['roi'], reverse=True)
    return len(ARBI_CACHE) > 0

# --- 4. UI HANDLERS ---
async def start(update, context):
    btns = [['🚀 SCAN PROFITS', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>SAME-DAY PROFIT ENGINE ACTIVE</b>\nHunting for Sum < 1.00", 
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    cmd = update.message.text
    if 'SCAN PROFITS' in cmd:
        m = await update.message.reply_text("🔎 <b>HUNTING SPREADS...</b>", parse_mode='HTML')
        if await scour_profitable_same_day():
            kb = [[InlineKeyboardButton(f"🟢 {a['roi']}% | {a['title']}", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:12])]
            await m.edit_text(f"<b>{len(ARBI_CACHE)} PROFITABLE SPREADS:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("🛰 <b>NO PROFITABLE ARBS DETECTED.</b>\nAll current same-day markets are efficient (Sum >= 1.0).")

    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT</b>\n<code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}", parse_mode='HTML')

    elif 'CALIBRATE' in cmd:
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [5, 10, 50, 100, 250, 500]]]
        await update.message.reply_text("🎯 <b>SET CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    
    if "SET_" in q.data:
        context.user_data['stake'] = int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>STAKE SET TO: ${context.user_data['stake']}</b>")
        
    elif "ARB_" in q.data:
        t = ARBI_CACHE[int(q.data.split("_")[1])]
        c = calculate_arbitrage(t['p_y'], t['p_n'], stake)
        msg = (f"<b>OPPORTUNITY</b>\n{t['title']}\n\n"
               f"⏳ <b>Ends in:</b> {t['hours']}h\n"
               f"✅ <b>YES:</b> ${c['s_y']} @ {t['p_y']}\n"
               f"❌ <b>NO:</b> ${c['s_n']} @ {t['p_n']}\n\n"
               f"💰 <b>NET PROFIT:</b> ${c['profit']}\n"
               f"📈 <b>ROI:</b> {t['roi']}%")
        kb = [[InlineKeyboardButton("🔥 EXECUTE DUAL BET", callback_data=f"EXE_{q.data.split('_')[1]}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif "EXE_" in q.data:
        t = ARBI_CACHE[int(q.data.split("_")[1])]
        c = calculate_arbitrage(t['p_y'], t['p_n'], stake)
        results = []
        for (tid, amt) in [(t['y_id'], c['s_y']), (t['n_id'], c['s_n'])]:
            try:
                order = MarketOrderArgs(token_id=str(tid), amount=float(amt), side="BUY")
                signed = clob_client.create_order(order)
                resp = clob_client.post_order(signed, OrderType.FOK)
                results.append(True if (resp.get("success") or "order_id" in resp) else False)
            except: results.append(False)
        
        status = "✅ <b>PROFIT LOCKED</b>" if all(results) else "⚠️ <b>LEG FAILED</b>\nCheck liquidity/balance."
        await context.bot.send_message(q.message.chat_id, status, parse_mode='HTML')

# --- 5. LAUNCH ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    print("Hydra v230 Stable Active.")
    app.run_polling()










