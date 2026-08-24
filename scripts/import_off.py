"""RETIRED (never run in anger): bulk Open Food Facts import.

The barcode route now looks products up live via the OFF REST API on first
scan and caches them into off_products (see
app/services/nutrition/off_client.py), so the table grows organically and
this bulk load — with its ~1 GB download and few hundred thousand rows of
Supabase storage — is unnecessary. Kept in case a preload is ever wanted
(e.g. seeding popular products before a launch).

Usage, from backend/:
    python scripts/import_off.py
    python scripts/import_off.py --limit 50000   # quick partial run to test
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.db.models import OffProduct  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "off"

# countries_tags values (en: prefix stripped) for Europe + US + Middle East.
MARKETS = {
    # US
    "united-states",
    # Europe
    "france", "germany", "united-kingdom", "spain", "italy", "netherlands",
    "belgium", "switzerland", "austria", "poland", "portugal", "sweden",
    "denmark", "norway", "finland", "ireland", "czech-republic", "romania",
    "greece", "hungary", "bulgaria", "croatia", "slovakia", "slovenia",
    "lithuania", "latvia", "estonia", "luxembourg", "malta", "cyprus",
    "iceland",
    # Middle East
    "saudi-arabia", "united-arab-emirates", "qatar", "kuwait", "bahrain",
    "oman", "jordan", "lebanon", "egypt", "iraq", "iran", "israel",
    "palestinian-territories", "turkey", "syria", "yemen",
}

KJ_TO_KCAL = 0.239006
BATCH = 5000


def download() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "products.csv.gz"
    if dest.exists() and dest.stat().st_size > 0:
        print(f"using cached {dest} ({dest.stat().st_size / 1e9:.2f} GB)")
        return dest
    print(f"downloading {URL} (~1 GB, be patient) ...")
    tmp = dest.with_suffix(".part")
    done = 0
    with httpx.stream("GET", URL, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 22):
                f.write(chunk)
                done += len(chunk)
                if done % (1 << 28) < (1 << 22):  # ~every 256 MB
                    print(f"  ... {done / 1e9:.2f} GB")
    tmp.rename(dest)
    print(f"downloaded {dest.stat().st_size / 1e9:.2f} GB")
    return dest


def _f(row: list[str], idx: int | None, lo: float, hi: float) -> float | None:
    if idx is None or idx >= len(row):
        return None
    try:
        v = float(row[idx])
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def parse(path: Path, limit: int | None) -> dict[str, dict]:
    """barcode -> row dict; dict dedupes OFF's occasional repeated codes."""
    csv.field_size_limit(10_000_000)
    out: dict[str, dict] = {}
    scanned = 0

    with gzip.open(path, "rb") as gz:
        reader = csv.reader(
            io.TextIOWrapper(gz, encoding="utf-8", errors="replace"),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}

        def idx(name: str) -> int | None:
            return col.get(name)

        i_code = col["code"]
        i_name = col["product_name"]
        i_brands = idx("brands")
        i_countries = col["countries_tags"]
        i_kcal = idx("energy-kcal_100g")
        i_kj = idx("energy_100g")
        i_prot = idx("proteins_100g")
        i_carb = idx("carbohydrates_100g")
        i_fat = idx("fat_100g")
        i_serv = idx("serving_quantity")

        for row in reader:
            scanned += 1
            if scanned % 500_000 == 0:
                print(f"  scanned {scanned:,} rows, kept {len(out):,}")
            if limit and len(out) >= limit:
                break
            try:
                code = row[i_code].strip()
                name = row[i_name].strip()
                countries = row[i_countries]
            except IndexError:
                continue
            if not code or len(code) > 64 or not name:
                continue
            if not any(
                c.removeprefix("en:") in MARKETS
                for c in countries.split(",")
            ):
                continue

            kcal = _f(row, i_kcal, 0, 900)
            if kcal is None:
                kj = _f(row, i_kj, 0, 3800)
                kcal = round(kj * KJ_TO_KCAL, 1) if kj is not None else None
            if kcal is None:
                continue  # unpriceable

            out[code] = {
                "barcode": code,
                "name": name[:500],
                "brand": (row[i_brands].strip()[:200] or None)
                if i_brands is not None and i_brands < len(row)
                else None,
                "serving_grams": _f(row, i_serv, 1, 5000),
                "kcal_100g": kcal,
                "protein_100g": _f(row, i_prot, 0, 100),
                "carbs_100g": _f(row, i_carb, 0, 100),
                "fat_100g": _f(row, i_fat, 0, 100),
            }

    print(f"scanned {scanned:,} rows total, kept {len(out):,}")
    return out


async def upsert(rows: list[dict]) -> None:
    async with SessionLocal() as session:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i : i + BATCH]
            stmt = pg_insert(OffProduct).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["barcode"],
                set_={c: stmt.excluded[c] for c in chunk[0] if c != "barcode"},
            )
            await session.execute(stmt)
            if (i // BATCH) % 10 == 0:
                print(f"  upserted {min(i + BATCH, len(rows)):,}/{len(rows):,}")
        await session.commit()


async def main() -> None:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    path = download()
    products = parse(path, limit)
    print(f"upserting {len(products):,} products ...")
    await upsert(list(products.values()))
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
