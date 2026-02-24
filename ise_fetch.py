#!/usr/bin/env python3
"""
ise_fetch.py

Connects to the Cisco ISE ERS (External RESTful Services) API, retrieves
every network device and its full detail record, then writes the raw data
to /opt/secret/data/ise_raw.json for further processing.

Credentials are loaded from /opt/secret/config.env (gitignored). Copy
docs/config.env.example to /opt/secret/config.env and fill in your values
before running.
"""

import json
import os
import sys
import time
import requests
import urllib3


def _load_env(path="/opt/secret/config.env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
ISE_HOST    = os.environ.get("ISE_HOST",    "192.168.0.100")
ISE_PORT    = int(os.environ.get("ISE_PORT", "9060"))
USERNAME    = os.environ.get("ISE_USERNAME", "")
PASSWORD    = os.environ.get("ISE_PASSWORD", "")
VERIFY_SSL  = os.environ.get("VERIFY_SSL",  "false").lower() == "true"
OUTPUT_FILE = "/opt/secret/data/ise_raw.json"
PAGE_SIZE   = 100
RETRY_DELAY = 2
MAX_RETRIES = 3
# ───────────────────────────────────────────────────────────────────────────────

if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = f"https://{ISE_HOST}:{ISE_PORT}/ers/config"

SESSION = requests.Session()
SESSION.auth    = (USERNAME, PASSWORD)
SESSION.verify  = VERIFY_SSL
SESSION.headers.update({
    "Accept":       "application/json",
    "Content-Type": "application/json",
})


# ── HELPERS ────────────────────────────────────────────────────────────────────

def get_with_retry(url):
    """GET a URL with retries on transient errors. Returns a Response object."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            print(f"    [WARN] Timeout on attempt {attempt}/{MAX_RETRIES}: {url}")
        except requests.exceptions.HTTPError as e:
            if resp.status_code in (429, 503):
                print(f"    [WARN] Rate limited / unavailable, retrying ({attempt}/{MAX_RETRIES})...")
            elif resp.status_code == 404:
                return resp          # caller handles 404
            else:
                raise
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * attempt)
    raise requests.exceptions.RetryError(f"Failed after {MAX_RETRIES} attempts: {url}")


def get_all_device_refs():
    """
    Page through GET /networkdevice and return a list of
    {id, name, description, link} dicts for every device in ISE.
    """
    refs  = []
    page  = 1
    total = None

    while True:
        url  = f"{BASE_URL}/networkdevice?size={PAGE_SIZE}&page={page}"
        resp = get_with_retry(url)
        data = resp.json()["SearchResult"]

        if total is None:
            total = data["total"]

        resources = data.get("resources", [])
        refs.extend(resources)

        if "nextPage" not in data or not resources:
            break
        page += 1

    return refs


def get_device_detail(device_id, device_name):
    """
    Fetch the full NetworkDevice record for a single device.
    Returns the dict or None if the device was not found.
    """
    url  = f"{BASE_URL}/networkdevice/{device_id}"
    resp = get_with_retry(url)

    if resp.status_code == 404:
        print(f"    [WARN] {device_name} ({device_id}) returned 404 — skipping.")
        return None

    return resp.json().get("NetworkDevice", {})


# ── PROGRESS BAR ───────────────────────────────────────────────────────────────

def print_progress(current, total, bar_width=40):
    """Print an in-place progress bar to stdout."""
    pct    = current / total
    filled = int(bar_width * pct)
    bar    = "█" * filled + "░" * (bar_width - filled)
    sys.stdout.write(f"\r  [{bar}] {pct:>4.0%}  ({current}/{total})")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")
        sys.stdout.flush()


# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    print(f"Connecting to Cisco ISE at https://{ISE_HOST}:{ISE_PORT}")

    try:
        device_refs = get_all_device_refs()
    except requests.exceptions.ConnectionError as e:
        print(f"\n[ERROR] Cannot reach ISE — check ISE_HOST and ISE_PORT.\n  {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] ISE returned an HTTP error.\n  {e}")
        sys.exit(1)

    # ── Step 2: fetch full detail for every device ─────────────────
    print("Fetching full device details...")
    devices  = []
    skipped  = []

    total = len(device_refs)
    for i, ref in enumerate(device_refs, 1):
        name = ref.get("name", ref["id"])
        print_progress(i, total)

        try:
            detail = get_device_detail(ref["id"], name)
        except Exception as e:
            skipped.append({"id": ref["id"], "name": name, "reason": str(e)})
            continue

        if detail:
            devices.append(detail)
        else:
            skipped.append({"id": ref["id"], "name": name, "reason": "404 not found"})

    print(f"  Fetched:  {len(devices)}")
    print(f"  Skipped:  {len(skipped)}")

    output = {
        "source":         "Cisco ISE ERS API",
        "ise_host":       ISE_HOST,
        "total_fetched":  len(devices),
        "total_skipped":  len(skipped),
        "skipped":        skipped,
        "devices":        devices,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    if skipped:
        print(f"\nSkipped devices:")
        for s in skipped:
            print(f"  - {s['name']}: {s['reason']}")


if __name__ == "__main__":
    main()
