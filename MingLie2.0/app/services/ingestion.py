from __future__ import annotations

from app.repositories import Store


def ingest_candidates(store: Store, job_id: int, source: str, candidates: list[dict], idempotency_key: str | None = None) -> dict:
    if not source.strip():
        raise ValueError("source is required")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    if idempotency_key:
        existing = store.get_ingestion_by_key(idempotency_key)
        if existing:
            return {"replayed": True, "ingestion": existing}
    ingestion = store.create_ingestion(job_id, source.strip(), idempotency_key)
    return {"replayed": False, "ingestion": store.ingest(ingestion["id"], job_id, source.strip(), candidates)}
