"""Shared sample statements and a books folder for the books tests (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qp.books.ledger import init

REPO = Path(__file__).resolve().parents[3]

SOURCES = """
sources:
  akbank:
    account: Assets:Bank:Akbank
    currency: TRY
    decimal: ","
    header_contains: ["Tarih", "Açıklama"]
    date: Tarih
    date_format: "%d.%m.%Y"
    description: [Açıklama]
    amount: Tutar
    balance: Bakiye
  papara:
    account: Assets:Wallet:Papara
    currency: TRY
    decimal: ","
    header_contains: ["Tarih"]
    date: Tarih
    date_format: "%d.%m.%Y %H:%M"
    description: [Açıklama]
    debit: Giden
    credit: Gelen
  revolut:
    account: Assets:Bank:RevolutUS
    currency: USD
    decimal: "."
    header_contains: ["Completed Date", "Amount"]
    date: Completed Date
    date_format: "%Y-%m-%d %H:%M:%S"
    description: [Description]
    amount: Amount
    fee: Fee
    state_column: State
    state_ok: [COMPLETED]
  empty:
    account: Assets:Bank:KuveytTurk
    currency: TRY
"""

AKBANK = """Hesap Hareketleri;;;
Hesap No: 123;;;
Tarih;Açıklama;Tutar;Bakiye
02.10.2026;MAAŞ ÖDEMESİ ACME;50.000,00;60.000,00
03.10.2026;MİGROS TİC A.Ş. İSTANBUL;-1.250,50;58.749,50
05.10.2026;PAPARA YUKLEME;-2.000,00;56.749,50
06.10.2026;XYZ LTD STI;-300,00;56.449,50
07.10.2026;EFT UCRETI;-12,50;56.437,00
;Toplam;;
"""

PAPARA = """Tarih,Açıklama,Gelen,Giden
"05.10.2026 14:02","Akbank'tan para yükleme","2.000,00",""
"08.10.2026 20:15","GETİR","","450,00"
"""

REVOLUT = "\n".join(
    [
        "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance",
        "CARD_PAYMENT,Current,2026-10-10 08:00:00,2026-10-10 09:00:00,Netflix,-15.99,0.00,USD,"
        "COMPLETED,484.01",
        "CARD_PAYMENT,Current,2026-10-11 08:00:00,,Pending shop,-5.00,0.00,USD,PENDING,479.01",
        "",
    ]
)

OPENING = """
2026-10-01 * "Opening balance"
  Assets:Bank:Akbank        10000.00 TRY
  Equity:Opening-Balances
2026-10-01 * "Opening balance"
  Assets:Bank:RevolutUS       500.00 USD
  Equity:Opening-Balances
"""


@pytest.fixture
def books_dir(tmp_path: Path) -> Path:
    """Books from the templates with the sample sources, opening balances and statement files
    (akbank.csv, papara.csv, revolut.csv) in ``tmp_path``."""
    root = tmp_path / "books"
    init(root, REPO / "configs" / "books")
    (root / "sources.yaml").write_text(SOURCES, encoding="utf-8")
    (root / "manual.beancount").write_text(OPENING, encoding="utf-8")
    for name, text in (("akbank.csv", AKBANK), ("papara.csv", PAPARA), ("revolut.csv", REVOLUT)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    return root
