"""Canva Connect API (free): OAuth2 PKCE login + asset upload + design creation.

Setup (one-time, in the app's Settings page - no code):
1. https://www.canva.com/developers/integrations  ->  Create an integration (free)
2. Scopes: asset:read asset:write design:content:write design:meta:read
3. Redirect URL: http://127.0.0.1:5890/canva/callback
4. Paste the Client ID and Client Secret into Settings > Canva and click "Connect".

Tokens are stored in the app data folder (``canva_token.json``) and refreshed automatically.
Reference: https://www.canva.dev/docs/connect/
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

AUTH_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
API = "https://api.canva.com/rest/v1"
SCOPES = "asset:read asset:write design:content:write design:meta:read"


class Canva:
    def __init__(self, client_id: str, client_secret: str, token_file: Path, redirect: str = "http://127.0.0.1:5890/canva/callback"):
        self.client_id, self.client_secret, self.token_file, self.redirect = client_id, client_secret, token_file, redirect
        self._verifier: str | None = None

    # ---------------------------------------------------------------- oauth
    def login_url(self) -> str:
        self._verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect, "scope": SCOPES,
             "code_challenge": challenge, "code_challenge_method": "s256", "state": secrets.token_urlsafe(16)}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def exchange(self, code: str) -> dict[str, Any]:
        data = {"grant_type": "authorization_code", "code": code, "code_verifier": self._verifier or "", "redirect_uri": self.redirect}
        return self._token_request(data)

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(),
                                     headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        self.token_file.write_text(json.dumps(tok))
        return tok

    def token(self) -> str:
        if not self.token_file.exists():
            raise RuntimeError("Canva is not connected yet. Open Settings > Canva and click Connect.")
        tok = json.loads(self.token_file.read_text())
        if time.time() >= tok.get("expires_at", 0):
            tok = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        return tok["access_token"]

    def connected(self) -> bool:
        return self.token_file.exists()

    # ---------------------------------------------------------------- api
    def _call(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        req = urllib.request.Request(API + path, data=body, method=method,
                                     headers={"Authorization": f"Bearer {self.token()}", **(headers or {})})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")

    def upload_asset(self, file: Path, name: str | None = None) -> dict[str, Any]:
        meta = {"name_base64": base64.b64encode((name or file.stem)[:50].encode()).decode()}
        job = self._call("POST", "/asset-uploads", file.read_bytes(),
                         {"Content-Type": "application/octet-stream", "Asset-Upload-Metadata": json.dumps(meta)})
        jid = job["job"]["id"]
        for _ in range(60):
            st = self._call("GET", f"/asset-uploads/{jid}")
            if st["job"]["status"] == "success":
                return st["job"]["asset"]
            if st["job"]["status"] == "failed":
                raise RuntimeError(f"Canva upload failed: {st['job'].get('error')}")
            time.sleep(2)
        raise RuntimeError("Canva upload timed out")

    def create_design(self, asset_id: str, title: str, width: int = 1280, height: int = 720) -> dict[str, Any]:
        body = json.dumps({"design_type": {"type": "custom", "width": width, "height": height}, "asset_id": asset_id, "title": title[:255]}).encode()
        return self._call("POST", "/designs", body, {"Content-Type": "application/json"})["design"]

    def send_episode_assets(self, files: list[tuple[Path, str]], make_thumbnail_design: Path | None = None, title: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {"assets": [], "designs": []}
        for f, name in files:
            if f.exists():
                a = self.upload_asset(f, name)
                out["assets"].append({"file": f.name, "asset_id": a["id"], "name": name})
                if make_thumbnail_design and f == make_thumbnail_design:
                    d = self.create_design(a["id"], f"{title} - thumbnail")
                    out["designs"].append({"id": d["id"], "url": d.get("urls", {}).get("edit_url")})
        return out


def guess_type(p: Path) -> str:
    return mimetypes.guess_type(str(p))[0] or "application/octet-stream"
