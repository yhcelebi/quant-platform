"""Health checks behind ``qp doctor``. Each check returns a CheckResult; none raise."""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from qp.platform.db import SCHEMAS, Database
from qp.platform.settings import Mode, Settings, live_trading_env_acknowledged


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def _guard(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    try:
        return fn()
    except Exception as exc:  # a health check must report, not crash
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        return CheckResult(name, Status.FAIL, f"{type(exc).__name__}: {first_line}")


def check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 12)
    return CheckResult(
        "python", Status.OK if ok else Status.FAIL, f"{v.major}.{v.minor}.{v.micro} (need 3.12)"
    )


def check_timezone() -> CheckResult:
    ZoneInfo("Europe/Istanbul")
    return CheckResult("tzdata", Status.OK, "Europe/Istanbul available")


def check_mode(settings: Settings) -> CheckResult:
    ack = live_trading_env_acknowledged()
    if settings.mode is Mode.LIVE:
        detail = "mode=live; QP_LIVE_TRADING " + ("set" if ack else "NOT set")
        detail += " (orders also need an approved_live promotion)"
        return CheckResult("mode", Status.WARN, detail)
    if ack:
        return CheckResult(
            "mode", Status.WARN, f"mode={settings.mode} but QP_LIVE_TRADING is set; unset it"
        )
    return CheckResult("mode", Status.OK, f"mode={settings.mode}")


def check_lake(settings: Settings) -> CheckResult:
    root = settings.lake.root
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, prefix=".doctor-", delete=True):
        pass
    return CheckResult("lake", Status.OK, f"{root.resolve()} writable")


def check_notifier(settings: Settings) -> CheckResult:
    if settings.telegram.enabled:
        return CheckResult("notifier", Status.OK, "telegram configured")
    return CheckResult("notifier", Status.WARN, "telegram not configured; notifications go to log")


def check_database(db: Database) -> CheckResult:
    db.ping()
    url = db.engine.url
    return CheckResult("database", Status.OK, f"reachable at {url.host}:{url.port}/{url.database}")


def check_schemas(db: Database) -> CheckResult:
    missing = sorted(set(SCHEMAS) - db.existing_schemas())
    if missing:
        return CheckResult("schemas", Status.FAIL, f"missing {missing}; run `make migrate`")
    return CheckResult("schemas", Status.OK, ", ".join(SCHEMAS))


def check_migrations(db: Database, alembic_ini: Path) -> CheckResult:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(alembic_ini)))
    heads = set(script.get_heads())
    with db.engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    if current == heads:
        return CheckResult("migrations", Status.OK, f"at head {sorted(heads)}")
    return CheckResult(
        "migrations", Status.FAIL, f"db at {sorted(current)}, head is {sorted(heads)}"
    )


def run_checks(settings: Settings, *, alembic_ini: Path | None = None) -> list[CheckResult]:
    results = [
        _guard("python", check_python),
        _guard("tzdata", check_timezone),
        _guard("mode", lambda: check_mode(settings)),
        _guard("lake", lambda: check_lake(settings)),
        _guard("notifier", lambda: check_notifier(settings)),
    ]
    db = Database.from_settings(settings.db)
    try:
        db_result = _guard("database", lambda: check_database(db))
        results.append(db_result)
        if db_result.status is Status.OK:
            results.append(_guard("schemas", lambda: check_schemas(db)))
            if alembic_ini is not None:
                results.append(_guard("migrations", lambda: check_migrations(db, alembic_ini)))
    finally:
        db.dispose()
    return results
