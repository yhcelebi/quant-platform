"""qp.platform — settings, logging, DB session, lake paths, notifier and health checks.

May import qp.stats and qp.core only. Built in phase 0.
"""

from qp.platform.bootstrap import init_platform
from qp.platform.db import SCHEMAS, Base, Database, create_db_engine, database_url
from qp.platform.lake import LakePaths
from qp.platform.logging import SecretRedactor, configure_logging, get_logger
from qp.platform.notifier import (
    LogNotifier,
    Notifier,
    NotifierError,
    TelegramNotifier,
    build_notifier,
)
from qp.platform.settings import (
    LIVE_TRADING_ACK,
    LIVE_TRADING_ENV_VAR,
    DbSettings,
    LakeSettings,
    LogSettings,
    Mode,
    Settings,
    TelegramSettings,
    live_trading_env_acknowledged,
    load_settings,
)

__all__ = [
    "LIVE_TRADING_ACK",
    "LIVE_TRADING_ENV_VAR",
    "SCHEMAS",
    "Base",
    "Database",
    "DbSettings",
    "LakePaths",
    "LakeSettings",
    "LogNotifier",
    "LogSettings",
    "Mode",
    "Notifier",
    "NotifierError",
    "SecretRedactor",
    "Settings",
    "TelegramNotifier",
    "TelegramSettings",
    "build_notifier",
    "configure_logging",
    "create_db_engine",
    "database_url",
    "get_logger",
    "init_platform",
    "live_trading_env_acknowledged",
    "load_settings",
]
