"""News end to end on a throwaway database: fetch (fake transport), dedup, entities, tags,
as-of queries and holding-news alerts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from apps.cli.main import app
from qp.alerts.service import run_news_alerts
from qp.core.clock import ManualClock
from qp.data.connectors.http import HttpClient
from qp.data.lake import RawStore
from qp.news.service import fetch_feeds
from qp.news.store import query
from qp.platform import Database, configure_logging, load_settings
from qp.platform.lake import LakePaths
from qp.platform.notifier import Level

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
EX = REPO / "docs" / "examples"
FIX = REPO / "tests" / "fixtures" / "news"
NOW = datetime(2025, 1, 14, 12, tzinfo=UTC)
HOLDING_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>THYAO temett\xc3\xbc da\xc4\x9f\xc4\xb1t\xc4\xb1m\xc4\xb1 hakk\xc4\xb1nda</title>
<link>https://example.org/a</link><pubDate>Tue, 14 Jan 2025 08:00:00 GMT</pubDate></item>
</channel></rss>"""


class Sink:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str, *, level: Level = "info") -> None:
        self.sent.append(text)


def test_news_flow(migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_MANUAL__DIR": str(EX),
        "QP_LAKE__ROOT": str(tmp_path / "lake"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    runner = CliRunner()
    for args in (
        ["ref", "import", str(EX / "instruments.csv")],
        ["ledger", "account", "add", "EX", "--name", "Example"],
        ["ledger", "import", str(EX / "ledger.csv")],
    ):
        assert runner.invoke(app, args).exit_code == 0
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    configure_logging(settings.log)  # the CLI runs above logged to the runner's closed stream
    feeds = {f.code: f for f in settings.sources.news.feeds}
    bodies = {
        feeds["tcmb_press_tr"].url: (FIX / "tcmb_tr.xml").read_bytes(),
        feeds["fed_monetary"].url: (FIX / "fed.xml").read_bytes(),
        feeds["ecb_press"].url: (FIX / "ecb.xml").read_bytes(),
        feeds["tcmb_press_en"].url: HOLDING_FEED,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bodies[str(request.url).replace("%2B", "+")])

    def fetch(now: datetime) -> tuple[int, int, list[str]]:
        client = HttpClient(
            user_agent="t", min_interval_s=0, retries=0, transport=httpx.MockTransport(handler)
        )
        with client:
            r = fetch_feeds(
                client,
                RawStore(LakePaths(tmp_path / "lake")),
                migrated_db,
                list(feeds.values()),
                rules_path=REPO / "configs" / "news_rules.yaml",
                clock=ManualClock(now),
            )
        return r.new, r.duplicates, r.errors

    new, dups, errors = fetch(NOW)
    assert new == 5  # tcmb 2 (one bad date), fed 1 (+1 same-title duplicate), ecb 1, holding 1
    assert dups == 1
    assert len(errors) == 1
    assert "unparseable date" in errors[0]
    assert fetch(NOW + timedelta(hours=1))[:2] == (0, 6)  # nothing new on refetch

    items = query(migrated_db, as_of=datetime(2030, 1, 1, tzinfo=UTC))
    titles = {i.title: i for i in items}
    thy = titles["THYAO temettü dağıtımı hakkında"]
    assert {(e.kind, e.key) for e in thy.entities} == {
        ("instrument", "THYAO.BIST"),
        ("institution", "TCMB"),  # from the feed's institution
    }
    assert "dividend" in thy.tags
    ecb = titles["Monetary policy decisions"]
    assert ecb.url == "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp261029~test.en.html"
    # point in time: a 2026 item fetched on 14 Jan 2025 is known only when published
    assert all(i.known_time >= i.published_at for i in items)
    assert "Monetary policy decisions" not in {i.title for i in query(migrated_db, as_of=NOW)}
    assert {i.title for i in query(migrated_db, as_of=NOW, entity_keys=["THYAO.BIST"])} == {
        thy.title
    }

    sink = Sink()
    sent = run_news_alerts(settings, migrated_db, day=date(2025, 1, 14), notifier=sink)
    assert len(sent.new_ids) == 1
    assert sink.sent[0].startswith("THYAO.BIST in the news (tcmb_press_en, matched 'THYAO')")
    again = run_news_alerts(settings, migrated_db, day=date(2025, 1, 14), notifier=sink)
    assert again.new_ids == []
