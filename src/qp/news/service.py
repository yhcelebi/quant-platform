"""Fetch the configured feeds into the news tables (ADR 0012). Raw payloads go to the lake."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from qp.core import Clock, SystemClock, ensure_utc
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.lake import RawStore
from qp.news.entities import Pattern, build_patterns
from qp.news.parse import FeedError, parse_feed
from qp.news.store import SourceInfo, ensure_sources, store_items
from qp.news.tags import load_rules
from qp.platform.db import Database
from qp.platform.jobs import job_run
from qp.platform.lake import LakePaths
from qp.platform.logging import get_logger
from qp.platform.settings import FeedSettings, Settings, default_config_dir
from qp.reference.repo import ReferenceRepository

log = get_logger("qp.news")
RULES_FILE = Path("news_rules.yaml")


@dataclass
class NewsReport:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    new_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def instrument_patterns(db: Database) -> list[Pattern]:
    with db.session() as s:
        repo = ReferenceRepository(s)
        aliases = repo.aliases()
        return build_patterns(
            (str(i.id), i.name, aliases.get(i.id, [])) for i in repo.instruments()
        )


def fetch_feeds(
    client: HttpClient,
    raw: RawStore,
    db: Database,
    feeds: list[FeedSettings],
    *,
    rules_path: Path,
    clock: Clock,
) -> NewsReport:
    report = NewsReport()
    ensure_sources(
        db,
        [SourceInfo(f.code, f.name, f.url, f.language, f.public_institution) for f in feeds],
    )
    patterns = instrument_patterns(db)
    rules = load_rules(rules_path)
    for feed in feeds:
        try:
            res = client.get(feed.url)
        except FetchError as exc:
            report.errors.append(f"{feed.code}: {exc}")
            continue
        fetched_at = ensure_utc(clock.now())
        rec = raw.write(
            "news",
            feed.code,
            f"{feed.code}-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.xml",
            res.content,
            fetched_at=fetched_at,
            meta={"url": res.safe_url, "status": res.status},
        )
        report.fetched += 1
        if res.status != 200:
            report.errors.append(f"{feed.code}: HTTP {res.status} (raw {rec.ref})")
            continue
        try:
            items, item_errors = parse_feed(
                res.content, naive_tz=feed.naive_tz, keep_summary=feed.public_institution
            )
        except FeedError as exc:
            report.errors.append(f"{feed.code}: {exc} (raw {rec.ref})")
            continue
        report.errors += [f"{feed.code}: {e}" for e in item_errors]
        stored = store_items(
            db,
            feed.code,
            items,
            fetched_at=fetched_at,
            raw_ref=rec.ref,
            patterns=patterns,
            rules=rules,
        )
        report.new += len(stored.new_ids)
        report.new_ids += stored.new_ids
        report.duplicates += stored.duplicates
    log.info("news.fetch", fetched=report.fetched, new=report.new, errors=len(report.errors))
    return report


def fetch_news(settings: Settings, db: Database, *, clock: Clock | None = None) -> NewsReport:
    """All enabled feeds, recorded as job ``fetch:news``."""
    clock = clock or SystemClock()
    news = settings.sources.news
    feeds = [f for f in news.feeds if f.enabled]
    raw = RawStore(LakePaths(settings.lake.root))
    with (
        job_run(db, "fetch:news") as run,
        HttpClient(
            user_agent=settings.sources.user_agent, min_interval_s=news.min_interval_s
        ) as client,
    ):
        report = fetch_feeds(client, raw, db, feeds, rules_path=rules_path(), clock=clock)
        run.note(f"{report.fetched} feeds, {report.new} new, {report.duplicates} duplicates")
        if report.errors:
            run.status = "partial" if report.fetched else "failed"
            run.lines.extend(report.errors[:50])
    return report


def rules_path() -> Path:
    """configs/news_rules.yaml in the configuration directory (QP_CONFIG_DIR or ./configs)."""
    return default_config_dir() / RULES_FILE
