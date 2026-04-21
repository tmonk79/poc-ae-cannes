"""
Vertex AI generation functions for Phase 1 (preroll) and Phase 2 (short).

All public functions are async and designed to be called with asyncio.gather
inside a Cloud Tasks handler.
"""

import asyncio
import base64
import os
import tempfile

import vertexai
from vertexai.preview.vision_models import ImageGenerationModel
from vertexai.preview.vision_models import VideoGenerationModel

import firestore_client as fs
import gcs_client as gcs

PROJECT = os.environ.get("GCP_PROJECT", "")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

IMAGE_MODEL = "gemini-2.5-flash-preview-05-20"
VIDEO_MODEL = "veo-3.0-fast-generate-preview"

_vertex_initialized = False


def _init_vertex():
    global _vertex_initialized
    if not _vertex_initialized:
        vertexai.init(project=PROJECT, location=LOCATION)
        _vertex_initialized = True


# ---------------------------------------------------------------------------
# Image generation (Nano Banana)
# ---------------------------------------------------------------------------

async def _generate_image(prompt: str, reference_image_gcs: str | None = None) -> bytes:
    """Generate an image and return PNG bytes."""
    _init_vertex()
    loop = asyncio.get_event_loop()

    def _call():
        model = ImageGenerationModel.from_pretrained(IMAGE_MODEL)
        kwargs = {"prompt": prompt, "number_of_images": 1, "aspect_ratio": "1:1"}
        if reference_image_gcs:
            # Pass reference image for style/person consistency
            from vertexai.preview.vision_models import Image as VxImage
            ref = VxImage.load_from_file(reference_image_gcs)
            kwargs["reference_images"] = [ref]
        result = model.generate_images(**kwargs)
        return result.images[0]._image_bytes

    return await loop.run_in_executor(None, _call)


async def _generate_poster_image(prompt: str, reference_image_gcs: str | None = None) -> bytes:
    """Generate a 9:16 poster image and return PNG bytes."""
    _init_vertex()
    loop = asyncio.get_event_loop()

    def _call():
        model = ImageGenerationModel.from_pretrained(IMAGE_MODEL)
        kwargs = {"prompt": prompt, "number_of_images": 1, "aspect_ratio": "9:16"}
        if reference_image_gcs:
            from vertexai.preview.vision_models import Image as VxImage
            ref = VxImage.load_from_file(reference_image_gcs)
            kwargs["reference_images"] = [ref]
        result = model.generate_images(**kwargs)
        return result.images[0]._image_bytes

    return await loop.run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# Video generation (Veo)
# ---------------------------------------------------------------------------

async def _generate_video(prompt: str, reference_image_bytes: bytes | None = None) -> bytes:
    """Generate a video clip and return MP4 bytes."""
    _init_vertex()
    loop = asyncio.get_event_loop()

    def _call():
        model = VideoGenerationModel.from_pretrained(VIDEO_MODEL)
        kwargs = {"prompt": prompt}
        if reference_image_bytes:
            from vertexai.preview.vision_models import Image as VxImage
            img = VxImage(image_bytes=reference_image_bytes)
            kwargs["image"] = img
        result = model.generate_video(**kwargs)
        # result.videos[0].video_bytes contains the MP4
        return result.videos[0].video_bytes

    return await loop.run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# Phase 1 — Preroll pipeline
# ---------------------------------------------------------------------------

async def run_preroll(session_id: str, guest_image_gcs: str):
    """
    Generate all Phase 1 assets in parallel:
      - imageAd1, imageAd2 (PNG)
      - videoAd (MP4)
      - poster (PNG 9:16)

    Updates Firestore per asset as each completes.
    """
    fs.set_status(session_id, "preroll_running")
    fs.set_timing(session_id, "prerollStarted")

    base_path = f"sessions/{session_id}"

    async def make_image_ad1():
        data = await _generate_image(
            "A vibrant cinematic advertisement for a YouTube Drive-in movie night, "
            "featuring the guest enjoying popcorn and the big screen.",
            reference_image_gcs=guest_image_gcs,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/image_ad_1.png", "image/png")
        fs.set_asset(session_id, "imageAd1", uri)

    async def make_image_ad2():
        data = await _generate_image(
            "A retro drive-in movie advertisement, neon signs, starry sky, "
            "featuring the guest as the star of the show.",
            reference_image_gcs=guest_image_gcs,
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/image_ad_2.png", "image/png")
        fs.set_asset(session_id, "imageAd2", uri)

    async def make_video_ad():
        data = await _generate_video(
            "A 5-second YouTube Drive-in promotional video ad. Cinematic opening shot "
            "of a classic drive-in theatre at night, cars lined up, screen glowing.",
        )
        uri = gcs.upload_bytes(data, f"{base_path}/ads/video_ad.mp4", "video/mp4")
        fs.set_asset(session_id, "videoAd", uri)

    async def make_poster():
        data = await _generate_poster_image(
            "A 9:16 movie poster for a YouTube Drive-in personalized Short. "
            "Cinematic, bold title treatment, featuring the guest as the hero.",
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

SHOT_PROMPTS = {
    "action-adventure": [
        "Epic wide shot of a hero charging across a rooftop at sunset",
        "Close-up of determined eyes scanning the horizon",
        "Slow-motion explosion behind a running silhouette",
        "Hero leaping between buildings, city far below",
        "Intense face-off between hero and villain in a dark alley",
        "Triumphant hero standing on a cliff edge, golden light",
    ],
    "romance": [
        "Two people meeting eyes across a crowded cafe",
        "Slow walk along a rain-soaked street, sharing an umbrella",
        "Laughing together on a rooftop with city lights below",
        "A shy hand-hold at the cinema",
        "Candlelit dinner, nervous smiles",
        "First kiss under falling cherry blossoms",
    ],
    "sci-fi": [
        "Vast alien landscape, twin suns setting",
        "Hero piloting a sleek spacecraft through an asteroid field",
        "Holographic map flickering in a dark command room",
        "Close-up of a robot hand touching a human hand",
        "Space battle — laser bursts lighting the void",
        "Landing on Earth — home at last, crowds cheering",
    ],
    "comedy": [
        "Slapstick mishap with a coffee machine going haywire",
        "Confused expression as a dog walks off with the script",
        "Awkward elevator moment with way too many people",
        "Surprise birthday cake in the face",
        "Frantic chase scene through a farmers market",
        "Victorious fist-pump — accidentally hitting the ceiling fan",
    ],
}

DEFAULT_SHOT_PROMPTS = [
    f"Cinematic shot {i + 1} of 6 for a personalized YouTube Drive-in Short film"
    for i in range(6)
]


def _get_shot_prompts(genre: str) -> list[str]:
    return SHOT_PROMPTS.get(genre, DEFAULT_SHOT_PROMPTS)


async def _process_shot(session_id: str, shot_index: int, prompt: str, guest_image_gcs: str):
    """Generate image then video for a single shot, updating Firestore after each step."""
    base_path = f"sessions/{session_id}/short"

    # Step 1: generate image
    image_bytes = await _generate_image(prompt, reference_image_gcs=guest_image_gcs)
    image_uri = gcs.upload_bytes(
        image_bytes,
        f"{base_path}/shot_{shot_index}_image.png",
        "image/png",
    )
    fs.set_shot_image(session_id, shot_index, image_uri)

    # Step 2: generate video from that image
    video_bytes = await _generate_video(prompt, reference_image_bytes=image_bytes)
    video_uri = gcs.upload_bytes(
        video_bytes,
        f"{base_path}/shot_{shot_index}_video.mp4",
        "video/mp4",
    )
    fs.set_shot_video(session_id, shot_index, video_uri)

    return video_bytes


async def run_short(session_id: str, genre: str, guest_image_gcs: str):
    """
    Generate all 6 shots in parallel, then concatenate into final MP4.
    Updates Firestore per shot as each completes.
    """
    fs.set_status(session_id, "short_running")
    fs.set_timing(session_id, "shortStarted")
    fs.set_genre(session_id, genre)

    prompts = _get_shot_prompts(genre)

    results = await asyncio.gather(
        *[_process_shot(session_id, i, prompts[i], guest_image_gcs) for i in range(6)]
    )

    # Concatenate all 6 video clips
    final_mp4 = await _concatenate_videos(list(results))

    final_uri = gcs.upload_bytes(
        final_mp4,
        f"sessions/{session_id}/short/final.mp4",
        "video/mp4",
    )
    fs.set_asset(session_id, "shortFinal", final_uri)
    fs.set_status(session_id, "complete")
    fs.set_timing(session_id, "shortComplete")


async def _concatenate_videos(video_bytes_list: list[bytes]) -> bytes:
    """Concatenate MP4 clips using moviepy. Runs in executor to avoid blocking."""
    loop = asyncio.get_event_loop()

    def _call():
        from moviepy.editor import VideoFileClip, concatenate_videoclips

        tmp_files = []
        clips = []
        try:
            for i, data in enumerate(video_bytes_list):
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
                c.close()
            for p in tmp_files:
                with contextlib.suppress(Exception):
                    os.unlink(p)

    import contextlib
    return await loop.run_in_executor(None, _call)
