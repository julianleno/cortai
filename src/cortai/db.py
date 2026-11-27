"""SQLite persistence for jobs, clips, and templates.

Opens a fresh connection per call instead of sharing one across threads,
since job updates come from the Flask request thread and from background
processing threads started per submission.
"""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    source_video_path TEXT,
    vocabulary_json TEXT,
    language TEXT,
    render_options_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    start_seconds REAL,
    end_seconds REAL,
    title TEXT,
    hook TEXT,
    justificativa TEXT,
    clip_score REAL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    template_id TEXT,
    result_file TEXT,
    result_path TEXT,
    error TEXT,
    word_overrides_json TEXT,
    render_options_json TEXT,
    crop_x_pct REAL,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
    fingerprint TEXT PRIMARY KEY,
    language TEXT,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


# Columns added after the initial CREATE TABLE (Fase 7): existing local dev
# databases from earlier sessions won't have them yet. SQLite's ADD COLUMN
# is enough here -- no real user data exists yet to warrant a full migration
# framework.
_JOBS_NEW_COLUMNS = {
    "source_video_path": "TEXT",
    "vocabulary_json": "TEXT",
    "language": "TEXT",
    "render_options_json": "TEXT",
}
_CLIPS_NEW_COLUMNS = {
    "start_seconds": "REAL", "end_seconds": "REAL", "title": "TEXT", "hook": "TEXT",
    "justificativa": "TEXT", "clip_score": "REAL", "source": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'rendered'", "template_id": "TEXT", "error": "TEXT",
    "updated_at": "REAL", "word_overrides_json": "TEXT",
    "render_options_json": "TEXT", "crop_x_pct": "REAL",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, column_type in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def init_db(db_path: str, recover_orphans: bool = False) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn, "jobs", _JOBS_NEW_COLUMNS)
        _ensure_columns(conn, "clips", _CLIPS_NEW_COLUMNS)
        conn.commit()
    if recover_orphans:
        cleanup_orphan_jobs(db_path)


def cleanup_orphan_jobs(db_path: str) -> None:
    """Marks unfinished jobs as interrupted without deleting their history.

    Kept under its historical name for compatibility. Startup callers may
    offer a retry/resume action later because both the job and its clips are
    intentionally preserved.
    """
    explanation = "Processamento interrompido pela reinicialização do aplicativo."
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'interrupted', "
            "error = CASE WHEN error IS NULL OR error = '' THEN ? ELSE error || ' | ' || ? END, "
            "updated_at = ? WHERE status IN ('queued', 'processing', 'paused')",
            (explanation, explanation, time.time()),
        )
        conn.commit()


def get_setting(db_path: str, key: str, default: str = None) -> str | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if (row and row["value"]) else default


def set_setting(db_path: str, key: str, value: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?", (key, value, value))
        conn.commit()


def get_all_settings(db_path: str) -> dict[str, str]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def delete_job(db_path: str, job_id: str) -> None:
    """Remove um projeto e todos os seus clipes associados do banco de dados."""
    with closing(_connect(db_path)) as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Keep every connection safe against partial/older databases, including
    # direct callers that did not invoke init_db(). CREATE IF NOT EXISTS is
    # idempotent and migrations below add columns missing from existing tables.
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "jobs", _JOBS_NEW_COLUMNS)
    _ensure_columns(conn, "clips", _CLIPS_NEW_COLUMNS)
    conn.commit()
    return conn


def _json_payload(value: dict | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        raise TypeError("Payload JSON deve ser um dicionario")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json_object(payload: str | None, *, field_name: str) -> dict | None:
    if not payload:
        return None
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} deve conter um objeto JSON")
    return value


def create_job(db_path: str, job_id: str, url: str, start_time: str, end_time: str,
               language: str | None = None, render_options: dict | None = None) -> dict:
    now = time.time()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO jobs (id, url, start_time, end_time, status, progress, error, language, "
            "render_options_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', 0, NULL, ?, ?, ?, ?)",
            (job_id, url, start_time, end_time, language, _json_payload(render_options), now, now),
        )
        conn.commit()
    return get_job(db_path, job_id)


def update_job(db_path: str, job_id: str, **fields) -> None:
    if not fields:
        return
    if "render_options" in fields:
        if "render_options_json" in fields:
            raise ValueError("Use apenas render_options ou render_options_json")
        fields["render_options_json"] = _json_payload(fields.pop("render_options"))
    fields["updated_at"] = time.time()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [job_id]
    with closing(_connect(db_path)) as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)
        conn.commit()


def _with_parsed_job_payloads(job: dict) -> dict:
    job["vocabulary"] = json.loads(job["vocabulary_json"]) if job.get("vocabulary_json") else []
    job["render_options"] = _parse_json_object(
        job.get("render_options_json"), field_name="jobs.render_options_json"
    )
    return job


def get_job(db_path: str, job_id: str) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _with_parsed_job_payloads(dict(row)) if row else None


def list_jobs(db_path: str, limit: int = 50) -> list[dict]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_with_parsed_job_payloads(dict(row)) for row in rows]


def create_clip(db_path: str, clip_id: str, job_id: str, result_file: str, result_path: str,
                render_options: dict | None = None, crop_x_pct: float | None = None) -> dict:
    now = time.time()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO clips (id, job_id, result_file, result_path, render_options_json, crop_x_pct, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (clip_id, job_id, result_file, result_path, _json_payload(render_options), crop_x_pct, now),
        )
        conn.commit()
    return get_clip(db_path, clip_id)


def _with_parsed_clip_payloads(clip: dict) -> dict:
    clip["word_overrides"] = json.loads(clip["word_overrides_json"]) if clip.get("word_overrides_json") else {}
    clip["render_options"] = _parse_json_object(
        clip.get("render_options_json"), field_name="clips.render_options_json"
    )
    return clip


def get_clip(db_path: str, clip_id: str) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _with_parsed_clip_payloads(dict(row)) if row else None


def list_clips_for_job(db_path: str, job_id: str) -> list[dict]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY created_at ASC", (job_id,)
        ).fetchall()
    return [_with_parsed_clip_payloads(dict(row)) for row in rows]


def create_clip_suggestion(db_path: str, clip_id: str, job_id: str, start_seconds: float, end_seconds: float,
                            title: str, hook: str, justificativa: str, clip_score: float, source: str,
                            status: str = "pending", render_options: dict | None = None,
                            crop_x_pct: float | None = None) -> dict:
    """Fase 7: persists one clip suggestion (from clip_selection.suggest_clips)
    before it has been rendered -- result_file/result_path stay NULL until
    update_clip() records a successful render."""
    now = time.time()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO clips (id, job_id, start_seconds, end_seconds, title, hook, justificativa, "
            "clip_score, source, status, render_options_json, crop_x_pct, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (clip_id, job_id, start_seconds, end_seconds, title, hook, justificativa,
             clip_score, source, status, _json_payload(render_options), crop_x_pct, now, now),
        )
        conn.commit()
    return get_clip(db_path, clip_id)


def update_clip(db_path: str, clip_id: str, **fields) -> dict | None:
    if not fields:
        return get_clip(db_path, clip_id)
    if "render_options" in fields:
        if "render_options_json" in fields:
            raise ValueError("Use apenas render_options ou render_options_json")
        fields["render_options_json"] = _json_payload(fields.pop("render_options"))
    fields["updated_at"] = time.time()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [clip_id]
    with closing(_connect(db_path)) as conn:
        conn.execute(f"UPDATE clips SET {columns} WHERE id = ?", values)
        conn.commit()
    return get_clip(db_path, clip_id)


def create_template(db_path: str, template_id: str, name: str, config: dict) -> dict:
    now = time.time()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO templates (id, name, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (template_id, name, json.dumps(config), now, now),
        )
        conn.commit()
    return get_template(db_path, template_id)


def get_template(db_path: str, template_id: str) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        return None
    template = dict(row)
    template["config"] = json.loads(template["config_json"])
    return template


def update_template(db_path: str, template_id: str, name: str = None, config: dict = None) -> dict | None:
    fields = {}
    if name is not None:
        fields["name"] = name
    if config is not None:
        fields["config_json"] = json.dumps(config)
    if not fields:
        return get_template(db_path, template_id)

    fields["updated_at"] = time.time()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [template_id]
    with closing(_connect(db_path)) as conn:
        conn.execute(f"UPDATE templates SET {columns} WHERE id = ?", values)
        conn.commit()
    return get_template(db_path, template_id)


def delete_template(db_path: str, template_id: str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()


def save_transcript(db_path: str, fingerprint: str, language: str | None, transcription: dict) -> None:
    now = time.time()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO transcripts (fingerprint, language, payload_json, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET language = excluded.language, "
            "payload_json = excluded.payload_json, created_at = excluded.created_at",
            (fingerprint, language, json.dumps(transcription), now),
        )
        conn.commit()


def get_cached_transcript(db_path: str, fingerprint: str) -> dict | None:
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def list_templates(db_path: str) -> list[dict]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM templates ORDER BY created_at ASC").fetchall()
    templates = []
    for row in rows:
        template = dict(row)
        template["config"] = json.loads(template["config_json"])
        templates.append(template)
    return templates
