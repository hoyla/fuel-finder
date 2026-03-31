#!/usr/bin/env python3
"""Bulk station lookup via the Fuel Finder API.

Usage:
    python scripts/lookup_stations.py <api_key> <node_ids_file> [output_file]

Arguments:
    api_key         Your X-Api-Key value
    node_ids_file   Text file with one node ID per line
    output_file     Output JSON path (default: stations_output.json)
"""

import json
import sys
import urllib.request

API_BASE = "https://staging-fuel.hoy.la"
BATCH_SIZE = 200  # stay within readonly tier limit


def load_node_ids(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def lookup_batch(api_key, node_ids):
    url = f"{API_BASE}/api/stations/lookup"
    payload = json.dumps({"node_ids": node_ids}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        sys.exit(1)

    api_key = sys.argv[1]
    ids_file = sys.argv[2]
    out_file = sys.argv[3] if len(sys.argv) > 3 else "stations_output.json"

    node_ids = load_node_ids(ids_file)
    if not node_ids:
        print("No node IDs found in", ids_file)
        sys.exit(1)

    print(f"Looking up {len(node_ids)} stations in batches of {BATCH_SIZE}...")

    all_results = []
    all_missing = []

    for i in range(0, len(node_ids), BATCH_SIZE):
        batch = node_ids[i : i + BATCH_SIZE]
        print(f"  Batch {i // BATCH_SIZE + 1}: {len(batch)} IDs...", end=" ", flush=True)
        resp = lookup_batch(api_key, batch)
        all_results.extend(resp["results"])
        all_missing.extend(resp.get("missing", []))
        print(f"found {resp['found']}, missing {len(resp.get('missing', []))}")

    output = {
        "total_requested": len(node_ids),
        "total_found": len(node_ids) - len(all_missing),
        "missing": all_missing,
        "stations": all_results,
    }

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nDone. {output['total_found']}/{output['total_requested']} found. Written to {out_file}")
    if all_missing:
        print(f"Missing IDs: {', '.join(all_missing[:10])}{'...' if len(all_missing) > 10 else ''}")


if __name__ == "__main__":
    main()
