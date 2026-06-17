from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

from rotkehlchen.mcp.backend import DEFAULT_BACKEND_TIMEOUT, DEFAULT_BACKEND_URL
from rotkehlchen.mcp.server import run_server

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m rotkehlchen.mcp')
    parser.add_argument(
        '--backend-url',
        default=os.environ.get('ROTKI_MCP_BACKEND_URL', DEFAULT_BACKEND_URL),
        help='rotki backend API URL. Defaults to %(default)s',
    )
    parser.add_argument(
        '--timeout',
        default=DEFAULT_BACKEND_TIMEOUT,
        type=int,
        help='Backend request timeout in seconds. Defaults to %(default)s',
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='MCP server log level. Defaults to %(default)s',
    )
    parser.add_argument(
        '--privacy-mode',
        default=os.environ.get('ROTKI_MCP_PRIVACY_MODE', 'balanced'),
        choices=('balanced', 'strict', 'raw'),
        help='Privacy mode for analytics tables. Defaults to %(default)s',
    )
    args = parser.parse_args(argv)
    run_server(
        backend_url=args.backend_url,
        timeout=args.timeout,
        log_level=args.log_level,
        privacy_mode=args.privacy_mode,
    )


if __name__ == '__main__':
    main()
