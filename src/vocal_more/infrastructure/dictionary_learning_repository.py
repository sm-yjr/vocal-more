"""SQLite persistence for deferred automatic dictionary-learning jobs."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from ..domain.dictionary_learning_models import (
    DictionaryLearningEvidence,
    DictionaryLearningJob,
    ValidatedDictionaryDecision,
)
from ..domain.dictionary_models import DictionaryMutation
from ..paths import default_app_paths


_REDACTED_EVIDENCE_JSON = json.dumps(
    {
        "raw_text": "",
        "pasted_text": "",
        "original_text": "",
        "baseline_text": "",
        "edited_text": "",
    }
)


class DictionaryLearningRepository:
    """Durable, thread-safe job repository backed by a small SQLite database."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        database_path: Path | None = None,
    ) -> None:
        paths = default_app_paths(base_dir)
        self.database_path = (
            Path(database_path)
            if database_path is not None
            else paths.dictionary_learning_path
        )
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dictionary_learning_jobs (
                    id TEXT PRIMARY KEY,
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    term_created INTEGER NOT NULL DEFAULT 0,
                    aliases_added_json TEXT NOT NULL DEFAULT '[]',
                    model TEXT NOT NULL DEFAULT 'qwen3.7-plus',
                    prompt_version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(dictionary_learning_jobs)"
                ).fetchall()
            }
            if "model" not in columns:
                connection.execute(
                    "ALTER TABLE dictionary_learning_jobs "
                    "ADD COLUMN model TEXT NOT NULL DEFAULT 'qwen3.7-plus'"
                )
            if "prompt_version" not in columns:
                connection.execute(
                    "ALTER TABLE dictionary_learning_jobs "
                    "ADD COLUMN prompt_version INTEGER NOT NULL DEFAULT 1"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dictionary_learning_due
                ON dictionary_learning_jobs(status, next_retry_at, created_at)
                """
            )
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'retry', updated_at = ?, error = 'interrupted'
                WHERE status IN ('processing', 'applying')
                """,
                (time.time(),),
            )

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._initialize()
            self._initialized = True

    def enqueue(
        self,
        evidence: DictionaryLearningEvidence,
        *,
        now: float | None = None,
    ) -> DictionaryLearningJob:
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        job_id = str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dictionary_learning_jobs (
                    id, evidence_json, status, created_at, updated_at,
                    attempt_count, next_retry_at
                ) VALUES (?, ?, 'pending', ?, ?, 0, ?)
                """,
                (
                    job_id,
                    json.dumps(evidence.to_dict(), ensure_ascii=False),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def claim_next(self, *, now: float | None = None) -> DictionaryLearningJob | None:
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id
                FROM dictionary_learning_jobs
                WHERE status IN ('pending', 'retry') AND next_retry_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'processing',
                    updated_at = ?,
                    attempt_count = attempt_count + 1,
                    error = ''
                WHERE id = ?
                """,
                (timestamp, row["id"]),
            )
            connection.commit()
        return self.get(str(row["id"]))

    def schedule_retry(
        self,
        job_id: str,
        *,
        error: str,
        now: float | None = None,
        max_attempts: int = 5,
    ) -> None:
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        job = self.get(job_id)
        if job is None:
            return
        if job.attempt_count >= max_attempts:
            self.mark_failed(job_id, error=error, now=timestamp)
            return
        delay = float(2 ** max(1, job.attempt_count))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'retry', updated_at = ?, next_retry_at = ?, error = ?
                WHERE id = ?
                """,
                (timestamp, timestamp + delay, str(error)[:2_000], job_id),
            )

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        result: ValidatedDictionaryDecision,
        term_created: bool = False,
        aliases_added: list[str] | None = None,
        now: float | None = None,
    ) -> None:
        self._ensure_initialized()
        if status not in ("applied", "review", "ignored"):
            raise ValueError(f"invalid completed status: {status}")
        timestamp = time.time() if now is None else now
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = ?, updated_at = ?, result_json = ?,
                    term_created = ?, aliases_added_json = ?, error = '',
                    evidence_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    timestamp,
                    json.dumps(result.to_dict(), ensure_ascii=False),
                    int(term_created),
                    json.dumps(aliases_added or [], ensure_ascii=False),
                    _REDACTED_EVIDENCE_JSON,
                    job_id,
                ),
            )

    def mark_applying(
        self,
        job_id: str,
        *,
        result: ValidatedDictionaryDecision,
        mutation: DictionaryMutation,
        now: float | None = None,
    ) -> None:
        """Journal a planned YAML mutation before it is applied."""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'applying', updated_at = ?, result_json = ?,
                    term_created = ?, aliases_added_json = ?, error = ''
                WHERE id = ?
                """,
                (
                    timestamp,
                    json.dumps(result.to_dict(), ensure_ascii=False),
                    int(mutation.term_created),
                    json.dumps(mutation.aliases_added, ensure_ascii=False),
                    job_id,
                ),
            )

    def mark_failed(
        self,
        job_id: str,
        *,
        error: str,
        now: float | None = None,
    ) -> None:
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'failed', updated_at = ?, error = ?,
                    evidence_json = ?
                WHERE id = ?
                """,
                (
                    timestamp,
                    str(error)[:2_000],
                    _REDACTED_EVIDENCE_JSON,
                    job_id,
                ),
            )

    def mark_reverted(self, job_id: str, *, now: float | None = None) -> None:
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE dictionary_learning_jobs
                SET status = 'reverted', updated_at = ?
                WHERE id = ? AND status = 'applied'
                """,
                (timestamp, job_id),
            )

    def get(self, job_id: str) -> DictionaryLearningJob | None:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dictionary_learning_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[DictionaryLearningJob]:
        self._ensure_initialized()
        limit = max(1, min(int(limit), 1_000))
        query = "SELECT * FROM dictionary_learning_jobs"
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_job(row) for row in rows]

    def next_due_at(self) -> float | None:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(next_retry_at) AS due_at
                FROM dictionary_learning_jobs
                WHERE status IN ('pending', 'retry')
                """
            ).fetchone()
        if row is None or row["due_at"] is None:
            return None
        return float(row["due_at"])

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> DictionaryLearningJob:
        raw_result = json.loads(row["result_json"]) if row["result_json"] else None
        result = (
            ValidatedDictionaryDecision(
                action=raw_result["action"],
                term=str(raw_result.get("term", "")),
                aliases=list(raw_result.get("aliases", [])),
                confidence=float(raw_result.get("confidence", 0.0)),
                reason_code=str(raw_result.get("reason_code", "")),
            )
            if raw_result
            else None
        )
        return DictionaryLearningJob(
            id=str(row["id"]),
            evidence=DictionaryLearningEvidence.from_dict(
                json.loads(row["evidence_json"])
            ),
            status=row["status"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            attempt_count=int(row["attempt_count"]),
            next_retry_at=float(row["next_retry_at"]),
            error=str(row["error"]),
            result=result,
            term_created=bool(row["term_created"]),
            aliases_added=list(json.loads(row["aliases_added_json"] or "[]")),
            model=str(row["model"]),
            prompt_version=int(row["prompt_version"]),
        )


__all__ = ["DictionaryLearningRepository"]
