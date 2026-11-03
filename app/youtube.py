"""YouTube Data API v3: search, channel uploads, single video, comments.

Auth: either a plain API key (read-only, enough for search and stats) in YOUTUBE_API_KEY,
or a connected Google account through app/connectors.py (needed for private data).
"""
import os

import httpx

from . import connectors, store

API = "https://www.googleapis.com/youtube/v3"


async def _auth():
    token = await connectors.access_token("google")
    if token:
        return {"Authorization": f"Bearer {token}"}
    key = os.getenv("YOUTUBE_API_KEY") or store.load_settings().get("youtube_api_key") or ""
    if not key:
        raise RuntimeError("No YouTube credentials: set YOUTUBE_API_KEY or connect Google in /api/connect/google")
    return {"X-Key": key}


async def _get(path, params):
    headers = await _auth()
    if "X-Key" in headers:
        params = {**params, "key": headers["X-Key"]}
        headers = {}
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.get(f"{API}/{path}", params=params, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"youtube {r.status_code}: {r.text[:300]}")
    return r.json()


async def search(query, limit=8):
    data = await _get("search", {"part": "snippet", "q": query, "maxResults": limit, "type": "video"})
    return "\n".join(
        f"- {it['snippet']['title']} | {it['snippet']['channelTitle']} | "
        f"https://youtu.be/{it['id']['videoId']}"
        for it in data.get("items", []))


async def channel_uploads(channel_id, limit=8):
    data = await _get("search", {"part": "snippet", "channelId": channel_id,
                                 "maxResults": limit, "order": "date", "type": "video"})
    return "\n".join(f"- {it['snippet']['title']} | https://youtu.be/{it['id']['videoId']}"
                     for it in data.get("items", []))


async def video_stats(video_id):
    data = await _get("videos", {"part": "snippet,statistics", "id": video_id})
    items = data.get("items", [])
    if not items:
        return "video not found"
    it = items[0]
    st = it.get("statistics", {})
    return (f"{it['snippet']['title']} by {it['snippet']['channelTitle']}\n"
            f"views {st.get('viewCount')} likes {st.get('likeCount')} comments {st.get('commentCount')}")


def _spec(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


SCHEMAS = [
    _spec("yt_search", "Search YouTube videos.", {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _spec("yt_channel_uploads", "List a channel's latest videos.", {"channel_id": {"type": "string"}, "limit": {"type": "integer"}}, ["channel_id"]),
    _spec("yt_video_stats", "Views, likes and comments for one video.", {"video_id": {"type": "string"}}, ["video_id"]),
]

HANDLERS = {
    "yt_search": search,
    "yt_channel_uploads": channel_uploads,
    "yt_video_stats": video_stats,
}
