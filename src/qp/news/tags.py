"""Rule-based tags (ADR 0012, 11): ``configs/news_rules.yaml`` maps a tag to keywords,
matched as whole words (or word prefixes ending in ``*``) on the folded text."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from qp.core import QPError
from qp.news.text import fold


class RulesError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class TagRule:
    tag: str
    regex: re.Pattern[str]


def _keyword(k: str) -> str:
    k = fold(k.strip())
    if k.endswith("*"):
        return rf"(?<!\w){re.escape(k[:-1])}\w*"
    return rf"(?<!\w){re.escape(k)}(?!\w)"


def load_rules(path: Path) -> list[TagRule]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RulesError(f"{path}: {exc}") from exc
    tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(tags, dict) or not tags:
        raise RulesError(f"{path}: expected a 'tags' mapping")
    rules: list[TagRule] = []
    for tag, keywords in tags.items():
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise RulesError(f"{path}: tag {tag!r} needs a list of keywords")
        rules.append(TagRule(str(tag), re.compile("|".join(_keyword(k) for k in keywords))))
    return rules


def tag(text: str, rules: Sequence[TagRule]) -> list[str]:
    folded = fold(text)
    return [r.tag for r in rules if r.regex.search(folded)]
