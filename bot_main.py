import os
import asyncio
import shadow_engine # File 1
import polymarket_clob # File 2
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Patching the engine to handle heartbeat/Dual-TX
async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    
    # Start Heartbeat (File 1)
    asyncio.create_task(shadow_engine.heartbeat())
    
    print(f"Shadow Bot Dual-TX Active.")
    app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
