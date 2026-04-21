"""
Cloud Tasks enqueue helpers.
"""

import json
import os

from google.cloud import tasks_v2
from google.protobuf import duration_pb2

PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("TASKS_LOCATION", "us-central1")

_client = None


def get_client():
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def _enqueue(queue_name: str, handler_path: str, payload: dict):
    client = get_client()
    parent = client.queue_path(PROJECT, LOCATION, queue_name)

    # On App Engine, the task targets the same app via relative URL
    task = {
        "app_engine_http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "relative_uri": handler_path,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
        },
        # Give tasks up to 10 minutes (App Engine standard limit)
        "dispatch_deadline": duration_pb2.Duration(seconds=600),
    }
    client.create_task(parent=parent, task=task)


def enqueue_preroll(session_id: str):
    _enqueue("preroll-queue", "/_tasks/preroll-worker", {"sessionId": session_id})


def enqueue_short(session_id: str, genre: str):
    _enqueue("short-queue", "/_tasks/short-worker", {"sessionId": session_id, "genre": genre})
