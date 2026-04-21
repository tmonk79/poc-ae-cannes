import os

from google.cloud import storage

_client = None
BUCKET = os.environ.get("GCS_BUCKET", "")


def get_client():
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def upload_bytes(data: bytes, gcs_path: str, content_type: str) -> str:
    """Upload raw bytes to GCS. Returns gs:// URI."""
    bucket = get_client().bucket(BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{BUCKET}/{gcs_path}"


def upload_file(local_path: str, gcs_path: str, content_type: str) -> str:
    """Upload a local file to GCS. Returns gs:// URI."""
    bucket = get_client().bucket(BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(local_path, content_type=content_type)
    return f"gs://{BUCKET}/{gcs_path}"


def signed_url(gcs_path: str, expiration_minutes: int = 60) -> str:
    """Return a signed HTTPS URL for a GCS object."""
    from datetime import timedelta
    bucket = get_client().bucket(BUCKET)
    blob = bucket.blob(gcs_path)
    return blob.generate_signed_url(
        expiration=timedelta(minutes=expiration_minutes),
        method="GET",
        version="v4",
    )


def gcs_path_from_uri(uri: str) -> str:
    """Strip gs://<bucket>/ prefix from a GCS URI."""
    prefix = f"gs://{BUCKET}/"
    if uri.startswith(prefix):
        return uri[len(prefix):]
    return uri
