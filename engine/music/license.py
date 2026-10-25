"""License gate for a *monetized YouTube podcast*.

Why these rules (Creative Commons 4.0 legal code, section 1):
* Synchronising music with moving images is explicitly an **Adaptation**.  So any
  **NoDerivatives (ND)** licence forbids using the track in a video at all.
* **NonCommercial (NC)** forbids use in monetized content.
* **ShareAlike (SA)** is legal only if the whole episode is released under the same CC
  licence - which most podcasters do not want.  It is *allowed with a warning* and off by
  default (``allow_sharealike``).
* **BY** (attribution) is fine as long as the attribution text is published - the engine
  writes it to ``attribution.txt`` for the video description.
* **CC0 / Public Domain** - no conditions.

Anything unknown is blocked, not guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED = {"cc0", "pdm", "publicdomain", "by"}
CONDITIONAL = {"by-sa"}
BLOCKED_TOKENS = ("nc", "nd")


@dataclass
class Verdict:
    ok: bool
    code: str            # normalised e.g. "by", "by-sa", "by-nc-nd", "cc0"
    reason: str
    attribution_required: bool
    sharealike: bool = False


def normalise(license_str: str | None, url: str | None = None) -> str:
    s = f"{license_str or ''} {url or ''}".lower()
    if "zero" in s or "cc0" in s or "publicdomain/zero" in s:
        return "cc0"
    if "publicdomain/mark" in s or s.strip() in ("pdm", "public domain") or "public domain" in s:
        return "pdm"
    m = re.search(r"\bby(?:[-_ ]?(nc|nd|sa))?(?:[-_ ]?(nc|nd|sa))?", s)
    if m:
        toks = ["by"] + sorted(t for t in m.groups() if t)
        # canonical order by-nc-nd / by-nc-sa / by-nd / by-sa
        order = {"nc": 0, "nd": 2, "sa": 1}
        toks = ["by"] + sorted({t for t in toks if t != "by"}, key=lambda t: order[t])
        return "-".join(toks)
    return "unknown"


def check(license_str: str | None, url: str | None = None, allow_sharealike: bool = False) -> Verdict:
    code = normalise(license_str, url)
    if code in ("cc0", "pdm"):
        return Verdict(True, code, "Public domain - no conditions.", False)
    if code == "by":
        return Verdict(True, code, "CC BY - allowed; attribution will be added to the description.", True)
    if any(t in code.split("-") for t in BLOCKED_TOKENS):
        why = []
        if "nc" in code:
            why.append("NonCommercial - not allowed in monetized videos")
        if "nd" in code:
            why.append("NoDerivatives - syncing music to video counts as an adaptation")
        return Verdict(False, code, "; ".join(why) + ".", True)
    if code == "by-sa":
        if allow_sharealike:
            return Verdict(True, code, "CC BY-SA - allowed ONLY if you publish the episode under CC BY-SA too.", True, True)
        return Verdict(False, code, "CC BY-SA - would require releasing your whole episode under CC BY-SA. Enable ShareAlike in Settings to allow.", True, True)
    return Verdict(False, code, "Licence could not be identified - blocked to be safe.", True)
