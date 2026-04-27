#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request
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


def aggregate_stats(token: str, repos: List[dict]) -> Tuple[int, Dict[str, int], int]:
    total_stars = 0
    languages: Dict[str, int] = {}
    included_repo_count = 0

    for repo in repos:
        if repo.get("fork"):
            continue
        included_repo_count += 1
        total_stars += int(repo.get("stargazers_count", 0))
        languages_url = repo.get("languages_url")
        if not languages_url:
            continue
        payload, _ = request_json(languages_url, token)
        if not isinstance(payload, dict):
            continue
        for language, byte_count in payload.items():
            languages[language] = languages.get(language, 0) + int(byte_count)

    return total_stars, languages, included_repo_count


def top_languages(languages: Dict[str, int], limit: int) -> List[Tuple[str, int, float]]:
    total = sum(languages.values())
    if total == 0:
        return []
    ranked = sorted(languages.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [(name, value, (value / total) * 100.0) for name, value in ranked]


def render_svg(username: str, stars_earned: int, total_owned: int, analyzed_repos: int, top_langs: List[Tuple[str, int, float]]) -> str:
    width = 860
    row_height = 30
    header_height = 150
    language_rows = max(len(top_langs), 1)
    height = header_height + (language_rows * row_height) + 70

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
        ".barFill { fill: #238636; rx: 6; }",
        "</style>",
        f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" rx="12" />',
        f'<text class="title" x="24" y="42">{username} | Verified GitHub Stats</text>',
        '<text class="meta" x="24" y="66">Computed by GitHub Actions from GitHub API (includes private repos with token access)</text>',
        '<text class="label" x="24" y="102">Stars earned (on owned repos)</text>',
        f'<text class="value" x="24" y="132">{stars_earned}</text>',
        '<text class="label" x="320" y="102">Owned repos scanned</text>',
        f'<text class="value" x="320" y="132">{analyzed_repos} / {total_owned}</text>',
        '<text class="label" x="24" y="174">Top languages by bytes</text>',
    ]

    y = 200
    bar_x = 240
    bar_w = 560
    if not top_langs:
        lines.append('<text class="lang" x="24" y="206">No language data found.</text>')
    else:
        for name, _count, pct in top_langs:
            fill_w = int((pct / 100.0) * bar_w)
            if pct > 0 and fill_w < 2:
                fill_w = 2
            lines.append(f'<text class="lang" x="24" y="{y + 15}">{name} ({pct:.2f}%)</text>')
            lines.append(f'<rect class="barBg" x="{bar_x}" y="{y}" width="{bar_w}" height="16" />')
            lines.append(f'<rect class="barFill" x="{bar_x}" y="{y}" width="{fill_w}" height="16" />')
            y += row_height

    lines.append("</svg>")
    return "\n".join(lines)


def main() -> None:
    token = os.getenv("PROFILE_STATS_TOKEN") or os.getenv("METRICS_TOKEN")
    username = os.getenv("PROFILE_STATS_USERNAME") or os.getenv("GITHUB_REPOSITORY_OWNER") or ""
    if not token:
        fatal("Missing token. Set PROFILE_STATS_TOKEN (or METRICS_TOKEN).")
    if not username:
        fatal("Missing username. Set PROFILE_STATS_USERNAME or GITHUB_REPOSITORY_OWNER.")

    repos = fetch_all_owned_repos(token, username)
    stars, languages, analyzed = aggregate_stats(token, repos)
    ranked = top_languages(languages, TOP_LANG_LIMIT)

    svg = render_svg(username, stars, len(repos), analyzed, ranked)
    with open(OUTPUT_SVG, "w", encoding="utf-8") as svg_file:
        svg_file.write(svg)

    audit_payload = {
        "username": username,
        "owned_repos_found": len(repos),
        "repos_analyzed_non_forks": analyzed,
        "stars_earned_total": stars,
        "top_languages": [
            {"language": name, "bytes": value, "percentage": round(pct, 4)}
            for name, value, pct in ranked
        ],
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as json_file:
        json.dump(audit_payload, json_file, indent=2)

    print(f"Repositories found: {len(repos)}")
    print(f"Repositories analyzed (non-forks): {analyzed}")
    print(f"Stars earned total: {stars}")
    print(f"Top languages: {[item[0] for item in ranked]}")
    print(f"Wrote {OUTPUT_SVG} and {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
