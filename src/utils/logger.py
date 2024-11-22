"""Logging configuration module."""
import logging
from pathlib import Path
from typing import Optional

from src.config import config

_logger: Optional[logging.Logger] = None

def get_logger(name: str) -> logging.Logger:
    """Get configured logger instance.
    
    Args:
        name: Logger name
        
    Returns:
        Configured logger instance
    """
    global _logger
    
    if _logger is None:
        # Создаем директорию для логов, если её нет
        log_path = Path(config.log.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Настраиваем формат
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Настраиваем вывод в файл
        file_handler = logging.FileHandler(config.log.file)
        file_handler.setFormatter(formatter)
        
        # Настраиваем вывод в консоль
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Создаем и настраиваем логгер
        _logger = logging.getLogger(name)
        _logger.setLevel(config.log.level)
        _logger.addHandler(file_handler)
        _logger.addHandler(console_handler)
    
    return _logger