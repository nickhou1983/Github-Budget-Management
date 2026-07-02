#!/usr/bin/env python3
"""
Enable the AI Credit Pool for Enterprise Cost Centers by name via GitHub API.

The AI credit pool lets a cost center draw only the included AI Credits funded
by licenses attributed to that cost center. The pool amount is calculated
automatically:
    - Copilot Business:   3,000 AI Credits per license per month
    - Copilot Enterprise: 7,000 AI Credits per license per month

Usage:
    # Enable for one or more cost centers by name
    python enable_ai_credit_pool.py --enterprise YOUR_ENTERPRISE --token ghp_xxx \
        --name "Cost Center A" --name "Cost Center B"

    # Enable for cost centers listed in a CSV file (one name per line)
    python enable_ai_credit_pool.py --enterprise YOUR_ENTERPRISE --token ghp_xxx \
        --config cost_centers.csv

    # Disable instead of enable
    python enable_ai_credit_pool.py --enterprise YOUR_ENTERPRISE --token ghp_xxx \
        --name "Cost Center A" --disable

    # List all cost centers (id, name, ai_credit_pool status)
    python enable_ai_credit_pool.py --enterprise YOUR_ENTERPRISE --token ghp_xxx --list

    # Preview without applying
    python enable_ai_credit_pool.py --enterprise YOUR_ENTERPRISE --token ghp_xxx \
        --name "Cost Center A" --dry-run

CSV format (cost_centers.csv):
    # Cost Center Name
    Cost Center A
    Cost Center B

API Reference:
    GET    /enterprises/{enterprise}/settings/billing/cost-centers
    PATCH  /enterprises/{enterprise}/settings/billing/cost-centers/{cost_center_id}
    https://docs.github.com/en/rest/enterprise-admin/billing
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

from settings import resolve_credentials

API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"


def load_cost_center_names(config_path: str) -> list[str]:
    """Load cost center names from a CSV file (one name per line)."""
    names = []
    path = Path(config_path)
    if not path.exists():
        print(f"Error: Config file '{config_path}' not found.")
        sys.exit(1)

    with open(path, "r") as f:
        reader = csv.reader(f)
        for line_num, row in enumerate(reader, 1):
            if not row or row[0].strip().startswith("#"):
                continue
            name = row[0].strip()
            if not name:
                continue
            names.append(name)

    return names


def get_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }


def build_cost_centers_url(enterprise: str) -> str:
    """Build the base cost-centers URL for an enterprise."""
    return f"{API_BASE}/enterprises/{enterprise}/settings/billing/cost-centers"


def list_cost_centers(token: str, enterprise: str) -> list[dict]:
    """List all cost centers with pagination support."""
    base_url = build_cost_centers_url(enterprise)
    headers = get_headers(token)
    all_cost_centers = []
    page = 1

    while True:
        resp = requests.get(base_url, headers=headers, params={"page": page, "per_page": 100})
        if resp.status_code == 200:
            data = resp.json()
            # API may return either a list or an object with a "costCenters"/"cost_centers" key.
            if isinstance(data, list):
                batch = data
            else:
                batch = data.get("costCenters") or data.get("cost_centers") or []
            active = [cc for cc in batch if cc.get("state") == "active"]
            all_cost_centers.extend(active)
            # Stop when fewer than a full page is returned (no explicit pagination flag).
            if len(batch) < 100:
                break
            page += 1
        elif resp.status_code == 404:
            print(f"Note: Cost centers endpoint returned 404. '{enterprise}' may not have cost centers enabled.")
            return []
        else:
            print(f"Error listing cost centers: {resp.status_code} - {resp.text}")
            return []

    return all_cost_centers


def find_cost_center_by_name(cost_centers: list[dict], name: str) -> dict | None:
    """Find a cost center by its name (case-insensitive)."""
    target = name.strip().lower()
    for cc in cost_centers:
        cc_name = (cc.get("name") or "").strip().lower()
        if cc_name == target:
            return cc
    return None


def set_ai_credit_pool(
    token: str,
    enterprise: str,
    cost_center_id: str,
    name: str,
    enabled: bool,
) -> dict:
    """Enable or disable the AI credit pool for a cost center.

    Note: this PATCH endpoint also serves as "update a cost center name", so the
    `name` field is required by the API even when only toggling the AI pool.
    """
    base_url = build_cost_centers_url(enterprise)
    url = f"{base_url}/{cost_center_id}"
    headers = get_headers(token)
    payload = {
        "name": name,
        "ai_credit_pool_enabled": enabled,
    }
    resp = requests.patch(url, headers=headers, json=payload)
    return {"status": resp.status_code, "body": resp.json() if resp.content else {}}


def display_cost_centers(token: str, enterprise: str):
    """List all cost centers and display their AI credit pool status."""
    print(f"\n{'='*72}")
    print(f"  Cost Centers")
    print(f"  Enterprise: {enterprise}")
    print(f"{'='*72}\n")

    print("Fetching cost centers...")
    cost_centers = list_cost_centers(token, enterprise)

    if not cost_centers:
        print("No cost centers found.")
        return

    print(f"Found {len(cost_centers)} cost center(s):\n")
    print(f"  {'Name':<36} {'AI Pool':<8} {'Cost Center ID'}")
    print(f"  {'-'*36} {'-'*8} {'-'*24}")

    for cc in sorted(cost_centers, key=lambda c: (c.get("name") or "")):
        name = cc.get("name", "N/A")
        cc_id = cc.get("id", "N/A")
        enabled = cc.get("ai_credit_pool_enabled")
        status = "ON" if enabled else ("OFF" if enabled is not None else "?")
        print(f"  {name:<36} {status:<8} {cc_id}")

    print(f"\n  Total cost centers: {len(cost_centers)}")


def batch_set_ai_credit_pool(
    token: str,
    enterprise: str,
    names: list[str],
    enabled: bool,
    dry_run: bool = False,
):
    """Enable/disable the AI credit pool for cost centers identified by name."""
    action = "Enable" if enabled else "Disable"
    print(f"\n{'='*72}")
    print(f"  Batch {action} AI Credit Pool")
    print(f"  Enterprise: {enterprise}")
    print(f"  Cost centers to configure: {len(names)}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*72}\n")

    print("Fetching cost centers...")
    cost_centers = list_cost_centers(token, enterprise)
    print(f"Found {len(cost_centers)} cost center(s).\n")

    results = {"updated": [], "skipped": [], "not_found": [], "failed": []}

    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] Processing cost center: '{name}'")

        cc = find_cost_center_by_name(cost_centers, name)
        if cc is None:
            print(f"  ✗ Cost center '{name}' not found.")
            results["not_found"].append(name)
            continue

        cc_id = cc.get("id")
        cc_name = cc.get("name") or name
        current = cc.get("ai_credit_pool_enabled")
        if current is not None and current == enabled:
            print(f"  ✓ Already {'enabled' if enabled else 'disabled'} (id={cc_id}), skipping.")
            results["skipped"].append(name)
            continue

        print(f"  → Setting ai_credit_pool_enabled={enabled} (id={cc_id})")

        if dry_run:
            print(f"  [DRY RUN] Would {action.lower()} AI credit pool for '{name}'")
            results["updated"].append(name)
        else:
            resp = set_ai_credit_pool(token, enterprise, cc_id, cc_name, enabled)
            if resp["status"] in (200, 204):
                print(f"  ✓ {action}d successfully.")
                results["updated"].append(name)
            else:
                print(f"  ✗ Failed: {resp['status']} - {resp['body']}")
                results["failed"].append((name, resp))

        # Rate limiting: respect GitHub API limits
        if not dry_run and i < len(names):
            time.sleep(1)

    # Summary
    print(f"\n{'='*72}")
    print(f"  Summary")
    print(f"{'='*72}")
    print(f"  Updated:   {len(results['updated'])} - {results['updated']}")
    print(f"  Skipped:   {len(results['skipped'])} - {results['skipped']}")
    print(f"  Not found: {len(results['not_found'])} - {results['not_found']}")
    print(f"  Failed:    {len(results['failed'])} - {[f[0] for f in results['failed']]}")
    print()

    if results["failed"]:
        print("Failed details:")
        for name, resp in results["failed"]:
            print(f"  {name}: {resp['status']} - {json.dumps(resp['body'], indent=2)}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Enable the AI Credit Pool for Enterprise Cost Centers by name."
    )

    parser.add_argument("--enterprise", help="GitHub Enterprise name (slug). Falls back to settings.ini / GITHUB_ENTERPRISE.")
    parser.add_argument("--token", help="GitHub Personal Access Token (with billing scope). Falls back to settings.ini / GITHUB_TOKEN.")
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="Cost center name to configure (can be repeated)",
    )
    parser.add_argument("--config", help="Path to CSV file with cost center names (one per line)")
    parser.add_argument("--list", action="store_true", help="List all cost centers and their AI pool status")
    parser.add_argument("--disable", action="store_true", help="Disable instead of enable the AI credit pool")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")

    args = parser.parse_args()

    enterprise, token = resolve_credentials(args.enterprise, args.token)
    if not enterprise:
        print("Error: Enterprise name is required. Provide --enterprise, set GITHUB_ENTERPRISE, or configure settings.ini.")
        sys.exit(1)
    if not token:
        print("Error: Token is required. Provide --token, set GITHUB_TOKEN, or configure settings.ini.")
        sys.exit(1)

    if args.list:
        display_cost_centers(token=token, enterprise=enterprise)
        return

    names = list(args.name)
    if args.config:
        names.extend(load_cost_center_names(args.config))

    # De-duplicate while preserving order
    seen = set()
    unique_names = []
    for n in names:
        key = n.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique_names.append(n.strip())

    if not unique_names:
        print("No cost center names provided. Use --name or --config.")
        sys.exit(1)

    enabled = not args.disable
    print(f"Cost centers to {'enable' if enabled else 'disable'} AI credit pool:")
    for n in unique_names:
        print(f"  {n}")

    batch_set_ai_credit_pool(
        token=token,
        enterprise=enterprise,
        names=unique_names,
        enabled=enabled,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
