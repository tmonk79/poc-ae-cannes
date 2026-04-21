# YouTube Drive-in POC

Personal GCP project to validate the YouTube Drive-in technical pipeline before production setup.

## Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed and authenticated
- Python 3.11+
- A GCP project with the following APIs enabled and resources created (see `CLAUDE.md` for full setup commands):
  - App Engine, Cloud Tasks, Firestore, Cloud Storage, Vertex AI
  - Two Cloud Tasks queues: `preroll-queue` and `short-queue`
  - A GCS bucket

## Configuration

1. Edit `backend/app.yaml` and replace the placeholder values:
   ```yaml
   GCP_PROJECT: "your-project-id"
   GCS_BUCKET:  "your-bucket-name"
   ```

2. Edit the Firebase config block in the `/poc/` UI route inside `backend/main.py`:
   ```js
   apiKey:    "YOUR_FIREBASE_API_KEY",
   authDomain: "your-project-id.firebaseapp.com",
   projectId:  "your-project-id",
   ```
   (Firebase console → Project settings → Your apps → Web app config)

3. Drop a test photo at `backend/test_assets/guest.jpg`.

## Local Development

```bash
cd backend

# Create and activate virtual environment (first time only)
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Authenticate with GCP
gcloud auth application-default login

# Run the dev server
flask --app main run --port 8080
```

The UI is available at [http://localhost:8080/poc/](http://localhost:8080/poc/).

> **Note:** Cloud Tasks cannot dispatch to localhost. To test worker routes locally, call them directly:
> ```bash
> curl -X POST http://localhost:8080/_tasks/preroll-worker \
>   -H "Content-Type: application/json" \
>   -d '{"sessionId": "your-session-id"}'
> ```

## Deploy to App Engine

```bash
cd backend
gcloud app deploy
```

## Useful Commands

```bash
# Tail live App Engine logs
gcloud app logs tail -s default

# List Firestore sessions (requires gcloud alpha)
gcloud firestore documents list --collection=sessions

# Inspect a Cloud Tasks queue
gcloud tasks queues describe preroll-queue --location=us-central1
```
