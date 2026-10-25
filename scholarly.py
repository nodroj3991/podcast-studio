"""Free scholarly APIs: OpenAlex (primary), Crossref and Semantic Scholar (fallbacks).

* OpenAlex  - https://api.openalex.org  - no key; add ``mailto=`` for the polite pool.
             CC0 data.  Gives works per year, citations, co-authors, concepts/topics,
             institutions with years.
* Crossref  - https://api.crossref.org - no key; polite pool via ``mailto``.  Used to
             cross-check paper counts when OpenAlex has no author match.
* Semantic Scholar - https://api.semanticscholar.org/graph/v1 - no key needed at low rate
             (100 req / 5 min); gives citation counts and h-index.  CC BY-NC-SA data licence
             for bulk; API use in a podcast chart with attribution is within their terms, but
             we label every chart with "Source: ...".

Nothing is invented: if an author cannot be disambiguated (several candidates with the same
name and no institution match) we return ``candidates`` and the UI asks the host to pick.
Missing fields are returned as ``None`` and the chart module prints "no data".
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

UA = "PodcastStudio/0.2 (research-visuals; local desktop app)"


def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------------------------ OpenAlex
def openalex_find_author(name: str, institution: str | None = None, mailto: str | None = None) -> dict[str, Any]:
    data = _get("https://api.openalex.org/authors", {"search": name, "per-page": 10, "mailto": mailto})
    cands = []
    for a in data.get("results", []):
        insts = [i.get("display_name") for i in (a.get("affiliations") or []) for i in [i.get("institution", {})] if i]
        last = (a.get("last_known_institutions") or [{}])
        cands.append({"id": a["id"], "name": a.get("display_name"), "works": a.get("works_count"),
                      "cited_by": a.get("cited_by_count"), "orcid": a.get("orcid"),
                      "institution": (last[0].get("display_name") if last else None),
                      "all_institutions": insts[:6], "h_index": (a.get("summary_stats") or {}).get("h_index")})
    chosen = None
    if institution:
        key = institution.lower()
        matches = [c for c in cands if any(key in (i or "").lower() or (i or "").lower() in key for i in [c["institution"]] + c["all_institutions"])]
        if len(matches) >= 1:
            chosen = max(matches, key=lambda c: c["works"] or 0)
    if not chosen and len(cands) == 1:
        chosen = cands[0]
    if not chosen and cands and (cands[0]["works"] or 0) >= 5 * max((c["works"] or 0) for c in cands[1:] or [{"works": 0}]):
        chosen = cands[0]      # one clearly dominant match
    return {"chosen": chosen, "candidates": cands}


def openalex_profile(author_id: str, mailto: str | None = None) -> dict[str, Any]:
    aid = author_id.rsplit("/", 1)[-1]
    author = _get(f"https://api.openalex.org/authors/{aid}", {"mailto": mailto})
    works: list[dict[str, Any]] = []
    cursor = "*"
    while cursor and len(works) < 2000:
        page = _get("https://api.openalex.org/works", {
            "filter": f"authorships.author.id:{aid}", "per-page": 200, "cursor": cursor, "mailto": mailto,
            "select": "id,title,publication_year,cited_by_count,authorships,primary_topic,counts_by_year,type"})
        works += page.get("results", [])
        cursor = page.get("meta", {}).get("next_cursor")
    per_year = Counter(w["publication_year"] for w in works if w.get("publication_year"))
    # citations received per year: sum of counts_by_year across works (OpenAlex only keeps last ~10 years)
    cit_year = Counter()
    for w in works:
        for c in w.get("counts_by_year") or []:
            cit_year[c["year"]] += c.get("cited_by_count", 0)
    # author-level counts_by_year is more complete for citations
    for c in author.get("counts_by_year") or []:
        cit_year[c["year"]] = max(cit_year[c["year"]], c.get("cited_by_count", 0))
    coauthors = Counter()
    inst_years: dict[str, set] = {}
    for w in works:
        for au in w.get("authorships") or []:
            a = au.get("author") or {}
            if a.get("id", "").rsplit("/", 1)[-1] == aid:
                for inst in au.get("institutions") or []:
                    if inst.get("display_name") and w.get("publication_year"):
                        inst_years.setdefault(inst["display_name"], set()).add(w["publication_year"])
            elif a.get("display_name"):
                coauthors[a["display_name"]] += 1
    topics = Counter()
    for w in works:
        t = w.get("primary_topic") or {}
        if t.get("display_name"):
            topics[t["display_name"]] += 1
    institutions = sorted(({"institution": k, "from": min(v), "to": max(v), "papers": len(v)} for k, v in inst_years.items()),
                          key=lambda x: x["from"])
    years = sorted(per_year)
    cum, cum_series = 0, []
    for y in range(min(cit_year) if cit_year else 0, (max(cit_year) + 1) if cit_year else 0):
        cum += cit_year.get(y, 0)
        cum_series.append({"year": y, "citations": cit_year.get(y, 0), "cumulative": cum})
    return {
        "source": "OpenAlex", "source_url": author.get("id"), "name": author.get("display_name"),
        "works_count": author.get("works_count"), "cited_by_count": author.get("cited_by_count"),
        "h_index": (author.get("summary_stats") or {}).get("h_index"),
        "papers_per_year": [{"year": y, "papers": per_year[y]} for y in years],
        "citations_per_year": cum_series,
        "top_coauthors": [{"name": n, "papers": c} for n, c in coauthors.most_common(10)],
        "topics": [{"topic": t, "papers": c} for t, c in topics.most_common(8)],
        "institutions": institutions,
        "top_papers": sorted(({"title": w.get("title"), "year": w.get("publication_year"), "citations": w.get("cited_by_count", 0)}
                              for w in works if w.get("title")), key=lambda x: -x["citations"])[:5],
        "retrieved": __import__("time").strftime("%Y-%m-%d"),
    }


# ------------------------------------------------------------------------------ Crossref
def crossref_count(name: str, mailto: str | None = None) -> dict[str, Any] | None:
    try:
        data = _get("https://api.crossref.org/works", {"query.author": name, "rows": 0, "mailto": mailto})
        return {"source": "Crossref", "works_matching_name": data["message"]["total-results"]}
    except Exception:
        return None


# ------------------------------------------------------------------------------ Semantic Scholar
def semantic_scholar(name: str, api_key: str | None = None) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(
            "https://api.semanticscholar.org/graph/v1/author/search?" + urllib.parse.urlencode(
                {"query": name, "limit": 5, "fields": "name,affiliations,paperCount,citationCount,hIndex,url"}),
            headers={"User-Agent": UA, **({"x-api-key": api_key} if api_key else {})})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {"source": "Semantic Scholar", "candidates": data.get("data", [])}
    except Exception:
        return None


def gather(name: str, institution: str | None, mailto: str | None = None, author_id: str | None = None) -> dict[str, Any]:
    """One call for the stage: returns {"profile": ..., "candidates": [...], "crosschecks": {...}, "missing": [...]}"""
    out: dict[str, Any] = {"profile": None, "candidates": [], "crosschecks": {}, "missing": []}
    if author_id:
        out["profile"] = openalex_profile(author_id, mailto)
    else:
        found = openalex_find_author(name, institution, mailto)
        out["candidates"] = found["candidates"]
        if found["chosen"]:
            out["profile"] = openalex_profile(found["chosen"]["id"], mailto)
        else:
            out["missing"].append("Could not identify the guest in OpenAlex with confidence - pick from candidates.")
    out["crosschecks"]["crossref"] = crossref_count(name, mailto)
    out["crosschecks"]["semantic_scholar"] = semantic_scholar(name)
    p = out["profile"]
    if p:
        for key, label in (("papers_per_year", "papers per year"), ("citations_per_year", "citation growth"),
                           ("top_coauthors", "co-authors"), ("topics", "research topics"), ("institutions", "institutions over time")):
            if not p.get(key):
                out["missing"].append(f"No data for {label}.")
    return out
