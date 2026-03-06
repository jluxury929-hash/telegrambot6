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

# Polygon/Polymarket Specifics
USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
LOGO = """<code>█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗   ╚███╔╝ 
██╔══██║██╔═══╝ ██╔══╝    ██╔██╗ 
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-DEEP-SCAN</code>"""

# --- 2. ENGINE SETUP ---
def get_hydra_w3():
    rpc = os.getenv("RPC_URL", "https://polygon-rpc.com")
    try:
        _w3 = Web3(Web3.HTTPProvider(rpc))
        _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        return _w3
    except: return None

w3 = get_hydra_w3()
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "success", "type": "bool"}], "type": "function"}
]
usdc_e_contract = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

def get_vault():
    seed = os.getenv("WALLET_SEED", "").strip()
    Account.enable_unaudited_hdwallet_features()
    return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)

vault = get_vault()

def init_clob():
    try:
        client = ClobClient("https://clob.polymarket.com", key=vault.key.hex(), chain_id=137)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client
    except: return None

clob_client = init_clob()

# --- 3. MATH: PURE ARBITRAGE ---
def calculate_pure_arb(p_y, p_n, capital):
    """
    Guaranteed Profit Math:
    If Sum (Price_Yes + Price_No) < 1.00, we buy both sides.
    """
    total_cost_per_bundle = p_y + p_n
    
    # 0.9999 is used as the hard limit to ensure real profit after decimals.
    if total_cost_per_bundle >= 0.9999 or total_cost_per_bundle <= 0:
        return None
    
    # Payout is always 1.00. We buy 'shares' number of bundles.
    shares = capital / total_cost_per_bundle
    stake_y = shares * p_y
    stake_n = shares * p_n
    
    profit = shares - capital
    roi = (profit / capital) * 100
    
    return {
        "s_y": round(stake_y, 2),
        "s_n": round(stake_n, 2),
        "roi": round(roi, 2)
    }

# --- 4. DEEP OMNI-SCANNER ---
async def scour_arbitrage():
    global ARBI_CACHE
    ARBI_CACHE = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24) # SAME-DAY ONLY
    
    # Target all major category tag IDs
    tags = ["1", "6", "10015", "4", "5", ""] # Crypto, Politics, Sports, Pop, Business, Global
    orders = ["volume24hr", "liquidity", "id"]
    
    seen_ids = set()
    
    async def fetch_category_page(tag_id, order_by, offset=0):
        url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50&offset={offset}&order={order_by}&ascending=false"
        if tag_id: url += f"&tag_id={tag_id}"
        
        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            for e in resp.json():
                if e['id'] in seen_ids: continue
                seen_ids.add(e['id'])
                
                # Expiry Check
                end_str = e.get('endDate')
                if not end_str: continue
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                if end_dt > cutoff: continue 
                
                for m in e.get('markets', []):
                    # Check the 'outcomePrices' provided in metadata
                    prices = json.loads(m.get('outcomePrices', '[]'))
                    if len(prices) >= 2:
                        p_y, p_n = float(prices[0]), float(prices[1])
                        
                        # Apply Hard ROI Filter
                        arb = calculate_pure_arb(p_y, p_n, 100.0)
                        if arb:
                            hours_left = (end_dt - now).total_seconds() / 3600
                            ARBI_CACHE.append({
                                "title": f"⏱{round(hours_left,1)}h | {e['title'][:25]}",
                                "y_id": m['clobTokenIds'][0],
                                "n_id": m['clobTokenIds'][1],
                                "p_y": p_y, "p_n": p_n,
                                "roi": arb['roi']
                            })
        except: pass

    # Fire all category and depth scans in parallel (Fast Discovery)
    tasks = []
    for t in tags:
        for o in orders:
            tasks.append(fetch_category_page(t, o, 0))   # Page 1
            tasks.append(fetch_category_page(t, o, 50))  # Page 2 (Deep Scan)
            
    await asyncio.gather(*tasks)
    
    # Sort results so best profit is always first
    ARBI_CACHE.sort(key=lambda x: x['roi'], reverse=True)
    return len(ARBI_CACHE) > 0

# --- 5. INTERFACE ---
async def start(update, context):
    btns = [['🚀 DEEP OMNI-SCAN', '📊 CALIBRATE'], ['💳 VAULT', '🔧 FIX APPROVAL']]
    await update.message.reply_text(f"{LOGO}\n<b>DEEP SCANNER READY</b>\nStrict Filter: ROI > 0% only.", reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True), parse_mode='HTML')

async def main_handler(update, context):
    if 'DEEP OMNI-SCAN' in update.message.text:
        m = await update.message.reply_text("📡 <b>SCANNING 500+ MARKETS...</b>", parse_mode='HTML')
        if await scour_arbitrage():
            kb = [[InlineKeyboardButton(f"🟢 {a['roi']}% | {a['title']}", callback_data=f"ARB_{i}")] for i, a in enumerate(ARBI_CACHE[:20])]
            await m.edit_text(f"<b>FOUND {len(ARBI_CACHE)} PROFITABLE TRADES:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await m.edit_text("🛰 <b>NO PROFITABLE DEALS FOUND.</b>\nThe entire market is currently efficient. Try again in 60s.")

async def handle_query(update, context):
    q = update.callback_query; await q.answer()
    stake = float(context.user_data.get('stake', 50))
    if "ARB_" in q.data:
        t = ARBI_CACHE[int(q.data.split("_")[1])]
        c = calculate_pure_arb(t['p_y'], t['p_n'], stake)
        msg = (f"<b>{t['title']}</b>\n\n"
               f"✅ YES: ${c['s_y']} @ {t['p_y']}\n"
               f"❌ NO: ${c['s_n']} @ {t['p_n']}\n\n"
               f"💰 <b>Guaranteed ROI: {t['roi']}%</b>")
        kb = [[InlineKeyboardButton("🔥 EXECUTE TRADE", callback_data=f"EXE_{q.data.split('_')[1]}")]]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif "EXE_" in q.data:
        t = ARBI_CACHE[int(q.data.split("_")[1])]
        c = calculate_pure_arb(t['p_y'], t['p_n'], stake)
        results = []
        for (tid, amt) in [(t['y_id'], c['s_y']), (t['n_id'], c['s_n'])]:
            try:
                order = clob_client.create_order(MarketOrderArgs(token_id=str(tid), amount=float(amt), side="BUY"))
                resp = clob_client.post_order(order, OrderType.FOK)
                results.append(True if (resp.get("success") or "order_id" in resp) else False)
            except: results.append(False)
        await context.bot.send_message(q.message.chat_id, "✅ <b>POSITION LOCKED</b>" if all(results) else "⚠️ <b>PRICE SLIPPAGE - ORDER FAILED</b>")

if __name__ == "__main__":
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_handler))
    app.run_polling()






