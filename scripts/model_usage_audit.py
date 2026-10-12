#!/usr/bin/env python
"""
Model usage audit: cross-reference installed Ollama models against proxy usage
to find cleanup candidates.

Data sources and why:
  * /proxy/analytics/histogram?view=daily  -- PRIMARY. Per-model/per-day request
    counts from `analytics_daily_by_model` (retained ~91 days). Verified against
    the hourly rollups: identical on every overlapping model-day except the
    oldest boundary day, where the 8-day hourly retention truncates.
  * /proxy/query_db  -- raw `request_logs`, pruned after LOG_RETENTION_DAYS
    (7 days here). Used only to enrich the recent window with distinct caller
    IPs and endpoint mix, which the rollups do not expose per model.
  * /api/tags -- installed models and on-disk sizes.

Do NOT use /proxy/analytics?hours=N for long windows: it reports whatever the
rollup/raw fallback yields and silently bottoms out at the request_logs
retention horizon, which makes long-idle models look unused.

Usage:
    PROXY_URL=http://host:8002 PROXY_ADMIN_KEY=... \
        ./.conda/bin/python scripts/model_usage_audit.py [--json out.json]
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

PAGE = 1000
EMBEDDING_ENDPOINTS = ("api/embed", "api/embeddings", "v1/embeddings")


def get(base, path, key, timeout=120):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"X-Admin-Key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def norm(name):
    """Canonical model key: Ollama treats `foo` and `foo:latest` alike, and tags
    are case-insensitive in practice (qwen3.6:35b vs qwen3.6:35B)."""
    if not name:
        return None
    n = str(name).strip().lower()
    if n.endswith(":latest"):
        n = n[: -len(":latest")]
    return n


def is_direct_path(name):
    """Names like `/models/Qwen3.6` are direct filesystem loads, not registry tags."""
    return str(name).startswith("/")


def is_probe(row):
    """Warm-up / enumeration traffic rather than a real user request.

    The model-sweep scripts on this deployment hit every installed tag from a
    single host with curl and an empty prompt. An empty prompt on a generate/chat
    endpoint is the reliable tell -- no human sends one.
    """
    ep = (row.get("endpoint") or "").strip().lower()
    if ep in EMBEDDING_ENDPOINTS:
        return False
    return not (row.get("prompt_text") or "").strip()


def fetch_recent_logs(base, key):
    rows, offset, total = [], 0, None
    while True:
        q = urllib.parse.urlencode({
            "limit": PAGE,
            "offset": offset,
            "sort_by": "timestamp_received",
            "sort_order": "desc",
            "fields": "model,timestamp_received,status,endpoint,ip_address,user_agent,prompt_text",
        })
        page = get(base, "/proxy/query_db?" + q, key)
        total = page.get("total_count", 0)
        batch = page.get("requests", [])
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return rows, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--idle-days", type=int, default=21)
    ap.add_argument("--low-req", type=int, default=5)
    args = ap.parse_args()

    base, key = os.getenv("PROXY_URL"), os.getenv("PROXY_ADMIN_KEY")
    if not base or not key:
        sys.exit("Set PROXY_URL and PROXY_ADMIN_KEY")

    tags = get(base, "/api/tags", key)
    hist = get(base, "/proxy/analytics/histogram?view=daily&metric=requests&top_n=500", key)
    logs, log_total = fetch_recent_logs(base, key)

    # --- installed inventory -------------------------------------------------
    installed = {}
    for m in tags.get("models", []):
        installed[norm(m["name"])] = {
            "display": m["name"],
            "size_gb": round(m.get("size", 0) / 1e9, 1),
            "pulled_at": (m.get("modified_at") or "")[:10],
            "param_size": (m.get("details") or {}).get("parameter_size"),
            "quant": (m.get("details") or {}).get("quantization_level"),
        }

    # --- 90-day usage from daily rollups ------------------------------------
    buckets = [b[:10] for b in hist.get("buckets", [])]
    usage = defaultdict(lambda: {"requests": 0.0, "days": set(), "aliases": set(), "last": None})
    for s in hist.get("by_model", []):
        raw = s["label"]
        k = norm(raw)
        u = usage[k]
        u["aliases"].add(raw)
        for b, v in zip(buckets, s.get("values") or []):
            if v:
                u["requests"] += v
                u["days"].add(b)
                if u["last"] is None or b > u["last"]:
                    u["last"] = b

    all_days = sorted({d for u in usage.values() for d in u["days"]})
    first_data, last_data = (all_days[0], all_days[-1]) if all_days else (None, None)
    covered = (date.fromisoformat(last_data) - date.fromisoformat(first_data)).days + 1 if all_days else 0
    today = date.fromisoformat(last_data) if last_data else datetime.utcnow().date()

    # --- recent-window enrichment from raw logs ------------------------------
    recent = defaultdict(lambda: {
        "requests": 0, "errors": 0, "embed": 0, "probe": 0, "organic": 0,
        "ips": set(), "organic_ips": set(),
    })
    for r in logs:
        k = norm(r.get("model"))
        if k is None:
            continue
        e = recent[k]
        e["requests"] += 1
        if (r.get("status") or "") == "failed":
            e["errors"] += 1
        if (r.get("endpoint") or "").strip().lower() in EMBEDDING_ENDPOINTS:
            e["embed"] += 1
        if r.get("ip_address"):
            e["ips"].add(r["ip_address"])
        if is_probe(r):
            e["probe"] += 1
        else:
            e["organic"] += 1
            if r.get("ip_address"):
                e["organic_ips"].add(r["ip_address"])

    # --- join ---------------------------------------------------------------
    rows = []
    for k in sorted(set(installed) | set(usage)):
        inst = installed.get(k)
        u = usage.get(k)
        rc = recent.get(k)
        reqs = int(round(u["requests"])) if u else 0
        last = u["last"] if u else None
        idle = (today - date.fromisoformat(last)).days if last else None
        aliases = sorted(u["aliases"]) if u else []
        rows.append({
            "model": inst["display"] if inst else (aliases[0] if aliases else k),
            "key": k,
            "installed": inst is not None,
            "direct_path": is_direct_path(aliases[0]) if aliases else False,
            "size_gb": inst["size_gb"] if inst else None,
            "param_size": inst["param_size"] if inst else None,
            "quant": inst["quant"] if inst else None,
            "pulled_at": inst["pulled_at"] if inst else None,
            "requests_90d": reqs,
            "days_used_90d": len(u["days"]) if u else 0,
            "last_used": last,
            "idle_days": idle,
            "aliases": aliases,
            "recent_requests_7d": rc["requests"] if rc else 0,
            "recent_distinct_ips": len(rc["ips"]) if rc else 0,
            "recent_embed_requests": rc["embed"] if rc else 0,
            "recent_probe_7d": rc["probe"] if rc else 0,
            "recent_organic_7d": rc["organic"] if rc else 0,
            "recent_organic_ips": len(rc["organic_ips"]) if rc else 0,
        })

    def verdict(r):
        if not r["installed"]:
            return "NOT INSTALLED"
        if r["requests_90d"] == 0:
            return "NEVER USED"
        # All known traffic accounted for by the probe sweep in the recent window
        # => nothing organic anywhere in the rollup window either.
        if r["recent_organic_7d"] == 0 and r["recent_probe_7d"] >= r["requests_90d"]:
            return "PROBE ONLY"
        if r["requests_90d"] <= args.low_req:
            return "BARELY USED"
        if r["idle_days"] is not None and r["idle_days"] >= args.idle_days:
            return "IDLE"
        return "IN USE"

    for r in rows:
        r["verdict"] = verdict(r)

    if args.json_out:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rollup_window": {"first_day_with_data": first_data, "last_day": last_data,
                              "days_covered": covered},
            "request_logs_window_rows": log_total,
            "rows": rows,
        }, open(args.json_out, "w"), indent=2, default=str)

    inst_rows = [r for r in rows if r["installed"]]
    print("Rollup usage window : %s -> %s (%d days with data)" % (first_data, last_data, covered))
    print("Raw request_logs    : %d rows (last ~7d, used for caller-IP detail)" % log_total)
    print("Installed models    : %d, %.1f GB on disk\n" % (
        len(inst_rows), sum(r["size_gb"] or 0 for r in inst_rows)))

    order = {"NEVER USED": 0, "PROBE ONLY": 1, "BARELY USED": 2, "IDLE": 3, "IN USE": 4}
    inst_rows.sort(key=lambda r: (order.get(r["verdict"], 9), r["requests_90d"], -(r["size_gb"] or 0)))

    hdr = "%-38s %6s %9s %7s %8s %6s %11s  %s" % (
        "model", "GB", "reqs_90d", "organic", "probe", "days", "last used", "verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in inst_rows:
        print("%-38s %6.1f %9d %7d %8d %6d %11s  %s" % (
            r["model"][:38], r["size_gb"] or 0, r["requests_90d"],
            r["recent_organic_7d"], r["recent_probe_7d"], r["days_used_90d"],
            r["last_used"] or "never", r["verdict"]))
    print("(organic/probe are last-7d only -- request_logs retention; reqs_90d from rollups)")

    print()
    for v in ("NEVER USED", "PROBE ONLY", "BARELY USED", "IDLE", "IN USE"):
        grp = [r for r in inst_rows if r["verdict"] == v]
        if grp:
            print("%-12s %2d models %8.1f GB" % (v, len(grp), sum(r["size_gb"] or 0 for r in grp)))

    safe = [r for r in inst_rows if r["verdict"] in ("NEVER USED", "PROBE ONLY")]
    print("\nCleanup candidates (NEVER USED + PROBE ONLY): %.1f GB across %d models" % (
        sum(r["size_gb"] or 0 for r in safe), len(safe)))
    for r in sorted(safe, key=lambda x: -(x["size_gb"] or 0)):
        print("  ollama rm %s   # %.1f GB" % (r["model"], r["size_gb"] or 0))

    multi = [r for r in rows if len(r["aliases"]) > 1]
    if multi:
        print("\nName aliases merged (same underlying model):")
        for r in multi:
            print("  %-38s <- %s" % (r["model"][:38], ", ".join(r["aliases"])))

    ghosts = [r for r in rows if not r["installed"] and r["requests_90d"] > 0]
    if ghosts:
        print("\nHad traffic but not in /api/tags now:")
        for r in sorted(ghosts, key=lambda x: -x["requests_90d"]):
            tag = " (direct path load)" if r["direct_path"] else " (deleted/renamed)"
            print("  %-38s %8d reqs, last %s%s" % (
                r["model"][:38], r["requests_90d"], r["last_used"], tag))


if __name__ == "__main__":
    main()
