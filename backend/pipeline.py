"""
Vertex AI generation functions for Phase 1 (preroll) and Phase 2 (short).

Two SDKs in use:
  - google-genai — all generation calls (image + video).
    * Image with guest reference: client.models.edit_image() + SubjectReferenceImage
      using model imagen-3.0-capability-001. Guest GCS URI passed directly.
    * Image without reference: client.models.generate_images() using imagen-4.0-fast-generate-001
    * Video: client.models.generate_videos() using veo-3.0-fast-generate-001 (long-polling)
  - vertexai (google-cloud-aiplatform) — no longer used for generation.
    Kept in requirements for potential future use.

All public functions are async. Blocking SDK calls run in run_in_executor
to avoid blocking the asyncio event loop. Veo calls use a polling loop
inside the executor (time.sleep is fine in a thread).
"""

import asyncio
import contextlib
import os
import tempfile
import time

from google import genai
from google.genai import types
from google.genai.types import (
    SubjectReferenceImage,
    SubjectReferenceConfig,
    EditImageConfig,
    Image as GenaiImage,
    GenerateImagesConfig,
)

import firestore_client as fs
import gcs_client as gcs

PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

# Confirmed available on poc-yt-cannes-494105
IMAGE_MODEL = "imagen-4.0-fast-generate-001"          # text-to-image (no reference)
IMAGE_MODEL_CAPABILITY = "imagen-3.0-capability-001"  # image with SubjectReferenceImage
VIDEO_MODEL = "veo-3.0-fast-generate-001"

# Veo clip duration. 6 shots × 8s = 48s final Short.
SHOT_DURATION_SECONDS = 8

_genai_client_instance = None


def _genai_client() -> genai.Client:
    global _genai_client_instance
    if _genai_client_instance is None:
        _genai_client_instance = genai.Client(
            vertexai=True, project=PROJECT, location=LOCATION
        )
    return _genai_client_instance


# ---------------------------------------------------------------------------
# Image generation (Nano Banana = Imagen)
# ---------------------------------------------------------------------------

async def _generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    reference_image_gcs: str | None = None,
) -> bytes:
    """
    Generate an image and return PNG bytes.

    With reference_image_gcs: uses edit_image() + SubjectReferenceImage on
    imagen-3.0-capability-001. The guest GCS URI is passed directly — no
    local download needed. Reference is tagged as [1] in the prompt.

    Without reference_image_gcs: uses generate_images() on
    imagen-4.0-fast-generate-001 (text-to-image only, used for video ad).
    """
    loop = asyncio.get_event_loop()

    def _call():
        client = _genai_client()

        if reference_image_gcs:
            # Inject the reference tag into the prompt
            ref_prompt = prompt + " The person in this image is [1]."
            subject_ref = SubjectReferenceImage(
                reference_id=1,
                reference_image=GenaiImage(gcs_uri=reference_image_gcs),
                config=SubjectReferenceConfig(
                    subject_description="the guest, the main subject of the image",
                    subject_type="SUBJECT_TYPE_PERSON",
                ),
            )
            result = client.models.edit_image(
                model=IMAGE_MODEL_CAPABILITY,
                prompt=ref_prompt,
                reference_images=[subject_ref],
                config=EditImageConfig(
                    edit_mode="EDIT_MODE_DEFAULT",
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    person_generation="ALLOW_ADULT",
                ),
            )
        else:
            result = client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    person_generation="ALLOW_ADULT",
                ),
            )

        return result.generated_images[0].image.image_bytes

    return await loop.run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# Video generation (Veo)
# ---------------------------------------------------------------------------

async def _generate_video(
    prompt: str,
    reference_image_path: str | None = None,
    duration_seconds: int = SHOT_DURATION_SECONDS,
) -> bytes:
    """
    Generate a video via Veo and return MP4 bytes.

    reference_image_path must be a local file path (not a gs:// URI).
    Veo calls are long-polling — this blocks in an executor thread until done.
    """
    loop = asyncio.get_event_loop()

    def _call():
        client = _genai_client()
        kwargs: dict = {
            "model": VIDEO_MODEL,
            "prompt": prompt,
            "config": types.GenerateVideosConfig(
                aspect_ratio="16:9",
                number_of_videos=1,
                duration_seconds=duration_seconds,
                person_generation="allow_adult",
                generate_audio=False,
            ),
        }

        if reference_image_path:
            kwargs["image"] = types.Image.from_file(location=reference_image_path)

        operation = client.models.generate_videos(**kwargs)
        while not operation.done:
            time.sleep(15)
            operation = client.operations.get(operation)

        return operation.result.generated_videos[0].video.video_bytes

    return await loop.run_in_executor(None, _call)


def _write_temp_image(image_bytes: bytes) -> str:
    """Write image bytes to a temp file and return the path. Caller must delete."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(image_bytes)
    tmp.flush()
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# Phase 1 — Preroll pipeline
# ---------------------------------------------------------------------------

async def run_preroll(session_id: str, guest_image_gcs: str):
    """
    Generate all Phase 1 assets in parallel via asyncio.gather:
      - imageAd1, imageAd2 (PNG 1:1)
      - videoAd (MP4, text-to-video)
      - poster (PNG 9:16)

    Updates Firestore per asset as each completes.
    """
    fs.set_status(session_id, "preroll_running")
    fs.set_timing(session_id, "prerollStarted")

    base_path = f"sessions/{session_id}"

    async def make_image_ad1():
        data = await _generate_image(
            prompt=(
                "A vibrant cinematic advertisement for a YouTube Drive-in movie night. "
                "The guest is the star — enjoying popcorn under a glowing screen, "
                "warm neon light, cinematic wide shot."
            ),
            aspect_ratio="1:1",
            reference_image_gcs=guest_image_gcs,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/image_ad_1.png", "image/png")
        fs.set_asset(session_id, "imageAd1", uri)

    async def make_image_ad2():
        data = await _generate_image(
            prompt=(
                "A retro drive-in movie advertisement. Neon signs, starry sky, "
                "the guest as the hero of the show. Bold typography, vintage poster style."
            ),
            aspect_ratio="1:1",
            reference_image_gcs=guest_image_gcs,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/image_ad_2.png", "image/png")
        fs.set_asset(session_id, "imageAd2", uri)

    async def make_video_ad():
        data = await _generate_video(
            prompt=(
                "Cinematic promotional ad for a YouTube Drive-in movie night. "
                "Low aerial drone shot glides forward over a packed 1950s-style drive-in lot at dusk — "
                "rows of gleaming vintage cars, their windows reflecting a giant screen as it flickers to life. "
                "The projection beam cuts dramatically through hazy night air. "
                "Neon concession stand signs buzz on in the foreground. "
                "Quick cut to a close-up of popcorn being tossed into the air in slow motion, "
                "golden kernels catching the screen light. "
                "End on a wide pull-back revealing the full glowing amphitheatre under a starry sky. "
                "Colour grade: warm amber, rich shadows, film grain. High energy, euphoric mood."
            ),
            duration_seconds=6,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/video_ad.mp4", "video/mp4")
        fs.set_asset(session_id, "videoAd", uri)

    async def make_poster():
        data = await _generate_image(
            prompt=(
                "A cinematic 9:16 vertical movie poster. "
                "The person in this image is [1], the star of the film — shown in a dramatic hero pose, "
                "facing slightly off-camera, centre-frame. "
                "Epic Hollywood blockbuster style: deep shadow, rich contrast, volumetric god-rays piercing "
                "through dark storm clouds behind them. "
                "Background: a giant glowing drive-in movie screen at night, rows of vintage cars, "
                "warm amber and teal colour grading. "
                "Bold sans-serif title text at the top reads 'YOUR STORY'. "
                "Tagline in smaller type near the bottom: 'One night. One screen. Unforgettable.' "
                "Photorealistic, ultra-detailed, anamorphic lens flare, IMAX quality."
            ),
            aspect_ratio="9:16",
            reference_image_gcs=guest_image_gcs,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/poster/poster.png", "image/png")
        fs.set_asset(session_id, "poster", uri)

    await asyncio.gather(
        make_image_ad1(),
        make_image_ad2(),
        make_video_ad(),
        make_poster(),
    )

    fs.set_status(session_id, "preroll_complete")
    fs.set_timing(session_id, "prerollComplete")


# ---------------------------------------------------------------------------
# Phase 2 — Short pipeline
# ---------------------------------------------------------------------------

SHOT_PROMPTS: dict[str, list[str]] = {
    "action-adventure": [
        "Epic wide shot of a hero charging across a rooftop at sunset, city skyline behind",
        "Close-up of determined eyes scanning the horizon, wind in hair",
        "Slow-motion explosion behind a running silhouette, debris flying",
        "Hero leaping between buildings, city far below, golden hour light",
        "Intense face-off between hero and villain in a dark rain-soaked alley",
        "Triumphant hero standing on a cliff edge, arms outstretched, golden light",
    ],
    "romance": [
        "Two people meeting eyes across a crowded candlelit cafe, soft focus background",
        "Slow walk along a rain-soaked Parisian street, sharing a single umbrella",
        "Laughing together on a rooftop terrace, city lights twinkling below",
        "A shy hand-hold at the cinema, faces lit by the screen",
        "Intimate candlelit dinner, nervous smiles across the table",
        "First kiss under a shower of falling cherry blossoms",
    ],
    "sci-fi": [
        "Vast alien landscape, twin suns setting over purple mountains",
        "Hero piloting a sleek spacecraft through a dense asteroid field",
        "Holographic star map flickering in a dark command room",
        "Close-up of a robot hand gently touching a human hand",
        "Epic space battle — laser bursts illuminating the void",
        "Spacecraft descending through clouds toward Earth, crowds cheering below",
    ],
    "comedy": [
        "Slapstick mishap — office coffee machine going completely haywire",
        "Bewildered expression as a dog calmly walks off with the TV remote",
        "Hilariously awkward elevator ride with way too many people squeezed in",
        "Surprise birthday cake launched directly into someone's face",
        "Frantic chase scene through a busy farmers market, vegetables flying",
        "Victorious fist-pump — accidentally hitting the ceiling fan",
    ],
}

DEFAULT_SHOT_PROMPTS = [
    f"Cinematic shot {i + 1} of 6 — a dramatic, beautifully lit scene for a personalised YouTube Drive-in Short film"
    for i in range(6)
]


def _get_shot_prompts(genre: str) -> list[str]:
    return SHOT_PROMPTS.get(genre, DEFAULT_SHOT_PROMPTS)


async def _process_shot(
    session_id: str,
    shot_index: int,
    prompt: str,
    guest_image_gcs: str,
) -> bytes:
    """
    Generate image then video for a single shot, updating Firestore after each step.

    Flow:
      1. Generate image (Imagen 4) → upload to GCS → set shot status image_complete
      2. Write image to temp file
      3. Generate video (Veo, image-to-video) → upload to GCS → set shot status complete
    """
    base_path = f"sessions/{session_id}/short"
    tmp_path = None

    try:
        # Step 1 — generate shot image
        image_bytes = await _generate_image(
            prompt=prompt,
            aspect_ratio="16:9",
            reference_image_gcs=guest_image_gcs,
        )
        image_uri = gcs.upload_bytes(
            image_bytes,
            f"{base_path}/shot_{shot_index}_image.png",
            "image/png",
        )
        fs.set_shot_image(session_id, shot_index, image_uri)

        # Step 2 — generate video from that image (image-to-video)
        tmp_path = _write_temp_image(image_bytes)
        video_bytes = await _generate_video(
            prompt=prompt,
            reference_image_path=tmp_path,
            duration_seconds=SHOT_DURATION_SECONDS,
        )
        video_uri = gcs.upload_bytes(
            video_bytes,
            f"{base_path}/shot_{shot_index}_video.mp4",
            "video/mp4",
        )
        fs.set_shot_video(session_id, shot_index, video_uri)

        return video_bytes

    finally:
        if tmp_path:
            with contextlib.suppress(Exception):
                os.unlink(tmp_path)


async def run_short(session_id: str, genre: str, guest_image_gcs: str):
    """
    Generate all 6 shots in parallel via asyncio.gather, then concatenate.
    Updates Firestore per shot as each completes.
    Final MP4 = 6 shots × 8s = 48s.
    """
    fs.set_status(session_id, "short_running")
    fs.set_timing(session_id, "shortStarted")
    fs.set_genre(session_id, genre)

    prompts = _get_shot_prompts(genre)

    video_results = await asyncio.gather(
        *[_process_shot(session_id, i, prompts[i], guest_image_gcs) for i in range(6)]
    )

    final_mp4 = await _concatenate_videos(list(video_results))

    final_uri = gcs.upload_bytes(
        final_mp4,
        f"sessions/{session_id}/short/final.mp4",
        "video/mp4",
    )
    fs.set_asset(session_id, "shortFinal", final_uri)
    fs.set_status(session_id, "complete")
    fs.set_timing(session_id, "shortComplete")


# ---------------------------------------------------------------------------
# Video concatenation
# ---------------------------------------------------------------------------

async def _concatenate_videos(video_bytes_list: list[bytes]) -> bytes:
    """Concatenate MP4 clips using moviepy. Runs in executor to avoid blocking."""
    loop = asyncio.get_event_loop()

    def _call():
        from moviepy.editor import VideoFileClip, concatenate_videoclips

        tmp_files: list[str] = []
        clips: list[VideoFileClip] = []
        out_path = None

        try:
            for data in video_bytes_list:
                tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                tmp.write(data)
                tmp.flush()
                tmp.close()
                tmp_files.append(tmp.name)
                clips.append(VideoFileClip(tmp.name))

            final = concatenate_videoclips(clips)
            out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            out_path = out_tmp.name
            out_tmp.close()
            final.write_videofile(out_path, codec="libx264", audio=False, logger=None)

            with open(out_path, "rb") as f:
                return f.read()
        finally:
            for c in clips:
                with contextlib.suppress(Exception):
                    c.close()
            for p in tmp_files:
                with contextlib.suppress(Exception):
                    os.unlink(p)
            if out_path:
                with contextlib.suppress(Exception):
                    os.unlink(out_path)

    return await loop.run_in_executor(None, _call)
