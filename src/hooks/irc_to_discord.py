import asyncio
import collections
import io
import logging
import os
import re
import signal
import threading

import discord
import dotenv

from .base import Hook

BOT_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+):\s+`.+`$')

USER_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+)\s+(.+)$')

# Delay between two Discord connection attempts, doubled on each failure.
# discord.py already retries by itself once logged in, this only covers
# the cases where it gives up (no network at startup, login error...).
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 300

# Messages kept while Discord is unreachable, sent once it is back.
PENDING_MAX = 100

RPC_TIMEOUT = 300
DISCORD_MAX_LENGTH = 2000


def sanitize_message(content: str) -> str:
    return re.sub(r'(\n|\r)+', '. ', content.strip())


async def reply(message, text: str, attachment: str | None = None):
    """Reply to a message, attaching long output as a file."""
    if attachment is None:
        await message.author.send(text)
    elif len(text) + len(attachment) + 9 <= DISCORD_MAX_LENGTH:
        await message.author.send(f'{text}\n```\n{attachment}\n```')
    else:
        file = discord.File(io.BytesIO(attachment.encode()), 'output.txt')
        await message.author.send(text, file=file)


class DiscordClient(discord.Client):
    def __init__(self, hook, **kwargs):
        super().__init__(**kwargs)
        self.hook = hook
        self.owner = None

        self.cmd_matrix = {
            re.compile(r'!rpc\b'): self.rpc,
            re.compile(r'.*', re.DOTALL): self.forward_message,
        }

    def extract_author_message(self, message):
        if message.reference:
            match = BOT_MESSAGE_AUTHOR_REGEX.match(
                message.reference.resolved.content)
            to, content = match.groups()[0], message.content
        else:
            match = USER_MESSAGE_AUTHOR_REGEX.match(message.content)
            to, content = match.groups()

        return to, sanitize_message(content)

    async def rpc(self, message):
        script = message.content.removeprefix('!rpc').strip()
        if not script:
            await message.author.send('Usage: `!rpc <shell commands>`')
            return

        logging.info(f'Executing RPC command: {script}')
        # Login shell, so that PATH and the environment are the user's
        # ones and not the minimal ones of the systemd service.
        process = await asyncio.create_subprocess_exec(
            'bash', '-l', '-c', script,
            cwd=os.path.expanduser('~'),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), RPC_TIMEOUT)
            status = f'exit code: {process.returncode}'
        except asyncio.TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = await process.communicate()
            status = f'killed after {RPC_TIMEOUT}s'

        output = output.decode(errors='replace').rstrip()
        logging.info(f'RPC command {status}: {output[:500]!r}')
        await reply(message, status, output or None)

    async def forward_message(self, message):
        try:
            to, content = self.extract_author_message(message)
        except Exception as e:
            await message.author.send('Syntax error, you must send messages with `#<nickname> <message>`')
            logging.error(f'Error extracting author message: {e}')
            return
        logging.info(
            f'DISCORD({message.author}) > "{message.content}" > IRC({to})')
        itm = self.hook.itm
        connection = itm.connection
        if not connection.is_connected() or not itm.welcomed:
            await message.author.send('IRC connection is down, message not sent')
            return
        itm.reactor.loop.call_soon_threadsafe(connection.privmsg, to, content)

    async def on_ready(self):
        logging.info('Discord bot connected.')
        self.owner = await self.fetch_user(self.hook.owner_id)
        self.hook.notify(':bell: Discord connected')

    async def on_message(self, message):
        if message.author == self.user:
            return
        if message.author.id != self.hook.owner_id:
            logging.warning(f'Ignoring message from {message.author}')
            return

        for pattern, handler in self.cmd_matrix.items():
            if pattern.match(message.content):
                try:
                    await handler(message)
                except Exception as e:
                    logging.exception('Error handling message')
                    await message.author.send(f'Error handling message: {e}')
                return


class Hook(Hook):
    def __init__(self, itm):
        dotenv.load_dotenv()
        self.intents = discord.Intents(2048)
        self.intents.dm_messages = True
        self.intents.message_content = True
        self.token = os.getenv('DISCORD_BOT_TOKEN')
        self.owner_id = int(os.getenv('MY_DISCORD_ID'))
        self.itm = itm
        self.client = None
        self.loop = None
        self.thread = None
        self.stopping = False
        self.pending = collections.deque(maxlen=PENDING_MAX)
        self.flush_lock = None

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.flush_lock = asyncio.Lock()
        delay = RECONNECT_MIN_DELAY
        while not self.stopping:
            self.client = DiscordClient(self, intents=self.intents)
            try:
                async with self.client:
                    await self.client.start(self.token, reconnect=True)
            except discord.LoginFailure as e:
                logging.error(f'Discord login failed: {e}')
                delay = RECONNECT_MAX_DELAY
            except Exception as e:
                logging.error(f'Discord connection failed: {e!r}')
            if self.stopping:
                break
            if self.client.owner is not None:
                delay = RECONNECT_MIN_DELAY
            logging.info(f'Reconnecting to Discord in {delay} seconds...')
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _flush(self):
        """Send pending messages to the owner, in order, while possible."""
        async with self.flush_lock:
            while self.pending:
                client = self.client
                if client is None or client.owner is None or not client.is_ready():
                    return
                try:
                    await client.owner.send(self.pending[0])
                except Exception as e:
                    logging.error(f'Failed to send Discord message: {e!r}')
                    return
                self.pending.popleft()

    def notify(self, text: str):
        """Send a message to the owner, from any thread."""
        self.pending.append(text)
        loop = self.loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._flush(), loop)

    def start(self):
        self.thread = threading.Thread(
            target=self._run, name='discord', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping = True
        loop, client = self.loop, self.client
        if loop is not None and client is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(client.close(), loop)
            try:
                future.result(timeout=10)
            except Exception as e:
                logging.error(f'Error closing discord client: {e!r}')
        if self.thread is not None:
            self.thread.join(timeout=10)
            self.thread = None

    def on_welcome(self, connection, event):
        self.notify(f':white_check_mark: IRC connected as {connection.get_nickname()}')

    def on_disconnect(self, connection, event):
        self.notify(':warning: IRC disconnected, reconnecting...')

    def on_msg(self, connection, event):
        if event.target != connection.get_nickname():
            return

        sender = event.source.nick
        message = event.arguments[0]
        logging.info(f'IRC({sender}) > "{message}"')
        self.notify(f"#{sender}: `{message}`")
