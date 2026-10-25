"""The books JSON API on the hand-calculated October (see test_books.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from qp.platform import load_settings

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def client(books_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("QP_BOOKS__ROOT", str(books_dir))
    monkeypatch.setenv("QP_LAKE__ROOT", str(tmp_path / "lake"))
    monkeypatch.setenv("QP_MANUAL__DIR", str(tmp_path / "manual"))
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    return TestClient(create_app(settings))


def test_import_review_add_and_statements(client: TestClient, tmp_path: Path) -> None:
    meta = client.get("/api/meta").json()
    assert "Expenses:Food:Groceries" in meta["categories"]
    assert "Assets:Cash:Wallet" in meta["accounts"]

    files = {"file": ("akbank.csv", (tmp_path / "akbank.csv").read_bytes(), "text/csv")}
    preview = client.post("/api/import", files=files, data={"source": "akbank", "dry_run": "true"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["written"] is False
    assert len(preview.json()["new"]) == 5
    done = client.post("/api/import", files=files, data={"source": "akbank", "dry_run": "false"})
    assert done.json()["written"] is True
    again = client.post("/api/import", files=files, data={"source": "akbank", "dry_run": "false"})
    assert again.json()["duplicates"] == 5

    queue = client.get("/api/review").json()["items"]
    assert [x["description"] for x in queue] == ["XYZ LTD STI"]
    r = client.post(
        f"/api/transactions/{queue[0]['id']}/category",
        json={"category": "home", "payee": "XYZ", "learn": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "Expenses:Shopping:Home"
    assert r.json()["learned"] == "xyz ltd"

    added = client.post(
        "/api/transactions",
        json={"amount": "85,50", "category": "coffee", "payee": "Kahveci", "date": "2026-10-12"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["amount"] == "-85.50"

    inc = client.get("/api/statements/income", params={"day": "2026-10-15"}).json()
    assert inc["total_income"] == "50000.00"
    assert inc["total_expenses"] == "1648.50"
    bal = client.get("/api/statements/balance", params={"day": "2026-10-31"}).json()
    assert bal["net_worth"] == "58351.50"  # 10,000 + 50,000 - 1,648.50; USD has no rate here
    assert bal["missing_rates"] == ["USD on 2026-10-01", "USD on 2026-10-31"]
    cf = client.get("/api/statements/cashflow", params={"day": "2026-10-15"}).json()
    assert cf["closing_cash"] == "58351.50"

    assert (
        client.post(
            "/api/budgets",
            json={
                "account": "Expenses:Food",
                "period": "daily",
                "amount": "50",
                "start": "2026-10-01",
            },
        ).status_code
        == 200
    )
    dash = client.get("/api/dashboard", params={"day": "2026-10-15"}).json()
    food = next(b for b in dash["budgets"] if b["account"] == "Expenses:Food")
    assert food["budget"] == "1550.00"  # 50 x 31
    assert food["actual"] == "1336.00"  # 1,250.50 + 85.50
    assert dash["review"] == 0
    week = client.get("/api/budgets", params={"period": "week", "day": "2026-10-15"}).json()
    assert next(b for b in week["items"] if b["account"] == "Expenses:Food")["budget"] == "350.00"

    txs = client.get("/api/transactions", params={"day": "2026-10-15", "q": "migros"}).json()
    assert [t["description"] for t in txs["items"]] == ["MİGROS TİC A.Ş. İSTANBUL"]


def test_errors_are_422_and_token_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    bad = client.post("/api/transactions", json={"amount": "abc", "category": "coffee"})
    assert bad.status_code == 422
    amb = client.post("/api/transactions", json={"amount": "1", "category": "other"})
    assert amb.status_code == 422
    assert "ambiguous" in amb.json()["detail"]
    assert client.get("/api/dashboard", params={"period": "decade"}).status_code == 422

    monkeypatch.setenv("QP_API__TOKEN", "s3cret")
    guarded = TestClient(create_app(load_settings(config_dir=REPO / "configs", env_file=None)))
    assert guarded.get("/api/meta").status_code == 401
    ok = guarded.get("/api/meta", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
