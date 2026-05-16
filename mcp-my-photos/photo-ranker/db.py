"""SQLite persistence for job state and photo results."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from jobs import Job, JobProgress, JobStatus

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".photo-ranker" / "jobs.db"


class JobDB:
    """Lightweight SQLite store for job state and results."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_path TEXT NOT NULL,
                request_json TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                progress_json TEXT DEFAULT '{}',
                result_json TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS photo_results (
                job_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                total_score REAL NOT NULL,
                quality_score REAL,
                family_score REAL,
                event_score REAL,
                uniqueness_score REAL,
                scene_description TEXT,
                event_type TEXT,
                faces_detected INTEGER DEFAULT 0,
                known_persons_json TEXT DEFAULT '[]',
                meaningful_score INTEGER DEFAULT 5,
                capture_date TEXT DEFAULT '',
                PRIMARY KEY (job_id, photo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_photo_results_job
                ON photo_results(job_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status);

            CREATE TABLE IF NOT EXISTS known_faces (
                name TEXT NOT NULL,
                face_idx INTEGER NOT NULL DEFAULT 0,
                embedding BLOB NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (name, face_idx)
            );

            CREATE TABLE IF NOT EXISTS face_embeddings (
                photo_id TEXT NOT NULL,
                face_idx INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                bbox_json TEXT DEFAULT '[]',
                gender TEXT DEFAULT '',
                age INTEGER DEFAULT 0,
                expression TEXT DEFAULT 'unknown',
                PRIMARY KEY (photo_id, face_idx)
            );

            CREATE TABLE IF NOT EXISTS job_assets (
                job_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                preview_path TEXT DEFAULT '',
                source_photo_path TEXT DEFAULT '',
                tags_json TEXT DEFAULT '[]',
                selected INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                PRIMARY KEY (job_id, photo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_job_assets_job
                ON job_assets(job_id);

            CREATE TABLE IF NOT EXISTS face_reviews (
                job_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                face_idx INTEGER NOT NULL,
                bbox_json TEXT DEFAULT '[]',
                crop_path TEXT DEFAULT '',
                label_name TEXT DEFAULT '',
                PRIMARY KEY (job_id, photo_id, face_idx),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_face_reviews_job
                ON face_reviews(job_id, photo_id);

            CREATE TABLE IF NOT EXISTS stage_checkpoints (
                job_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                completed_at REAL NOT NULL,
                PRIMARY KEY (job_id, photo_id, stage),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_stage_checkpoints_job
                ON stage_checkpoints(job_id, stage);
            """
        )
        self._ensure_column("jobs", "request_json", "TEXT DEFAULT '{}' ")
        self._ensure_column("face_embeddings", "bbox_json", "TEXT DEFAULT '[]'")
        self._repair_stale_jobs()
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {row[1] for row in rows}
        if column not in columns:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def _repair_stale_jobs(self) -> None:
        """Repair impossible persisted job states left by older sync workflow code."""
        repaired = self._conn.execute(
            """
            UPDATE jobs
            SET
                status = 'completed',
                started_at = COALESCE(started_at, created_at),
                finished_at = COALESCE(finished_at, created_at)
            WHERE
                status = 'pending'
                AND result_json IS NOT NULL
                AND error_message IS NULL
            """
        ).rowcount
        if repaired:
            logger.info("Repaired %d stale pending job records", repaired)

    def save_job(self, job: Job) -> None:
        """Insert or replace a job record."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO jobs
                (id, source, source_path, request_json, status, created_at,
                 started_at, finished_at, progress_json,
                 result_json, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.source,
                job.source_path,
                json.dumps(job.request_options or {}, ensure_ascii=False),
                job.status.value,
                job.created_at,
                job.started_at,
                job.finished_at,
                json.dumps(job.progress.to_dict()),
                json.dumps(job.result_summary) if job.result_summary else None,
                job.error_message,
            ),
        )
        self._conn.commit()

    def load_job(self, job_id: str) -> Job | None:
        """Load a job from DB."""
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def list_jobs(self, status: str | None = None) -> list[Job]:
        """List jobs, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def save_photo_results(
        self, job_id: str, results: list[dict]
    ) -> None:
        """Save ranked photo results for a job."""
        for r in results:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO photo_results
                    (job_id, photo_id, total_score, quality_score,
                     family_score, event_score, uniqueness_score,
                     scene_description, event_type, faces_detected,
                     known_persons_json, meaningful_score, capture_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    r.get("photo_id", ""),
                    r.get("total_score", 0),
                    r.get("quality_score", 0),
                    r.get("family_score", 0),
                    r.get("event_score", 0),
                    r.get("uniqueness_score", 0),
                    r.get("scene_description", ""),
                    r.get("event_type", ""),
                    r.get("faces_detected", 0),
                    json.dumps(r.get("known_persons", [])),
                    r.get("meaningful_score", 5),
                    r.get("capture_date", ""),
                ),
            )
        self._conn.commit()

    def load_photo_results(self, job_id: str) -> list[dict]:
        """Load ranked results for a job."""
        rows = self._conn.execute(
            """SELECT * FROM photo_results
               WHERE job_id = ?
               ORDER BY total_score DESC""",
            (job_id,),
        ).fetchall()
        return [
            {
                "photo_id": r["photo_id"],
                "total_score": r["total_score"],
                "quality_score": r["quality_score"],
                "family_score": r["family_score"],
                "event_score": r["event_score"],
                "uniqueness_score": r["uniqueness_score"],
                "scene_description": r["scene_description"],
                "event_type": r["event_type"],
                "faces_detected": r["faces_detected"],
                "known_persons": json.loads(r["known_persons_json"]),
                "meaningful_score": r["meaningful_score"] if "meaningful_score" in r.keys() else 5,
                "capture_date": r["capture_date"] if "capture_date" in r.keys() else "",
            }
            for r in rows
        ]

    def save_job_asset(
        self,
        job_id: str,
        photo_id: str,
        preview_path: str = "",
        source_photo_path: str = "",
    ) -> None:
        """Persist preview/source paths used by review UIs."""
        self._conn.execute(
            """
            INSERT INTO job_assets
                (job_id, photo_id, preview_path, source_photo_path)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, photo_id) DO UPDATE SET
                preview_path = excluded.preview_path,
                source_photo_path = excluded.source_photo_path
            """,
            (job_id, photo_id, preview_path, source_photo_path),
        )
        self._conn.commit()

    def update_photo_review(
        self,
        job_id: str,
        photo_id: str,
        tags: list[str] | None = None,
        selected: bool | None = None,
        note: str | None = None,
    ) -> dict:
        """Update review metadata for a classified photo."""
        current = self.list_job_assets(job_id).get(
            photo_id,
            {
                "job_id": job_id,
                "photo_id": photo_id,
                "preview_path": "",
                "source_photo_path": "",
                "tags": [],
                "selected": False,
                "note": "",
            },
        )
        next_tags = current["tags"] if tags is None else tags
        next_selected = current["selected"] if selected is None else bool(selected)
        next_note = current["note"] if note is None else note

        self._conn.execute(
            """
            INSERT INTO job_assets
                (job_id, photo_id, preview_path, source_photo_path, tags_json, selected, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, photo_id) DO UPDATE SET
                tags_json = excluded.tags_json,
                selected = excluded.selected,
                note = excluded.note
            """,
            (
                job_id,
                photo_id,
                current["preview_path"],
                current["source_photo_path"],
                json.dumps(next_tags, ensure_ascii=False),
                1 if next_selected else 0,
                next_note,
            ),
        )
        self._conn.commit()
        current["tags"] = next_tags
        current["selected"] = next_selected
        current["note"] = next_note
        return current

    def list_job_assets(self, job_id: str) -> dict[str, dict]:
        """Load preview/source/review metadata for a job."""
        rows = self._conn.execute(
            "SELECT * FROM job_assets WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        return {
            row["photo_id"]: {
                "job_id": row["job_id"],
                "photo_id": row["photo_id"],
                "preview_path": row["preview_path"],
                "source_photo_path": row["source_photo_path"],
                "tags": json.loads(row["tags_json"] or "[]"),
                "selected": bool(row["selected"]),
                "note": row["note"] or "",
            }
            for row in rows
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Stage Checkpoints ──

    def save_checkpoint(
        self, job_id: str, stage: str, photo_id: str, candidate_dict: dict,
    ) -> None:
        """Save a per-photo stage checkpoint for resume support."""
        import time as _time

        self._conn.execute(
            """
            INSERT OR REPLACE INTO stage_checkpoints
                (job_id, photo_id, stage, candidate_json, completed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, photo_id, stage, json.dumps(candidate_dict), _time.time()),
        )
        self._conn.commit()

    def load_checkpoints(
        self, job_id: str, stage: str,
    ) -> dict[str, dict]:
        """Load all checkpointed candidates for a job stage.

        Returns:
            {photo_id: candidate_dict}
        """
        rows = self._conn.execute(
            "SELECT photo_id, candidate_json FROM stage_checkpoints "
            "WHERE job_id = ? AND stage = ?",
            (job_id, stage),
        ).fetchall()
        return {r["photo_id"]: json.loads(r["candidate_json"]) for r in rows}

    def clear_checkpoints(self, job_id: str) -> None:
        """Remove all checkpoints for a completed/cancelled job."""
        self._conn.execute(
            "DELETE FROM stage_checkpoints WHERE job_id = ?", (job_id,),
        )
        self._conn.commit()

    # ── Known Faces ──

    def save_known_face(self, name: str, embedding: list[float]) -> int:
        """Register a known person's face embedding. Returns face_idx."""
        import struct
        import time

        blob = struct.pack(f"{len(embedding)}f", *embedding)

        # Find next face_idx for this name
        row = self._conn.execute(
            "SELECT COALESCE(MAX(face_idx), -1) + 1 FROM known_faces WHERE name = ?",
            (name,),
        ).fetchone()
        face_idx = row[0]

        self._conn.execute(
            "INSERT OR REPLACE INTO known_faces (name, face_idx, embedding, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, face_idx, blob, time.time()),
        )
        self._conn.commit()
        return face_idx

    def load_known_faces(self) -> dict[str, list[list[float]]]:
        """Load all known faces as {name: [embedding, ...]}."""
        import struct

        rows = self._conn.execute(
            "SELECT name, embedding FROM known_faces ORDER BY name, face_idx"
        ).fetchall()

        result: dict[str, list[list[float]]] = {}
        for row in rows:
            name = row["name"]
            blob = row["embedding"]
            n_floats = len(blob) // 4
            embedding = list(struct.unpack(f"{n_floats}f", blob))
            if name not in result:
                result[name] = []
            result[name].append(embedding)
        return result

    def delete_known_face(self, name: str) -> int:
        """Delete all embeddings for a known person. Returns deleted count."""
        cursor = self._conn.execute(
            "DELETE FROM known_faces WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cursor.rowcount

    def list_known_faces(self) -> list[dict]:
        """List registered known faces with count per person."""
        rows = self._conn.execute(
            "SELECT name, COUNT(*) as count FROM known_faces GROUP BY name ORDER BY name"
        ).fetchall()
        return [{"name": r["name"], "embedding_count": r["count"]} for r in rows]

    # ── Face Embedding Cache ──

    def save_face_embedding(
        self,
        photo_id: str,
        face_idx: int,
        embedding: list[float],
        bbox: list[int] | tuple[int, int, int, int] | None = None,
        gender: str = "",
        age: int = 0,
        expression: str = "unknown",
    ) -> None:
        """Cache a face embedding for a photo."""
        import struct

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO face_embeddings "
            "(photo_id, face_idx, embedding, bbox_json, gender, age, expression) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                photo_id,
                face_idx,
                blob,
                json.dumps(list(bbox) if bbox else []),
                gender,
                age,
                expression,
            ),
        )
        self._conn.commit()

    def load_face_embeddings(self, photo_id: str) -> list[dict]:
        """Load cached face embeddings for a photo."""
        import struct

        rows = self._conn.execute(
            "SELECT * FROM face_embeddings WHERE photo_id = ? ORDER BY face_idx",
            (photo_id,),
        ).fetchall()

        results = []
        for row in rows:
            blob = row["embedding"]
            n_floats = len(blob) // 4
            embedding = list(struct.unpack(f"{n_floats}f", blob))
            results.append({
                "face_idx": row["face_idx"],
                "embedding": embedding,
                "bbox": json.loads(row["bbox_json"] or "[]"),
                "gender": row["gender"],
                "age": row["age"],
                "expression": row["expression"],
            })
        return results

    def save_face_review(
        self,
        job_id: str,
        photo_id: str,
        face_idx: int,
        bbox: list[int] | None = None,
        crop_path: str = "",
        label_name: str = "",
    ) -> None:
        """Persist face review metadata used by tagging UI."""
        self._conn.execute(
            """
            INSERT INTO face_reviews
                (job_id, photo_id, face_idx, bbox_json, crop_path, label_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, photo_id, face_idx) DO UPDATE SET
                bbox_json = excluded.bbox_json,
                crop_path = excluded.crop_path,
                label_name = CASE
                    WHEN excluded.label_name != '' THEN excluded.label_name
                    ELSE face_reviews.label_name
                END
            """,
            (
                job_id,
                photo_id,
                face_idx,
                json.dumps(bbox or []),
                crop_path,
                label_name,
            ),
        )
        self._conn.commit()

    def list_face_reviews(self, job_id: str, photo_id: str) -> list[dict]:
        """Return per-face review metadata for one classified photo."""
        rows = self._conn.execute(
            "SELECT * FROM face_reviews WHERE job_id = ? AND photo_id = ? ORDER BY face_idx",
            (job_id, photo_id),
        ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "photo_id": row["photo_id"],
                "face_idx": row["face_idx"],
                "bbox": json.loads(row["bbox_json"] or "[]"),
                "crop_path": row["crop_path"] or "",
                "label_name": row["label_name"] or "",
            }
            for row in rows
        ]

    def label_face_review(
        self,
        job_id: str,
        photo_id: str,
        face_idx: int,
        name: str,
    ) -> None:
        """Assign a human-readable label to a detected face."""
        self._conn.execute(
            """
            INSERT INTO face_reviews (job_id, photo_id, face_idx, label_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, photo_id, face_idx) DO UPDATE SET
                label_name = excluded.label_name
            """,
            (job_id, photo_id, face_idx, name),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_job(row) -> Job:
        progress_data = json.loads(row["progress_json"] or "{}")
        return Job(
            id=row["id"],
            source=row["source"],
            source_path=row["source_path"],
            request_options=json.loads(row["request_json"] or "{}"),
            status=JobStatus(row["status"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            progress=JobProgress(
                total=progress_data.get("total", 0),
                completed=progress_data.get("completed", 0),
                stage=progress_data.get("stage", ""),
                current_file=progress_data.get("current_file", ""),
                errors=progress_data.get("errors", []),
            ),
            result_summary=(
                json.loads(row["result_json"])
                if row["result_json"]
                else None
            ),
            error_message=row["error_message"],
        )
