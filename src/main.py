"""Main entry point for VK Terminal Bot."""
import asyncio
import signal
from typing import Optional

from src.bot.vk_bot import VKTerminalBot
from src.utils.logger import get_logger

logger = get_logger(__name__)

class BotApplication:
    """Main application class."""
    
    def __init__(self):
        """Initialize application."""
        self.bot = VKTerminalBot()
        self._shutdown_event: Optional[asyncio.Event] = None
    
    async def start(self) -> None:
        """Start the application."""
        self._shutdown_event = asyncio.Event()
        
        # Настраиваем обработчики сигналов
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)
        
        try:
            # Запускаем бота
            bot_task = asyncio.create_task(self.bot.start())
            
            # Ждем сигнала завершения
            await self._shutdown_event.wait()
            
            # Останавливаем бота
            await self.bot.stop()
            
            # Ждем завершения задачи бота
            await bot_task
            
        except Exception as e:
            logger.error(f"Application error: {e}")
            raise
        finally:
            # Очищаем обработчики сигналов
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)

def main() -> None:
    """Application entry point."""
    try:
        app = BotApplication()
        asyncio.run(app.start())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Application failed: {e}")
        raise

if __name__ == "__main__":
    main()