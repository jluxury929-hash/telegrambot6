import os
import asyncio
import requests
import time
from datetime import datetime
from decimal import Decimal, getcontext
from dotenv import load_dotenv

# Blockchain & Trading
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# Polymarket SDK
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY
from py_clob_client.order_builder.builder import OrderBuilder

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# --- 0. INITIALIZATION ---
load_dotenv()
getcontext().prec = 28
ARBI_CACHE = []

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFbE613d03C895dB366BC36B3D966A488007284")
NEG_RISK_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

ERC20_ABI = [
    {"constant": True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant": False,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"type":"function"}
]

LOGO = """<pre>
█████╗ ██████╗ ███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗ ██╔╝
███████║██████╔╝█████╗     ╚███╔╝
██╔══██║██╔═══╝ ██╔══╝      ██╔██╗
██║  ██║██║     ███████╗██╔╝ ██╗
╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝ v230-STABLE</pre>

"""

# --- UTIL ---
class Map(dict):
    def __getattr__(self, name):
        return self.get(name)

def get_vault():
    seed=os.getenv("WALLET_SEED","").strip()
    Account.enable_unaudited_hdwallet_features()
    try:
        return Account.from_mnemonic(seed) if " " in seed else Account.from_key(seed)
    except:
        return None

vault=get_vault()

def get_hydra_w3():
    raw=os.getenv("RPC_URL","").strip()
    endpoints=[raw,"https://polygon-rpc.com","https://1rpc.io/matic"]

    for url in endpoints:
        if not url:
            continue
        try:
            w3=Web3(Web3.HTTPProvider(url,request_kwargs={'timeout':15}))
            if w3.is_connected():
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware,layer=0)
                return w3
        except:
            continue
    return None

w3=get_hydra_w3()
usdc_e_contract=w3.eth.contract(address=USDC_E,abi=ERC20_ABI) if w3 else None

# --- POLYMARKET ---
def init_clob():
    try:
        if not vault:
            return None

        sig=int(os.getenv("SIGNATURE_TYPE",1))
        funder=os.getenv("FUNDER_ADDRESS",vault.address)

        client=ClobClient(
            host="https://clob.polymarket.com",
            key=vault.key.hex(),
            chain_id=137,
            signature_type=sig,
            funder=funder
        )

        creds=client.create_or_derive_api_creds()

        if creds:
            client.set_api_creds(creds)
            return client

        return None

    except Exception as e:
        print("AUTH ERROR:",repr(e))
        return None

def calculate_arbitrage_guaranteed(p_yes,p_no,total_capital):

    combined=p_yes+p_no
    if combined<=0:
        return None

    stake_yes=(p_no/combined)*total_capital
    stake_no=(p_yes/combined)*total_capital

    if stake_yes<1 or stake_no<1:
        return None

    profit=(stake_yes/p_yes)-total_capital
    roi=(profit/total_capital)*100

    return {
        "stake_yes":round(stake_yes,2),
        "stake_no":round(stake_no,2),
        "roi":round(roi,2),
        "eff":round(combined,4)
    }

# --- SCAN ---
async def fetch_full_market(cid):
    try:
        r=await asyncio.to_thread(requests.get,f"https://clob.polymarket.com/markets/{cid}",timeout=5)
        d=r.json()

        return {
            "tokens":{
                t['outcome'].upper():{
                    "id":t['token_id'],
                    "price":float(t['price'])
                } for t in d.get('tokens',[])
            },
            "neg_risk":d.get("neg_risk",False)
        }
    except:
        return None

async def scour_arbitrage():

    global ARBI_CACHE
    ARBI_CACHE=[]

    limit=time.time()+(3*24*3600)

    for tag in [1,10,100,4,6,237]:

        try:
            resp=await asyncio.to_thread(
                requests.get,
                f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=40&tag_id={tag}",
                timeout=5
            )

            for e in resp.json():

                m_list=e.get("markets",[])
                if not m_list:
                    continue

                m=m_list[0]
                if not m.get("conditionId"):
                    continue

                end_dt=datetime.fromisoformat(m['endDate'].replace('Z','+00:00'))

                if end_dt.timestamp()>limit:
                    continue

                m_data=await fetch_full_market(m['conditionId'])

                if m_data and 'YES' in m_data['tokens'] and 'NO' in m_data['tokens']:

                    arb=calculate_arbitrage_guaranteed(
                        m_data['tokens']['YES']['price'],
                        m_data['tokens']['NO']['price'],
                        100
                    )

                    if arb:

                        ARBI_CACHE.append({

                            "title":f"[{round((end_dt.timestamp()-time.time())/86400,1)}d] "+e.get('title')[:25],

                            "condition_id":m['conditionId'],

                            "yes_id":m_data['tokens']['YES']['id'],
                            "no_id":m_data['tokens']['NO']['id'],

                            "p_y":m_data['tokens']['YES']['price'],
                            "p_n":m_data['tokens']['NO']['price'],

                            "roi":arb['roi'],
                            "eff":arb['eff'],

                            "ends":m['endDate'],
                            "neg_risk":m_data['neg_risk']
                        })

        except:
            continue

    ARBI_CACHE.sort(key=lambda x:x['eff'])

    return len(ARBI_CACHE)>0

# --- TELEGRAM ---
async def start(update,context):

    btns=[['🚀 START ARBI-SCAN','📊 CALIBRATE'],['💳 VAULT','🔧 FIX APPROVAL']]

    await update.message.reply_text(
        f"{LOGO}\n<b>HYDRA ARBITRAGE SYSTEM ONLINE</b>",
        reply_markup=ReplyKeyboardMarkup(btns,resize_keyboard=True),
        parse_mode="HTML"
    )

async def main_handler(update,context):

    cmd=update.message.text

    if 'START ARBI-SCAN' in cmd:

        m=await update.message.reply_text("📡 <b>SCANNING...</b>",parse_mode="HTML")

        if await scour_arbitrage():

            kb=[[InlineKeyboardButton(
                f"{'🟢' if a['roi']>0 else '🟡'} {a['title']} ({a['roi']}%)",
                callback_data=f"ARB_{i}"
            )] for i,a in enumerate(ARBI_CACHE[:10])]

            await m.edit_text(
                "<b>SHORT-TERM OPPORTUNITIES:</b>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML"
            )
        else:
            await m.edit_text("🛰 <b>NO ARBS DETECTED.</b>")

    elif 'VAULT' in cmd:

        bal=usdc_e_contract.functions.balanceOf(vault.address).call()

        await update.message.reply_text(
            f"<b>VAULT</b>\n<code>{vault.address}</code>\n<b>USDC.e:</b> ${bal/1e6:.2f}",
            parse_mode="HTML"
        )

    elif 'CALIBRATE' in cmd:

        kb=[[InlineKeyboardButton(f"${x}",callback_data=f"SET_{x}") for x in [5,10,50,100,250,500]]]

        await update.message.reply_text(
            "🎯 <b>CALIBRATE STRIKE CAPITAL:</b>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )

async def handle_query(update,context):

    q=update.callback_query
    await q.answer()

    if "SET_" in q.data:
        context.user_data['stake']=int(q.data.split("_")[1])
        await q.edit_message_text(f"✅ <b>CAPITAL LOADED: ${context.user_data['stake']}</b>")
        return

    stake=float(context.user_data.get('stake',5))

    if "EXE_" in q.data:

        idx=int(q.data.split("_")[1])
        target=ARBI_CACHE[idx]

        calc=calculate_arbitrage_guaranteed(target['p_y'],target['p_n'],stake)

        err_msg=""

        try:

            client=init_clob()
            if not client:
                raise Exception("CLOB init failed")

            raw=client.get_market(target['condition_id'])
            market_metadata=Map(raw)

            ob=OrderBuilder(client.get_address(),137,int(os.getenv("SIGNATURE_TYPE",1)))

            ob.funder=os.getenv("FUNDER_ADDRESS",vault.address)
            ob.contract_address=NEG_RISK_EXCHANGE if target['neg_risk'] else CTF_EXCHANGE

            for (tid,amt) in [(target['yes_id'],calc['stake_yes']),(target['no_id'],calc['stake_no'])]:

                price=target['p_y'] if tid==target['yes_id'] else target['p_n']

                print("ORDER:",tid,amt,price)

                order_args=OrderArgs(
                    token_id=str(tid),
                    price=float(price),
                    size=float(amt),
                    side=BUY
                )

                signed=ob.create_order(order_args,market_metadata)

                resp=client.post_order(signed,OrderType.GTC)

                print("ORDER RESPONSE:",resp)

                if not resp:
                    err_msg="Empty response from CLOB"
                    break

                if isinstance(resp,dict):
                    if resp.get("success") or resp.get("orderID"):
                        continue

                    err_msg=resp.get("errorMsg") or resp.get("error") or resp.get("message") or "Order rejected"
                    break

                err_msg=str(resp)
                break

        except Exception as e:

            err_msg=str(e) if str(e) else repr(e)
            print("EXECUTION ERROR:",repr(e))

        if not err_msg:
            status="✅ <b>ARBITRAGE SECURED</b>"
        else:
            status=f"⚠️ <b>EXE ERROR</b>\n<code>{err_msg or 'Unknown execution failure'}</code>"

        await context.bot.send_message(q.message.chat_id,status,parse_mode="HTML")

# --- ENTRY ---
if __name__=="__main__":

    app=ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(handle_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),main_handler))

    print("Hydra Bot Active...")
    app.run_polling(drop_pending_updates=True)













































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































