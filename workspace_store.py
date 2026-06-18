# -*- coding: utf-8 -*-
"""Minimal SQLite workspace store for plugin session data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _ensure_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.commit()


def load_workspace_state(path):
    workspace_path = Path(path)
    if not workspace_path.exists():
        return {}

    with sqlite3.connect(str(workspace_path)) as connection:
        _ensure_schema(connection)
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


def save_workspace_state(path, state):
    workspace_path = Path(path)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(workspace_path)) as connection:
        _ensure_schema(connection)
        for key, value in state.items():
            connection.execute(
                """
                INSERT INTO plugin_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )
        connection.commit()
