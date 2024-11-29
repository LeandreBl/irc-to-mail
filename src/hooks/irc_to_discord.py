import logging
import os

import discord
import asyncio
import dotenv
import threading
import re

from .base import Hook

BOT_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+):\s+`.+`$')

USER_MESSAGE_AUTHOR_REGEX = re.compile(r'^#(\S+)\s+(.+)$')


class DiscordClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_user_id = os.getenv('MY_DISCORD_ID')
        self.me = None
        self.itm = kwargs['itm']

    def extract_author_message(self, message):
        if message.reference:
            match = BOT_MESSAGE_AUTHOR_REGEX.match(
                message.reference.resolved.content)
            to, content = match.groups()[0], message.content
        else:
            match = USER_MESSAGE_AUTHOR_REGEX.match(message.content)
            to, content = match.groups()

        return to, content

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

        try:
            to, content = self.extract_author_message(message)
        except:
            await message.author.send('Syntax error, you must send messages with `#<nickname> <message>`')
            return
        logging.info(
            f'DISCORD({message.author}) > "{message.content}" > IRC({to})')
        self.itm.connection.reactor.loop.call_soon(
            self.itm.connection.privmsg,
            to,
            content
        )


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
        self.client.close()
        self.thread.join()
        self.thread = None

    def on_msg(self, connection, event):
        sender = event.source.nick
        message = event.arguments[0]
        logging.info(f'IRC({sender}) > "{message}"')
        asyncio.run_coroutine_threadsafe(
            self.client.me.send(f"#{sender}: `{message}`"),
            self.client.loop
        )
