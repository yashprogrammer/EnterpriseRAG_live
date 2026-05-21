from __future__ import annotations

from statistics import mean

METRIC_KEYS = [
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_relevancy",
]

def aggregate(rows: list[dict]) -> dict:
    out: dict = {}
    for k in METRIC_KEYS:
        vals = [
            r["ragas_metrics"].get(k)
            for r in rows
            if r.get("ragas_metrics") and r["ragas_metrics"].get(k) is not None
        ]
        out[k] = round(mean(vals), 3) if vals else None
    out["forbidden_violations"] = sum(
        1 for r in rows if not r.get("forbidden_check", {}).get("passed", True)
    )
    return out


def print_table(payload: dict) -> None:
    """Print a markdown table of per-question results + aggregate row.

    Args:
        payload: The full result payload dict (profile, mode, rows, aggregate).
    """
    print(f"\n## Eval — profile={payload['profile']} mode={payload['mode']}")
    print(f"Skipped: {len(payload['skipped'])}")
    print()
    print(
        "| id | feature | faith | ctx_prec | ctx_recall | ans_rel | forbidden |"
    )
    print(
        "|----|---------|-------|----------|------------|---------|-----------|"
    )

    for r in payload["rows"]:
        m = r.get("ragas_metrics") or {}
        fb = (
            "OK"
            if r["forbidden_check"]["passed"]
            else f"FAIL: {r['forbidden_check']['hits']}"
        )
        print(
            f"| {r['id']} | {r['demonstrates_feature']} | "
            f"{m.get('faithfulness', 0):.2f} | "
            f"{m.get('context_precision', 0):.2f} | "
            f"{m.get('context_recall', 0):.2f} | "
            f"{m.get('answer_relevancy', 0):.2f} | {fb} |"
        )

    if not payload["rows"]:
        print("(no rows evaluated — all goldens skipped)")
        return

    a = payload["aggregate"]


    print(
        f"| **AGG** | — | **{a['faithfulness']}** | "
        f"**{a['context_precision']}** | **{a['context_recall']}** | "
        f"**{a['answer_relevancy']}** | "
        f"violations={a['forbidden_violations']} |"
    )