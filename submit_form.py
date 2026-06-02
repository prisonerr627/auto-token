#!/usr/bin/env python3
"""
Automate submission of the "Summer 2025-26 :: Final Registration Token
Generator (FST)" Google Form.

The form has two required short-text questions:
    Student ID  -> entry.1220498033
    Email       -> entry.1729216927

Usage
-----
Single submission:
    ./submit_form.py --id 24-22322-1 --email dwd@adwaa.com

Wait until the form is OPEN, then submit (polls every --interval seconds):
    ./submit_form.py --id 24-22322-1 --email dwd@adwaa.com --wait

Batch submit from a CSV/TSV file with rows  "id,email"  (header optional):
    ./submit_form.py --batch students.csv

Dry run (build + validate the request but do not POST):
    ./submit_form.py --id 24-22322-1 --email dwd@adwaa.com --dry-run

Exit codes: 0 = all submissions recorded, 1 = at least one failure/error.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request

# --- Form configuration -----------------------------------------------------
FORM_ID = "1FAIpQLSct2MGrn3rtz7ZHusAk1fBRyn-ZbfTZhEmPr0x0BpuBmI8AvQ"
BASE = f"https://docs.google.com/forms/d/e/{FORM_ID}"
VIEW_URL = f"{BASE}/viewform"
SUBMIT_URL = f"{BASE}/formResponse"

ENTRY_ID = "entry.1220498033"     # Student ID
ENTRY_EMAIL = "entry.1729216927"  # Email

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _fetch(url, data=None, timeout=30):
    """Return (final_url, body_text). `data` (dict) triggers a POST."""
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method="POST")
    else:
        req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl(), resp.read().decode("utf-8", "ignore")


def _load_data(html):
    """Extract and parse the FB_PUBLIC_LOAD_DATA_ JSON, or None."""
    m = re.search(r"FB_PUBLIC_LOAD_DATA_ = (.*?);</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def is_open():
    """True if the form is currently accepting responses."""
    final_url, html = _fetch(VIEW_URL)
    return "/closedform" not in final_url and "FB_PUBLIC_LOAD_DATA_" in html


def wait_until_open(interval=5, max_wait=None):
    waited = 0
    while True:
        try:
            if is_open():
                return True
        except Exception as e:  # network hiccup -> keep trying
            print(f"  (check error: {e})", file=sys.stderr)
        if max_wait is not None and waited >= max_wait:
            return False
        time.sleep(interval)
        waited += interval


def submit(student_id, email, dry_run=False):
    """Submit one response. Return True if recorded successfully."""
    payload = {
        ENTRY_ID: student_id,
        ENTRY_EMAIL: email,
        "fvv": "1",
        "pageHistory": "0",
    }
    if dry_run:
        print(f"[dry-run] would POST {payload} -> {SUBMIT_URL}")
        return True

    final_url, html = _fetch(SUBMIT_URL, data=payload)
    data = _load_data(html)

    # Success signature: the formResponse page renders with an EMPTY questions
    # array (data[1] == []). A validation failure re-renders the form WITH the
    # questions populated (data[1] non-empty).
    if data is not None and len(data) > 1 and isinstance(data[1], list) \
            and len(data[1]) == 0:
        return True
    # Fallback: explicit confirmation text (older form layouts).
    if re.search(r"response (has been|was) recorded", html, re.I):
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Auto-submit the FST token form.")
    ap.add_argument("--id", help="Student ID")
    ap.add_argument("--email", help="Email address")
    ap.add_argument("--batch", help="CSV/TSV file of 'id,email' rows")
    ap.add_argument("--wait", action="store_true",
                    help="Wait until the form is open before submitting")
    ap.add_argument("--interval", type=int, default=5,
                    help="Poll interval seconds when --wait (default 5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate without actually submitting")
    args = ap.parse_args()

    # Build the list of (id, email) to submit.
    entries = []
    if args.batch:
        with open(args.batch, newline="") as f:
            for row in csv.reader(f, delimiter="\t" if "\t" in f.readline() else ","):
                f.seek(0)  # only used the readline to sniff delimiter once
                break
        with open(args.batch, newline="") as f:
            sample = f.read(2048)
            delim = "\t" if sample.count("\t") > sample.count(",") else ","
            f.seek(0)
            for row in csv.reader(f, delimiter=delim):
                if len(row) < 2 or not row[0].strip():
                    continue
                a, b = row[0].strip(), row[1].strip()
                if a.lower() in ("id", "student id", "studentid"):
                    continue  # header
                entries.append((a, b))
    elif args.id and args.email:
        entries.append((args.id.strip(), args.email.strip()))
    else:
        ap.error("provide --id and --email, or --batch FILE")

    if args.wait:
        print("Waiting for the form to open...")
        if not wait_until_open(interval=args.interval):
            print("Form did not open in time.", file=sys.stderr)
            return 1
        print("Form is OPEN. Submitting.")

    all_ok = True
    for sid, mail in entries:
        try:
            ok = submit(sid, mail, dry_run=args.dry_run)
        except Exception as e:
            ok = False
            print(f"  ERROR {sid} <{mail}>: {e}", file=sys.stderr)
        status = "OK" if ok else "FAILED"
        print(f"[{status}] {sid}  <{mail}>")
        all_ok = all_ok and ok

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
