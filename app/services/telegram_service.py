import asyncio
import os
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from app.core.config import settings
from app.core.logger import logger

class TelegramStorageService:
    def __init__(self):
        self.client: TelegramClient = None

    async def start(self):
        session_path = os.path.join(settings.TEMP_STORAGE_PATH, "telegram_session")
        self.client = TelegramClient(session_path, settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
        await self.client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
        logger.info("Telegram Client connected successfully.")

    async def stop(self):
        if self.client and self.client.is_connected():
            await self.client.disconnect()

    async def upload_file(self, file_path: str, caption: str) -> int:
        retries = 3
        while retries > 0:
            try:
                message = await self.client.send_file(
                    entity=settings.TELEGRAM_CHANNEL_ID,
                    file=file_path,
                    caption=caption
                )
                return message.id
            except FloodWaitError as e:
                logger.warning(f"Telegram FloodWait triggered. Waiting {e.seconds}s...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Telegram upload error: {str(e)}")
                retries -= 1
                if retries == 0:
                    raise e
                await asyncio.sleep(2)

    async def download_file_bytes(self, message_id: int, start: int = 0, end: Optional[int] = None) -> bytes:
        message = await self.client.get_messages(settings.TELEGRAM_CHANNEL_ID, ids=message_id)
        if not message or not message.media:
            raise ValueError("Telegram message media not found.")
        
        buffer = await self.client.download_media(message, file=bytes)
        if end is not None:
            return buffer[start:end + 1]
        return buffer[start:]

telegram_service = TelegramStorageService()
