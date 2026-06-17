from typing import Final, Literal

LogLevel = Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
PrivacyMode = Literal['balanced', 'strict', 'raw']

SERVICE_NAME: Final = 'rotki MCP'
