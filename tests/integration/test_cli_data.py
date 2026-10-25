"""``qp data`` against a real (throwaway) database, with the TCMB server faked."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from apps.cli.common import console
from apps.cli.main import app
from qp.core import USD, SystemClock
from qp.data import service
from qp.data.connectors.http import HttpClient
from qp.data.service import market_data
from qp.platform import Database, load_settings

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "tcmb" / "bulletin.xml"


def fake_tcmb(request: httpx.Request) -> httpx.Response:
    name = request.url.path.rsplit("/", 1)[-1].removesuffix(".xml")
    day = f"{name[:2]}.{name[2:4]}.{name[4:]}".encode()
    return httpx.Response(200, content=FIXTURE.read_bytes().replace(b"16.10.2026", day))


def test_data_fetch_status_quality(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_LAKE__ROOT": str(tmp_path / "lake"),
        "QP_MANUAL__DIR": str(tmp_path / "manual"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(
        service,
        "http_client",
        lambda settings, min_interval_s: HttpClient(
            user_agent="t", min_interval_s=0, transport=httpx.MockTransport(fake_tcmb)
        ),
    )
    monkeypatch.setattr(console, "width", 200)
    runner = CliRunner()
    out = runner.invoke(
        app, ["data", "fetch", "tcmb", "--since", "2026-10-12", "--until", "2026-10-16"]
    )
    assert out.exit_code == 0, out.output
    assert "fetched 5" in out.output
    assert "wrote 45 new rows" in out.output
    status = runner.invoke(app, ["data", "status"])
    assert status.exit_code == 0, status.output
    assert "tcmb_fx_daily" in status.output
    assert "2026-10-16" in status.output
    assert "fetch:tcmb" in status.output
    quality = runner.invoke(app, ["data", "quality", "--since", "2026-10-12"])
    assert "missing_sessions" in quality.output
    assert "EUR forex_buying" in quality.output  # not in the fixture: stale -> fail
    assert quality.exit_code == 1
    no_key = runner.invoke(app, ["data", "fetch", "fred"])
    assert no_key.exit_code == 1
    assert "QP_SOURCES__FRED__API_KEY" in no_key.output
    # valuation reads the lake: USD/TRY from the fetched bulletin

    point = market_data(load_settings(config_dir=REPO / "configs", env_file=None)).fx_try(
        USD, date(2026, 10, 18), as_of=SystemClock().now()
    )
    assert point is not None
    assert (point.valid_date, str(point.rate_try)) == (date(2026, 10, 16), "40.1")

    # the Data page shows the lake, the FX chart and the stored quality results
    at = AppTest.from_file(str(REPO / "apps" / "terminal" / "app.py"), default_timeout=30).run()
    at.switch_page("pages/data.py").run()
    assert not at.exception
    assert not at.error
    assert any("checks are not ok" in w.value for w in at.warning)  # EUR is missing
    assert "No TCMB rates" not in " ".join(i.value for i in at.info)
