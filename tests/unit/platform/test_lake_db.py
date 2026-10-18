from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from qp.core import QPValidationError
from qp.platform import LakePaths, database_url
from qp.platform.settings import DbSettings


def test_lake_paths(tmp_path: Path) -> None:
    lake = LakePaths(tmp_path)
    assert lake.raw("evds", "fx_rates", date(2026, 10, 18)) == (
        tmp_path / "raw" / "evds" / "fx_rates" / "fetched_date=2026-10-18"
    )
    assert lake.clean("prices_eod") == tmp_path / "clean" / "prices_eod"
    assert lake.derived("adj_prices") == tmp_path / "derived" / "adj_prices"
    assert lake.results("backtests", "bt-001") == tmp_path / "results" / "backtests" / "bt-001"
    lake.ensure_zones()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clean", "derived", "raw", "results"]


@pytest.mark.parametrize("bad", ["../etc", "EVDS", "a/b", "", ".hidden"])
def test_lake_rejects_unsafe_names(tmp_path: Path, bad: str) -> None:
    with pytest.raises(QPValidationError):
        LakePaths(tmp_path).clean(bad)


def test_lake_rejects_bad_kind_and_date(tmp_path: Path) -> None:
    with pytest.raises(QPValidationError):
        LakePaths(tmp_path).results("other", "x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LakePaths(tmp_path).raw("evds", "fx", "2026-10-18")  # type: ignore[arg-type]


def test_database_url_hides_password() -> None:
    url = database_url(DbSettings(host="h", port=5433, password=SecretStr("pw-123")))
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "pw-123"
    assert "pw-123" not in str(url)
    assert url.port == 5433
