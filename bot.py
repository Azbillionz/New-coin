import asyncio
import logging
import os
import json
import base64
from datetime import datetime
from typing import Dict, Any
import httpx
from solana.rpc.async_api import AsyncClient
from solana.rpc.websocket_api import connect
from solders.pubkey import Pubkey
from solders.signature import Signature
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command

# ─── Load Environment Variables ─────────────────────
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # From BotFather
YOUR_ID = int(os.getenv("YOUR_TELEGRAM_ID"))  # Your Telegram ID
RPC = os.getenv("SOLANA_RPC")  # Solana RPC URL
WSS = os.getenv("SOLANA_WSS")  # Solana WebSocket URL

JUPITER = "https://quote-api.jup.ag/v6"
PUMP_PROG_STR = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_PROG = Pubkey.from_string(PUMP_PROG_STR)

bot = Bot(TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

seen_tokens: set = set()
client: AsyncClient = None
require_socials: bool = True  # /socials toggle

# Discriminator for Create instruction (first 8 bytes)
CREATE_DISC = bytes([24, 30, 200, 40, 5, 28, 7, 119])

# ─── Utility Functions ─────────────────────────────
async def fetch_metadata(uri: str) -> Dict:
    """Fetch JSON metadata from Arweave/IPFS URI"""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(uri)
            return r.json() if r.status_code == 200 else {}
    except:
        return {}

async def extract_socials(metadata: Dict) -> Dict[str, str]:
    """Pull TG/Discord from metadata"""
    ext = metadata.get("external_url", "") or ""
    tg = ""
    discord = ""
    desc = metadata.get("description", "").lower()
    if "t.me" in desc or "telegram" in desc:
        tg = next((w for w in desc.split() if "t.me" in w), "")
    if "discord" in desc:
        discord = next((w for w in desc.split() if "discord.gg" in w or "discord.com" in w), "")
    extensions = metadata.get("extensions", {})
    tg = extensions.get("telegram") or tg
    discord = extensions.get("discord") or discord
    return {"tg": tg, "discord": discord}

async def get_dex_info(ca: str) -> Dict[str, Any]:
    """Get basic info from DexScreener"""
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
            data = r.json()
            if data.get("pairs") and data["pairs"]:
                pair = data["pairs"][0]
                socials = pair.get("info", {}).get("socials", [])
                tg = next((s["url"] for s in socials if "telegram" in s.get("type", "")), "")
                dc = next((s["url"] for s in socials if "discord" in s.get("type", "")), "")
                return {
                    "mc": pair.get("fdv", 0),
                    "holders": pair.get("holderCount", "?"),
                    "tg": tg,
                    "discord": dc,
                    "price": pair.get("priceUsd", 0)
                }
    except:
        pass
    return {}

async def dev_check(dev: str) -> Dict:
    try:
        bal = await client.get_balance(Pubkey.from_string(dev))
        sol = bal.value / 1_000_000_000
        return {"sol": sol, "rich": sol > 5}
    except:
        return {"sol": 0, "rich": False}

async def buy_keyboard(ca: str) -> InlineKeyboardMarkup:
    url = f"https://jup.ag/tokens/{ca}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("💰 Buy on Jupiter 🚀", url=url)
    ]])

# ─── Telegram Commands ─────────────────────────────
@dp.message(CommandStart())
async def start(msg: types.Message):
    if msg.from_user.id != YOUR_ID: return
    await msg.answer("🔥 Pump.fun Tracker ON! Only coins WITH TG/Discord.\n/socials toggle | /status")

@dp.message(Command("status"))
async def status(msg: types.Message):
    if msg.from_user.id != YOUR_ID: return
    filt = "TG/Discord required" if require_socials else "All"
    await msg.answer(f"Seen: {len(seen_tokens)}\nFilter: {filt}")

@dp.message(Command("socials"))
async def toggle(msg: types.Message):
    global require_socials
    if msg.from_user.id != YOUR_ID: return
    require_socials = not require_socials
    await msg.answer(f"Social filter: {'ON (only TG/Disc)' if require_socials else 'OFF'}")

# ─── Handle New Token ─────────────────────────────
async def handle_create(logs: list, sig: str):
    program_data_logs = [l for l in logs if "Program data: " in l]
    if not program_data_logs: return

    data_b64 = program_data_logs[0].split("Program data: ")[1].strip()
    data_bytes = base64.b64decode(data_b64)
    if data_bytes[:8] != CREATE_DISC: return

    name = data_bytes[8:40].decode('utf-8').rstrip('\x00')
    symbol = data_bytes[40:72].decode('utf-8').rstrip('\x00')
    uri_len = int.from_bytes(data_bytes[72:76], 'little')
    uri = data_bytes[76:76+uri_len].decode('utf-8').rstrip('\x00')

    tx = await client.get_transaction(Signature.from_string(sig), encoding="jsonParsed")
    if not tx.value: return
    accounts = tx.value.transaction.transaction.message.account_keys
    dev = str(accounts[0].pubkey)
    ca = "PARSE_MINT_HERE"  # TODO: extract proper mint address

    if ca in seen_tokens: return
    seen_tokens.add(ca)

    meta = await fetch_metadata(uri)
    socials_meta = await extract_socials(meta)
    dex = await get_dex_info(ca)
    tg = dex.get("tg") or socials_meta["tg"]
    dc = dex.get("discord") or socials_meta["discord"]
    if require_socials and not (tg or dc): return

    dev_info = await dev_check(dev)
    rug = "🟢 Low risk" if dev_info["rich"] else "🔴 Watch dev"
    social_txt = f"{f'📱 TG: {tg}\n' if tg else ''}{f'💬 Discord: {dc}\n' if dc else ''}"

    msg_txt = f"""
🆕 **NEW PUMP.FUN COIN WITH SOCIALS!**
CA: `{ca}`
Name: {name} ({symbol})
Dev: `{dev}` (SOL: {dev_info['sol']:.2f} {'💰 Rich' if dev_info['rich'] else 'Poor'})
MC: ${dex.get('mc', '?'):,.0f} | Holders: {dex.get('holders', '?')}
Price: ${dex.get('price', 0):.8f}
{rug}
{social_txt}
Time: {datetime.now().strftime('%H:%M WAT')}
    """
    kb = await buy_keyboard(ca)
    await bot.send_message(YOUR_ID, msg_txt, reply_markup=kb, parse_mode="Markdown")

# ─── WebSocket Listener ─────────────────────────
async def ws_listener():
    global client
    client = AsyncClient(RPC)
    async with connect(WSS) as ws:
        await ws.logs_subscribe(filter_={"mentions": [PUMP_PROG_STR]})
        first_resp = await ws.recv()
        sub_id = first_resp.result if hasattr(first_resp, 'result') else None
        async for msg in ws:
            if hasattr(msg, 'result') and 'value' in msg.result:
                value = msg.result.value
                logs = value.logs
                sig = value.signature
                if "Instruction: Create" in ''.join(logs):
                    await handle_create(logs, sig)

# ─── Main ─────────────────────────────
async def main():
    asyncio.create_task(ws_listener())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
