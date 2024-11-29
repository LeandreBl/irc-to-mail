import sys
import logging
import time
import os

import irc.client
import irc
import irc.client_aio
import irc.connection

from .dynamic_loader import DynamicLoader
from .hooks.base import Hook


class Ith(irc.client_aio.AioSimpleIRCClient):
    def __init__(self, username: str, password: str, nickname: str, address: str, port: int, ssl: bool = False, hooks_directory: str = os.path.join(os.path.dirname(__file__), 'hooks')):
        irc.client_aio.AioSimpleIRCClient.__init__(self)
        self.username = username
        self.password = password
        self.nickname = nickname
        self.address = address
        self.port = port
        self.ssl = ssl
        self.connected_at = None
        self.forwarding = False
        self.hooks: dict[Hook] = {}
        self.hooks_directory = hooks_directory
        self.dynamic_loader = DynamicLoader()
        self._load_hooks()

    def on_error(self, connection, event):
        logging.error(f'Error: {event.arguments[0]}')

    def on_welcome(self, connection, event):
        logging.info(
            f'Connected to {self.address}:{self.port} as {self.nickname}')

    def on_privmsg(self, connection, event):
        if not self.forwarding:
            diff = time.time() - self.connected_at
            if diff < 2:
                return
            self.forwarding = True

        for hook in self.hooks.values():
            hook.on_msg(connection, event)

    def on_disconnect(self, connection, event):
        logging.info(f'Disconnected from {self.address}:{self.port}')
        sys.exit(0)

    def connect(self):
        if self.ssl:
            logging.info('Using SSL for connection')
            ssl_factory = irc.connection.AioFactory(ssl=True)
        else:
            logging.info('Connecting without SSL')
            ssl_factory = None
        try:
            self.connection = self.reactor.loop.run_until_complete(self.reactor.server().connect(self.address, self.port, self.nickname,
                                                                                                 password=self.password, username=self.username,
                                                                                                 connect_factory=ssl_factory))
            self.connected_at = time.time()
            self.forwarding = False
        except irc.client.ServerConnectionError as e:
            logging.error(f'Failed to connect: {e}')
            sys.exit(1)

    def _load_hooks(self):
        for filename in os.listdir(self.hooks_directory):
            if filename.endswith('.py') and not filename.startswith('_') and not filename == 'base.py':
                module_name = filename[:-3]
                module = self.dynamic_loader.load(
                    module_name, os.path.join(self.hooks_directory, filename))
                self.hooks[module_name] = module.Hook(self)
                logging.info(f'Hook "{module_name}" loaded successfully')

    def run(self):
        for name, hook in self.hooks.items():
            logging.info(f'Starting hook: {name}')
            hook.start()
        try:
            self.start()
        except Exception as e:
            logging.exception(e)
        finally:
            self.connection.disconnect()
            for name, hook in self.hooks.items():
                logging.info(f'Stopping hook: {name}')
                hook.stop()
