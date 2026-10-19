"""Raw zone: provider payloads exactly as received, write-once (rule 5: raw data is immutable).

    raw/{source}/{dataset}/fetched_date=YYYY-MM-DD/{name}            payload bytes
    raw/{source}/{dataset}/fetched_date=YYYY-MM-DD/{name}.meta.json  how it was fetched

``fetched_date`` is the UTC date of ``fetched_at``. Files are created exclusively and made
read-only; writing an existing name is an error. Metadata never contains secrets: connectors
pass URLs without credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from qp.core import QPError, ensure_utc
from qp.platform.lake import LakePaths

_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
META_SUFFIX = ".meta.json"


class LakeError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class RawRecord:
    path: Path
    ref: str  # path relative to the lake root, stored with every clean row
    sha256: str
    fetched_at: datetime


def _write_once(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise LakeError(f"raw file already exists (raw data is immutable): {path}") from exc
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


class RawStore:
    def __init__(self, paths: LakePaths) -> None:
        self.paths = paths

    def write(
        self,
        source: str,
        dataset: str,
        name: str,
        payload: bytes,
        *,
        fetched_at: datetime,
        meta: Mapping[str, str | int | None],
    ) -> RawRecord:
        """Store ``payload`` under a new, unique name. Never overwrites."""
        if not _FILE_RE.fullmatch(name) or name.endswith(META_SUFFIX):
            raise LakeError(f"invalid raw file name: {name!r}")
        fetched_at = ensure_utc(fetched_at)
        folder = self.paths.raw(source, dataset, fetched_at.date())
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        digest = hashlib.sha256(payload).hexdigest()
        sidecar = {
            **dict(meta),
            "source": source,
            "dataset": dataset,
            "fetched_at": fetched_at.isoformat(),
            "sha256": digest,
            "bytes": len(payload),
        }
        _write_once(path, payload)
        _write_once(
            folder / f"{name}{META_SUFFIX}",
            json.dumps(sidecar, sort_keys=True, indent=1).encode(),
        )
        return RawRecord(path, path.relative_to(self.paths.root).as_posix(), digest, fetched_at)

    def read(self, ref: str) -> bytes:
        path = self.paths.root / ref
        if not path.is_file():
            raise LakeError(f"no raw file {ref}")
        return path.read_bytes()

    def meta(self, ref: str) -> dict[str, object]:
        data = json.loads((self.paths.root / f"{ref}{META_SUFFIX}").read_text())
        if not isinstance(data, dict):
            raise LakeError(f"bad metadata for {ref}")
        return data
