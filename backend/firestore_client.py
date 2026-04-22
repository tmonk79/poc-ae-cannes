import os
import uuid
from datetime import datetime, timezone

from google.cloud import firestore

_db = None
FIRESTORE_DB = os.environ.get("FIRESTORE_DB", "drivein-poc")


def get_db():
    global _db
    if _db is None:
        _db = firestore.Client(database=FIRESTORE_DB)
    return _db


def create_session(guest_image_gcs_uri: str) -> str:
    session_id = str(uuid.uuid4())
    doc = {
        "sessionId": session_id,
        "status": "pending",
        "genre": None,
        "guestImage": guest_image_gcs_uri,
        "createdAt": datetime.now(timezone.utc),
        "assets": {
            "imageAd1": {"uri": None, "status": "pending"},
            "imageAd2": {"uri": None, "status": "pending"},
            "videoAd": {"uri": None, "status": "pending"},
            "poster": {"uri": None, "status": "pending"},
            "shots": [
                {"shot": i, "imageUri": None, "videoUri": None, "status": "pending"}
                for i in range(6)
            ],
            "shortFinal": {"uri": None, "status": "pending"},
        },
        "timing": {
            "prerollStarted": None,
            "prerollComplete": None,
            "shortStarted": None,
            "shortComplete": None,
        },
    }
    get_db().collection("sessions").document(session_id).set(doc)
    return session_id


def get_session(session_id: str) -> dict | None:
    doc = get_db().collection("sessions").document(session_id).get()
    return doc.to_dict() if doc.exists else None


def update_session(session_id: str, fields: dict):
    get_db().collection("sessions").document(session_id).update(fields)


def set_status(session_id: str, status: str):
    update_session(session_id, {"status": status})


def set_timing(session_id: str, key: str):
    update_session(session_id, {f"timing.{key}": datetime.now(timezone.utc)})


def set_asset(session_id: str, asset_key: str, uri: str):
    """Update a top-level asset (imageAd1, imageAd2, videoAd, poster, shortFinal)."""
    update_session(session_id, {
        f"assets.{asset_key}.uri": uri,
        f"assets.{asset_key}.status": "complete",
    })


def set_shot_image(session_id: str, shot_index: int, uri: str):
    doc_ref = get_db().collection("sessions").document(session_id)
    doc = doc_ref.get().to_dict()
    shots = doc["assets"]["shots"]
    shots[shot_index]["imageUri"] = uri
    shots[shot_index]["status"] = "image_complete"
    doc_ref.update({"assets.shots": shots})


def set_shot_video(session_id: str, shot_index: int, uri: str):
    doc_ref = get_db().collection("sessions").document(session_id)
    doc = doc_ref.get().to_dict()
    shots = doc["assets"]["shots"]
    shots[shot_index]["videoUri"] = uri
    shots[shot_index]["status"] = "complete"
    doc_ref.update({"assets.shots": shots})


def set_genre(session_id: str, genre: str):
    update_session(session_id, {"genre": genre})
