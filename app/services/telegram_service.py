from telethon import TelegramClient
from telethon.sessions import StringSession
import logging

from app.core.config import settings

logger = logging.getLogger("gallery_vault")

class TelegramService:
    def __init__(self):
        # StringSession ব্যবহার করে Telethon Client ডিফাইন করা
        self.client = TelegramClient(
            StringSession(settings.TELEGRAM_STRING_SESSION),
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH
        )

    async def start(self):
        try:
            logger.info("Connecting to Telegram via StringSession...")
            await self.client.start()  # bot_token ছাড়াই স্টার্ট হবে
            
            me = await self.client.get_me()
            logger.info(f"Telegram User Client successfully connected as: {me.first_name} (@{me.username})")
        except Exception as e:
            logger.error(f"Failed to start Telegram Client: {str(e)}")
            raise e

    async def stop(self):
        await self.client.disconnect()
        logger.info("Telegram Client disconnected.")

telegram_service = TelegramService()
