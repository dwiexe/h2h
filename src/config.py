from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    result = []
    for part in raw.replace(',', ' ').split():
        try:
            result.append(int(part))
        except ValueError:
            pass
    return result


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: os.getenv('TELEGRAM_BOT_TOKEN', ''))
    owner_telegram_ids: list[int] = field(default_factory=lambda: _parse_ids(os.getenv('OWNER_TELEGRAM_IDS', '')))

    # H2H.id
    h2h_member_id: str = field(default_factory=lambda: os.getenv('H2H_MEMBER_ID', ''))
    h2h_pin: str = field(default_factory=lambda: os.getenv('H2H_PIN', ''))
    h2h_password: str = field(default_factory=lambda: os.getenv('H2H_PASSWORD', ''))
    h2h_base_url: str = field(default_factory=lambda: os.getenv('H2H_BASE_URL', 'https://api.h2h.id/api/trx'))

    # Database
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', 'sqlite+aiosqlite:////opt/h2hbot/src/bot.db'))

    # App
    app_name: str = field(default_factory=lambda: os.getenv('APP_NAME', 'Bot PPOB H2H'))
    log_level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))

    # Intervals
    product_refresh_minutes: int = field(default_factory=lambda: int(os.getenv('PRODUCT_REFRESH_MINUTES', '30')))
    pending_check_minutes: int = field(default_factory=lambda: int(os.getenv('PENDING_CHECK_MINUTES', '2')))

    # Timeout
    request_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('REQUEST_TIMEOUT_SECONDS', '45')))
    connect_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('CONNECT_TIMEOUT_SECONDS', '15')))

    # Mode
    transaction_testing_mode: bool = field(default_factory=lambda: os.getenv('TRANSACTION_TESTING_MODE', 'false').lower() == 'true')

    # Webhook
    webhook_secret: str = field(default_factory=lambda: os.getenv('WEBHOOK_SECRET', ''))


settings = Settings()


def validate_settings() -> None:
    errors = []
    if not settings.telegram_bot_token:
        errors.append('TELEGRAM_BOT_TOKEN belum diisi')
    if not settings.owner_telegram_ids:
        errors.append('OWNER_TELEGRAM_IDS belum diisi')
    if not settings.h2h_member_id:
        errors.append('H2H_MEMBER_ID belum diisi')
    if not settings.h2h_pin:
        errors.append('H2H_PIN belum diisi')
    if not settings.h2h_password:
        errors.append('H2H_PASSWORD belum diisi')
    if errors:
        raise ValueError('Konfigurasi tidak lengkap:\n' + '\n'.join(f'  - {e}' for e in errors))
