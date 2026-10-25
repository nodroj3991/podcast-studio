"""Local HTTP API for the desktop app (stdlib only, binds to 127.0.0.1).

    GET  /api/hw                              hardware + model choice + 90-min estimate
    GET  /api/settings      POST /api/settings  global settings (keys, brand, defaults)
    GET  /api/presets       POST /api/presets   named presets {name: settings-overrides}
    GET  /api/episodes                        list projects in the episodes folder
    POST /api/episodes                        {title, guest:{name,title,institution}, inputs:[{path,role,speaker}], preset, settings}
    GET  /api/episodes/<id>                   project.json + estimate + progress
    POST /api/episodes/<id>/run               {until?, only?, force?, from?}  -> queued
    POST /api/episodes/<id>/cancel
    GET  /api/episodes/<id>/progress
    GET  /api/episodes/<id>/transcript|decisions|edl|music|charts|log
    POST /api/episodes/<id>/decisions         full decisions.json (review screen save)
    POST /api/episodes/<id>/correct           {word_id, text}
    POST /api/episodes/<id>/delete            {word_ids, note} ; /undelete {id}
    POST /api/episodes/<id>/speakers          {label: name}
    POST /api/episodes/<id>/music/search      {mood, min_s, max_s}
    POST /api/episodes/<id>/music/choose      {slot: intro|outro|bed, track}
    POST /api/episodes/<id>/charts/custom     {title, csv?, pasted?, kind, x?, y?, source?}
    POST /api/episodes/<id>/charts/place      {key, src_in|null, enabled}
    POST /api/episodes/<id>/canva/send
    GET  /api/canva/login  GET /canva/callback  GET /api/canva/status
    GET  /api/queue                           job queue
    GET  /files/<id>/<path>                   any project file (preview video, charts, ...)
    GET  /                                    the UI (app/renderer) when present
"""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import queue
import shutil
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core.project import Project, read_json, write_json, STAGES, STAGE_LABELS
from .core import hw as HW
from . import pipeline as PL
from .edit import textedit as TE
from .music.stage import search_all
from .music import providers as MP
from .visuals import charts as CH
from .canva import Canva
from . import models as MODELS

UI_DIR = Path(__file__).resolve().parent.parent / "app" / "renderer"


def app_data_dir() -> Path:
    env = os.environ.get("PODCAST_STUDIO_DATA")
    if env:
        return Path(env)
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "PodcastStudio"
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", home)) / "PodcastStudio"
    return home / ".podcast-studio"


class State:
    def __init__(self, episodes_dir: Path | None):
        self.data = app_data_dir()
        self.data.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.data / "settings.json"
        self.presets_file = self.data / "presets.json"
        self.settings: dict[str, Any] = read_json(self.settings_file, {}) or {}
        self.presets: dict[str, Any] = read_json(self.presets_file, {}) or {}
        self.episodes_dir = episodes_dir or Path(self.settings.get("episodes_dir") or (Path.home() / "Podcast Episodes"))
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.current: dict[str, Any] | None = None
        self.queued: list[dict[str, Any]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.canva_pending: dict[str, Any] = {}
        threading.Thread(target=self._worker, daemon=True).start()
        if not self.presets:
            self.presets = {"Standard episode": {}}
            self.save_presets()

    def save_settings(self) -> None:
        write_json(self.settings_file, self.settings)

    def save_presets(self) -> None:
        write_json(self.presets_file, self.presets)

    def project(self, eid: str) -> Project:
        p = self.episodes_dir / eid
        if not p.exists() or not (p / "project.json").exists():
            raise FileNotFoundError(eid)
        return Project(p)

    def effective_settings(self, preset: str | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
        s = {**PL.default_settings(), **self.settings, **self.presets.get(preset or "", {}), **(overrides or {})}
        s["models_dir"] = str(self.data / "models")
        return s

    def enqueue(self, eid: str, opts: dict[str, Any]) -> None:
        job = {"id": eid, "opts": opts, "queued_at": time.time()}
        self.queued.append(job)
        self.jobs.put(job)

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            self.queued = [j for j in self.queued if j is not job]
            self.current = job
            try:
                o = job["opts"]
                p = self.project(job["id"])
                if o.get("settings"):
                    meta = p.load(); meta["settings"] = {**meta.get("settings", {}), **o["settings"]}; p.save(meta)
                if o.get("from"):
                    p.invalidate_from(o["from"])
                res = PL.run(p.root, only=o.get("only"), force=bool(o.get("force")), until=o.get("until"), skip=o.get("skip"))
            except Exception as e:  # noqa: BLE001
                res = {"state": "failed", "message": str(e), "fix": "See the log file.", "trace": traceback.format_exc()}
            self.results[job["id"]] = {**res, "finished": time.time()}
            self.current = None

    def canva(self) -> Canva:
        return Canva(self.settings.get("canva_client_id", ""), self.settings.get("canva_client_secret", ""),
                     self.data / "canva_token.json", f"http://127.0.0.1:{self.settings.get('port', 5890)}/canva/callback")


STATE: State


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    # ---------------------------------------------------------------- helpers
    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a or 0); end = int(b) if b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk); remaining -= len(chunk)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ---------------------------------------------------------------- routing
    def do_GET(self):
        try:
            self._route("GET")
        except FileNotFoundError as e:
            self._json({"error": f"episode not found: {e}"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    def do_POST(self):
        try:
            self._route("POST")
        except FileNotFoundError as e:
            self._json({"error": f"episode not found: {e}"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _route(self, method: str) -> None:
        S = STATE
        url = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.strip("/").split("/") if p]
        q = dict(urllib.parse.parse_qsl(url.query))

        if not parts or parts[0] != "api" and parts[0] not in ("files", "canva"):
            return self._static(parts)
        if parts[0] == "files" and len(parts) >= 3:
            p = S.project(parts[1])
            return self._file(p.root.joinpath(*parts[2:]))
        if parts[0] == "canva" and parts[1:2] == ["callback"]:
            code = q.get("code")
            S.canva().exchange(code)
            body = b"<html><body style='font-family:sans-serif;padding:40px'><h2>Canva connected.</h2>You can close this window and go back to the app.</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        a = parts[1:]
        # ---- global
        if a == ["hw"]:
            h = HW.detect()
            return self._json({"hardware": h, "model": HW.choose_model(h), "estimate_90min": HW.estimate(h, 5400),
                               "pretty": {k: HW.fmt_duration(v) for k, v in HW.estimate(h, 5400).items() if isinstance(v, (int, float))}})
        if a == ["settings"]:
            if method == "POST":
                S.settings.update(self._body()); S.save_settings()
            return self._json({**PL.default_settings(), **S.settings, "episodes_dir": str(S.episodes_dir), "data_dir": str(S.data)})
        if a == ["presets"]:
            if method == "POST":
                b = self._body()
                if b.get("delete"):
                    S.presets.pop(b["delete"], None)
                else:
                    S.presets[b["name"]] = b.get("settings", {})
                S.save_presets()
            return self._json(S.presets)
        if a == ["models", "status"]:
            h = HW.detect()
            model = S.settings.get("whisper_model") or HW.choose_model(h, S.settings.get("quality", "balanced"))
            return self._json(MODELS.status(S.data / "models", model))
        if a == ["models", "download"] and method == "POST":
            h = HW.detect()
            model = self._body().get("model") or S.settings.get("whisper_model") or HW.choose_model(h, S.settings.get("quality", "balanced"))
            MODELS.download(S.data / "models", model, S.settings.get("hf_token"))
            return self._json(MODELS.status(S.data / "models", model))
        if a == ["gpu", "install"] and method == "POST":
            # NVIDIA acceleration for faster-whisper (CTranslate2) = cuBLAS + cuDNN pip wheels (~800 MB)
            import subprocess, sys
            cmd = [sys.executable, "-m", "pip", "install", "--quiet", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
            threading.Thread(target=lambda: subprocess.run(cmd), daemon=True).start()
            return self._json({"started": True})
        if a == ["queue"]:
            return self._json({"current": S.current, "queued": S.queued, "results": S.results})
        if a == ["canva", "status"]:
            return self._json({"configured": bool(S.settings.get("canva_client_id")), "connected": S.canva().connected()})
        if a == ["canva", "login"]:
            url_ = S.canva().login_url()
            webbrowser.open(url_)
            return self._json({"url": url_})
        if a == ["open"] and method == "POST":
            path = self._body().get("path", "")
            _open_in_os(path)
            return self._json({"ok": True})
        # ---- episodes
        if a == ["episodes"]:
            if method == "POST":
                return self._json(self._new_episode(self._body()))
            return self._json(self._list_episodes())
        if len(a) >= 2 and a[0] == "episodes":
            eid = a[1]
            p = S.project(eid)
            sub = a[2:]
            if not sub:
                meta = p.load()
                prog = read_json(p.work / "progress.json", {})
                return self._json({"id": eid, "project": meta, "progress": prog, "estimate": PL.estimates_for(p),
                                   "stage_labels": STAGE_LABELS, "stages": STAGES, "result": S.results.get(eid)})
            if sub == ["run"] and method == "POST":
                o = self._body()
                S.enqueue(eid, o)
                return self._json({"queued": True, "position": len(S.queued)})
            if sub == ["cancel"] and method == "POST":
                (p.work / "CANCEL").touch()
                S.queued = [j for j in S.queued if j["id"] != eid]
                return self._json({"ok": True})
            if sub == ["progress"]:
                return self._json({"progress": read_json(p.work / "progress.json", {}), "result": S.results.get(eid),
                                   "running": bool(S.current and S.current["id"] == eid)})
            if sub == ["transcript"]:
                return self._json(read_json(p.transcript, {}))
            if sub == ["decisions"]:
                if method == "POST":
                    write_json(p.decisions, self._body()); p.invalidate_from("edl")
                return self._json(read_json(p.decisions, {}))
            if sub == ["edl"]:
                return self._json({"edl": read_json(p.edl_json, {}), "text": (p.edit_dir / "edl.txt").read_text() if (p.edit_dir / "edl.txt").exists() else ""})
            if sub == ["music"]:
                return self._json(read_json(p.music_dir / "music.json", {}))
            if sub == ["charts"]:
                return self._json({"placements": read_json(p.charts_dir / "placements.json", []), "profile": read_json(p.charts_dir / "profile.json", {}),
                                   "report": (p.charts_dir / "charts_report.txt").read_text() if (p.charts_dir / "charts_report.txt").exists() else ""})
            if sub == ["log"]:
                f = p.logs / "engine.log"
                return self._json({"path": str(f), "text": f.read_text(errors="replace")[-20000:] if f.exists() else ""})
            if sub == ["correct"] and method == "POST":
                b = self._body()
                tr = read_json(p.transcript)
                words = TE.correct(tr, b["word_id"], b["text"])
                write_json(p.transcript, tr); p.invalidate_from("edl")
                return self._json({"words": words})
            if sub == ["delete"] and method == "POST":
                b = self._body(); d = read_json(p.decisions, {}) or {}
                rec = TE.delete_range(d, b["word_ids"], b.get("note", ""))
                write_json(p.decisions, d); p.invalidate_from("edl")
                return self._json(rec)
            if sub == ["undelete"] and method == "POST":
                b = self._body(); d = read_json(p.decisions, {}) or {}
                TE.undelete(d, b["id"]); write_json(p.decisions, d); p.invalidate_from("edl")
                return self._json({"ok": True})
            if sub == ["speakers"] and method == "POST":
                names = self._body()
                d = read_json(p.decisions, {}) or {}
                d["speakers"] = {**d.get("speakers", {}), **names}
                write_json(p.decisions, d)
                tr = read_json(p.transcript, {})
                if tr:
                    tr["speakers"] = {**tr.get("speakers", {}), **names}; write_json(p.transcript, tr)
                meta = p.load(); meta["speakers"] = {**meta.get("speakers", {}), **names}; p.save(meta)
                p.invalidate_from("edl")
                return self._json(d["speakers"])
            if sub == ["music", "search"] and method == "POST":
                b = self._body()
                settings = S.effective_settings(p.load().get("preset"), p.load().get("settings"))
                res = search_all(settings, b.get("mood", "curious"), int(b.get("min_s", 15)), int(b.get("max_s", 90)))
                return self._json({"tracks": res})
            if sub == ["music", "choose"] and method == "POST":
                b = self._body(); slot, t = b["slot"], b.get("track")
                m = read_json(p.music_dir / "music.json", {}) or {}
                if t is None:
                    m[slot] = None
                else:
                    if not t.get("verdict", {}).get("ok", False):
                        return self._json({"error": "This track's licence does not allow use in a monetized video."}, 400)
                    dst = p.music_dir / f"{slot}_{t['provider']}_{t['id']}.mp3"
                    if not dst.exists():
                        MP.download(t["download"], dst)
                    m[slot] = {**t, "file": str(dst), "user_chosen": True}
                lines = [f"{k.title()} music: {m[k]['attribution']}" for k in ("intro", "outro", "bed") if m.get(k) and m[k].get("attribution")]
                m["attribution"] = lines
                (p.music_dir / "attribution.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                write_json(p.music_dir / "music.json", m); p.invalidate_from("edl")
                return self._json(m)
            if sub == ["charts", "custom"] and method == "POST":
                b = self._body()
                rows, cols = (CH.rows_from_csv(b["csv"]) if b.get("csv") else CH.rows_from_pasted(b.get("pasted", "")))
                x, y = b.get("x") or cols[0], b.get("y") or (cols[1] if len(cols) > 1 else cols[0])
                (p.charts_dir / "custom").mkdir(exist_ok=True)
                key = "".join(c for c in b["title"].lower() if c.isalnum())[:30] or "chart"
                write_json(p.charts_dir / "custom" / f"{key}.json", {"title": b["title"], "rows": rows, "x": x, "y": y,
                                                                      "kind": b.get("kind", "bar"), "source": b.get("source") or "Guest-provided data"})
                p.invalidate_from("charts")
                return self._json({"ok": True, "rows": len(rows), "columns": cols})
            if sub == ["charts", "place"] and method == "POST":
                b = self._body()
                pl = read_json(p.charts_dir / "placements.json", []) or []
                for c in pl:
                    if c["key"] == b["key"]:
                        c["enabled"] = bool(b.get("enabled", True))
                        if "src_in" in b:
                            c["src_in"] = b["src_in"]; c["src_out"] = (b["src_in"] + c.get("seconds", 6.0)) if b["src_in"] is not None else None
                            c["user_placed"] = True; c["cue"] = b.get("cue", "placed by you")
                write_json(p.charts_dir / "placements.json", pl); p.invalidate_from("charts")
                return self._json(pl)
            if sub == ["canva", "send"] and method == "POST":
                meta = p.load()
                files = [(p.graphics / "thumbnail.jpg", f"{meta.get('title', eid)} thumbnail"), (p.graphics / "title_card.png", "Title card"),
                         (p.graphics / "lower_third.png", "Lower third")]
                files += [(f, f"Chart - {f.stem}") for f in sorted(p.charts_dir.glob("*.png"))]
                res = S.canva().send_episode_assets(files, p.graphics / "thumbnail.jpg", meta.get("title", eid))
                meta["canva"] = res; p.save(meta)
                return self._json(res)
            if sub == ["guest"] and method == "POST":
                meta = p.load(); meta["guest"] = {**meta.get("guest", {}), **self._body()}; p.save(meta)
                (p.charts_dir / "profile.json").unlink(missing_ok=True)
                return self._json(meta["guest"])
            if sub == ["estimate"]:
                return self._json(PL.estimates_for(p))
        return self._json({"error": "unknown route"}, 404)

    def _static(self, parts: list[str]) -> None:
        rel = "/".join(parts) or "index.html"
        f = (UI_DIR / rel).resolve()
        if UI_DIR.exists() and str(f).startswith(str(UI_DIR.resolve())) and f.is_file():
            return self._file(f)
        return self._json({"ok": True, "message": "Podcast Studio engine", "ui": str(UI_DIR)})

    def _list_episodes(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(STATE.episodes_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if (d / "project.json").exists():
                m = read_json(d / "project.json", {}) or {}
                stages = m.get("stages", {})
                done = [s for s in STAGES if stages.get(s, {}).get("state") == "done"]
                out.append({"id": d.name, "title": m.get("title") or d.name, "guest": (m.get("guest") or {}).get("name", ""),
                            "created": m.get("created"), "stages_done": len(done), "stages_total": len(STAGES),
                            "last_stage": done[-1] if done else None, "output_zip": m.get("output_zip"),
                            "qa_passed": m.get("qa_passed"), "duration": m.get("duration")})
        return out

    def _new_episode(self, b: dict[str, Any]) -> dict[str, Any]:
        S = STATE
        guest = b.get("guest") or {}
        title = b.get("title") or f"{time.strftime('%Y-%m-%d')} {guest.get('name', 'Episode')}".strip()
        slug = "".join(c for c in title if c.isalnum() or c in " -_").strip().replace(" ", "_")[:60] or f"episode_{int(time.time())}"
        root = S.episodes_dir / slug
        i = 2
        while root.exists():
            root = S.episodes_dir / f"{slug}_{i}"; i += 1
        settings = S.effective_settings(b.get("preset"), b.get("settings"))
        p = PL.prepare(root, b.get("inputs") or [], guest, title, settings)
        meta = p.load(); meta["preset"] = b.get("preset") or "Standard episode"; meta["date"] = time.strftime("%Y-%m-%d"); p.save(meta)
        return {"id": root.name, "path": str(root), "estimate": PL.estimates_for(p)}


def _open_in_os(path: str) -> None:
    import subprocess
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    elif platform.system() == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


def serve(port: int = 5890, episodes_dir: Path | None = None, open_review: Path | None = None) -> None:
    global STATE
    STATE = State(episodes_dir)
    STATE.settings["port"] = port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Podcast Studio engine listening on http://127.0.0.1:{port}  (episodes: {STATE.episodes_dir})", flush=True)
    if open_review:
        webbrowser.open(f"http://127.0.0.1:{port}/#/episode/{Path(open_review).name}/review")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
