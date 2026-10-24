"""News parsing, text helpers, entity links and tags (ADR 0012), on synthetic fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from qp.news.entities import build_patterns, link
from qp.news.parse import FeedError, parse_date, parse_feed
from qp.news.tags import RulesError, load_rules, tag
from qp.news.text import canonical_url, fold, normalized_title

FIX = Path(__file__).parents[2] / "fixtures" / "news"
REPO = Path(__file__).parents[3]


@pytest.mark.parametrize(
    ("text", "tz", "expected"),
    [
        ("22 Eki 2026 14:00:00", "Europe/Istanbul", datetime(2026, 10, 22, 11, tzinfo=UTC)),
        ("3 Şub 2026 09:30:00", "Europe/Istanbul", datetime(2026, 2, 3, 6, 30, tzinfo=UTC)),
        ("Oct 22, 2026, 2:00:00 PM", "Europe/Istanbul", datetime(2026, 10, 22, 11, tzinfo=UTC)),
        ("Oct 9, 2026, 12:00:00 AM", "Europe/Istanbul", datetime(2026, 10, 8, 21, tzinfo=UTC)),
        ("Wed, 28 Oct 2026 18:00:00 GMT", "UTC", datetime(2026, 10, 28, 18, tzinfo=UTC)),
        ("Thu, 29 Oct 2026 14:15:00 +0100", "UTC", datetime(2026, 10, 29, 13, 15, tzinfo=UTC)),
        ("2026-10-22T14:00:00+03:00", "UTC", datetime(2026, 10, 22, 11, tzinfo=UTC)),
    ],
)
def test_parse_date_forms(text: str, tz: str, expected: datetime) -> None:
    assert parse_date(text, tz) == expected


def test_parse_atom_tcmb_skips_bad_dates() -> None:
    items, errors = parse_feed(
        (FIX / "tcmb_tr.xml").read_bytes(), naive_tz="Europe/Istanbul", keep_summary=True
    )
    assert [i.title for i in items] == [
        "Faiz Oranlarına İlişkin Basın Duyurusu (2026-45)",
        "Zorunlu Karşılıklara İlişkin Basın Duyurusu (2026-40)",
    ]
    assert items[1].published_at == datetime(2026, 8, 3, 6, 30, tzinfo=UTC)
    assert items[0].summary == ""  # blank summary stays empty
    assert len(errors) == 1
    assert "unparseable date" in errors[0]
    [en] = parse_feed((FIX / "tcmb_en.xml").read_bytes(), naive_tz="Europe/Istanbul")[0]
    assert en.published_at == items[0].published_at


def test_parse_rss_keeps_short_summary_only_when_allowed() -> None:
    items, errors = parse_feed((FIX / "fed.xml").read_bytes(), keep_summary=True)
    assert errors == []
    assert items[0].summary.startswith("The Committee decided")
    assert items[0].categories == ("Monetary Policy",)
    no_summary, _ = parse_feed((FIX / "fed.xml").read_bytes(), keep_summary=False)
    assert no_summary[0].summary == ""


def test_hostile_or_foreign_xml_is_rejected() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><rss>&b;</rss>'
    )
    with pytest.raises(FeedError, match="not a feed"):
        parse_feed(bomb)
    with pytest.raises(FeedError, match="unknown feed root"):
        parse_feed(b"<html></html>")


def test_text_helpers() -> None:
    assert fold("İSTANBUL ve IĞDIR") == "istanbul ve ığdır"
    assert (
        canonical_url("http://WWW.ecb.europa.eu//press/pr/x.en.html?utm_source=rss&id=3#top")
        == "https://www.ecb.europa.eu/press/pr/x.en.html?id=3"
    )
    assert normalized_title("  Faiz Oranlarına İlişkin — Basın Duyurusu!") == (
        "faiz oranlarına ilişkin basın duyurusu"
    )


def test_entities_and_tags() -> None:
    patterns = build_patterns(
        [("THYAO.BIST", "Türk Hava Yolları", ["THYAO.IS"]), ("AAPL.NASDAQ", "Apple Inc.", [])]
    )
    found = link("TÜRK HAVA YOLLARI and THYAO.IS: dolar ve TCMB; Fed says no", patterns)
    keys = {(e.kind, e.key) for e in found}
    assert ("instrument", "THYAO.BIST") in keys
    assert ("currency", "USD") in keys
    assert {("institution", "TCMB"), ("institution", "FED")} <= keys
    assert not any(e.key == "AAPL.NASDAQ" for e in link("pineapple pie", patterns))
    rules = load_rules(REPO / "configs" / "news_rules.yaml")
    assert set(tag("Faiz Oranlarına İlişkin Basın Duyurusu", rules)) == {"interest_rates"}
    assert "inflation" in tag("Enflasyon Raporu 2026-IV", rules)
    assert tag("Monetary policy decisions", rules) == ["interest_rates"]
    assert tag("Press Release on Interest Rates (2026-45)", rules) == ["interest_rates"]
    assert tag("Frank Elderson: Interview with Die Presse", rules) == ["speech"]
    assert {e.key for e in link("INFLATION in Istanbul", patterns)} == set()


def test_rules_file_validation(tmp_path: Path) -> None:
    bad = tmp_path / "r.yaml"
    bad.write_text("tags: {x: notalist}\n")
    with pytest.raises(RulesError):
        load_rules(bad)
