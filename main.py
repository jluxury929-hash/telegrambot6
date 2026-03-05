import os, asyncio, json, time, requests, sys
import numpy as np
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Polymarket SDK Imports
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# --- 1. CORE CONFIG ---
getcontext().prec = 28
load_dotenv()
ARBI_CACHE = []

# RAILWAY PROXY FIX: 
# Add PROXY_URL (e.g., http://user:pass@host:port) to your Railway Variables
PROXY_URL = os.getenv("PROXY_URL")
session = requests.Session()
if PROXY_URL:
    session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    print(f"🛡 [RAILWAY] Proxy Route Active.")

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")
LOGO = """<code>█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v242-RAILWAY</code>"""

# --- 2. HYDRA ENGINE ---
def get_hydra_w3():
    endpoints = [os.getenv("RPC_URL"), "https://polygon-rpc.com"]
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
    print("FATAL: RPC Failure."); sys.exit(1)

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

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
        # Pass proxy to CLOB client if exists
        client = ClobClient(
            host="https://clob.polymarket.com", 
            key=vault.key.hex(), 
            chain_id=137, 
            signature_type=sig_type, 
            funder=os.getenv("FUNDER_ADDRESS", vault.address)
        )
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except Exception as e:
        print(f"Auth derivation failed: {e}"); return None

clob_client = init_clob()

# --- 4. ARBITRAGE MATH ---
def calculate_arbitrage_guaranteed(p_yes, p_no, total_capital):
    # FIX: Price(0) Error - Ignore markets with no price
    if p_yes <= 0.001 or p_no <= 0.001: return None
    combined_prob = p_yes + p_no
    if combined_prob >= 0.999: return None # Ensure profit
    
    stake_yes = (p_no / combined_prob) * total_capital
    stake_no = (p_yes / combined_prob) * total_capital
    profit = (stake_yes / p_yes) - total_capital
    return {
        "stake_yes": round(stake_yes, 2), "stake_no": round(stake_no, 2),
        "profit": round(profit, 2), "roi": round((profit / total_capital) * 100, 2), "eff": round(combined_prob, 4)
    }

async def fetch_full_market(cond_id):
    try:
        url = f"https://clob.polymarket.com/markets/{cond_id}"
        r = await asyncio.to_thread(session.get, url, timeout=5)
        d = r.json()
        return {t['outcome'].upper(): {"id": t['token_id'], "price": float(t['price'])} for t in d.get('tokens', [])}
    except: return None

async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    tags = [1, 10, 100, 4, 6, 237] 
    for tag in tags:
        url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=15&tag_id={tag}"
        try:
            resp = await asyncio.to_thread(session.get, url, timeout=5)
            for e in resp.json():
                m = e.get('markets', [])
                if not m: continue
                m_data = await fetch_full_market(m[0]['conditionId'])
                if m_data and 'YES' in m_data and 'NO' in m_data:
                    arb = calculate_arbitrage_guaranteed(m_data['YES']['price'], m_data['NO']['price'], 100.0)
                    if arb and arb['roi'] > 0:
                        ARBI_CACHE.append({
                            "title": e.get('title')[:30], "yes_id": m_data['YES']['id'], "no_id": m_data['NO']['id'],
                            "p_y": m_data['YES']['price'], "p_n": m_data['NO']['price'], "roi": arb['roi'], "eff": arb['eff']
                        })
        except: continue
    ARBI_CACHE.sort(key=lambda x: x['eff'])
    return len(ARBI_CACHE) > 0

# --- 5. BOT LOGIC ---
async def start(update, context):
    btns = [['🚀 START ARBI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>HYDRA RAILWAY ONLINE</b>", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    cmd = update.message.text
    if 'START ARBI-SCAN' in cmd:
        m = await update.message.reply_text("📡 <b>SCANNING...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"🟢 {a['title']} ({a['roi']}%)", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:8])]
            await m.edit_text("<b>OPPORTUNITIES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await m.edit_text("🛰 <b>NO ARBITRAGE DETECTED.</b>")
    elif 'VAULT' in cmd:
        bal = usdc_e_contract.functions.balanceOf(vault.address).call()
        await update.message.reply_text(f"<b>VAULT</b>: ${bal/1e6:.2f}", parse_mode='HTML')
    elif 'CALIBRATE' in cmd:
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [5, 10, 50, 100]]]
        await update.message.reply_text("🎯 <b>STRIKE CAPITAL:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    if "SET_" in q.data:
        context.user_data['stake'] = q.data.split("_")[1]
        await q.edit_message_text(f"✅ <b>STAKE: ${context.user_data['stake']}</b>")
    elif "ARB_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        kb = [[InlineKeyboardButton("🔥 EXECUTE", callback_data=f"EXE_{q.data.split('_')[1]}")]]
        await q.edit_message_text(f"<b>ROI: {calc['roi']}%</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    elif "EXE_" in q.data:
        target = ARBI_CACHE[int(q.data.split("_")[1])]
        calc = calculate_arbitrage_guaranteed(target['p_y'], target['p_n'], stake)
        for (t_id, amt) in [(target['yes_id'], calc['stake_yes']), (target['no_id'], calc['stake_no'])]:
            try:
                clob_client.post_order(clob_client.create_order(MarketOrderArgs(token_id=str(t_id), amount=float(amt), side="BUY")), OrderType.FOK)
            except Exception as e: print(f"Trade Error: {e}")
        await context.bot.send_message(q.message.chat_id, "✅ <b>SENT</b>")

async def ghost_pilot(app):
    while True:
        try:
            if await scour_arbitrage():
                for arb in ARBI_CACHE:
                    if arb['roi'] > 0.8:
                        # Auto Execution logic here...
                        pass
            await asyncio.sleep(60)
        except: await asyncio.sleep(20)

# --- 7. RAILWAY STARTUP ---
async def main():
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    
    # Run Pilot as background task
    asyncio.create_task(ghost_pilot(app))
    
    async with app:
        await app.initialize()
        await app.start()
        print("Hydra Active on Railway.")
        # drop_pending_updates=True fixes the "Conflict" error
        await app.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(1000)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass




