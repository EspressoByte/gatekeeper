#!/usr/bin/env python3
"""
export_devices_csv.py

Reads data/ise_raw.json and exports a timestamped CSV file containing
one row per network device with:
  - Device Name
  - IP Address (primary / first)
  - Location
  - Device Type

Output: data/devices_YYYYMMDD_HHMMSS.csv

Run ise_fetch.py first to refresh ise_raw.json before running this.
"""

import csv
import json
import os
from datetime import datetime

RAW_FILE   = "/opt/secret/data/ise_raw.json"
OUTPUT_DIR = "/opt/secret/data"
DELETE_RAW_FILE = True  # Set False to retain ise_raw.json for troubleshooting


def parse_ndg(group_list, prefix, full_path=False):
    """
    Extract value from the NDG entry matching a given prefix.
    full_path=False  -> last segment only  e.g. "Atlanta"
    full_path=True   -> everything after the prefix, separated by ' > '
                        e.g. "All Locations > Georgia > Atlanta"
    """
    for entry in group_list:
        if entry.startswith(prefix):
            parts = [p for p in entry[len(prefix):].split("#") if p != "All Locations"]
            return " > ".join(parts) if full_path else parts[-1]
    return ""


def main():
    # Load raw ISE data
    if not os.path.exists(RAW_FILE):
        print(f"[ERROR] {RAW_FILE} not found. Run ise_fetch.py first.")
        return

    with open(RAW_FILE) as f:
        raw = json.load(f)

    devices = raw.get("devices", [])
    if not devices:
        print("[ERROR] No devices found in ise_raw.json.")
        return

    # Build output filename with timestamp
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(OUTPUT_DIR, f"data_{timestamp}.csv")

    # Write CSV
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["device name", "ip address", "location", "device type"])

        for device in sorted(devices, key=lambda d: d.get("name", "")):
            name = device.get("name", "").lower()

            ip_list   = device.get("NetworkDeviceIPList", [])
            ip        = ip_list[0].get("ipaddress", "") if ip_list else ""

            groups    = device.get("NetworkDeviceGroupList", [])
            location  = parse_ndg(groups, "Location#", full_path=True).lower()
            dev_type  = parse_ndg(groups, "Device Type#").lower()

            writer.writerow([name, ip, location, dev_type])

    print(f"Exported {len(devices)} devices → {os.path.basename(output_file)}")

    if DELETE_RAW_FILE:
        os.remove(RAW_FILE)
        print(f"Deleted {RAW_FILE}")


if __name__ == "__main__":
    main()
