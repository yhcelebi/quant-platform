"""Read bank statement exports (CSV, XLSX, XLS) into rows (ADR 0017, 3).

Cells keep their type where the file has one (Excel numbers and dates), so a Turkish
decimal comma is applied only to text. Nothing is guessed: a missing column or an
unreadable amount or date is an error naming the line.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from qp.books.config import Source, fold
from qp.core import QPValidationError

type Cell = str | Decimal | date | datetime | None


def _csv_rows(path: Path) -> list[list[Cell]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1254", "latin-1"):  # cp1254: Turkish Windows exports
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _number(x: float | int) -> Decimal:
    return Decimal(str(x))


def _xlsx_rows(path: Path, sheet: str | int | None) -> list[list[Cell]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = (
        wb.worksheets[sheet] if isinstance(sheet, int) else wb[sheet] if sheet else wb.worksheets[0]
    )
    out: list[list[Cell]] = []
    for row in ws.iter_rows(values_only=True):
        cells: list[Cell] = []
        for v in row:
            if isinstance(v, bool) or v is None:
                cells.append(None if v is None else str(v))
            elif isinstance(v, (int, float)):
                cells.append(_number(v))
            elif isinstance(v, (datetime, date)):
                cells.append(v)
            else:
                cells.append(str(v))
        out.append(cells)
    wb.close()
    return out


def _xls_rows(path: Path, sheet: str | int | None) -> list[list[Cell]]:
    import xlrd

    book = xlrd.open_workbook(str(path))
    ws = (
        book.sheet_by_index(sheet)
        if isinstance(sheet, int)
        else book.sheet_by_name(sheet)
        if sheet
        else book.sheet_by_index(0)
    )
    out: list[list[Cell]] = []
    for r in range(ws.nrows):
        cells: list[Cell] = []
        for c in range(ws.ncols):
            cell = ws.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                cells.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                cells.append(_number(cell.value))
            elif cell.ctype == xlrd.XL_CELL_EMPTY:
                cells.append(None)
            else:
                cells.append(str(cell.value))
        out.append(cells)
    return out


def read_table(path: Path, sheet: str | int | None = None) -> list[list[Cell]]:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        return _csv_rows(path)
    if suffix in (".xlsx", ".xlsm"):
        return _xlsx_rows(path, sheet)
    if suffix == ".xls":
        return _xls_rows(path, sheet)
    raise QPValidationError(f"{path.name}: unsupported file type {suffix} (csv, xlsx, xls)")


def _text(c: Cell) -> str:
    if c is None:
        return ""
    if isinstance(c, datetime):
        return c.isoformat(sep=" ")
    return str(c).strip()


def find_header(rows: list[list[Cell]], words: tuple[str, ...]) -> int:
    """Index of the header row: the first row containing every word (folded), or without
    words the first row with at least three non-empty text cells."""
    want = [fold(w) for w in words]
    for i, row in enumerate(rows):
        cells = [fold(_text(c)) for c in row]
        if want and all(any(w == c or w in c for c in cells) for w in want):
            return i
        if not want and sum(1 for c in row if isinstance(c, str) and c.strip()) >= 3:
            return i
    raise QPValidationError(
        "header row not found" + (f": no row contains {', '.join(words)}" if words else "")
    )


_STRIP = re.compile(r"(?i)\b(tl|try|usd|eur|gbp)\b|[₺$€£\s]")  # \s covers no-break spaces


def parse_amount(cell: Cell, decimal: str) -> Decimal | None:
    """Text like '1.234,56', '-1.234,56 TL', '1.234,56-', '(12,00)', '$1,234.56'."""
    if cell is None:
        return None
    if isinstance(cell, Decimal):
        return cell
    if isinstance(cell, (date, datetime)):
        raise QPValidationError(f"expected an amount, found a date {cell}")
    t = _STRIP.sub("", str(cell))
    if not t or t in ("-", "+"):
        return None
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t.endswith("-"):
        negative, t = True, t[:-1]
    if t.startswith("-"):
        negative, t = not negative, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    t = t.replace(".", "").replace(",", ".") if decimal == "," else t.replace(",", "")
    try:
        value = Decimal(t)
    except InvalidOperation:
        raise QPValidationError(f"not an amount: {cell!r}") from None
    return -value if negative else value


def parse_date(cell: Cell, fmt: str) -> date:
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    text = _text(cell)
    for f in (fmt, fmt.split(" ")[0], "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, f).date()  # noqa: DTZ007 (a calendar date)
        except ValueError:
            continue
    raise QPValidationError(f"not a date in format {fmt}: {text!r}")


@dataclass(frozen=True, slots=True)
class Row:
    line: int  # 1-based line in the file
    day: date
    description: str
    amount: Decimal  # signed, money in > 0
    fee: Decimal  # positive, charged on top
    balance: Decimal | None


def statement_rows(path: Path, source: Source) -> tuple[list[Row], list[str]]:
    """Rows of a statement for a configured source, and the lines skipped (with why)."""
    if not source.configured:
        raise QPValidationError(
            f"source {source.name} has no column mapping yet: run `qp books inspect "
            f"{path.name}` and fill data/books/sources.yaml"
        )
    table = read_table(path, source.sheet)
    h = find_header(table, source.header_contains)
    header = [_text(c) for c in table[h]]
    missing = [c for c in source.columns() if c not in header]
    if missing:
        raise QPValidationError(
            f"{path.name}: columns {missing} not found; the header has {[c for c in header if c]}"
        )
    col = {name: header.index(name) for name in source.columns()}
    skip = re.compile(source.skip_if) if source.skip_if else None
    rows: list[Row] = []
    skipped: list[str] = []
    for i in range(h + 1, len(table)):
        r = table[i]
        line = i + 1

        def get(name: str | None, r: list[Cell] = r) -> Cell:
            if name is None:
                return None
            j = col[name]
            return r[j] if j < len(r) else None

        if all(_text(c) == "" for c in r):
            continue
        description = " / ".join(t for t in (_text(get(c)) for c in source.description) if t)
        if source.state_column and _text(get(source.state_column)) not in source.state_ok:
            skipped.append(f"line {line}: state {_text(get(source.state_column))!r}")
            continue
        if skip and skip.search(description):
            skipped.append(f"line {line}: skip_if matched")
            continue
        if _text(get(source.date)) == "":
            skipped.append(f"line {line}: no date (a total or note line)")
            continue
        try:
            day = parse_date(get(source.date), source.date_format)
            if source.amount:
                amount = parse_amount(get(source.amount), source.decimal)
            else:
                out_ = parse_amount(get(source.debit), source.decimal) or Decimal(0)
                in_ = parse_amount(get(source.credit), source.decimal) or Decimal(0)
                amount = in_ - abs(out_)
            fee = parse_amount(get(source.fee), source.decimal) if source.fee else None
            balance = parse_amount(get(source.balance), source.decimal) if source.balance else None
        except QPValidationError as exc:
            raise QPValidationError(f"{path.name} line {line}: {exc}") from None
        if amount is None or amount == 0:
            skipped.append(f"line {line}: no amount")
            continue
        rows.append(Row(line, day, description, amount, abs(fee or Decimal(0)), balance))
    return rows, skipped


def preview(path: Path, sheet: str | int | None = None, limit: int = 12) -> list[list[str]]:
    """The first rows as text, for `qp books inspect`."""
    return [[_text(c) for c in r] for r in read_table(path, sheet)[:limit]]
