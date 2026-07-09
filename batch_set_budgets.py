#!/usr/bin/env python3
"""
Batch set User-Level Budgets with differentiated amounts via GitHub API.

Supports both Organization and Enterprise level budget management.

Usage:
    # Organization mode
    python batch_set_budgets.py --org YOUR_ORG --token YOUR_TOKEN --config config.csv [--dry-run]

    # Enterprise mode
    python batch_set_budgets.py --enterprise YOUR_ENTERPRISE --token YOUR_TOKEN --config config.csv [--dry-run]

    # List all user budgets
    python batch_set_budgets.py --org YOUR_ORG --token YOUR_TOKEN --list

CSV format:
    username,amount
    octocat,100
    developer1,200

API Reference:
    GET    /organizations/{org}/settings/billing/budgets
    POST   /organizations/{org}/settings/billing/budgets
    PATCH  /organizations/{org}/settings/billing/budgets/{budget_id}
    GET    /enterprises/{enterprise}/settings/billing/budgets
    POST   /enterprises/{enterprise}/settings/billing/budgets
    PATCH  /enterprises/{enterprise}/settings/billing/budgets/{budget_id}
    https://docs.github.com/en/rest/billing/budgets
"""

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from settings import resolve_credentials

API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"


@dataclass
class BudgetConfig:
    username: str
    amount: int


def load_config(config_path: str) -> list[BudgetConfig]:
    """Load user budget configurations from CSV file."""
    configs = []
    path = Path(config_path)
    if not path.exists():
        print(f"Error: Config file '{config_path}' not found.")
        sys.exit(1)

    with open(path, "r") as f:
        reader = csv.reader(f)
        for line_num, row in enumerate(reader, 1):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 2:
                print(f"Warning: Skipping malformed line {line_num}: {row}")
                continue
            username = row[0].strip()
            try:
                amount = int(float(row[1].strip()))
            except ValueError:
                print(f"Warning: Invalid amount on line {line_num}: '{row[1]}', skipping.")
                continue
            if amount <= 0:
                print(f"Warning: Amount must be positive on line {line_num}, skipping.")
                continue
            configs.append(BudgetConfig(username=username, amount=amount))

    return configs


def get_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _print_curl(
    method: str,
    url: str,
    headers: dict,
    params: dict | None = None,
    payload: dict | None = None,
) -> None:
    """Print the equivalent curl command for a GitHub API request."""
    params = params or {}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}" if qs else url

    print("# Request")
    print("curl -L \\")
    print(f"  -X {method} \\")
    for key, value in headers.items():
        display_value = "Bearer ***" if key == "Authorization" else value
        print(f'  -H "{key}: {display_value}" \\')
    if payload is not None:
        print(f"  -d '{json.dumps(payload, ensure_ascii=False)}' \\")
    print(f"  \"{full_url}\"")
    print()


def build_budgets_url(org: str | None = None, enterprise: str | None = None) -> str:
    """Build the base budgets URL for org or enterprise."""
    if enterprise:
        return f"{API_BASE}/enterprises/{enterprise}/settings/billing/budgets"
    return f"{API_BASE}/organizations/{org}/settings/billing/budgets"


def list_existing_budgets(
    token: str, org: str | None = None, enterprise: str | None = None
) -> list[dict]:
    """List all existing budgets with pagination support."""
    base_url = build_budgets_url(org=org, enterprise=enterprise)
    headers = get_headers(token)
    all_budgets = []
    page = 1

    while True:
        params = {"page": page, "per_page": 10, "scope": "user"}
        _print_curl("GET", base_url, headers, params=params)
        resp = requests.get(base_url, headers=headers, params=params)
        if resp.status_code == 200:
            data = resp.json()
            all_budgets.extend(data.get("budgets", []))
            if not data.get("has_next_page", False):
                break
            page += 1
        elif resp.status_code == 404:
            entity = enterprise or org
            print(f"Note: Budget endpoint returned 404. '{entity}' may not have billing budgets enabled.")
            return []
        else:
            print(f"Error listing budgets: {resp.status_code} - {resp.text}")
            return []

    return all_budgets


def find_user_budget(budgets: list[dict], username: str) -> dict | None:
    """Find an existing budget for a specific user."""
    for budget in budgets:
        if (
            budget.get("budget_scope") == "user"
            and budget.get("budget_entity_name") == username
        ):
            return budget
    return None


def get_user_consumed(
    token: str,
    username: str,
    org: str | None = None,
    enterprise: str | None = None,
) -> dict | None:
    """Fetch the consumed (used) amount for a single user's budget.

    Calls the budgets endpoint with the `user` filter, which returns a
    top-level `effective_budget` object containing `budget_amount` and
    `consumed_amount` (month-to-date usage in USD) for that user.

    Returns the `effective_budget` dict, or None when unavailable.
    """
    base_url = build_budgets_url(org=org, enterprise=enterprise)
    headers = get_headers(token)
    params = {"scope": "user", "user": username, "per_page": 10}
    _print_curl("GET", base_url, headers, params=params)
    resp = requests.get(
        base_url,
        headers=headers,
        params=params,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    effective = data.get("effective_budget")
    if not isinstance(effective, dict):
        return None
    return effective


def create_user_budget(
    token: str,
    username: str,
    amount: int,
    org: str | None = None,
    enterprise: str | None = None,
) -> dict:
    """Create a new user-level budget."""
    url = build_budgets_url(org=org, enterprise=enterprise)
    headers = get_headers(token)
    payload = {
        "budget_amount": amount,
        "prevent_further_usage": True,
        "budget_scope": "user",
        "budget_entity_name": username,
        "budget_product_sku": "ai_credits",
        "budget_type": "BundlePricing",
        "budget_alerting": {
            "will_alert": True,
            "alert_recipients": [username],
        },
        "user": username,
    }
    _print_curl("POST", url, headers, payload=payload)
    resp = requests.post(url, headers=headers, json=payload)
    return {"status": resp.status_code, "body": resp.json() if resp.content else {}}


def update_user_budget(
    token: str,
    budget_id: str,
    username: str,
    amount: int,
    org: str | None = None,
    enterprise: str | None = None,
) -> dict:
    """Update an existing user-level budget."""
    base_url = build_budgets_url(org=org, enterprise=enterprise)
    url = f"{base_url}/{budget_id}"
    headers = get_headers(token)
    payload = {
        "budget_amount": amount,
        "prevent_further_usage": True,
    }
    _print_curl("PATCH", url, headers, payload=payload)
    resp = requests.patch(url, headers=headers, json=payload)
    return {"status": resp.status_code, "body": resp.json() if resp.content else {}}


def list_user_budgets(
    token: str,
    org: str | None = None,
    enterprise: str | None = None,
):
    """List all user-level budgets and display them."""
    entity_name = enterprise or org
    entity_type = "Enterprise" if enterprise else "Organization"

    print(f"\n{'='*60}")
    print(f"  User-Level Budgets")
    print(f"  {entity_type}: {entity_name}")
    print(f"{'='*60}\n")

    print("Fetching user budgets...")
    budgets = list_existing_budgets(token, org=org, enterprise=enterprise)

    if not budgets:
        print("No user budgets found.")
        return

    print(f"Found {len(budgets)} user budget(s):\n")
    print(f"  {'Username':<30} {'Amount':>10} {'Budget ID'}")
    print(f"  {'-'*30} {'-'*10} {'-'*36}")

    for budget in sorted(budgets, key=lambda b: b.get("budget_entity_name", "")):
        username = budget.get("budget_entity_name", "N/A")
        amount = budget.get("budget_amount", 0)
        budget_id = budget.get("id", "N/A")
        print(f"  {username:<30} ${amount:>9} {budget_id}")

    total = sum(b.get("budget_amount", 0) for b in budgets)
    print(f"\n  {'Total':<30} ${total:>9}")
    print(f"  {'Users':<30} {len(budgets):>10}")


def report_budget_usage(
    token: str,
    org: str | None = None,
    enterprise: str | None = None,
):
    """Report the consumed (used) amount for each user-level budget.

    Enumerates all user budgets, then queries each user's month-to-date
    consumed amount and prints budget / used / remaining / used%.
    """
    entity_name = enterprise or org
    entity_type = "Enterprise" if enterprise else "Organization"

    print(f"\n{'='*78}")
    print(f"  User-Level Budget Usage")
    print(f"  {entity_type}: {entity_name}")
    print(f"{'='*78}\n")

    print("Fetching user budgets...")
    budgets = list_existing_budgets(token, org=org, enterprise=enterprise)

    if not budgets:
        print("No user budgets found.")
        return

    budgets = sorted(budgets, key=lambda b: b.get("budget_entity_name", ""))
    print(f"Found {len(budgets)} user budget(s). Querying usage per user...\n")

    print(f"  {'Username':<30} {'Budget':>10} {'Used':>12} {'Remaining':>12} {'Used%':>8}")
    print(f"  {'-'*30} {'-'*10} {'-'*12} {'-'*12} {'-'*8}")

    total_budget = 0
    total_used = 0.0

    for i, budget in enumerate(budgets, 1):
        username = budget.get("budget_entity_name", "N/A")
        limit = int(budget.get("budget_amount", 0) or 0)

        effective = get_user_consumed(token, username, org=org, enterprise=enterprise)
        if effective is not None:
            limit = int(effective.get("budget_amount", limit) or limit)
            used = float(effective.get("consumed_amount", 0) or 0)
            remaining = limit - used
            pct = (used / limit * 100) if limit else 0.0
            print(
                f"  {username:<30} ${limit:>9,} ${used:>11,.2f} "
                f"${remaining:>11,.2f} {pct:>7.1f}%"
            )
            total_used += used
        else:
            print(
                f"  {username:<30} ${limit:>9,} {'N/A':>12} {'N/A':>12} {'N/A':>8}"
            )

        total_budget += limit

        # Rate limiting: one request per user, respect GitHub API limits.
        if i < len(budgets):
            time.sleep(1)

    total_remaining = total_budget - total_used
    total_pct = (total_used / total_budget * 100) if total_budget else 0.0
    print(f"  {'-'*30} {'-'*10} {'-'*12} {'-'*12} {'-'*8}")
    print(
        f"  {'Total':<30} ${total_budget:>9,} ${total_used:>11,.2f} "
        f"${total_remaining:>11,.2f} {total_pct:>7.1f}%"
    )
    print(f"  {'Users':<30} {len(budgets):>10}")


def batch_set_budgets(
    token: str,
    configs: list[BudgetConfig],
    org: str | None = None,
    enterprise: str | None = None,
    dry_run: bool = False,
):
    """Batch set user-level budgets with differentiated amounts."""
    entity_name = enterprise or org
    entity_type = "Enterprise" if enterprise else "Organization"

    print(f"\n{'='*60}")
    print(f"  Batch User-Level Budget Configuration")
    print(f"  {entity_type}: {entity_name}")
    print(f"  Users to configure: {len(configs)}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    # List existing budgets
    print("Fetching existing budgets...")
    existing_budgets = list_existing_budgets(token, org=org, enterprise=enterprise)
    print(f"Found {len(existing_budgets)} existing user budget(s).\n")

    results = {"created": [], "updated": [], "skipped": [], "failed": []}

    for i, config in enumerate(configs, 1):
        print(f"[{i}/{len(configs)}] Processing: {config.username} -> ${config.amount}/month")

        existing = find_user_budget(existing_budgets, config.username)

        if existing:
            current_amount = existing.get("budget_amount", 0)
            if current_amount == config.amount:
                print(f"  ✓ Already set to ${config.amount}, skipping.")
                results["skipped"].append(config.username)
                continue

            budget_id = existing.get("id")
            print(f"  → Updating budget (id={budget_id}): ${current_amount} -> ${config.amount}")

            if dry_run:
                print(f"  [DRY RUN] Would update budget for {config.username}")
                results["updated"].append(config.username)
            else:
                resp = update_user_budget(
                    token, budget_id, config.username, config.amount,
                    org=org, enterprise=enterprise,
                )
                if resp["status"] in (200, 204):
                    print(f"  ✓ Updated successfully.")
                    results["updated"].append(config.username)
                else:
                    print(f"  ✗ Failed: {resp['status']} - {resp['body']}")
                    results["failed"].append((config.username, resp))
        else:
            print(f"  → Creating new budget: ${config.amount}")

            if dry_run:
                print(f"  [DRY RUN] Would create budget for {config.username}")
                results["created"].append(config.username)
            else:
                resp = create_user_budget(
                    token, config.username, config.amount,
                    org=org, enterprise=enterprise,
                )
                if resp["status"] in (200, 201):
                    print(f"  ✓ Created successfully.")
                    results["created"].append(config.username)
                else:
                    print(f"  ✗ Failed: {resp['status']} - {resp['body']}")
                    if "budget_entity_name" in str(resp.get("body", "")):
                        print(f"  ⚠ Note: User-scope budgets may need to be created via GitHub UI first.")
                        print(f"    Then this script can update the amount.")
                    results["failed"].append((config.username, resp))

        # Rate limiting: respect GitHub API limits
        if not dry_run and i < len(configs):
            time.sleep(1)

    # Summary
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Created: {len(results['created'])} - {results['created']}")
    print(f"  Updated: {len(results['updated'])} - {results['updated']}")
    print(f"  Skipped: {len(results['skipped'])} - {results['skipped']}")
    print(f"  Failed:  {len(results['failed'])} - {[f[0] for f in results['failed']]}")
    print()

    if results["failed"]:
        print("Failed details:")
        for username, resp in results["failed"]:
            print(f"  {username}: {resp['status']} - {json.dumps(resp['body'], indent=2)}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Batch set GitHub User-Level Budgets with differentiated amounts."
    )

    # Mutually exclusive: org or enterprise
    target_group = parser.add_mutually_exclusive_group(required=False)
    target_group.add_argument("--org", help="GitHub organization name")
    target_group.add_argument("--enterprise", help="GitHub Enterprise name (falls back to settings.ini / GITHUB_ENTERPRISE)")

    parser.add_argument("--token", help="GitHub Personal Access Token (falls back to settings.ini / GITHUB_TOKEN)")
    parser.add_argument("--config", default="config.csv", help="Path to CSV config file (default: config.csv)")
    parser.add_argument("--list", action="store_true", help="List all existing user budgets")
    parser.add_argument("--usage", action="store_true", help="Report consumed (used) amount for each user budget")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")

    args = parser.parse_args()

    # Resolve credentials: token always from args/env/settings; enterprise may
    # also come from settings.ini when neither --org nor --enterprise is given.
    resolved_enterprise, token = resolve_credentials(args.enterprise, args.token)
    enterprise = args.enterprise
    org = args.org
    if not org and not enterprise:
        enterprise = resolved_enterprise

    if not org and not enterprise:
        print("Error: Target is required. Provide --org or --enterprise, set GITHUB_ENTERPRISE, or configure settings.ini.")
        sys.exit(1)
    if not token:
        print("Error: Token is required. Provide --token, set GITHUB_TOKEN, or configure settings.ini.")
        sys.exit(1)

    if args.list:
        list_user_budgets(
            token=token,
            org=org,
            enterprise=enterprise,
        )
        return

    if args.usage:
        report_budget_usage(
            token=token,
            org=org,
            enterprise=enterprise,
        )
        return

    configs = load_config(args.config)
    if not configs:
        print("No valid configurations found in config file.")
        sys.exit(1)

    print(f"Loaded {len(configs)} user budget configuration(s):")
    for c in configs:
        print(f"  {c.username}: ${c.amount}")

    batch_set_budgets(
        token=token,
        configs=configs,
        org=org,
        enterprise=enterprise,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
