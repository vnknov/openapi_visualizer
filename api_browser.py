#!/usr/bin/env python3
"""
api_browser.py  –  Browse and visualise Swagger/OpenAPI specs from a swagger-resources endpoint.

Usage:
    python3 api_browser.py <swagger-resources-url> -o <target-folder>

Example:
    python3 api_browser.py https://app.ausgeochem.org/swagger-resources/ -o ./api_output
MIT License

Copyright (c) 2025
@Author vnknov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


# ── helpers ───────────────────────────────────────────────────────────────────

def fetch_json(url: str) -> object:
    """Fetch a URL and parse its body as JSON."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def safe_filename(name: str) -> str:
    """Turn an API group name into a safe file-system name, e.g. '03 Model' → '03_Model'."""
    return re.sub(r"[^\w\-]", "_", name).strip("_")


def resolve_spec_url(base_url: str, relative_path: str) -> str:
    """Join the base URL with the relative path returned by swagger-resources."""
    # URL-encode the relative path to handle spaces and special characters
    # Parse the path to separate path and query components
    if '?' in relative_path:
        path_part, query_part = relative_path.split('?', 1)
        # URL-encode the query parameters
        encoded_query = urllib.parse.quote(query_part, safe='=&')
        encoded_path = path_part + '?' + encoded_query
    else:
        encoded_path = urllib.parse.quote(relative_path, safe='/')

    return urllib.parse.urljoin(base_url, encoded_path)


def print_table(groups: list[dict]) -> None:
    """Pretty-print the list of API groups as a numbered table."""
    col_num  = 4
    col_name = max(len(g["name"]) for g in groups) + 2
    col_url  = max(len(g["url"])  for g in groups) + 2

    sep = f"+{'-'*(col_num+2)}+{'-'*(col_name+2)}+{'-'*(col_url+2)}+"
    hdr = f"| {'#'.center(col_num)} | {'Name'.ljust(col_name)} | {'Spec URL'.ljust(col_url)} |"

    print()
    print(sep)
    print(hdr)
    print(sep)
    for i, g in enumerate(groups, 1):
        num  = str(i).center(col_num)
        name = g["name"].ljust(col_name)
        url  = g["url"].ljust(col_url)
        print(f"| {num} | {name} | {url} |")
    print(sep)
    print()


def find_visualizer() -> str:
    """Locate openapi_visualizer.py – same directory as this script, or on PATH."""
    here = Path(__file__).parent / "openapi_visualizer.py"
    if here.exists():
        return str(here)
    # fall back: look anywhere on PATH via `which`
    try:
        result = subprocess.run(
            ["which", "openapi_visualizer.py"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        pass
    sys.exit(
        "\nXXX openapi_visualizer.py not found.\n"
        "    Place it in the same directory as this script or add it to your PATH.\n"
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browse Swagger specs from a swagger-resources endpoint and visualise them."
    )
    parser.add_argument("url",  help="Base URL of the swagger-resources endpoint")
    parser.add_argument("-o",   dest="output_dir", required=True,
                        help="Target folder for downloaded JSON specs and generated HTML files")
    args = parser.parse_args()

    base_url   = args.url.rstrip("/") + "/"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. fetch group list ───────────────────────────────────────────────────
    print(f"\n  Fetching API groups from {base_url} …")
    try:
        groups = fetch_json(base_url)
    except Exception as exc:
        sys.exit(f"XXX  Could not fetch swagger-resources: {exc}")

    if not isinstance(groups, list) or not groups:
        sys.exit("XXX  Unexpected response – expected a JSON array of API groups.")

    # ── 2. display table ──────────────────────────────────────────────────────
    print_table(groups)

    # ── 3. ask user to pick ───────────────────────────────────────────────────
    while True:
        raw = input(f"Enter a number [1–{len(groups)}] to download and visualise, or 'q' to quit: ").strip()
        if raw.lower() == "q":
            print("Bye!")
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= len(groups):
            choice = int(raw) - 1
            break
        print(f"  Please enter a number between 1 and {len(groups)}.")

    group     = groups[choice]
    api_name  = group["name"]
    spec_path = group.get("url") or group.get("location")
    spec_url  = resolve_spec_url(base_url, spec_path)
    slug      = safe_filename(api_name)

    json_file = output_dir / f"{slug}.json"
    html_file = output_dir / f"{slug}.html"

    # ── 4. download JSON spec ─────────────────────────────────────────────────
    print(f"\n   Downloading spec for '{api_name}' …")
    print(f"    URL : {spec_url}")
    try:
        spec = fetch_json(spec_url)
    except Exception as exc:
        sys.exit(f"XXX  Could not download spec: {exc}")

    with open(json_file, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
    print(f"    JSON: {json_file}")

    # ── 5. run visualizer ─────────────────────────────────────────────────────
    visualizer = find_visualizer()
    cmd = [sys.executable, visualizer, str(json_file), "-o", str(html_file)]

    print(f"\n   Running visualizer …\n    {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        sys.exit(f"\nXXX  Visualizer exited with code {result.returncode}.")

    # ── 6. open in browser ────────────────────────────────────────────────────
    if html_file.exists():
        html_uri = html_file.resolve().as_uri()
        print(f"\n   Opening in browser: {html_uri}")
        webbrowser.open(html_uri)
    else:
        print(f"\n   HTML file not found at {html_file} – check visualizer output above.")


if __name__ == "__main__":
    main()
