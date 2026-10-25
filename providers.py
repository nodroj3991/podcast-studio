"""Music providers with an API and machine-readable licences.

* **Jamendo** (https://developer.jamendo.com) - free API key ("client_id"), 35k+ CC tracks,
  licence flags per track, mood/instrument tags, download URLs.  Free tier: non-commercial
  *use of the API* is free; the music itself is licensed per track by its CC licence.
  Jamendo's own terms allow using its API for "personal and non-commercial applications"
  without a paid plan - you are not reselling the music, so this fits; check
  https://devportal.jamendo.com/ if your usage grows.
* **Openverse** (https://api.openverse.org) - WordPress-foundation search over CC audio
  (Jamendo, Freesound, Wikimedia Commons).  No key needed for anonymous use (rate-limited);
  a free registration raises limits.  Returns licence + attribution text per item.

Both calls are plain HTTPS GETs with ``urllib`` (no SDK).  Every result is normalised to::

    {"provider","id","title","artist","duration","license","license_url","url","download",
     "tags":[...], "attribution": "..."}
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

UA = "PodcastStudio/0.2 (+local desktop app)"

MOODS = {
    "calm": ["calm", "relaxing", "ambient", "peaceful", "soft", "piano"],
    "curious": ["inspiring", "hopeful", "light", "acoustic", "playful", "uplifting"],
    "upbeat": ["upbeat", "energetic", "happy", "positive", "motivational"],
    "serious": ["cinematic", "dramatic", "documentary", "epic", "strings"],
    "warm": ["warm", "folk", "acoustic", "guitar", "melodic"],
}


def _get(url: str, params: dict[str, Any], timeout: int = 20) -> Any:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""}, doseq=True)
    req = urllib.request.Request(url + ("?" + q if q else ""), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def jamendo_search(client_id: str, mood: str, min_s: int, max_s: int, limit: int = 20, instrumental: bool = True) -> list[dict[str, Any]]:
    tags = MOODS.get(mood, [mood])
    data = _get("https://api.jamendo.com/v3.0/tracks/", {
        "client_id": client_id, "format": "json", "limit": limit, "fuzzytags": "+".join(tags),
        "durationbetween": f"{min_s}_{max_s}", "include": "licenses+musicinfo", "audioformat": "mp32",
        "vocalinstrumental": "instrumental" if instrumental else None, "order": "popularity_month",
        "ccnc": "false", "ccnd": "false",     # ask the API to exclude NC/ND up front; we re-check anyway
    })
    out = []
    for t in data.get("results", []):
        lic_url = t.get("license_ccurl") or ""
        out.append({
            "provider": "jamendo", "id": str(t["id"]), "title": t.get("name"), "artist": t.get("artist_name"),
            "duration": float(t.get("duration") or 0), "license": lic_url.split("licenses/")[-1].split("/")[0] if "licenses/" in lic_url else "",
            "license_url": lic_url, "url": t.get("shareurl"), "download": t.get("audiodownload") or t.get("audio"),
            "download_allowed": bool(t.get("audiodownload_allowed", True)),
            "tags": (t.get("musicinfo", {}).get("tags", {}).get("vartags") or []) + (t.get("musicinfo", {}).get("tags", {}).get("genres") or []),
            "attribution": f"\"{t.get('name')}\" by {t.get('artist_name')} - {t.get('shareurl')} - licensed under {lic_url}",
        })
    return out


def openverse_search(mood: str, min_s: int, max_s: int, limit: int = 20) -> list[dict[str, Any]]:
    tags = MOODS.get(mood, [mood])
    # Openverse ANDs the words of a query, so search one mood word at a time and merge.
    # Length is filtered here (intro/outro are trimmed + faded, so anything >= min_s works).
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for tag in tags[:4]:
        try:
            data = _get("https://api.openverse.org/v1/audio/", {"q": tag, "category": "music", "license": "cc0,by,by-sa,pdm", "page_size": limit})
        except Exception:
            continue
        for t in data.get("results", []):
            if t["id"] not in seen:
                seen.add(t["id"]); results.append(t)
    out = []
    for t in results:
        dur = float(t.get("duration") or 0) / 1000.0
        if dur and dur < min_s:
            continue
        out.append({
            "provider": "openverse", "id": t["id"], "title": t.get("title"), "artist": t.get("creator"),
            "duration": dur, "license": f"{t.get('license')}", "license_url": t.get("license_url"),
            "url": t.get("foreign_landing_url"), "download": t.get("url"), "download_allowed": True,
            "tags": [x.get("name") for x in (t.get("tags") or [])][:10],
            "attribution": t.get("attribution") or f"\"{t.get('title')}\" by {t.get('creator')} - {t.get('foreign_landing_url')} - {t.get('license_url')}",
        })
    return out


def download(url: str, dst, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
