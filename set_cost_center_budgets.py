#!/usr/bin/env python3
"""
Set Cost-Center-level User Budgets via GitHub API.

Creates a per-user AI Credits budget that applies to every user in a cost
center (budget_scope = "multi_user_cost_center"). The budget is identified by
cost center *name*; the script resolves the name to its cost center ID for
creation, and matches existing budgets (which the API reports with
budget_scope = "cost_center" and budget_entity_name = the cost center name)
to decide whether to create or update.

Enterprise name and token are read from settings.ini / environment variables.
See settings.py for resolution order.

Usage:
    # From a CSV config (cost_center_name,amount)
    python set_cost_center_budgets.py --config cost_center_budgets.csv [--dry-run]

    # One-off via command line
    python set_cost_center_budgets.py --name "IT" --amount 50 [--dry-run]

    # List existing cost-center budgets
    python set_cost_center_budgets.py --list

CSV format (cost_center_budgets.csv):
    # Cost Center Name, Monthly per-user Budget (USD)
    IT,50
    Marketing,100

API Reference:
    GET    /enterprises/{enterprise}/settings/billing/cost-centers
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

# Scope used when creating a cost-center user-level budget.
CREATE_SCOPE = "multi_user_cost_center"
# Scopes the API may report for the same budget when listing.
LIST_SCOPES = {"multi_user_cost_center", "cost_center"}


@dataclass
class CostCenterBudget:
    name: str
    amount: int


def load_config(config_path: str) -> list[CostCenterBudget]:
    """Load cost-center budget configurations from a CSV file."""
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
            name = row[0].strip()
            try:
                amount = int(float(row[1].strip()))
            except ValueError:
                print(f"Warning: Invalid amount on line {line_num}: '{row[1]}', skipping.")
                continue
            if amount <= 0:
                print(f"Warning: Amount must be positive on line {line_num}, skipping.")
                continue
            configs.append(CostCenterBudget(name=name, amount=amount))

    return configs


def get_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }


def list_cost_centers(token: str, enterprise: str) -> list[dict]:
    """List all cost centers with pagination support."""
    base_url = f"{API_BASE}/enterprises/{enterprise}/settings/billing/cost-centers"
    headers = get_headers(token)
    all_cost_centers = []
    page = 1

    while True:
        resp = requests.get(base_url, headers=headers, params={"page": page, "per_page": 100})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                batch = data
            else:
                batch = data.get("costCenters") or data.get("cost_centers") or []
            all_cost_centers.extend(batch)
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
    """Find an active cost center by its name (case-insensitive)."""
    target = name.strip().lower()
    for cc in cost_centers:
        cc_name = (cc.get("name") or "").strip().lower()
        if cc_name == target:
            return cc
    return None


def list_existing_budgets(token: str, enterprise: str) -> list[dict]:
    """List all existing budgets with pagination support."""
    base_url = f"{API_BASE}/enterprises/{enterprise}/settings/billing/budgets"
    headers = get_headers(token)
    all_budgets = []
    page = 1

    while True:
        resp = requests.get(base_url, headers=headers, params={"page": page, "per_page": 10})
        if resp.status_code == 200:
            data = resp.json()
            all_budgets.extend(data.get("budgets", []))
            if not data.get("has_next_page", False):
                break
            page += 1
        elif resp.status_code == 404:
            print(f"Note: Budget endpoint returned 404. '{enterprise}' may not have billing budgets enabled.")
            return []
        else:
            print(f"Error listing budgets: {resp.status_code} - {resp.text}")
            return []

    return all_budgets


def find_cost_center_budget(budgets: list[dict], name: str) -> dict | None:
    """Find an existing cost-center budget by cost center name."""
    target = name.strip().lower()
    for budget in budgets:
        if (
            budget.get("budget_scope") in LIST_SCOPES
            and (budget.get("budget_entity_name") or "").strip().lower() == target
        ):
            return budget
    return None


def create_cost_center_budget(
    token: str,
    enterprise: str,
    cost_center_id: str,
    amount: int,
) -> dict:
    """Create a cost-center user-level budget."""
    url = f"{API_BASE}/enterprises/{enterprise}/settings/billing/budgets"
    headers = get_headers(token)
    payload = {
        "budget_amount": amount,
        "prevent_further_usage": True,
        "budget_scope": CREATE_SCOPE,
        "budget_entity_name": cost_center_id,
        "budget_type": "BundlePricing",
        "budget_product_sku": "ai_credits",
        "budget_alerting": {
            "will_alert": False,
            "alert_recipients": [],
        },
    }
    resp = requests.post(url, headers=headers, json=payload)
    return {"status": resp.status_code, "body": resp.json() if resp.content else {}}


def update_cost_center_budget(
    token: str,
    enterprise: str,
    budget_id: str,
    amount: int,
) -> dict:
    """Update the amount of an existing cost-center budget."""
    url = f"{API_BASE}/enterprises/{enterprise}/settings/billing/budgets/{budget_id}"
    headers = get_headers(token)
    payload = {
        "budget_amount": amount,
        "prevent_further_usage": True,
    }
    resp = requests.patch(url, headers=headers, json=payload)
    return {"status": resp.status_code, "body": resp.json() if resp.content else {}}


def list_cost_center_budgets(token: str, enterprise: str):
    """List all existing cost-center budgets and display them."""
    print(f"\n{'='*64}")
    print(f"  Cost-Center User-Level Budgets")
    print(f"  Enterprise: {enterprise}")
    print(f"{'='*64}\n")

    print("Fetching budgets...")
    budgets = list_existing_budgets(token, enterprise)
    cc_budgets = [b for b in budgets if b.get("budget_scope") in LIST_SCOPES]

    if not cc_budgets:
        print("No cost-center budgets found.")
        return

    print(f"Found {len(cc_budgets)} cost-center budget(s):\n")
    print(f"  {'Cost Center':<30} {'Amount':>10} {'Budget ID'}")
    print(f"  {'-'*30} {'-'*10} {'-'*36}")

    for budget in sorted(cc_budgets, key=lambda b: b.get("budget_entity_name", "")):
        name = budget.get("budget_entity_name", "N/A")
        amount = budget.get("budget_amount", 0)
        budget_id = budget.get("id", "N/A")
        print(f"  {name:<30} ${amount:>9} {budget_id}")


def batch_set_cost_center_budgets(
    token: str,
    enterprise: str,
    configs: list[CostCenterBudget],
    dry_run: bool = False,
):
    """Batch create/update cost-center user-level budgets."""
    print(f"\n{'='*64}")
    print(f"  Batch Cost-Center User-Level Budget Configuration")
    print(f"  Enterprise: {enterprise}")
    print(f"  Cost centers to configure: {len(configs)}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*64}\n")

    print("Fetching cost centers...")
    cost_centers = list_cost_centers(token, enterprise)
    print(f"Found {len(cost_centers)} cost center(s).")

    print("Fetching existing budgets...")
    existing_budgets = list_existing_budgets(token, enterprise)
    print(f"Found {len(existing_budgets)} existing budget(s).\n")

    results = {"created": [], "updated": [], "skipped": [], "not_found": [], "failed": []}

    for i, config in enumerate(configs, 1):
        print(f"[{i}/{len(configs)}] Processing cost center: '{config.name}' -> ${config.amount}/user/month")

        cc = find_cost_center_by_name(cost_centers, config.name)
        if cc is None:
            print(f"  ✗ Cost center '{config.name}' not found.")
            results["not_found"].append(config.name)
            continue

        cc_id = cc.get("id")
        existing = find_cost_center_budget(existing_budgets, config.name)

        if existing:
            current_amount = existing.get("budget_amount", 0)
            if current_amount == config.amount:
                print(f"  ✓ Already set to ${config.amount}, skipping.")
                results["skipped"].append(config.name)
                continue

            budget_id = existing.get("id")
            print(f"  → Updating budget (id={budget_id}): ${current_amount} -> ${config.amount}")

            if dry_run:
                print(f"  [DRY RUN] Would update budget for '{config.name}'")
                results["updated"].append(config.name)
            else:
                resp = update_cost_center_budget(token, enterprise, budget_id, config.amount)
                if resp["status"] in (200, 204):
                    print(f"  ✓ Updated successfully.")
                    results["updated"].append(config.name)
                else:
                    print(f"  ✗ Failed: {resp['status']} - {resp['body']}")
                    results["failed"].append((config.name, resp))
        else:
            print(f"  → Creating new budget: ${config.amount} (cost_center_id={cc_id})")

            if dry_run:
                print(f"  [DRY RUN] Would create budget for '{config.name}'")
                results["created"].append(config.name)
            else:
                resp = create_cost_center_budget(token, enterprise, cc_id, config.amount)
                if resp["status"] in (200, 201):
                    print(f"  ✓ Created successfully.")
                    results["created"].append(config.name)
                else:
                    print(f"  ✗ Failed: {resp['status']} - {resp['body']}")
                    results["failed"].append((config.name, resp))

        # Rate limiting: respect GitHub API limits
        if not dry_run and i < len(configs):
            time.sleep(1)

    # Summary
    print(f"\n{'='*64}")
    print(f"  Summary")
    print(f"{'='*64}")
    print(f"  Created:   {len(results['created'])} - {results['created']}")
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
        description="Set Cost-Center-level User Budgets (per-user AI Credits) via GitHub API."
    )

    parser.add_argument("--enterprise", help="GitHub Enterprise name (falls back to settings.ini / GITHUB_ENTERPRISE)")
    parser.add_argument("--token", help="GitHub Personal Access Token (falls back to settings.ini / GITHUB_TOKEN)")
    parser.add_argument("--config", help="Path to CSV config file (cost_center_name,amount)")
    parser.add_argument("--name", help="Cost center name for a one-off budget")
    parser.add_argument("--amount", type=int, help="Monthly per-user budget amount (USD) for --name")
    parser.add_argument("--list", action="store_true", help="List all existing cost-center budgets")
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
        list_cost_center_budgets(token=token, enterprise=enterprise)
        return

    configs: list[CostCenterBudget] = []
    if args.config:
        configs.extend(load_config(args.config))
    if args.name:
        if args.amount is None or args.amount <= 0:
            print("Error: --name requires a positive --amount.")
            sys.exit(1)
        configs.append(CostCenterBudget(name=args.name.strip(), amount=args.amount))

    if not configs:
        print("No configurations provided. Use --config or --name + --amount.")
        sys.exit(1)

    print(f"Loaded {len(configs)} cost-center budget configuration(s):")
    for c in configs:
        print(f"  {c.name}: ${c.amount}/user/month")

    batch_set_cost_center_budgets(
        token=token,
        enterprise=enterprise,
        configs=configs,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
