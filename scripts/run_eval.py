"""Score the analysis pipeline against the benchmark in eval/.

For every row of eval/truth.csv: run the photo through the REAL pipeline
(resilient scan -> matcher -> sanity -> pricing, cache bypassed), compare
against the known truth, and report:

  * kcal MAPE + median APE   — how far off the calories are
  * food-ID hit rate         — did we name the dish right (trigram >= 0.4)
  * DB match rate            — items priced from the DB vs model fallback
  * worst-5 photos           — where to aim the next fix

Writes a timestamped JSON report to eval/reports/ so runs are comparable
("did prompt v2 beat v1?"). Cost: one model call per photo (~$0.001).

Usage, from backend/:
    python scripts/run_eval.py
    python scripts/run_eval.py --limit 5
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.nutrition.matcher import normalize  # noqa: E402
from app.services.nutrition.nutri_cal import price_scan  # noqa: E402
from app.services.vision.base import VisionAnalysisError  # noqa: E402
from app.services.vision.gemini import PROMPT_VERSION  # noqa: E402
from app.services.vision.resilient import resilient_scan  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
NAME_HIT_THRESHOLD = 0.4


def trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


def name_similarity(a: str, b: str) -> float:
    """Same trigram idea as the DB matcher, in pure Python."""
    ta, tb = trigrams(normalize(a)), trigrams(normalize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def eval_photo(path: Path, true_kcal: float, true_name: str) -> dict:
    started = time.perf_counter()
    scan, meta = await resilient_scan(image_bytes=path.read_bytes())
    if scan.not_food or not scan.items:
        return {"filename": path.name, "error": "model said not_food"}
    result, matched = await price_scan(scan, meta)

    est = result.calories_per_serving
    ape = abs(est - true_kcal) / true_kcal
    name_sim = max(
        name_similarity(true_name, result.name),
        max((name_similarity(true_name, i.label) for i in result.detected_items),
            default=0.0),
    )
    db_items = sum(1 for m in matched if m["source"] != "model_fallback")
    return {
        "filename": path.name,
        "true_kcal": true_kcal,
        "est_kcal": est,
        "ape": round(ape, 4),
        "true_name": true_name,
        "est_name": result.name,
        "name_similarity": round(name_sim, 3),
        "name_hit": name_sim >= NAME_HIT_THRESHOLD,
        "items_total": len(matched),
        "items_from_db": db_items,
        "model": meta.model,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "cost_usd": meta.estimated_cost_usd,
        "matched": matched,
    }


async def main() -> None:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    rows = list(csv.DictReader(open(EVAL_DIR / "truth.csv", encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]

    results: list[dict] = []
    for i, row in enumerate(rows, 1):
        path = EVAL_DIR / "photos" / row["filename"].strip()
        if not path.exists():
            print(f"[{i}/{len(rows)}] SKIP {row['filename']} — file not found")
            continue
        print(f"[{i}/{len(rows)}] {row['filename']} ...", end=" ", flush=True)
        try:
            r = await eval_photo(
                path, float(row["true_kcal"]), row["true_name"].strip()
            )
        except VisionAnalysisError:
            r = {"filename": path.name, "error": "analysis failed (all providers)"}
        results.append(r)
        if "error" in r:
            print(r["error"])
        else:
            print(
                f"true {r['true_kcal']:.0f} / est {r['est_kcal']} kcal "
                f"(APE {r['ape'] * 100:.0f}%), name={'HIT' if r['name_hit'] else 'miss'}"
            )

    scored = [r for r in results if "error" not in r]
    if not scored:
        sys.exit("nothing scored — add photos to eval/photos/ and rows to truth.csv")

    apes = [r["ape"] for r in scored]
    total_items = sum(r["items_total"] for r in scored)
    db_items = sum(r["items_from_db"] for r in scored)
    summary = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "photos_scored": len(scored),
        "photos_failed": len(results) - len(scored),
        "kcal_mape_pct": round(100 * statistics.mean(apes), 1),
        "kcal_median_ape_pct": round(100 * statistics.median(apes), 1),
        "name_hit_rate_pct": round(
            100 * sum(r["name_hit"] for r in scored) / len(scored), 1
        ),
        "db_match_rate_pct": round(100 * db_items / total_items, 1)
        if total_items
        else 0.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in scored), 4),
    }

    print("\n================ SUMMARY ================")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nWorst 5 by kcal error:")
    for r in sorted(scored, key=lambda r: -r["ape"])[:5]:
        print(
            f"  {r['filename']}: true {r['true_kcal']:.0f} vs est {r['est_kcal']} "
            f"({r['ape'] * 100:.0f}% off) — est name: {r['est_name']!r}"
        )

    reports = EVAL_DIR / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = reports / f"report_{stamp}.json"
    out.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2),
        encoding="utf-8",
    )
    print(f"\nreport written: {out}")


if __name__ == "__main__":
    asyncio.run(main())
