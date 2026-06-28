import argparse
import os
import random
import time
from pathlib import Path

import psycopg2
from loguru import logger

from app.middleware.auth import hash_password



DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/adv_rag")
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "seed", "migrations")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "seed", "docs")

DEMO_USERS = [
    ("agent@demo.local", "agent123", False),
    ("admin@demo.local", "admin123", True),
]

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".md"}
SAMPLE_SEED = 42

# Files to never ingest (too large / not worth the OCR cost).
EXCLUDE_FILES = {
    "AMD64 Architecture Programmer's Manual - Volume 5 - 64-Bit Media and x87 Floating-point Instructions (26569, r3.13, May-2013).pdf",
}



def _collect_files(subdir: str) -> list[Path]:
    root = Path(DOCS_DIR) / subdir
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.name != ".gitkeep"
        and p.name not in EXCLUDE_FILES
    )

def _dir_size_mb(files: list[Path]) -> float:
    return sum(p.stat().st_size for p in files) / (1024 * 1024)


def _select_noisy_by_size(all_noisy: list[Path], budget_mb: float) -> list[Path]:
    """Deterministically pack noisy files until cumulative size reaches budget_mb."""
    budget_bytes = budget_mb * 1024 * 1024
    rng = random.Random(SAMPLE_SEED)
    shuffled = all_noisy[:]
    rng.shuffle(shuffled)

    selected: list[Path] = []
    total = 0
    for p in shuffled:
        if total >= budget_bytes:
            break
        selected.append(p)
        total += p.stat().st_size
    selected.sort()
    return selected


def _select_corpus(
    noise_sample_size: int | str,
    target_mb: float | None = None,
) -> tuple[list[Path], list[Path]]:
    true_files = _collect_files("true_data")
    all_noisy = _collect_files("noisy_data")

    legacy_files = [
        p for p in Path(DOCS_DIR).iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.name != ".gitkeep"
    ]

    if legacy_files:
        logger.info("Found {} legacy top-level docs (treating as true signal)", len(legacy_files))

    true_files = legacy_files + true_files

    # Size-budget mode: always keep ALL clean data, fill noisy up to target_mb total.
    if target_mb is not None:
        true_mb = _dir_size_mb(true_files)
        noisy_budget_mb = max(0.0, target_mb - true_mb)
        logger.info(
            "Size budget: target={:.0f} MB, clean={:.1f} MB (all kept), noisy budget={:.1f} MB",
            target_mb, true_mb, noisy_budget_mb,
        )
        noisy_files = _select_noisy_by_size(all_noisy, noisy_budget_mb)
        return true_files, noisy_files

    if noise_sample_size == "all":
        noisy_files = all_noisy
    else:
        n = int(noise_sample_size)
        if n <= 0 or n >= len(all_noisy):
            noisy_files = all_noisy if n > 0 else []

        else:
            rng = random.Random(SAMPLE_SEED)
            noisy_files = rng.sample(all_noisy, n)
            noisy_files.sort()
    return true_files, noisy_files


def _existing_sources() -> set[str]:
    """Return the set of `source` basenames already present in the Qdrant collection."""
    from app.config import settings
    from app.services.vector_store import get_client

    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in existing:
        return set()

    sources: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=1000,
            with_payload=["source"],
            with_vectors=False,
            offset=offset,
        )
        for p in points:
            if p.payload and p.payload.get("source"):
                sources.add(p.payload["source"])
        if offset is None:
            break
    return sources


def seed_docs(
    noise_sample_size: int | str = 150,
    target_mb: float | None = None,
    skip_existing: bool = False,
    do_ocr: bool = True,
) -> dict:
    from app.models import RetrievedChunk
    from app.services.document_processor import DocumentProcessor
    from app.services.embedding_service import embed_texts
    from app.services.vector_store import upsert_chunks

    if not do_ocr:
        logger.info("OCR DISABLED — extracting embedded text only (fast; ideal for noise docs)")
    processor = DocumentProcessor(do_ocr=do_ocr)
    true_files, noisy_files = _select_corpus(noise_sample_size, target_mb)

    done: set[str] = set()
    if skip_existing:
        done = _existing_sources()
        before_t, before_n = len(true_files), len(noisy_files)
        true_files = [f for f in true_files if f.name not in done]
        noisy_files = [f for f in noisy_files if f.name not in done]
        logger.info(
            "RESUME: {} sources already in Qdrant → skipping {} clean + {} noisy already-done files",
            len(done), before_t - len(true_files), before_n - len(noisy_files),
        )

    total = len(true_files) + len(noisy_files)

    noisy_label = f"{_dir_size_mb(noisy_files):.1f} MB" if target_mb else f"sample={noise_sample_size}"
    logger.info("=" * 60)
    logger.info("INGESTION PLAN")
    logger.info("  true_data  : {} files ({:.1f} MB, full signal)", len(true_files), _dir_size_mb(true_files))
    logger.info("  noisy_data : {} files ({})", len(noisy_files), noisy_label)
    logger.info("  total      : {} files ({:.1f} MB)", total, _dir_size_mb(true_files + noisy_files))
    logger.info("=" * 60)

    
    if total == 0:
        logger.warning("No files found to ingest — did you run `make seed-data`?")
        return {"true_ingested": 0, "noisy_ingested": 0, "failed": 0, "chunks": 0}

    counters = {"true_ingested": 0, "noisy_ingested": 0, "failed": 0, "chunks": 0}
    t0 = time.time()


    for idx, src in enumerate(true_files, start=1):
        _ingest_one(processor, src, idx, total, counters, embed_texts, upsert_chunks, RetrievedChunk)
        if counters["chunks"] > 0 and idx == len(true_files):
            logger.info("✓ All {} true (signal) files done", len(true_files))

    for jdx, src in enumerate(noisy_files, start=1):
        idx = len(true_files) + jdx
        _ingest_one(processor, src, idx, total, counters, embed_texts, upsert_chunks, RetrievedChunk)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("INGESTION COMPLETE in {:.1f} min", elapsed / 60)
    logger.info("  true_data ingested  : {}", counters["true_ingested"])
    logger.info("  noisy_data ingested : {}", counters["noisy_ingested"])
    logger.info("  failed (skipped)    : {}", counters["failed"])
    logger.info("  total chunks upserted: {}", counters["chunks"])
    logger.info("=" * 60)

    return counters


def _ingest_one(processor, src: Path, idx: int, total: int, counters: dict,
                embed_texts_fn, upsert_chunks_fn, RetrievedChunk) -> None:
    label = "true" if "true_data" in str(src) else "noisy"
    logger.info("[{}/{}] start {} {}", idx, total, label, src.name)
    try:
        chunks_meta = processor.process_document(str(src))
        if not chunks_meta:
            logger.warning("[{}/{}] {} {} → 0 chunks (skipped)", idx, total, label, src.name)
            counters["failed"] += 1
            return
        chunks = [RetrievedChunk(text=c["text"], source=c["source"]) for c in chunks_meta]
        texts = [c.text for c in chunks]
        embeddings = embed_texts_fn(texts)
        upsert_chunks_fn(chunks, embeddings)
        counters["chunks"] += len(chunks)
        counters[f"{label}_ingested"] += 1
        if idx % 10 == 0 or idx == total:
            logger.info("  [{}/{}] progress — {} chunks so far",idx, total, counters["chunks"])

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[{}/{}] FAILED {} {}: {}: {} (module={})",
            idx, total, label, src.name, type(exc).__name__, exc, type(exc).__module__,
        )
        counters["failed"] += 1


def run_migrations(conn: psycopg2.extensions.connection) -> None:
    cur = conn.cursor()
    files = sorted([f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")])
    for filename in files:
        path = os.path.join(MIGRATIONS_DIR, filename)
        with open(path) as f:
            sql = f.read()
        logger.info("Running migration: {}", filename)
        cur.execute(sql)
    conn.commit()
    cur.close()

def seed_users(conn: psycopg2.extensions.connection) -> None:
    cur = conn.cursor()
    for username, password, is_admin in DEMO_USERS:
        password_hash = hash_password(password)
        cur.execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                is_admin = EXCLUDED.is_admin
            """,
            (username, password_hash, is_admin),
        )
        logger.info("Seeded user: {} (admin={})", username, is_admin)
    conn.commit()
    cur.close()

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DB + ingest documents")
    parser.add_argument(
        "--no-ingest", action="store_true",
        help="Run migrations + users only; skip vector-store ingestion",
    )
    parser.add_argument(
        "--noise-sample", default="150",
        help="Number of noisy docs to sample (default 150). Use 0 or 'all'.",
    )
    parser.add_argument(
        "--target-mb", default=None, type=float,
        help="Total corpus size budget in MB. Ingests ALL clean data, then fills "
             "noisy data up to this total. Overrides --noise-sample when set.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Resume mode: skip files whose source is already in Qdrant (idempotent).",
    )
    parser.add_argument(
        "--no-ocr", action="store_true",
        help="Disable OCR (extract embedded text only). Much faster; ideal for noise docs.",
    )
    args = parser.parse_args()

    logger.info("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    logger.info("Running migrations...")
    run_migrations(conn)
    logger.info("Seeding demo users...")
    seed_users(conn)
    conn.close()
    logger.info("DB seeding done.")

    if args.no_ingest:
        logger.info("--no-ingest set; skipping doc ingestion.")
        return

    # Parse noise-sample arg (int or 'all')
    noise_arg: int | str = args.noise_sample
    if noise_arg != "all":
        try:
            noise_arg = int(noise_arg)
        except ValueError:
            raise SystemExit(f"--noise-sample must be int or 'all', got {noise_arg!r}")

    seed_docs(
        noise_sample_size=noise_arg,
        target_mb=args.target_mb,
        skip_existing=args.skip_existing,
        do_ocr=not args.no_ocr,
    )

if __name__ == "__main__":
    main()