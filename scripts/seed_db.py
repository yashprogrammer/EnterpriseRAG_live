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



def _collect_files(subdir: str) -> list[Path]:
    root = Path(DOCS_DIR) / subdir
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.name != ".gitkeep"
    )

def _select_corpus(noise_sample_size: int | str) -> tuple[list[Path], list[Path]]:
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


def seed_docs(noise_sample_size: int | str = 150) -> dict:
    from app.models import RetrievedChunk
    from app.services.document_processor import DocumentProcessor
    from app.services.embedding_service import embed_texts
    from app.services.vector_store import upsert_chunks

    processor = DocumentProcessor()
    true_files, noisy_files = _select_corpus(noise_sample_size)
    total = len(true_files) + len(noisy_files)

    logger.info("=" * 60)
    logger.info("INGESTION PLAN")
    logger.info("  true_data  : {} files (full signal)", len(true_files))
    logger.info("  noisy_data : {} files (sample={})", len(noisy_files), noise_sample_size)
    logger.info("  total      : {} files", total)
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
        logger.warning("[{}/{}] FAILED {} {}: {}", idx, total, label, src.name, type(exc).__name__)
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

    seed_docs(noise_sample_size=noise_arg)

if __name__ == "__main__":
    main()