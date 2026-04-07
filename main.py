import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from database import init_db

# Imports commented out for now as requested
from handlers.group import group_router
from handlers.private import private_router

async def main():
    # Load environment variables
    load_dotenv()
    
    token = os.getenv("BOT_TOKEN")
    if not token:
        logging.error("BOT_TOKEN is missing in the generated environment.")
        return

    # Initialize bot using DefaultBotProperties for ParseMode.HTML
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Include routers (commented out for now)
    dp.include_router(group_router)
    dp.include_router(private_router)

    # Initialize the database (creates tables if they don't exist)
    logger = logging.getLogger(__name__)
    logger.info("Initializing database...")
    await init_db()

    # Start polling
    logger.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(main())
