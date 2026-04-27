#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple


API_BASE = "https://api.github.com"
OUTPUT_SVG = "profile-stats.svg"
OUTPUT_JSON = "profile-stats.json"
TOP_LANG_LIMIT = 8


def fatal(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def request_json(url: str, token: str) -> Tuple[object, dict]:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req) as response:  # nosec B310
        body = response.read().decode("utf-8")
        headers = dict(response.headers.items())
        return json.loads(body), headers


def parse_next_link(link_header: str) -> str:
    if not link_header:
        return ""
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' in section:
            start = section.find("<")
            end = section.find(">")
            if start != -1 and end != -1:
                return section[start + 1 : end]
    return ""


def fetch_all_owned_repos(token: str, username: str) -> List[dict]:
    params = urllib.parse.urlencode(
        {
            "visibility": "all",
            "affiliation": "owner",
            "per_page": 100,
            "sort": "updated",
        }
    )
    next_url = f"{API_BASE}/user/repos?{params}"
    repos: List[dict] = []
    while next_url:
        payload, headers = request_json(next_url, token)
        if not isinstance(payload, list):
            fatal("Unexpected response while fetching repositories.")
        repos.extend(payload)
        next_url = parse_next_link(headers.get("Link", ""))
    owned = [r for r in repos if r.get("owner", {}).get("login", "").lower() == username.lower()]
    return owned


def parse_github_timestamp(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def aggregate_stats(token: str, repos: List[dict], recent_days: int) -> Tuple[int, Dict[str, int], Dict[str, int], int, int]:
    total_stars = 0
    languages_all_time: Dict[str, int] = {}
    languages_recent: Dict[str, int] = {}
    included_repo_count = 0
    recent_repo_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

    for repo in repos:
        if repo.get("fork"):
            continue
        included_repo_count += 1
        total_stars += int(repo.get("stargazers_count", 0))

        pushed_at = repo.get("pushed_at", "")
        is_recent = False
        if pushed_at:
            try:
                is_recent = parse_github_timestamp(pushed_at) >= cutoff
            except ValueError:
                is_recent = False
        if is_recent:
            recent_repo_count += 1

        languages_url = repo.get("languages_url")
        if not languages_url:
            continue
        payload, _ = request_json(languages_url, token)
        if not isinstance(payload, dict):
            continue
        for language, byte_count in payload.items():
            value = int(byte_count)
            languages_all_time[language] = languages_all_time.get(language, 0) + value
            if is_recent:
                languages_recent[language] = languages_recent.get(language, 0) + value

    return total_stars, languages_all_time, languages_recent, included_repo_count, recent_repo_count


def top_languages(languages: Dict[str, int], limit: int) -> List[Tuple[str, int, float]]:
    total = sum(languages.values())
    if total == 0:
        return []
    ranked = sorted(languages.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [(name, value, (value / total) * 100.0) for name, value in ranked]


def append_language_block(
    lines: List[str],
    title: str,
    x_label: int,
    y_start: int,
    bar_x: int,
    bar_w: int,
    top_langs: List[Tuple[str, int, float]],
    fill_class: str,
) -> int:
    lines.append(f'<text class="label" x="{x_label}" y="{y_start - 26}">{title}</text>')
    y = y_start
    if not top_langs:
        lines.append(f'<text class="lang" x="{x_label}" y="{y + 6}">No language data found.</text>')
        return y + 28

    for name, _count, pct in top_langs:
        fill_w = int((pct / 100.0) * bar_w)
        if pct > 0 and fill_w < 2:
            fill_w = 2
        lines.append(f'<text class="lang" x="{x_label}" y="{y + 15}">{name} ({pct:.2f}%)</text>')
        lines.append(f'<rect class="barBg" x="{bar_x}" y="{y}" width="{bar_w}" height="16" />')
        lines.append(f'<rect class="{fill_class}" x="{bar_x}" y="{y}" width="{fill_w}" height="16" />')
        y += 30
    return y


def render_svg(
    username: str,
    stars_earned: int,
    total_owned: int,
    analyzed_repos: int,
    recent_days: int,
    recent_repo_count: int,
    top_langs_all: List[Tuple[str, int, float]],
    top_langs_recent: List[Tuple[str, int, float]],
) -> str:
    width = 860
    block_rows = max(max(len(top_langs_all), 1), max(len(top_langs_recent), 1))
    height = 220 + (block_rows * 30 * 2) + 34

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        ".bg { fill: #0d1117; }",
        ".title { fill: #c9d1d9; font: 700 26px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; }",
        ".meta { fill: #8b949e; font: 500 14px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; }",
        ".label { fill: #c9d1d9; font: 600 16px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; }",
        ".value { fill: #58a6ff; font: 700 24px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; }",
        ".lang { fill: #c9d1d9; font: 500 14px -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; }",
        ".barBg { fill: #21262d; rx: 6; }",
        ".barFillAll { fill: #238636; rx: 6; }",
        ".barFillRecent { fill: #1f6feb; rx: 6; }",
        "</style>",
        f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" rx="12" />',
        f'<text class="title" x="24" y="42">{username} | Verified GitHub Stats</text>',
        '<text class="meta" x="24" y="66">Computed by GitHub Actions from GitHub API (includes private repos with token access)</text>',
        '<text class="label" x="24" y="102">Stars earned (on owned repos)</text>',
        f'<text class="value" x="24" y="132">{stars_earned}</text>',
        '<text class="label" x="320" y="102">Owned repos scanned</text>',
        f'<text class="value" x="320" y="132">{analyzed_repos} / {total_owned}</text>',
        f'<text class="meta" x="24" y="166">Recent window: last {recent_days} days ({recent_repo_count} repos pushed)</text>',
    ]

    y = 214
    bar_x = 290
    bar_w = 560
    y = append_language_block(
        lines,
        f"Top languages (last {recent_days} days, by bytes in recently pushed repos)",
        24,
        y,
        bar_x,
        bar_w,
        top_langs_recent,
        "barFillRecent",
    )
    y += 40
    append_language_block(
        lines,
        "Top languages (all-time, by bytes)",
        24,
        y,
        bar_x,
        bar_w,
        top_langs_all,
        "barFillAll",
    )

    lines.append("</svg>")
    return "\n".join(lines)


def main() -> None:
    token = os.getenv("PROFILE_STATS_TOKEN") or os.getenv("METRICS_TOKEN")
    username = os.getenv("PROFILE_STATS_USERNAME") or os.getenv("GITHUB_REPOSITORY_OWNER") or ""
    recent_days = int(os.getenv("PROFILE_STATS_RECENT_DAYS", "180"))
    if recent_days <= 0:
        recent_days = 180
    if not token:
        fatal("Missing token. Set PROFILE_STATS_TOKEN (or METRICS_TOKEN).")
    if not username:
        fatal("Missing username. Set PROFILE_STATS_USERNAME or GITHUB_REPOSITORY_OWNER.")

    repos = fetch_all_owned_repos(token, username)
    stars, languages_all, languages_recent, analyzed, recent_repo_count = aggregate_stats(token, repos, recent_days)
    ranked_all = top_languages(languages_all, TOP_LANG_LIMIT)
    ranked_recent = top_languages(languages_recent, TOP_LANG_LIMIT)

    svg = render_svg(
        username,
        stars,
        len(repos),
        analyzed,
        recent_days,
        recent_repo_count,
        ranked_all,
        ranked_recent,
    )
    with open(OUTPUT_SVG, "w", encoding="utf-8") as svg_file:
        svg_file.write(svg)

    audit_payload = {
        "username": username,
        "owned_repos_found": len(repos),
        "repos_analyzed_non_forks": analyzed,
        "recent_window_days": recent_days,
        "recent_repos_pushed": recent_repo_count,
        "stars_earned_total": stars,
        "top_languages_all_time": [
            {"language": name, "bytes": value, "percentage": round(pct, 4)}
            for name, value, pct in ranked_all
        ],
        "top_languages_recent_window": [
            {"language": name, "bytes": value, "percentage": round(pct, 4)}
            for name, value, pct in ranked_recent
        ],
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as json_file:
        json.dump(audit_payload, json_file, indent=2)

    print(f"Repositories found: {len(repos)}")
    print(f"Repositories analyzed (non-forks): {analyzed}")
    print(f"Stars earned total: {stars}")
    print(f"Recent window days: {recent_days}")
    print(f"Recent repos pushed: {recent_repo_count}")
    print(f"Top languages (all-time): {[item[0] for item in ranked_all]}")
    print(f"Top languages (recent window): {[item[0] for item in ranked_recent]}")
    print(f"Wrote {OUTPUT_SVG} and {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
