from src.ith import Ith
import argparse
import sys
import re
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


IRC_ADDRESS_REG = r'(.*)[:|\/]([0-9]+)'


def irc_address(string: str):
    m = re.match(IRC_ADDRESS_REG, string)
    if m is None:
        raise ValueError

    return (m.group(1), int(m.group(2)))


def str_to_bool(string: str):
    string = string.lower()
    if string in ('y', 'yes', 'true', 'on'):
        return True
    elif string in ('n', 'no', 'false', 'off'):
        return False

    raise ValueError


def main(argv: list[str]):
    parser = argparse.ArgumentParser(
        prog='itm',
        description='Forward irc messages to emails',
    )

    parser.add_argument('-n', '--nickname',
                        help='IRC nickname to use', type=str, required=True)
    parser.add_argument(
        '-a', '--address', help='IRC server address <addr:port> or <addr/port>', type=irc_address, required=True)
    parser.add_argument('-u', '--username', type=str, required=True)
    parser.add_argument('-p', '--password', type=str, required=True)
    parser.add_argument('--level', default='INFO', type=str, choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Set the logging level')
    parser.add_argument('--ssl', default=True,
                        type=str_to_bool, required=False)

    args = parser.parse_args(argv)

    itm = Ith(
        username=args.username,
        password=args.password,
        nickname=args.nickname,
        address=args.address[0],
        port=args.address[1],
        ssl=args.ssl,
    )

    itm.connect()

    itm.run()


if __name__ == '__main__':
    main(sys.argv[1:])
