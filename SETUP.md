# Setup Guide

Step-by-step instructions to get the YouTube Drive-in POC running from scratch.

---

## Prerequisites

Before starting, make sure you have:

- [ ] [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed
- [ ] Python 3.11+
- [ ] A personal GCP project created (or create one at console.cloud.google.com)
- [ ] Billing enabled on the project (required for Vertex AI and App Engine)

---

## Step 1 — Authenticate and target your project

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

Confirm it's set:

```bash
gcloud config get project
```

---

## Step 2 — Enable required APIs

```bash
gcloud services enable \
  appengine.googleapis.com \
  cloudtasks.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com
```

This can take a minute or two.

---

## Step 3 — Create GCP resources

Run these once. If a resource already exists, the command will error safely — just skip it.

**App Engine app** (pick `us-central1` for Vertex AI proximity):
```bash
gcloud app create --region=us-central1
```

**Firestore database** (native mode):
```bash
gcloud firestore databases create --location=us-central1
```

**GCS bucket** (name must be globally unique):
```bash
gsutil mb -l us-central1 gs://YOUR_BUCKET_NAME
```

**Cloud Tasks queues:**
```bash
gcloud tasks queues create preroll-queue --location=us-central1
gcloud tasks queues create short-queue --location=us-central1
```

---

## Step 4 — Create the service account

This is the identity App Engine will use to call Vertex AI, Firestore, and GCS.

```bash
gcloud iam service-accounts create drivein-poc \
  --display-name="Drive-in POC Service Account"
```

Grant the required roles:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:drivein-poc@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:drivein-poc@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:drivein-poc@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

## Step 5 — Verify Veo access

> **Do this before building anything else.** Veo requires explicit enablement and is the highest-risk unknown.

Run a quick smoke test from the Python REPL:

```python
import vertexai
from vertexai.preview.vision_models import VideoGenerationModel

vertexai.init(project="YOUR_PROJECT_ID", location="us-central1")
model = VideoGenerationModel.from_pretrained("veo-3.0-fast-generate-preview")
print("Veo access confirmed")
```

If this fails with a permission or quota error, contact your GCP account team to enable Veo on the project before continuing.

---

## Step 6 — Configure the backend

Edit `backend/app.yaml` and replace the two placeholder values:

```yaml
env_variables:
  GCP_PROJECT: "YOUR_PROJECT_ID"    # ← your project ID
  GCS_BUCKET:  "YOUR_BUCKET_NAME"   # ← the bucket you created in Step 3
```

---

## Step 7 — Configure the Firebase frontend

The UI uses the Firestore JS SDK for real-time updates. You need your project's Firebase web config.

1. Go to [Firebase Console](https://console.firebase.google.com) → select your GCP project
2. Project Settings → Your apps → click **Add app** → choose **Web**
3. Register the app (name it anything, e.g. "drive-in-poc")
4. Copy the config object

Then edit `frontend/firebase-config.js`:

```js
export const firebaseConfig = {
  apiKey:        "YOUR_API_KEY",
  authDomain:    "YOUR_PROJECT_ID.firebaseapp.com",
  projectId:     "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.appspot.com",
};
```

> `firebase-config.js` is gitignored — never commit it.

---

## Step 8 — Add a test guest photo

Drop any portrait photo at:

```
backend/test_assets/guest.jpg
```

This is the reference image used as input for the Vertex AI image generation calls.

---

## Step 9 — Create a virtual environment and install dependencies

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Step 10 — Run locally

```bash
# In the backend/ directory
source .venv/bin/activate
gcloud auth application-default login
flask --app main run --port 8080
```

Open the UI at **http://localhost:8080/poc/**

> **Cloud Tasks does not work with localhost.** When running locally, trigger the pipeline workers directly with curl instead:
>
> ```bash
> # After calling /poc/start, grab the sessionId from the response, then:
> curl -X POST http://localhost:8080/_tasks/preroll-worker \
>   -H "Content-Type: application/json" \
>   -d '{"sessionId": "PASTE_SESSION_ID_HERE"}'
>
> curl -X POST http://localhost:8080/_tasks/short-worker \
>   -H "Content-Type: application/json" \
>   -d '{"sessionId": "PASTE_SESSION_ID_HERE", "genre": "action-adventure"}'
> ```

---

## Step 11 — Deploy to App Engine

Once local testing is working:

```bash
cd backend
gcloud app deploy
```

After deploy, Cloud Tasks will dispatch automatically and the full end-to-end flow works.

View logs:
```bash
gcloud app logs tail -s default
```

---

## Checklist summary

| # | Task | Done |
|---|------|------|
| 1 | GCP auth + project set | [ ] |
| 2 | APIs enabled | [ ] |
| 3 | App Engine, Firestore, GCS bucket, Cloud Tasks queues created | [ ] |
| 4 | Service account created with correct roles | [ ] |
| 5 | Veo access verified | [ ] |
| 6 | `backend/app.yaml` configured | [ ] |
| 7 | `frontend/firebase-config.js` configured | [ ] |
| 8 | Test guest photo added | [ ] |
| 9 | `pip install -r requirements.txt` done | [ ] |
| 10 | Local run working at http://localhost:8080/poc/ | [ ] |
| 11 | Deployed to App Engine | [ ] |
