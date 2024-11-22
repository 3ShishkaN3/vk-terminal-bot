"""VK Bot module for terminal control."""
from typing import Optional, List
import asyncio
import re

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

from src.config import config
from src.terminal.terminal_manager import TerminalManager
from src.utils.logger import get_logger

logger = get_logger(__name__)

class VKTerminalBot:
    """VK Bot for terminal control."""
    
    def __init__(self):
        """Initialize VK bot."""
        self.vk_session = vk_api.VkApi(token=config.vk.token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, group_id=config.vk.group_id)
        self.terminal = TerminalManager()
        self._running = False
    
    @staticmethod
    def _parse_message(text: str) -> tuple[Optional[str], List[str]]:
        """Parse message to extract command and special keys.
        
        Args:
            text: Message text
            
        Returns:
            Tuple of (command, special_keys)
        """
        if not text.startswith(config.vk.bot_tag):
            return None, []
        
        # Убираем тег бота
        text = text[len(config.vk.bot_tag):].strip()
        
        # Ищем специальные клавиши (начинаются с ^)
        special_keys = re.findall(r'\^[^\s]+', text)
        
        # Убираем специальные клавиши из команды
        command = re.sub(r'\^[^\s]+', '', text).strip()
        
        return command or None, special_keys
    
    def _send_message(self, text: str) -> None:
        """Send message to VK chat.
        
        Args:
            text: Message text
        """
        # Разбиваем длинные сообщения
        while text:
            chunk = text[:config.terminal.max_output_length]
            text = text[config.terminal.max_output_length:]
            
            try:
                self.vk.messages.send(
                    peer_id=2000000000 + config.vk.peer_id,
                    message=chunk,
                    random_id=0
                )
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                
    async def _handle_message(self, text: str) -> None:
        """Handle incoming message.
        
        Args:
            text: Message text
        """
        
        command, special_keys = self._parse_message(text)

        if not command and not special_keys:
            return
            
        try:
            # Обрабатываем специальные клавиши
            for key in special_keys:
                self.terminal.handle_special_key(key)
                await asyncio.sleep(0.1)  # Небольшая задержка между клавишами
            
            # Выполняем команду, если она есть
            if command:
                cwd, output = await self.terminal.execute_command(command)
                
                # Форматируем ответ
                response = f"{cwd}> {command}\n{output}"
                self._send_message(response)
                
        except TimeoutError:
            self._send_message("⚠️ Превышено время выполнения команды")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            self._send_message(f"❌ Ошибка: {str(e)}")
    
    async def start(self) -> None:
        """Start the bot."""
        if self._running:
            return
            
        self._running = True
        
        try:
            # Запускаем терминал
            await self.terminal.start()
            
            logger.info("Bot started")
            self._send_message("✅ Бот запущен и готов к работе")
            
            # Основной цикл обработки сообщений
            while self._running:
                try:
                    for event in self.longpoll.listen():
                        if event.type == VkBotEventType.MESSAGE_NEW and event.chat_id == config.vk.peer_id:
                            await self._handle_message(event.message.text)
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    await asyncio.sleep(5)  # Пауза перед повторным подключением
                    
        except Exception as e:
            logger.error(f"Critical error: {e}")
            self._running = False
            raise
        finally:
            await self.stop()
    
    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        await self.terminal.stop()
        logger.info("Bot stopped")