# -*- coding: utf-8 -*-
"""SQLite-backed workspace store for projects, works, and UI state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def _connect(path):
    workspace_path = Path(path)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(workspace_path))
    _ensure_schema(connection)
    _migrate_legacy_state(connection)
    return connection


@contextmanager
def _open_connection(path):
    connection = _connect(path)
    try:
        yield connection
    finally:
        connection.close()


def _ensure_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            project_name TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
            work_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            work_name TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            UNIQUE(project_id, work_name)
        )
        """
    )
    connection.commit()


def _load_plugin_state_map(connection):
    rows = connection.execute(
        "SELECT key, value FROM plugin_state"
    ).fetchall()
    state = {}
    for key, value in rows:
        try:
            state[key] = json.loads(value)
        except json.JSONDecodeError:
            state[key] = value
    return state


def _dump_json(value):
    return json.dumps(value, ensure_ascii=False)


def _serialize_tuple_dict(values):
    serialized = {}
    for key, value in (values or {}).items():
        if isinstance(key, tuple):
            key_text = ":".join(str(part) for part in key)
        else:
            key_text = str(key)
        serialized[key_text] = value
    return serialized


def _deserialize_tuple_dict(values):
    deserialized = {}
    for key_text, value in (values or {}).items():
        if ":" in str(key_text):
            parts = str(key_text).split(":")
            try:
                key = tuple(int(part) for part in parts)
            except ValueError:
                key = tuple(parts)
        else:
            key = key_text
        if isinstance(value, list):
            value = tuple(value)
        deserialized[key] = value
    return deserialized


def _normalize_snapshot_for_storage(snapshot, project_id, project_name, work_id, work_name):
    normalized_snapshot = dict(snapshot)
    normalized_snapshot["project_id"] = project_id
    normalized_snapshot["project_name"] = project_name
    normalized_snapshot["work_id"] = work_id
    normalized_snapshot["work_name"] = work_name
    normalized_snapshot["geo_values"] = _serialize_tuple_dict(
        normalized_snapshot.get("geo_values", {})
    )
    normalized_snapshot["station_reference_targets"] = _serialize_tuple_dict(
        normalized_snapshot.get("station_reference_targets", {})
    )
    record_data = dict(normalized_snapshot.get("project_record", {}))
    if record_data:
        record_data["project_name"] = project_name
        record_data["business_name"] = work_name
    normalized_snapshot["project_record"] = record_data
    return normalized_snapshot


def _normalize_snapshot_from_storage(snapshot):
    normalized_snapshot = dict(snapshot or {})
    normalized_snapshot["geo_values"] = _deserialize_tuple_dict(
        normalized_snapshot.get("geo_values", {})
    )
    normalized_snapshot["station_reference_targets"] = _deserialize_tuple_dict(
        normalized_snapshot.get("station_reference_targets", {})
    )
    return normalized_snapshot


def _project_id_for_name(project_name):
    return str(project_name or "").strip()


def _work_id_for_names(project_name, work_name):
    return f"{str(project_name or '').strip()}\t{str(work_name or '').strip()}"


def _migrate_legacy_state(connection):
    project_count = connection.execute(
        "SELECT COUNT(*) FROM projects"
    ).fetchone()[0]
    work_count = connection.execute(
        "SELECT COUNT(*) FROM works"
    ).fetchone()[0]
    if project_count or work_count:
        return

    state = _load_plugin_state_map(connection)
    legacy_works = state.get("works") or state.get("project_snapshots") or {}
    legacy_projects = state.get("projects") or {}
    if not legacy_works and not legacy_projects:
        return

    projects = {}
    for project_id, project_data in legacy_projects.items():
        normalized_id = _project_id_for_name(project_id)
        if not normalized_id:
            continue
        projects[normalized_id] = {
            "project_id": normalized_id,
            "project_name": str(project_data.get("project_name") or normalized_id).strip(),
        }

    for work_id, snapshot in legacy_works.items():
        record_data = dict(snapshot.get("project_record", {}))
        project_name = str(
            snapshot.get("project_id")
            or record_data.get("project_name")
            or snapshot.get("project_name")
            or ""
        ).strip()
        work_name = str(
            record_data.get("business_name")
            or snapshot.get("work_name")
            or ""
        ).strip()
        if not project_name or not work_name:
            continue
        project_id = _project_id_for_name(project_name)
        projects.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project_name": project_name,
            },
        )
        record_data["project_name"] = project_name
        record_data["business_name"] = work_name
        normalized_work_id = _work_id_for_names(project_name, work_name)
        normalized_snapshot = _normalize_snapshot_for_storage(
            snapshot,
            project_id,
            project_name,
            normalized_work_id,
            work_name,
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO works (work_id, project_id, work_name, snapshot_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                normalized_work_id,
                project_id,
                work_name,
                _dump_json(normalized_snapshot),
            ),
        )

    for project_id, project_data in projects.items():
        connection.execute(
            """
            INSERT OR REPLACE INTO projects (project_id, project_name)
            VALUES (?, ?)
            """,
            (project_id, project_data["project_name"]),
        )
    connection.commit()


def load_workspace_state(path):
    workspace_path = Path(path)
    if not workspace_path.exists():
        return {}

    with _open_connection(workspace_path) as connection:
        return _load_plugin_state_map(connection)


def save_workspace_state(path, state):
    with _open_connection(path) as connection:
        for key, value in state.items():
            connection.execute(
                """
                INSERT INTO plugin_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, _dump_json(value)),
            )
        connection.commit()


def list_projects(path):
    workspace_path = Path(path)
    if not workspace_path.exists():
        return []
    with _open_connection(workspace_path) as connection:
        rows = connection.execute(
            "SELECT project_id, project_name FROM projects ORDER BY project_name COLLATE NOCASE"
        ).fetchall()
    return [
        {"project_id": project_id, "project_name": project_name}
        for project_id, project_name in rows
    ]


def list_works(path, project_name):
    workspace_path = Path(path)
    if not workspace_path.exists():
        return []
    project_id = _project_id_for_name(project_name)
    with _open_connection(workspace_path) as connection:
        rows = connection.execute(
            """
            SELECT work_id, work_name
            FROM works
            WHERE project_id = ?
            ORDER BY work_name COLLATE NOCASE
            """,
            (project_id,),
        ).fetchall()
    return [
        {"work_id": work_id, "work_name": work_name}
        for work_id, work_name in rows
    ]


def load_work_snapshot(path, project_name, work_name):
    workspace_path = Path(path)
    if not workspace_path.exists():
        return None
    work_id = _work_id_for_names(project_name, work_name)
    with _open_connection(workspace_path) as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM works WHERE work_id = ?",
            (work_id,),
        ).fetchone()
    if row is None:
        return None
    return _normalize_snapshot_from_storage(json.loads(row[0]))


def save_project_entry(path, project_name):
    project_id = _project_id_for_name(project_name)
    with _open_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, project_name)
            VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET project_name=excluded.project_name
            """,
            (project_id, project_name),
        )
        connection.commit()


def save_work_snapshot(path, project_name, work_name, snapshot):
    project_id = _project_id_for_name(project_name)
    work_id = _work_id_for_names(project_name, work_name)
    normalized_snapshot = _normalize_snapshot_for_storage(
        snapshot,
        project_id,
        project_name,
        work_id,
        work_name,
    )
    with _open_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO projects (project_id, project_name)
            VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET project_name=excluded.project_name
            """,
            (project_id, project_name),
        )
        connection.execute(
            """
            INSERT INTO works (work_id, project_id, work_name, snapshot_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET
                project_id=excluded.project_id,
                work_name=excluded.work_name,
                snapshot_json=excluded.snapshot_json
            """,
            (work_id, project_id, work_name, _dump_json(normalized_snapshot)),
        )
        connection.commit()


def rename_project(path, old_name, new_name):
    old_project_id = _project_id_for_name(old_name)
    new_project_id = _project_id_for_name(new_name)
    with _open_connection(path) as connection:
        rows = connection.execute(
            "SELECT work_name, snapshot_json FROM works WHERE project_id = ?",
            (old_project_id,),
        ).fetchall()
        connection.execute("DELETE FROM projects WHERE project_id = ?", (old_project_id,))
        connection.execute(
            """
            INSERT INTO projects (project_id, project_name)
            VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET project_name=excluded.project_name
            """,
            (new_project_id, new_name),
        )
        connection.execute("DELETE FROM works WHERE project_id = ?", (old_project_id,))
        for work_name, snapshot_json in rows:
            snapshot = _normalize_snapshot_from_storage(json.loads(snapshot_json))
            normalized_work_id = _work_id_for_names(new_name, work_name)
            normalized_snapshot = _normalize_snapshot_for_storage(
                snapshot,
                new_project_id,
                new_name,
                normalized_work_id,
                work_name,
            )
            connection.execute(
                """
                INSERT INTO works (work_id, project_id, work_name, snapshot_json)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_work_id, new_project_id, work_name, _dump_json(normalized_snapshot)),
            )
        connection.commit()


def rename_work(path, project_name, old_name, new_name):
    snapshot = load_work_snapshot(path, project_name, old_name)
    if snapshot is None:
        return
    delete_work(path, project_name, old_name)
    save_work_snapshot(path, project_name, new_name, snapshot)


def delete_project(path, project_name):
    project_id = _project_id_for_name(project_name)
    with _open_connection(path) as connection:
        connection.execute("DELETE FROM works WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        connection.commit()


def delete_work(path, project_name, work_name):
    work_id = _work_id_for_names(project_name, work_name)
    with _open_connection(path) as connection:
        connection.execute("DELETE FROM works WHERE work_id = ?", (work_id,))
        connection.commit()
