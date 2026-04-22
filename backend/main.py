"""
Flask app for the YouTube Drive-in POC.

User-facing routes:
  POST /poc/start
  POST /poc/genre
  GET  /poc/session/<session_id>
  GET  /poc/

Cloud Tasks internal routes:
  POST /_tasks/preroll-worker
  POST /_tasks/short-worker
"""

import asyncio
import json
import os
from pathlib import Path

from flask import Flask, jsonify, request, Response, send_from_directory, redirect

import firestore_client as fs
import gcs_client as gcs
from tasks import enqueue_preroll, enqueue_short

app = Flask(__name__)

PROJECT = os.environ.get("GCP_PROJECT", "")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

FRONTEND_DIR = Path(__file__).parent / "frontend"


# ---------------------------------------------------------------------------
# User-facing routes
# ---------------------------------------------------------------------------

@app.route("/poc/start", methods=["POST"])
def start():
    """
    Simulate kiosk photo capture.
    Body (App Engine / production): { "guestImageGcs": "gs://bucket/path/guest.jpg" }
    Body (local dev fallback):      { "guestImagePath": "test_assets/guest.jpg" }
    Returns: { "sessionId": "..." }
    """
    body = request.get_json(force=True)
    import uuid
    session_id = str(uuid.uuid4())

    guest_image_gcs = body.get("guestImageGcs")
    if guest_image_gcs:
        # Kiosk/production path — GCS URI provided directly
        guest_image_uri = guest_image_gcs
    else:
        # Local dev fallback — upload from local file
        local_path = body.get("guestImagePath", "test_assets/guest.jpg")
        gcs_path = f"sessions/{session_id}/input/guest.jpg"
        guest_image_uri = gcs.upload_file(local_path, gcs_path, "image/jpeg")

    # Create Firestore session doc
    # We want the session_id we already generated, so we build the doc directly
    import firestore_client as _fs
    from datetime import datetime, timezone
    doc = {
        "sessionId": session_id,
        "status": "pending",
        "genre": None,
        "guestImage": guest_image_uri,
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
    _fs.get_db().collection("sessions").document(session_id).set(doc)

    enqueue_preroll(session_id)
    return jsonify({"sessionId": session_id}), 202


@app.route("/poc/genre", methods=["POST"])
def genre():
    """
    Simulate guest selecting genre and hitting Play.
    Body: { "sessionId": "...", "genre": "action-adventure" }
    Returns: 202
    """
    body = request.get_json(force=True)
    session_id = body.get("sessionId")
    genre_value = body.get("genre", "action-adventure")

    if not session_id:
        return jsonify({"error": "sessionId required"}), 400

    session = fs.get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    enqueue_short(session_id, genre_value)
    return jsonify({"status": "queued"}), 202


@app.route("/poc/session/<session_id>", methods=["GET"])
def get_session(session_id):
    """Return current Firestore session doc."""
    session = fs.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404
    return jsonify(session)


@app.route("/poc/", methods=["GET"])
def index():
    """Serve the POC frontend."""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/poc/static/<path:filename>", methods=["GET"])
def frontend_static(filename):
    """Serve static frontend assets (e.g. firebase-config.js) from the frontend/ directory."""
    return send_from_directory(FRONTEND_DIR, filename)


@app.route("/poc/media", methods=["GET"])
def media_proxy():
    """
    Serve GCS assets to the browser.

    Locally: streams the file directly (ADC tokens can't sign URLs).
    On App Engine: redirects to a signed URL (service account has a private key).
    Usage: /poc/media?uri=gs://bucket/path/to/file
    """
    uri = request.args.get("uri", "")
    if not uri.startswith("gs://"):
        return jsonify({"error": "invalid uri"}), 400

    path = gcs.gcs_path_from_uri(uri)

    try:
        signed = gcs.signed_url(path, expiration_minutes=15)
        return redirect(signed)
    except AttributeError:
        # Running locally with ADC — stream the file directly instead
        import io
        bucket = gcs.get_client().bucket(GCS_BUCKET)
        blob = bucket.blob(path)
        data = blob.download_as_bytes()
        content_type = blob.content_type or "application/octet-stream"
        return Response(io.BytesIO(data), mimetype=content_type)


# ---------------------------------------------------------------------------
# Cloud Tasks internal routes
# ---------------------------------------------------------------------------

@app.route("/_tasks/preroll-worker", methods=["POST"])
def preroll_worker():
    """Run Phase 1 pipeline for a given sessionId."""
    body = request.get_json(force=True)
    session_id = body.get("sessionId")
    if not session_id:
        return jsonify({"error": "sessionId required"}), 400

    session = fs.get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    from pipeline import run_preroll
    try:
        asyncio.run(run_preroll(session_id, session["guestImage"]))
    except Exception as e:
        fs.set_status(session_id, "failed")
        app.logger.exception("preroll_worker failed for session %s", session_id)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok"}), 200


@app.route("/_tasks/short-worker", methods=["POST"])
def short_worker():
    """Run Phase 2 pipeline for a given sessionId + genre."""
    body = request.get_json(force=True)
    session_id = body.get("sessionId")
    genre = body.get("genre", "action-adventure")
    if not session_id:
        return jsonify({"error": "sessionId required"}), 400

    session = fs.get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    from pipeline import run_short
    try:
        asyncio.run(run_short(session_id, genre, session["guestImage"]))
    except Exception as e:
        fs.set_status(session_id, "failed")
        app.logger.exception("short_worker failed for session %s", session_id)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
