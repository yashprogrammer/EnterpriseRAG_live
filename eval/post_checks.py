
from __future__ import annotations


def forbidden_keywords_check(answer: str, forbidden: list[str]) -> dict:
    answer_lower = answer.lower()
    hits = [kw for kw in forbidden if kw.lower() in answer_lower]
    return {"passed": not hits, "hits": hits}


def source_overlap(actual: list[str], golden: list[str]) -> dict:
    import os
    from urllib.parse import urlparse

    def _aliases(source: str) -> set[str]:
        raw = source.strip().lower()
        aliases: set[str] = set()
        if not raw:
            return aliases

        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"}:
            aliases.add("tavily_web")
            raw = parsed.path or parsed.netloc

        basename = os.path.basename(raw)
        stem = os.path.splitext(basename)[0]
        if stem:
            aliases.add(stem)
            aliases.update(part for part in stem.split("__") if part)
        return aliases

    actual_aliases = set().union(*(_aliases(s) for s in actual)) if actual else set()
    golden_aliases_by_source = {source: _aliases(source) for source in golden}
    matched = [
        source
        for source, aliases in golden_aliases_by_source.items()
        if aliases & actual_aliases
    ]

    return {
        "overlap_pct": round(len(matched) / max(len(golden), 1), 3),
        "matched": sorted(matched),
        "missed": sorted(set(golden) - set(matched)),
    }
