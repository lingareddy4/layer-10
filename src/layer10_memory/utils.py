from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Iterator, Sequence


TOKEN_RE = re.compile(r"[a-z0-9_#/\-.]+")
SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?\n]?")


def utc_now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)


def normalize_space(text: str | None) -> str:
    return SPACE_RE.sub(" ", (text or "")).strip()


def slugify(text: str, max_len: int = 64) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:max_len] or "unknown"


def stable_hash(parts: Sequence[str], length: int = 16) -> str:
    joined = "||".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def sentence_spans(text: str) -> Iterator[tuple[int, int, str]]:
    for match in SENTENCE_RE.finditer(text or ""):
        start = match.start()
        end = match.end()
        sentence = normalize_space(match.group(0))
        if sentence:
            yield start, end, sentence


def short_excerpt(text: str, start: int, end: int, max_chars: int = 240) -> str:
    snippet = normalize_space((text or "")[start:end])
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 3].rstrip() + "..."


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def dump_json(path: Path, payload: dict | list) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result

