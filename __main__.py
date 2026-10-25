"""Command line entry: ``python -m engine <command> ...``

    hw                                   show hardware + estimates for a 90 min episode
    new  <dir> --input f1 [--input f2] --guest "Name" --institution "..." --title "..."
    run  <dir> [--until STAGE] [--only STAGE ...] [--force]
    review <dir>                         open the review screen (local web page)
    serve [--port 5890]                  HTTP API for the desktop app
    estimate <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hw")
    n = sub.add_parser("new")
    n.add_argument("dir"); n.add_argument("--input", action="append", default=[]); n.add_argument("--guest", default="")
    n.add_argument("--institution", default=""); n.add_argument("--guest-title", default=""); n.add_argument("--title", default="")
    n.add_argument("--host", default=""); n.add_argument("--settings", default="")
    r = sub.add_parser("run")
    r.add_argument("dir"); r.add_argument("--until"); r.add_argument("--only", action="append"); r.add_argument("--force", action="store_true")
    r.add_argument("--skip", action="append")
    e = sub.add_parser("estimate"); e.add_argument("dir")
    v = sub.add_parser("review"); v.add_argument("dir"); v.add_argument("--port", type=int, default=5891)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=5890); s.add_argument("--episodes", default="")
    a = ap.parse_args(argv)

    if a.cmd == "hw":
        from .core import hw
        h = hw.detect()
        print(json.dumps({"hardware": h, "model": hw.choose_model(h), "estimate_90min": hw.estimate(h, 5400)}, indent=2))
        return 0
    if a.cmd == "new":
        from .pipeline import prepare
        settings = json.loads(Path(a.settings).read_text()) if a.settings else {}
        if a.host:
            settings["host_name"] = a.host
        p = prepare(Path(a.dir), [{"path": str(Path(x).resolve()), "role": "auto"} for x in a.input],
                    {"name": a.guest, "institution": a.institution, "title": a.guest_title}, a.title or None, settings)
        print(f"project ready: {p.root}")
        return 0
    if a.cmd == "estimate":
        from .pipeline import estimates_for
        from .core.project import Project
        from .core.hw import fmt_duration
        est = estimates_for(Project(Path(a.dir)))
        for k, v in est.items():
            print(f"{k:15s} {fmt_duration(v)}")
        print(f"{'TOTAL':15s} {fmt_duration(sum(est.values()))}")
        return 0
    if a.cmd == "run":
        from .pipeline import run
        res = run(Path(a.dir), only=a.only, force=a.force, until=a.until, skip=a.skip)
        print(json.dumps(res, indent=2))
        return 0 if res.get("state") == "done" else 1
    if a.cmd == "review":
        from .server import serve
        serve(port=a.port, open_review=Path(a.dir))
        return 0
    if a.cmd == "serve":
        from .server import serve
        serve(port=a.port, episodes_dir=Path(a.episodes) if a.episodes else None)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
