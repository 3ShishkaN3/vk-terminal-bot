"""Configuration module for VK Terminal Bot."""
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os

# Загружаем переменные окружения из .env файла
load_dotenv()

@dataclass(frozen=True)
class VKConfig:
    """VK Bot configuration settings."""
    token: str
    peer_id: int
    bot_tag: str
    group_id: int

@dataclass(frozen=True)
class TerminalConfig:
    """Terminal configuration settings."""
    shell_path: str
    working_dir: str
    max_output_length: int
    command_timeout: int

@dataclass(frozen=True)
class LogConfig:
    """Logging configuration settings."""
    level: str
    file: Path

@dataclass(frozen=True)
class Config:
    """Main configuration class."""
    vk: VKConfig
    terminal: TerminalConfig
    log: LogConfig

def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        vk=VKConfig(
            token=os.getenv('VK_TOKEN'),
            peer_id=int(os.getenv('PEER_ID')),
            bot_tag=os.getenv('BOT_TAG'),
            group_id=os.getenv('GROUP_ID')
        ),
        terminal=TerminalConfig(
            shell_path=str(os.getenv('SHELL_PATH')),
            working_dir=str(os.getenv('WORKING_DIR')),
            max_output_length=int(os.getenv('MAX_OUTPUT_LENGTH')),
            command_timeout=int(os.getenv('COMMAND_TIMEOUT'))
        ),
        log=LogConfig(
            level=os.getenv('LOG_LEVEL'),
            file=Path(os.getenv('LOG_FILE'))
        )
    )

# Создаем глобальный объект конфигурации
config = load_config()