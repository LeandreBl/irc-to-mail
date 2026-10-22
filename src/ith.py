import asyncio
import logging
import os
import time

import irc.client
import irc.client_aio
import irc.connection

from .dynamic_loader import DynamicLoader
from .hooks.base import Hook

# Delay between two connection attempts, doubled on each failure.
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 300
# TCP/SSL connection and IRC registration timeouts.
CONNECT_TIMEOUT = 30
REGISTER_TIMEOUT = 60
# Without any data from the server for PING_AFTER seconds, send a PING;
# without any data for TIMEOUT_AFTER seconds, consider the link dead.
WATCHDOG_INTERVAL = 30
PING_AFTER = 120
TIMEOUT_AFTER = 240
# Messages received right after connecting are replayed history, skip them.
FORWARD_GRACE = 2


class Ith(irc.client_aio.AioSimpleIRCClient):
    def __init__(self, username: str, password: str, nickname: str, address: str, port: int, ssl: bool = False, hooks_directory: str = os.path.join(os.path.dirname(__file__), 'hooks')):
        asyncio.set_event_loop(asyncio.new_event_loop())
        super().__init__()
        self.username = username
        self.password = password
        self.nickname = nickname
        self.address = address
        self.port = port
        self.ssl = ssl
        self.connected_at = 0.0
        self.last_activity = 0.0
        self.welcomed = False
        self.forwarding = False
        self._watchdog_handle = None
        self.hooks: dict[str, Hook] = {}
        self.hooks_directory = hooks_directory
        self.dynamic_loader = DynamicLoader()
        self._load_hooks()

    def _dispatcher(self, connection, event):
        # Ignore late events from a previous, dropped connection.
        if connection is not self.connection:
            return
        super()._dispatcher(connection, event)

    def _call_hooks(self, method: str, *args):
        for name, hook in self.hooks.items():
            try:
                getattr(hook, method)(*args)
            except Exception:
                logging.exception(f'Hook "{name}" failed in {method}')

    def on_all_raw_messages(self, connection, event):
        self.last_activity = time.monotonic()

    def on_error(self, connection, event):
        logging.error(f'Error: {event.target}')

    def on_nicknameinuse(self, connection, event):
        # Once registered, this is a failed attempt to regain our nickname
        # from the watchdog: keep the current one.
        if self.welcomed:
            return
        nickname = connection.get_nickname() + '_'
        logging.warning(f'Nickname in use, trying {nickname}')
        connection.nick(nickname)

    def on_welcome(self, connection, event):
        self.welcomed = True
        logging.info(
            f'Connected to {self.address}:{self.port} as {connection.get_nickname()}')
        self._call_hooks('on_welcome', connection, event)

    def on_privmsg(self, connection, event):
        self._on_msg(connection, event)

    def on_pubmsg(self, connection, event):
        self._on_msg(connection, event)

    def _on_msg(self, connection, event):
        if not self.forwarding:
            if time.time() - self.connected_at < FORWARD_GRACE:
                return
            self.forwarding = True
        self._call_hooks('on_msg', connection, event)

    def on_disconnect(self, connection, event):
        logging.info(f'Disconnected from {self.address}:{self.port}')
        if self.welcomed:
            self._call_hooks('on_disconnect', connection, event)
        # Stop the loop so that _run_session() returns and we reconnect.
        if self.reactor.loop.is_running():
            self.reactor.loop.stop()

    def _watchdog(self):
        self._watchdog_handle = self.reactor.loop.call_later(
            WATCHDOG_INTERVAL, self._watchdog)
        connection = self.connection
        now = time.monotonic()

        if not self.welcomed:
            if now - self.connected_at_monotonic > REGISTER_TIMEOUT:
                logging.error('IRC registration timed out')
                connection.disconnect('Registration timeout')
            return

        idle = now - self.last_activity
        if idle > TIMEOUT_AFTER:
            logging.error(f'No data from IRC server for {int(idle)}s')
            connection.disconnect('Ping timeout')
            return
        if idle > PING_AFTER:
            connection.ping(connection.get_server_name())

        if connection.get_nickname() != self.nickname:
            connection.nick(self.nickname)

    def _connect(self) -> bool:
        # Use a fresh connection each time: the transport of the previous
        # one may still report its loss later and must not affect this one.
        old = self.connection
        self.reactor._remove_connection(old)
        self.connection = self.reactor.server()

        logging.info(f'Connecting to {self.address}:{self.port}'
                     f'{" with SSL" if self.ssl else ""}')
        factory = irc.connection.AioFactory(ssl=self.ssl or None)
        connect = self.connection.connect(self.address, self.port, self.nickname,
                                          password=self.password, username=self.username,
                                          connect_factory=factory)
        try:
            self.reactor.loop.run_until_complete(
                asyncio.wait_for(connect, CONNECT_TIMEOUT))
        except (OSError, asyncio.TimeoutError, irc.client.ServerConnectionError) as e:
            logging.error(f'Failed to connect: {e!r}')
            return False
        return True

    def _run_session(self):
        self.welcomed = False
        self.forwarding = False
        self.connected_at = time.time()
        self.connected_at_monotonic = self.last_activity = time.monotonic()
        self._watchdog_handle = self.reactor.loop.call_later(
            WATCHDOG_INTERVAL, self._watchdog)
        try:
            self.reactor.process_forever()
        finally:
            self._watchdog_handle.cancel()
            if self.connection.is_connected():
                self.connection.disconnect('Reconnecting')
            transport = getattr(self.connection, 'transport', None)
            if transport is not None:
                transport.abort()

    def _load_hooks(self):
        for filename in os.listdir(self.hooks_directory):
            if filename.endswith('.py') and not filename.startswith('_') and not filename == 'base.py':
                module_name = filename[:-3]
                module = self.dynamic_loader.load(
                    module_name, os.path.join(self.hooks_directory, filename))
                self.hooks[module_name] = module.Hook(self)
                logging.info(f'Hook "{module_name}" loaded successfully')

    def run_forever(self):
        """Connect to the IRC server and reconnect whenever the link is lost."""
        self._call_hooks('start')
        delay = RECONNECT_MIN_DELAY
        try:
            while True:
                if self._connect():
                    try:
                        self._run_session()
                    except Exception:
                        logging.exception('IRC session failed')
                    if self.welcomed:
                        delay = RECONNECT_MIN_DELAY
                logging.info(f'Reconnecting to IRC in {delay} seconds...')
                time.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
        finally:
            self._call_hooks('stop')
