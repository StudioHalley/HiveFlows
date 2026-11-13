"""Tests — activity_bus (Phase 7 Bloc B13)."""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── build_event ───────────────────────────────────────────────────────────────

def test_build_event_structure():
    from services.activity_bus import build_event

    raw = build_event(
        event_type="task_created",
        workspace_id="ws-123",
        source="user:abc",
        data={"type": "python", "name": "Run Script"},
        task_id="task-456",
    )
    event = json.loads(raw)
    assert event["type"] == "task_created"
    assert event["workspace_id"] == "ws-123"
    assert event["source"] == "user:abc"
    assert event["task_id"] == "task-456"
    assert event["data"]["name"] == "Run Script"
    assert "timestamp" in event


def test_build_event_no_task_id():
    from services.activity_bus import build_event

    raw = build_event("app_started", "ws-99", "beekeeper", {"app": "redis"})
    event = json.loads(raw)
    assert event["task_id"] is None
    assert event["type"] == "app_started"


def test_build_event_all_types():
    from services.activity_bus import build_event

    types = [
        "task_created", "task_progress", "task_done",
        "task_error", "task_cancelled",
        "app_started", "app_stopped", "app_crashed",
    ]
    for t in types:
        raw = build_event(t, "ws-x", "system", {})
        assert json.loads(raw)["type"] == t


# ── activity_channel ──────────────────────────────────────────────────────────

def test_activity_channel_format():
    from services.activity_bus import activity_channel

    assert activity_channel("abc-123") == "hive:ws:abc-123:activity"

