import asyncio
import contextlib
import pty
import os
from pathlib import Path
from typing import Optional, Tuple
import fcntl
import termios
import struct
import signal
from dataclasses import dataclass
import re
from src.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass
class TerminalSize:
    """Terminal size configuration."""
    rows: int = 24
    cols: int = 80
    xpixel: int = 0
    ypixel: int = 0

class TerminalManager:
    """Manages terminal interactions."""
    
    def __init__(self, working_dir: Path = config.terminal.working_dir):
        """Initialize terminal manager."""
        self.working_dir = working_dir
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.shell_pid: Optional[int] = None
        self.prompt_markers = ['$ ', '# ', '> ']
        self._process_alive = False
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_attempts = 0
        self.MAX_RECONNECT_ATTEMPTS = 3
        self.READ_TIMEOUT = 0.1  # Timeout for individual read attempts
        self.MAX_READ_RETRIES = 50  # Maximum number of read retries
        
    async def _ensure_connection(self) -> bool:
        """Ensure terminal connection is alive, attempt reconnection if needed."""
        if self._process_alive and self.shell_pid:
            try:
                os.kill(self.shell_pid, 0)  # Check if process is alive
                return True
            except ProcessLookupError:
                self._process_alive = False
                logger.warning("Process not found, marking as not alive")

        async with self._reconnect_lock:
            if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                logger.error("Max reconnection attempts reached")
                return False
                
            try:
                logger.info(f"Attempting to reconnect terminal session (attempt {self._reconnect_attempts + 1})")
                self._reconnect_attempts += 1
                await self.stop()
                await asyncio.sleep(1)  # Wait before reconnecting
                await self.start()
                if self._process_alive:
                    self._reconnect_attempts = 0  # Reset counter on successful connection
                    logger.info("Successfully reconnected terminal session")
                    return True
                return False
            except Exception as e:
                logger.error(f"Failed to reconnect: {e}")
                return False
        
    async def start(self) -> None:
        """Start terminal session with improved error handling."""
        try:
            # Close any existing session
            await self.stop()

            self.master_fd, self.slave_fd = pty.openpty()
            logger.debug(f"Created pty: master_fd={self.master_fd}, slave_fd={self.slave_fd}")

            # Set terminal attributes
            attrs = termios.tcgetattr(self.master_fd)
            attrs[3] = attrs[3] & ~termios.ECHO  # Disable echo
            termios.tcsetattr(self.master_fd, termios.TCSANOW, attrs)

            self._set_terminal_size(TerminalSize())

            self.shell_pid = os.fork()
            logger.debug(f"Forked process: pid={self.shell_pid}")

            if self.shell_pid == 0:  # Child process
                try:
                    os.chdir(str(self.working_dir))
                    os.setsid()
                    os.dup2(self.slave_fd, 0)
                    os.dup2(self.slave_fd, 1)
                    os.dup2(self.slave_fd, 2)

                    if self.master_fd is not None:
                        os.close(self.master_fd)
                    if self.slave_fd is not None:
                        os.close(self.slave_fd)

                    # Set minimal environment
                    os.environ.clear()
                    os.environ |= {
                        'TERM': 'xterm',
                        'PATH': os.environ.get(
                            'PATH',
                            '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                        ),
                        'HOME': os.environ.get('HOME', ''),
                        'SHELL': config.terminal.shell_path,
                        'PS1': '$ ',
                        'LANG': 'en_US.UTF-8',
                    }


                    os.execv(config.terminal.shell_path, [config.terminal.shell_path, '--norc'])
                except Exception as e:
                    logger.error(f"Child process failed: {e}")
                    os._exit(1)

            else:  # Parent process
                if self.slave_fd is not None:
                    os.close(self.slave_fd)
                    self.slave_fd = None
                self._process_alive = True

                # Set non-blocking mode
                if self.master_fd is not None:
                    flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
                    fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                # Wait for shell initialization and verify it's working
                await asyncio.sleep(0.5)
                if not await self._verify_shell():
                    raise RuntimeError("Shell initialization failed")
                await self._clear_initial_output()

        except Exception as e:
            logger.error(f"Failed to start terminal: {e}")
            await self.stop()
            raise
            
    async def _verify_shell(self) -> bool:
        """Verify shell is responsive after initialization."""
        try:
            if self.master_fd is None or not self._process_alive:
                return False

            # Send a test command
            test_cmd = "echo 'shell_test'\n"
            os.write(self.master_fd, test_cmd.encode('utf-8'))

            # Try to read response
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < 5:  # 5 second timeout
                try:
                    response = os.read(self.master_fd, 1024).decode('utf-8')
                    if 'shell_test' in response:
                        return True
                except OSError:
                    await asyncio.sleep(0.1)
            return False
        except Exception as e:
            logger.error(f"Shell verification failed: {e}")
            return False
    
    async def _read_terminal_output(self, timeout: int) -> str:
        """Read terminal output with improved retry mechanism and buffering."""
        if not self.master_fd or not self._process_alive:
            raise RuntimeError("Terminal not started or process not alive")

        output_chunks = []
        start_time = asyncio.get_event_loop().time()
        last_read_time = start_time
        retry_count = 0
        buffer = bytearray()

        while True:
            current_time = asyncio.get_event_loop().time()

            if current_time - start_time > timeout:
                if not output_chunks:
                    raise TimeoutError(f"Command execution timeout ({timeout}s) with no data received")
                break

            if current_time - last_read_time > 2 and output_chunks:
                # We have some data and haven't received more in 2 seconds
                break

            try:
                if self.master_fd is None:
                    raise RuntimeError("Terminal file descriptor is None")

                if chunk := os.read(self.master_fd, 4096):
                    buffer.extend(chunk)
                    # Try to decode complete UTF-8 sequences
                    with contextlib.suppress(UnicodeDecodeError):
                        text = buffer.decode('utf-8')
                        output_chunks.append(text)
                        buffer.clear()
                        last_read_time = current_time
                        retry_count = 0  # Reset retry count on successful read
                else:
                    retry_count += 1
                    if retry_count >= self.MAX_READ_RETRIES:
                        if not output_chunks:
                            raise RuntimeError("No data received from terminal after maximum retries")
                        break
                    await asyncio.sleep(self.READ_TIMEOUT)

            except BlockingIOError:
                await asyncio.sleep(self.READ_TIMEOUT)
            except OSError as e:
                logger.error(f"Error reading from terminal: {e}")
                break

        # Handle any remaining buffer content
        if buffer:
            output_chunks.append(buffer.decode('utf-8', errors='replace'))

        return ''.join(output_chunks)

    async def execute_command(self, command: str, timeout: int = config.terminal.command_timeout) -> Tuple[str, str]:
        """
        Execute command with automatic reconnection and improved error handling.

        Parameters:
        command (str): The command to be executed.
        timeout (int, optional): The timeout for command execution. Defaults to the value specified in the config.

        Returns:
        Tuple[str, str]: A tuple containing the current working directory and the cleaned output of the command.

        Raises:
        RuntimeError: If the terminal connection cannot be established.
        OSError: If an error occurs while reading from or writing to the terminal.
        """
        for attempt in range(2):  # Try twice: initial attempt + one retry
            try:
                if not await self._ensure_connection():
                    raise RuntimeError("Failed to establish terminal connection")

                logger.debug(f"Executing command (attempt {attempt + 1}): {command}")

                # Clear output before executing command
                await self._clear_initial_output()

                if self.master_fd is None:
                    raise RuntimeError("Terminal file descriptor is None")

                self._send_command_to_terminal(command)
                output = await self._retrieve_command_output(timeout)

                # Get current directory
                cwd = await self._get_current_directory()

                # Clean output
                cleaned_output = self._clean_output(command, output)
                self._validate_cleaned_output(cleaned_output, output)

                return cwd, cleaned_output

            except (OSError, RuntimeError) as e:
                logger.error(f"Command execution failed (attempt {attempt + 1}): {e}")
                if attempt == 0:  # Only attempt reconnection on the first failure
                    await asyncio.sleep(1)  # Wait before retrying
                    continue
                raise

    def _send_command_to_terminal(self, command: str) -> None:
        """Send the command to the terminal."""
        cmd_bytes = (command + "\n").encode('utf-8')
        written = os.write(self.master_fd, cmd_bytes)
        if written != len(cmd_bytes):
            raise RuntimeError(f"Failed to write complete command: wrote {written} of {len(cmd_bytes)} bytes")
        logger.debug(f"Wrote {written} bytes to terminal")

    async def _retrieve_command_output(self, timeout: int) -> str:
        """Read command output with improved error handling."""
        try:
            return await self._read_terminal_output(timeout)
        except TimeoutError:
            logger.error(f"Command timed out after {timeout} seconds")
            raise

    def _validate_cleaned_output(self, cleaned: str, output: str) -> None:
        """Validate cleaned output and log any potential issues."""
        if not cleaned and output:  # We have output but nothing after cleaning
            logger.warning("Output was completely cleaned away, might indicate an issue")
        logger.debug(f"Cleaned output: {repr(cleaned)}")


    def _set_terminal_size(self, size: TerminalSize) -> None:
        """Set terminal size using TIOCSWINSZ."""
        if self.master_fd is not None:
            winsize = struct.pack('HHHH', size.rows, size.cols, size.xpixel, size.ypixel)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
    
    async def _clear_initial_output(self) -> None:
        """Clear initial shell output."""
        if not self.master_fd or not self._process_alive:
            return

        try:
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < 1:  # 1 second timeout
                try:
                    chunk = os.read(self.master_fd, 4096)
                    if not chunk:
                        break
                except OSError:
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error clearing output: {e}")
    
    async def stop(self) -> None:
        """Stop the terminal process."""
        if self.shell_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.shell_pid, signal.SIGKILL)
                
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError as e:
                logger.warning(f"Failed to close master_fd: {e}")
                
        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except OSError as e:
                logger.warning(f"Failed to close slave_fd: {e}")
                
        self.master_fd = None
        self.slave_fd = None
        self.shell_pid = None
        self._process_alive = False
        logger.info("Terminal session stopped")
    
    async def _get_current_directory(self) -> str:
        """Get current working directory in the terminal."""
        # Using a simplified approach to avoid recursion
        if self.master_fd is not None:
            cmd = "pwd\n"
            os.write(self.master_fd, cmd.encode('utf-8'))
            try:
                output = await self._read_terminal_output(5)  # Short timeout for pwd
                if cleaned := self._clean_output(cmd, output):
                    return cleaned.strip()
            except Exception as e:
                logger.error(f"Error getting current directory: {e}")
        return str(self.working_dir)
    
    def _clean_output(self, command: str, output: str) -> str:
        """Clean command output from prompts and other artifacts."""
        if not output:
            return ""

        # Удаление управляющих последовательностей терминала
        ansi_escape = re.compile(r'(?:\x1B[@-Z\\-_]|\x1B\[?.*?[ -/]*[@-~])')
        cleaned_output = ansi_escape.sub('', output)

        lines = cleaned_output.splitlines()

        # Remove the command line itself
        if lines and command in lines[0]:
            lines = lines[1:]

        # Filter out prompt lines and empty lines
        cleaned = []
        for line in lines:
            line = line.strip()
            if line and all(marker not in line for marker in self.prompt_markers):
                cleaned.append(line)

        return '\n'.join(cleaned)