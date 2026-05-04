"""SQLite-backed mailbox index storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


def utc_now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def encode_json(value: object) -> str:
    """Serialize a value for storage in SQLite JSON text columns."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def decode_json(value: str | None) -> Any:
    """Deserialize SQLite JSON text, returning None for empty values."""
    if value is None:
        return None
    return json.loads(value)


def normalize_message_id(message_id: str | None) -> str | None:
    """Normalize an RFC Message-ID header value.

    Args:
        message_id: Header value from Thunderbird summary or full headers.

    Returns:
        Lowercase Message-ID without wrapping angle brackets, or None.
    """
    if message_id is None:
        return None
    normalized = message_id.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1]
    normalized = normalized.strip().lower()
    return normalized or None


def sha256_text(value: str) -> str:
    """Return the SHA-256 hex digest for UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class InvalidIntegerFieldError(TypeError):
    """Raised when an integer-like payload field has the wrong shape."""

    def __init__(self, value: object) -> None:
        """Initialize the error with the invalid value type."""
        super().__init__(f"Expected integer-like value, got {type(value).__name__}.")


def required_int(value: object) -> int:
    """Convert an expected integer-like value or raise a clear error."""
    if isinstance(value, bool):
        raise InvalidIntegerFieldError(value)
    if isinstance(value, int | str):
        return int(value)
    raise InvalidIntegerFieldError(value)


def canonical_identity(message: Mapping[str, object]) -> tuple[str, str, str | None]:
    """Resolve canonical identity for one message observation.

    Args:
        message: Message observation payload from Thunderbird.

    Returns:
        Tuple of canonical key, identity kind, and normalized Message-ID.
    """
    raw_message_id = message.get("message_id")
    message_id = normalize_message_id(raw_message_id if isinstance(raw_message_id, str) else None)
    if message_id is not None:
        return message_id, "message_id", message_id

    body_text = str(message.get("body_text") or "")
    fallback_parts = [
        str(message.get("subject") or ""),
        str(message.get("author") or ""),
        str(message.get("date") or ""),
        encode_json(message.get("recipients") or []),
        sha256_text(body_text),
    ]
    return f"fallback:{sha256_text('|'.join(fallback_parts))}", "fallback_hash", None


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a plain dictionary."""
    return dict(row)


class MailboxIndexStore:
    """Persist Thunderbird mailbox index runs and message observations."""

    def __init__(self, db_path: Path) -> None:
        """Create a store for a SQLite database path.

        Args:
            db_path: SQLite file path. Parent directories are created.
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create all database tables and indexes if they do not exist."""
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def start_run(self) -> str:
        """Create a new index run and return its id."""
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO index_runs (
                  id, started_at, status, folder_count,
                  message_observation_count, error_count, summary_json
                )
                VALUES (?, ?, 'running', 0, 0, 0, '{}')
                """,
                (run_id, now),
            )
        return run_id

    def record_folder(self, run_id: str, folder: Mapping[str, object]) -> None:
        """Record one folder traversal observation."""
        error_json = folder.get("error")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO folder_observations (
                  run_id, account_id, folder_id, folder_path, folder_name,
                  folder_special_use_json, is_unified, is_virtual, is_tag,
                  included, message_count_seen, error_json, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, account_id, folder_id) DO UPDATE SET
                  folder_path = excluded.folder_path,
                  folder_name = excluded.folder_name,
                  folder_special_use_json = excluded.folder_special_use_json,
                  is_unified = excluded.is_unified,
                  is_virtual = excluded.is_virtual,
                  is_tag = excluded.is_tag,
                  included = excluded.included,
                  message_count_seen = excluded.message_count_seen,
                  error_json = excluded.error_json,
                  observed_at = excluded.observed_at
                """,
                (
                    run_id,
                    str(folder["account_id"]),
                    str(folder["folder_id"]),
                    str(folder["folder_path"]),
                    str(folder.get("folder_name") or ""),
                    encode_json(folder.get("folder_special_use") or []),
                    bool(folder.get("is_unified", False)),
                    bool(folder.get("is_virtual", False)),
                    bool(folder.get("is_tag", False)),
                    bool(folder.get("included", True)),
                    required_int(folder.get("message_count_seen", 0)),
                    encode_json(error_json) if error_json else None,
                    utc_now(),
                ),
            )

    def ingest_messages(self, run_id: str, messages: Iterable[Mapping[str, object]]) -> int:
        """Ingest a batch of message observations.

        Args:
            run_id: Existing index run id.
            messages: Message observations from Thunderbird.

        Returns:
            Number of observations accepted.
        """
        accepted = 0
        now = utc_now()
        with self.connect() as connection:
            for message in messages:
                self._ingest_one(connection, run_id, message, now)
                accepted += 1
        return accepted

    def finish_run(self, run_id: str) -> dict[str, Any]:
        """Complete a run, deactivate missing observed-folder locations, and summarize it."""
        with self.connect() as connection:
            self._deactivate_missing_locations(connection, run_id)
            summary = self._run_summary(connection, run_id, "completed")
            connection.execute(
                """
                UPDATE index_runs
                SET finished_at = ?, status = 'completed', folder_count = ?,
                    message_observation_count = ?, error_count = ?, summary_json = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    summary["folder_count"],
                    summary["message_observation_count"],
                    summary["error_count"],
                    encode_json(summary),
                    run_id,
                ),
            )
        return summary

    def fail_run(self, run_id: str, error: Mapping[str, object]) -> dict[str, Any]:
        """Mark a run failed and store error context."""
        with self.connect() as connection:
            summary = self._run_summary(connection, run_id, "failed")
            summary["error"] = dict(error)
            connection.execute(
                """
                UPDATE index_runs
                SET finished_at = ?, status = 'failed', summary_json = ?
                WHERE id = ?
                """,
                (utc_now(), encode_json(summary), run_id),
            )
        return summary

    def latest_run_summary(self) -> dict[str, Any] | None:
        """Return the most recently started run summary."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM index_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return self._stored_run_summary(row) if row else None

    def get_run_summary(self, run_id: str) -> dict[str, Any] | None:
        """Return one stored run summary."""
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM index_runs WHERE id = ?", (run_id,)).fetchone()
        return self._stored_run_summary(row) if row else None

    def get_message(self, canonical_key: str) -> dict[str, Any]:
        """Return a canonical message with locations and recent observations."""
        with self.connect() as connection:
            message = connection.execute(
                "SELECT * FROM canonical_messages WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()
            if message is None:
                raise KeyError(canonical_key)
            locations = connection.execute(
                """
                SELECT * FROM message_locations
                WHERE canonical_key = ?
                ORDER BY active DESC, folder_path
                """,
                (canonical_key,),
            ).fetchall()
            observations = connection.execute(
                """
                SELECT * FROM message_observations
                WHERE canonical_key = ?
                ORDER BY observed_at DESC
                LIMIT 20
                """,
                (canonical_key,),
            ).fetchall()
        return {
            "canonical_message": self._message_row(message),
            "locations": [self._location_row(row) for row in locations],
            "recent_observations": [self._observation_row(row) for row in observations],
        }

    def list_inactive_locations(self) -> list[dict[str, Any]]:
        """List recently inactive message locations."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM message_locations
                WHERE active = 0
                ORDER BY last_seen_at DESC, folder_path
                """
            ).fetchall()
        return [self._location_row(row) for row in rows]

    def list_duplicates(self) -> list[dict[str, Any]]:
        """List canonical keys that currently have multiple active locations."""
        with self.connect() as connection:
            keys = connection.execute(
                """
                SELECT canonical_key, COUNT(*) AS active_location_count
                FROM message_locations
                WHERE active = 1
                GROUP BY canonical_key
                HAVING COUNT(*) > 1
                ORDER BY canonical_key
                """
            ).fetchall()
            duplicates = []
            for row in keys:
                locations = connection.execute(
                    """
                    SELECT account_id, folder_id, folder_path
                    FROM message_locations
                    WHERE canonical_key = ? AND active = 1
                    ORDER BY folder_path
                    """,
                    (row["canonical_key"],),
                ).fetchall()
                duplicates.append(
                    {
                        "canonical_key": row["canonical_key"],
                        "active_location_count": row["active_location_count"],
                        "locations": [row_to_dict(location) for location in locations],
                    }
                )
        return duplicates

    def list_folder_errors(self) -> list[dict[str, Any]]:
        """List folder observations that recorded traversal errors."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, account_id, folder_id, folder_path, error_json
                FROM folder_observations
                WHERE error_json IS NOT NULL
                ORDER BY observed_at DESC
                """
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "account_id": row["account_id"],
                "folder_id": row["folder_id"],
                "folder_path": row["folder_path"],
                "error": decode_json(row["error_json"]),
            }
            for row in rows
        ]

    def _ingest_one(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        message: Mapping[str, object],
        observed_at: str,
    ) -> None:
        canonical_key, identity_kind, message_id = canonical_identity(message)
        body_text = str(message.get("body_text") or "")
        body_sha256 = sha256_text(body_text)
        recipients = message.get("recipients") or []
        headers = message.get("headers") or {}
        account_id = str(message["account_id"])
        folder_id = str(message["folder_id"])
        context = {
            "canonical_key": canonical_key,
            "identity_kind": identity_kind,
            "message_id": message_id,
            "body_sha256": body_sha256,
            "account_id": account_id,
            "folder_id": folder_id,
            "observed_at": observed_at,
        }

        connection.execute(
            """
            INSERT INTO canonical_messages (
              canonical_key, identity_kind, message_id, subject, author,
              recipients_json, date, body_text, body_sha256, headers_json,
              first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
              subject = excluded.subject,
              author = excluded.author,
              recipients_json = excluded.recipients_json,
              date = excluded.date,
              body_text = excluded.body_text,
              body_sha256 = excluded.body_sha256,
              headers_json = excluded.headers_json,
              last_seen_at = excluded.last_seen_at
            """,
            (
                canonical_key,
                identity_kind,
                message_id,
                str(message.get("subject") or ""),
                str(message.get("author") or ""),
                encode_json(recipients),
                str(message.get("date") or ""),
                body_text,
                body_sha256,
                encode_json(headers),
                observed_at,
                observed_at,
            ),
        )
        self._upsert_location(connection, run_id, message, context)
        self._insert_observation(connection, run_id, message, context)

    def _upsert_location(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        message: Mapping[str, object],
        context: Mapping[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO message_locations (
              canonical_key, account_id, folder_id, folder_path, folder_name,
              folder_special_use_json, is_unified, is_virtual, is_tag, active,
              first_seen_at, last_seen_at, last_runtime_message_id,
              last_observed_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(canonical_key, account_id, folder_id) DO UPDATE SET
              folder_path = excluded.folder_path,
              folder_name = excluded.folder_name,
              folder_special_use_json = excluded.folder_special_use_json,
              is_unified = excluded.is_unified,
              is_virtual = excluded.is_virtual,
              is_tag = excluded.is_tag,
              active = 1,
              last_seen_at = excluded.last_seen_at,
              last_runtime_message_id = excluded.last_runtime_message_id,
              last_observed_run_id = excluded.last_observed_run_id
            """,
            (
                str(context["canonical_key"]),
                str(context["account_id"]),
                str(context["folder_id"]),
                str(message.get("folder_path") or ""),
                str(message.get("folder_name") or ""),
                encode_json(message.get("folder_special_use") or []),
                bool(message.get("is_unified", False)),
                bool(message.get("is_virtual", False)),
                bool(message.get("is_tag", False)),
                str(context["observed_at"]),
                str(context["observed_at"]),
                required_int(message["runtime_message_id"]),
                run_id,
            ),
        )

    def _insert_observation(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        message: Mapping[str, object],
        context: Mapping[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO message_observations (
              run_id, runtime_message_id, canonical_key, identity_kind,
              account_id, folder_id, folder_path, message_id, subject, author,
              date, body_sha256, observed_at, error_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                required_int(message["runtime_message_id"]),
                str(context["canonical_key"]),
                str(context["identity_kind"]),
                str(message["account_id"]),
                str(message["folder_id"]),
                str(message.get("folder_path") or ""),
                context["message_id"],
                str(message.get("subject") or ""),
                str(message.get("author") or ""),
                str(message.get("date") or ""),
                str(context["body_sha256"]),
                str(context["observed_at"]),
                encode_json(message.get("error")) if message.get("error") else None,
            ),
        )

    def _deactivate_missing_locations(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE message_locations
            SET active = 0
            WHERE active = 1
              AND last_observed_run_id != ?
              AND EXISTS (
                SELECT 1
                FROM folder_observations folders
                WHERE folders.run_id = ?
                  AND folders.account_id = message_locations.account_id
                  AND folders.folder_id = message_locations.folder_id
                  AND folders.included = 1
                  AND folders.error_json IS NULL
              )
            """,
            (run_id, run_id),
        )

    def _run_summary(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        status: str,
    ) -> dict[str, Any]:
        fallback_rows = connection.execute(
            """
            SELECT DISTINCT canonical_key
            FROM message_observations
            WHERE run_id = ? AND identity_kind = 'fallback_hash'
            ORDER BY canonical_key
            """,
            (run_id,),
        ).fetchall()
        folder_count = self._count(connection, "folder_observations", run_id)
        message_count = self._count(connection, "message_observations", run_id)
        error_count = self._error_count(connection, run_id)
        return {
            "run_id": run_id,
            "status": status,
            "folder_count": folder_count,
            "message_observation_count": message_count,
            "active_location_count": self._active_location_count(connection),
            "inactive_location_count": self._inactive_location_count(connection),
            "fallback_identity_count": len(fallback_rows),
            "fallback_identity_keys": [row["canonical_key"] for row in fallback_rows],
            "error_count": error_count,
        }

    def _stored_run_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        summary = decode_json(row["summary_json"]) or {}
        if summary:
            return summary
        return {
            "run_id": row["id"],
            "status": row["status"],
            "folder_count": row["folder_count"],
            "message_observation_count": row["message_observation_count"],
            "error_count": row["error_count"],
        }

    def _count(self, connection: sqlite3.Connection, table_name: str, run_id: str) -> int:
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM {table_name} WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["count"])

    def _error_count(self, connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM folder_observations
               WHERE run_id = ? AND error_json IS NOT NULL)
              +
              (SELECT COUNT(*) FROM message_observations
               WHERE run_id = ? AND error_json IS NOT NULL) AS count
            """,
            (run_id, run_id),
        ).fetchone()
        return int(row["count"])

    def _active_location_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM message_locations WHERE active = 1"
        ).fetchone()
        return int(row["count"])

    def _inactive_location_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM message_locations WHERE active = 0"
        ).fetchone()
        return int(row["count"])

    def _message_row(self, row: sqlite3.Row) -> dict[str, Any]:
        message = row_to_dict(row)
        message["recipients"] = decode_json(message.pop("recipients_json"))
        message["headers"] = decode_json(message.pop("headers_json"))
        return message

    def _location_row(self, row: sqlite3.Row) -> dict[str, Any]:
        location = row_to_dict(row)
        location["folder_special_use"] = decode_json(location.pop("folder_special_use_json"))
        location["active"] = bool(location["active"])
        location["is_unified"] = bool(location["is_unified"])
        location["is_virtual"] = bool(location["is_virtual"])
        location["is_tag"] = bool(location["is_tag"])
        return location

    def _observation_row(self, row: sqlite3.Row) -> dict[str, Any]:
        observation = row_to_dict(row)
        observation["error"] = decode_json(observation.pop("error_json"))
        return observation


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS index_runs (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  folder_count INTEGER NOT NULL DEFAULT 0,
  message_observation_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS canonical_messages (
  canonical_key TEXT PRIMARY KEY,
  identity_kind TEXT NOT NULL CHECK (identity_kind IN ('message_id', 'fallback_hash')),
  message_id TEXT,
  subject TEXT NOT NULL,
  author TEXT NOT NULL,
  recipients_json TEXT NOT NULL,
  date TEXT NOT NULL,
  body_text TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  headers_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_locations (
  canonical_key TEXT NOT NULL REFERENCES canonical_messages(canonical_key),
  account_id TEXT NOT NULL,
  folder_id TEXT NOT NULL,
  folder_path TEXT NOT NULL,
  folder_name TEXT NOT NULL,
  folder_special_use_json TEXT NOT NULL,
  is_unified INTEGER NOT NULL,
  is_virtual INTEGER NOT NULL,
  is_tag INTEGER NOT NULL,
  active INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_runtime_message_id INTEGER NOT NULL,
  last_observed_run_id TEXT NOT NULL REFERENCES index_runs(id),
  PRIMARY KEY (canonical_key, account_id, folder_id)
);

CREATE TABLE IF NOT EXISTS message_observations (
  run_id TEXT NOT NULL REFERENCES index_runs(id),
  runtime_message_id INTEGER NOT NULL,
  canonical_key TEXT NOT NULL REFERENCES canonical_messages(canonical_key),
  identity_kind TEXT NOT NULL CHECK (identity_kind IN ('message_id', 'fallback_hash')),
  account_id TEXT NOT NULL,
  folder_id TEXT NOT NULL,
  folder_path TEXT NOT NULL,
  message_id TEXT,
  subject TEXT NOT NULL,
  author TEXT NOT NULL,
  date TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  error_json TEXT,
  PRIMARY KEY (run_id, runtime_message_id)
);

CREATE TABLE IF NOT EXISTS folder_observations (
  run_id TEXT NOT NULL REFERENCES index_runs(id),
  account_id TEXT NOT NULL,
  folder_id TEXT NOT NULL,
  folder_path TEXT NOT NULL,
  folder_name TEXT NOT NULL,
  folder_special_use_json TEXT NOT NULL,
  is_unified INTEGER NOT NULL,
  is_virtual INTEGER NOT NULL,
  is_tag INTEGER NOT NULL,
  included INTEGER NOT NULL,
  message_count_seen INTEGER NOT NULL,
  error_json TEXT,
  observed_at TEXT NOT NULL,
  PRIMARY KEY (run_id, account_id, folder_id)
);

CREATE INDEX IF NOT EXISTS idx_message_locations_active
ON message_locations(active, canonical_key);

CREATE INDEX IF NOT EXISTS idx_message_observations_canonical_key
ON message_observations(canonical_key, observed_at);

CREATE INDEX IF NOT EXISTS idx_folder_observations_errors
ON folder_observations(run_id, error_json);
"""
