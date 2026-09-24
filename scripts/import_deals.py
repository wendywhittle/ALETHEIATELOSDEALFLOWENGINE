#!/usr/bin/env python3
"""Import deals from a JSON file through the app's real submission path.

Usage:
    python3 scripts/import_deals.py --file deals.json

The file must contain a JSON array of deal dicts. Accepted keys:

    name, asset_type, location, purchase_price, noi, egi, operating_expenses,
    scheduled_rent_annual, vacancy_loss_annual, other_income_annual,
    occupancy, ltv, interest_rate, amortization_years, hold_period,
    exit_cap_rate, closing_costs, source,
    contact_name, contact_email, contact_phone, source_url, notes

Each deal goes through the exact same path as a web submission:
validation -> canonical record -> deterministic underwriting -> stored.
Invalid deals are reported and skipped; the exit code is 1 if any failed.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import records  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Import deals from JSON.")
    parser.add_argument("--file", required=True, help="Path to JSON array file.")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as fh:
        deals = json.load(fh)

    if not isinstance(deals, list):
        print("ERROR: top-level JSON value must be an array of deal dicts.",
              file=sys.stderr)
        return 2

    failed = 0
    for i, raw in enumerate(deals):
        label = (raw.get("name") or "deal #%d" % (i + 1)) if isinstance(raw, dict) else "deal #%d" % (i + 1)
        try:
            deal_id = records.submit_deal(raw)
        except Exception as exc:  # validation or persistence failure
            failed += 1
            print("FAIL %-40s %s" % (label, exc))
        else:
            print("OK   %-40s %s" % (deal_id, label))

    print("Imported %d of %d deals." % (len(deals) - failed, len(deals)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
