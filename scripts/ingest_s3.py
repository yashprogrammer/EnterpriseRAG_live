from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote_plus

import boto3
from loguru import logger

from app.models import RetrievedChunk
from app.services.document_processor import DocumentProcessor
from app.services.embedding_service import embed_texts
from app.services.vector_store import upsert_chunks

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".md"}
SAMPLE_SEED = 42


def _iter_objects(bucket: str, prefix: str) -> list[dict]:
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if Path(key).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            objects.append({"key": key, "size": int(obj["Size"])})
    return sorted(objects, key=lambda item: item["key"])


def _select_by_size(objects: list[dict], size_mb: int, seed: int) -> list[dict]:
    if size_mb <= 0:
        return []

    limit_bytes = size_mb * 1024 * 1024
    candidates = objects[:]
    random.Random(seed).shuffle(candidates)

    selected: list[dict] = []
    selected_bytes = 0
    for obj in candidates:
        size = obj["size"]
        if size > limit_bytes and not selected:
            selected.append(obj)
            break
        if selected_bytes + size <= limit_bytes:
            selected.append(obj)
            selected_bytes += size

    return sorted(selected, key=lambda item: item["key"])


def _download(bucket: str, key: str, tmp_dir: Path) -> Path:
    client = boto3.client("s3")
    filename = Path(unquote_plus(key)).name
    dest = tmp_dir / filename
    client.download_file(bucket, key, str(dest))
    return dest


def ingest_s3(
    bucket: str,
    prefix: str,
    size_mb: int,
    seed: int,
    shard_index: int = 0,
    shard_count: int = 1,
    do_ocr: bool = True,
) -> dict[str, int]:
    objects = _iter_objects(bucket, prefix)
    selected = _select_by_size(objects, size_mb, seed)
    if shard_count < 1:
        raise ValueError("--shard-count must be >= 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("--shard-index must be between 0 and shard-count - 1")
    selected = [
        obj for idx, obj in enumerate(selected)
        if idx % shard_count == shard_index
    ]
    selected_mb = sum(obj["size"] for obj in selected) / 1024 / 1024

    logger.info("=" * 60)
    logger.info("S3 INGESTION PLAN")
    logger.info("  bucket     : {}", bucket)
    logger.info("  prefix     : {}", prefix)
    logger.info("  candidates : {} files", len(objects))
    logger.info("  selected   : {} files, {:.1f} MB", len(selected), selected_mb)
    logger.info("  shard      : {}/{}", shard_index + 1, shard_count)
    logger.info("=" * 60)

    counters = {"ingested": 0, "failed": 0, "chunks": 0}
    if not selected:
        return counters

    if not do_ocr:
        logger.info("OCR DISABLED — extracting embedded text only (fast path)")
    processor = DocumentProcessor(do_ocr=do_ocr)
    t0 = time.time()
    tmp_root = Path(tempfile.mkdtemp(prefix="adv-rag-s3-ingest-"))
    try:
        for idx, obj in enumerate(selected, start=1):
            key = obj["key"]
            logger.info("[{}/{}] start s3://{}/{}", idx, len(selected), bucket, key)
            local_path: Path | None = None
            try:
                local_path = _download(bucket, key, tmp_root)
                chunks_meta = processor.process_document(str(local_path))
                if not chunks_meta:
                    logger.warning("[{}/{}] 0 chunks: {}", idx, len(selected), key)
                    counters["failed"] += 1
                    continue
                chunks = [
                    RetrievedChunk(text=chunk["text"], source=f"s3://{bucket}/{key}")
                    for chunk in chunks_meta
                ]
                embeddings = embed_texts([chunk.text for chunk in chunks])
                upsert_chunks(chunks, embeddings)
                counters["ingested"] += 1
                counters["chunks"] += len(chunks)
                logger.info(
                    "[{}/{}] done: {} chunks, {} total chunks",
                    idx,
                    len(selected),
                    len(chunks),
                    counters["chunks"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[{}/{}] FAILED {}: {} {}",
                    idx,
                    len(selected),
                    key,
                    type(exc).__name__,
                    repr(exc),
                )
                counters["failed"] += 1
            finally:
                if local_path is not None:
                    local_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("S3 INGESTION COMPLETE in {:.1f} min", elapsed / 60)
    logger.info("  files ingested      : {}", counters["ingested"])
    logger.info("  failed/skipped      : {}", counters["failed"])
    logger.info("  total chunks upserted: {}", counters["chunks"])
    logger.info("=" * 60)
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a size-bounded S3 corpus into Qdrant")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="noisy_data/")
    parser.add_argument("--size-mb", type=int, default=200)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument(
        "--no-ocr", action="store_true",
        help="Disable OCR (extract embedded text only). Much faster; ideal for noise docs.",
    )
    args = parser.parse_args()

    ingest_s3(
        args.bucket, args.prefix, args.size_mb, args.seed,
        args.shard_index, args.shard_count, do_ocr=not args.no_ocr,
    )


if __name__ == "__main__":
    main()
