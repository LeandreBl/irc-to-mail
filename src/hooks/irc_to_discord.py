import logging
import os

import discord
import asyncio
import dotenv
import threading
import re
import tempfile

from .base import Hook

BOT_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+):\s+`.+`$')

USER_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+)\s+(.+)$')

def sanitize_message(content: str) -> str:
    return re.sub(r'(\n|\r)+', '. ', content.strip())

class DiscordClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_user_id = os.getenv('MY_DISCORD_ID')
        self.me = None
        self.itm = kwargs['itm']

        self.cmd_matrix = {
            re.compile(r'!rpc'): self.rpc,
            re.compile(r'#.*'): self.forward_message,
            re.compile(r'.*'): self.forward_message
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
        logging.info(f'Executing RPC command: {message.content}')

        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.sh') as tmp_file:
                tmp_file.write(message.content[len('!rpc '):])
                tmp_file.flush()
                tmp_file.close()
                await asyncio.create_subprocess_shell(f'chmod +x {tmp_file.name} && {tmp_file.name}', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                process = await asyncio.create_subprocess_shell(f'{tmp_file.name}', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()

                logs = stdout.decode()
                errors = stderr.decode()

                res = f'exit code: {process.returncode}'

                if logs:
                    res += f'\n```\n{logs}\n```'

                if errors:
                    res += f'\n```\n{errors}\n```'

                await message.author.send(res)

                logging.info(f'RPC command output: {logs}\n{errors}')

                os.remove(tmp_file.name)

    async def forward_message(self, message):
        try:
            to, content = self.extract_author_message(message)
        except Exception as e:
            await message.author.send('Syntax error, you must send messages with `#<nickname> <message>`')
            logging.error(f'Error extracting author message: {e}')
            return
        logging.info(
            f'DISCORD({message.author}) > "{message.content}" > IRC({to})')
        self.itm.connection.reactor.loop.call_soon(
            self.itm.connection.privmsg,
            to,
            content
        )

    async def on_ready(self):
        logging.info('Discord bot started.')
        self.me = await self.fetch_user(self.default_user_id)
        if not self.me:
            logging.error(f'User {self.default_user_id} not found')
        else:
            await self.me.send(':bell: Reconnected')

    async def on_message(self, message):
        if message.author == self.user:
            return

        for pattern, handler in self.cmd_matrix.items():
            if pattern.match(message.content):
                try:
                    await handler(message)
                except Exception as e:
                    logging.error(f'Error handling message: {e}')
                    await message.author.send(f'Error handling message: {e}')
                    return


class Hook(Hook):
    def __init__(self, itm):
        dotenv.load_dotenv()
        self.intents = discord.Intents(2048)
        self.intents.dm_messages = True
        self.intents.message_content = True
        self.client = None
        self.token = os.getenv('DISCORD_BOT_TOKEN')
        self.thread = threading.Thread(target=self._run)
        self.itm = itm

    def _run(self):
        logging.info("Starting discord asyncio.")
        self.client.run(self.token)

    def start(self):
        self.client = DiscordClient(intents=self.intents, itm=self.itm)
        self.thread.start()

    def stop(self):
        asyncio.run(self.client.close())
        self.thread.join()
        self.thread = None

    def on_msg(self, connection, event):
        if event.target != connection.get_nickname():
            return

        sender = event.source.nick
        message = event.arguments[0]
        logging.info(f'IRC({sender}) > "{message}"')
        asyncio.run_coroutine_threadsafe(
            self.client.me.send(f"#{sender}: `{message}`"),
            self.client.loop
        )
