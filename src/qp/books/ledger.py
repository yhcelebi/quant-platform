"""The household books on disk (ADR 0017, 1): load, write qp-managed journal files, keep a
local git history of every change, and set the books up from the templates."""

from __future__ import annotations

import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.core import data
from beancount.parser import printer

from qp.core import QPError

TEMPLATES = ("main.beancount", "accounts.beancount", "budgets.beancount", "manual.beancount",
             "rules.yaml", "sources.yaml")  # fmt: skip
JOURNAL_HEADER = (
    "; Written by qp (imports, quick adds, review). Edit with `qp books` or the web app;\n"
    "; hand-written entries belong in manual.beancount.\n"
)


def units(p: data.Posting) -> tuple[Decimal, str]:
    """A loaded posting's number and currency (always present after interpolation)."""
    if p.units is None or p.units.number is None:
        raise BooksError(f"posting on {p.account} has no amount")
    return p.units.number, p.units.currency


class BooksError(QPError):
    """The books do not load, or a change would break them."""


@dataclass
class Books:
    root: Path
    entries: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def main(self) -> Path:
        return self.root / "main.beancount"

    @property
    def journal(self) -> Path:
        return self.root / "journal"

    def transactions(self) -> list[data.Transaction]:
        return [e for e in self.entries if isinstance(e, data.Transaction)]

    def accounts(self) -> dict[str, data.Open]:
        closed = {e.account for e in self.entries if isinstance(e, data.Close)}
        return {
            e.account: e
            for e in self.entries
            if isinstance(e, data.Open) and e.account not in closed
        }

    def import_ids(self) -> set[str]:
        return {str(t.meta["import_id"]) for t in self.transactions() if "import_id" in t.meta}


def load(root: Path) -> Books:
    main = root / "main.beancount"
    if not main.is_file():
        raise BooksError(f"{main} not found: run `uv run qp books init`")
    entries, errors, options = loader.load_file(str(main))
    return Books(root, list(entries), [_error_text(e) for e in errors], dict(options))


def _error_text(e: Any) -> str:
    meta = getattr(e, "source", None) or {}
    where = f"{Path(str(meta.get('filename', '?'))).name}:{meta.get('lineno', '?')}"
    return f"{where} {getattr(e, 'message', e)}"


def _git(root: Path, *args: str) -> bool:
    if shutil.which("git") is None:
        return False
    result = subprocess.run(  # noqa: S603 (fixed git arguments, local repository)
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def commit(root: Path, message: str) -> None:
    """Record the change in the books' own git history (best effort)."""
    if (root / ".git").is_dir():
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", message)


def init(root: Path, templates: Path) -> list[str]:
    """Create the books from the templates; existing files are kept. Returns created names."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "journal").mkdir(exist_ok=True)
    start = root / "journal" / "0000-start.beancount"
    if not start.exists():  # the include glob must match at least one file
        start.write_text(JOURNAL_HEADER)
    created = []
    for name in TEMPLATES:
        target = root / name
        if not target.exists():
            shutil.copyfile(templates / name, target)
            created.append(name)
    if not (root / ".git").is_dir() and _git(root, "init", "-q"):
        (root / ".gitignore").write_text("*.tmp\n")
        _git(root, "config", "user.name", "qp")
        _git(root, "config", "user.email", "qp@localhost")
    commit(root, "books created from qp templates")
    return created


def _month_file(root: Path, d: date) -> Path:
    return root / "journal" / f"{d:%Y-%m}.beancount"


def _render(entries: Sequence[data.Transaction]) -> str:
    body = "\n".join(printer.format_entry(e) for e in entries)
    return JOURNAL_HEADER + "\n" + body


def _file_entries(books: Books, path: Path) -> list[data.Transaction]:
    target = str(path.resolve())
    return [
        t
        for t in books.transactions()
        if str(Path(str(t.meta.get("filename", ""))).resolve()) == target
    ]


def _strip(t: data.Transaction) -> data.Transaction:
    """Drop the loader's bookkeeping metadata and interpolated amounts' markers."""
    meta = {k: v for k, v in t.meta.items() if k not in ("filename", "lineno", "__tolerances__")}
    postings = [
        p._replace(
            meta={
                k: v
                for k, v in (p.meta or {}).items()
                if k not in ("filename", "lineno", "__automatic__")
            }
            or None
        )
        for p in t.postings
    ]
    return t._replace(meta=meta, postings=postings)


def write(
    books: Books,
    add: Iterable[data.Transaction] = (),
    replace: dict[str, data.Transaction] | None = None,
    *,
    message: str,
) -> Books:
    """Add transactions and/or replace existing ones (by ``import_id``) in the journal files,
    then reload. If the books would not load cleanly, every file is restored and nothing
    changes."""
    replace = replace or {}
    new_by_file: dict[Path, list[data.Transaction]] = defaultdict(list)
    for t in add:
        new_by_file[_month_file(books.root, t.date)].append(t)
    touched = set(new_by_file)
    for t in books.transactions():
        iid = t.meta.get("import_id")
        if iid in replace:
            touched.add(Path(str(t.meta["filename"])))
    backups = {p: p.read_text(encoding="utf-8") if p.exists() else None for p in touched}
    try:
        for path in touched:
            kept = []
            for t in _file_entries(books, path) if path.exists() else []:
                iid = t.meta.get("import_id")
                kept.append(_strip(replace[iid]) if iid in replace else _strip(t))
            merged = sorted(kept + new_by_file.get(path, []), key=lambda t: t.date)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render(merged), encoding="utf-8")
        reloaded = load(books.root)
        before = set(books.errors)
        new_errors = [e for e in reloaded.errors if e not in before]
        if new_errors:
            raise BooksError("; ".join(new_errors[:5]))
    except Exception:
        for path, text in backups.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text, encoding="utf-8")
        raise
    commit(books.root, message)
    return reloaded
