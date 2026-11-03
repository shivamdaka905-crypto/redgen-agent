"""Image generation, text-to-speech and video assembly.

Providers are pluggable through env vars so you can point this at OpenAI, OpenRouter,
Together, fal.ai or a local endpoint:

    IMAGE_API_URL=https://api.openai.com/v1     IMAGE_API_KEY=sk-...   IMAGE_MODEL=gpt-image-1

TTS uses edge-tts (free, no key). Video assembly uses ffmpeg.
"""
import asyncio
import base64
import os
import subprocess
import time
from pathlib import Path

import httpx

from . import store

MEDIA = store.WORKSPACE / "media"
MEDIA.mkdir(parents=True, exist_ok=True)

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def _cfg(settings_key, env_key, default=""):
    s = store.load_settings()
    return s.get(settings_key) or os.getenv(env_key) or default


def _font(size):
    from PIL import ImageFont
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _run(cmd, timeout=300):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr[-600:]}")
    return proc.stdout


async def generate_image(prompt, size="1024x1024", count=1):
    base = _cfg("image_base_url", "IMAGE_API_URL", "https://api.openai.com/v1").rstrip("/")
    key = _cfg("image_api_key", "IMAGE_API_KEY")
    model = _cfg("image_model", "IMAGE_MODEL", "gpt-image-1")
    if not key:
        raise RuntimeError("No image API key. Set IMAGE_API_KEY in .env (OpenAI-compatible images endpoint).")
    payload = {"model": model, "prompt": prompt, "n": int(count), "size": size}
    async with httpx.AsyncClient(timeout=240) as c:
        r = await c.post(f"{base}/images/generations", json=payload,
                         headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise RuntimeError(f"image API {r.status_code}: {r.text[:300]}")
    saved = []
    for item in r.json().get("data", []):
        name = f"img-{int(time.time() * 1000)}.png"
        path = MEDIA / name
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            async with httpx.AsyncClient(timeout=120) as c:
                img = await c.get(item["url"])
            path.write_bytes(img.content)
        else:
            continue
        saved.append(name)
    return {"images": saved, "dir": str(MEDIA)}


async def text_to_speech(text, voice="en-US-AndrewMultilingualNeural", rate="+0%", pitch="+0Hz", tries=3):
    """edge-tts occasionally returns empty audio (soft rate limit); retry, then fail cleanly."""
    import edge_tts

    raw = MEDIA / f"tts-raw-{int(time.time() * 1000)}.mp3"
    last = None
    for attempt in range(tries):
        try:
            await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(raw))
            if raw.exists() and raw.stat().st_size > 1000:
                break
            last = RuntimeError("empty audio")
        except Exception as exc:
            last = exc
        raw.unlink(missing_ok=True)
        await asyncio.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"TTS failed after {tries} tries: {last}")
    out = MEDIA / raw.name.replace("tts-raw", "tts")
    try:
        _run(["ffmpeg", "-y", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
              "-ar", "44100", str(out)])
        raw.unlink(missing_ok=True)
    except Exception:
        return {"audio": raw.name}
    return {"audio": out.name}


def _card(title, body, index, total, size=(1280, 720)):
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size, (11, 14, 22))
    d = ImageDraw.Draw(img)
    for y in range(h):  # simple vertical gradient
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(16 + 20 * t), int(24 + 60 * t), int(48 + 120 * t)))
    margin = 90
    d.text((margin, 250), title[:70], font=_font(58), fill=(255, 255, 255))
    words, line, lines = body.split(), "", []
    for word in words:
        probe = (line + " " + word).strip()
        if d.textlength(probe, font=_font(32)) > w - 2 * margin:
            lines.append(line)
            line = word
        else:
            line = probe
    lines.append(line)
    for i, text_line in enumerate(lines[:6]):
        d.text((margin, 350 + i * 46), text_line, font=_font(32), fill=(210, 222, 240))
    d.text((margin, h - 90), f"{index + 1}/{total}", font=_font(24), fill=(140, 160, 190))
    name = f"card-{int(time.time() * 1000)}-{index}.png"
    img.save(MEDIA / name)
    return name


def _duration(path):
    out = _run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path)])
    return max(1.5, float(out.strip() or 2))


async def make_video(scenes, voice="en-US-AndrewMultilingualNeural", rate="+4%"):
    """scenes: [{"title": str, "text": str, "image": optional filename in workspace/media}]

    Returns the mp4 name in workspace/media.
    """
    if not scenes:
        raise RuntimeError("No scenes given")
    stamp = int(time.time())
    clips, images, audios = [], [], []
    total = len(scenes)
    for i, scene in enumerate(scenes):
        text = (scene.get("text") or scene.get("title") or "").strip()
        image = scene.get("image")
        if image and (MEDIA / image).exists():
            img_name = image
        else:
            img_name = _card(scene.get("title", ""), text, i, total)
        tts = {}
        if text:
            try:
                tts = await text_to_speech(text, voice=voice, rate=rate)
            except Exception as exc:  # keep the video usable, just silently timed
                print(f"[media] scene {i + 1} voice failed: {exc}")
        audio_path = MEDIA / tts["audio"] if tts.get("audio") else None
        seconds = _duration(audio_path) if audio_path else 3.5
        images.append((img_name, seconds))
        if audio_path:
            audios.append(audio_path.name)

    list_file = MEDIA / f"list-{stamp}.txt"
    lines = []
    for name, seconds in images:
        lines.append(f"file '{MEDIA / name}'")
        lines.append(f"duration {seconds:.2f}")
    # concat demuxer needs the last file repeated
    lines.append(f"file '{MEDIA / images[-1][0]}'")
    list_file.write_text("\n".join(lines))

    silent = MEDIA / f"video-silent-{stamp}.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                 "fps=30,format=yuv420p",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(silent)])

    if not audios:
        final = MEDIA / f"video-{stamp}.mp4"
        silent.rename(final)
        return {"video": final.name}

    alist = MEDIA / f"alist-{stamp}.txt"
    alist.write_text("\n".join(f"file '{MEDIA / a}'" for a in audios))
    audio_track = MEDIA / f"audio-{stamp}.mp3"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(alist), "-c", "copy", str(audio_track)])

    final = MEDIA / f"video-{stamp}.mp4"
    _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(audio_track),
          "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "apad", "-shortest", str(final)])
    return {"video": final.name, "scenes": total, "seconds": round(sum(s for _, s in images), 1)}


def _spec(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


SCHEMAS = [
    _spec("generate_image", "Generate an image from a prompt and save it in workspace/media.",
          {"prompt": {"type": "string"}, "size": {"type": "string"}, "count": {"type": "integer"}},
          ["prompt"]),
    _spec("text_to_speech", "Turn text into an mp3 voice track (edge-tts, free).",
          {"text": {"type": "string"}, "voice": {"type": "string"},
           "rate": {"type": "string"}, "pitch": {"type": "string"}}, ["text"]),
    _spec("make_video",
          "Build an mp4 from scenes: each scene has a title, a narration text and an optional image. "
          "Text is voiced, scenes are rendered and stitched with ffmpeg.",
          {"scenes": {"type": "array", "items": {"type": "object"}},
           "voice": {"type": "string"}, "rate": {"type": "string"}}, ["scenes"]),
    _spec("list_media", "List generated images, audio and videos.", {}),
]

HANDLERS = {
    "generate_image": generate_image,
    "text_to_speech": text_to_speech,
    "make_video": make_video,
    "list_media": lambda: "\n".join(p.name for p in sorted(MEDIA.glob("*"))) or "(empty)",
}
