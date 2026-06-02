#!/usr/bin/env python3
"""
auto_submit.py — "be first" auto-submitter for the FST token Google Form.

Strategy (see README): the bottleneck for being first is NOT language speed,
it is detection latency + keeping a human out of the loop. So this script:

  1. Holds a persistent keep-alive HTTPS connection to docs.google.com
     (TLS handshake paid once, not per poll).
  2. Polls /viewform and decides OPEN by CONTENT, never by status code:
     open  <=>  the page contains FB_PUBLIC_LOAD_DATA_  AND  the real answer
     field `entry.1220498033` is present. A "Token generation will start from
     mentioned time" placeholder (which is a real HTTP 200 page) fails this
     test, so we never fire on it.
  3. The instant it detects open, it SUBMITS immediately in-process, verifies
     the response was recorded (empty questions array in the reply), and only
     THEN fires the Discord webhook to tell you it's already done.
  4. Optional --open-at ramps the poll rate up to --fast-interval in the final
     --ramp-window seconds before a known open time, so the submit fires the
     instant the page flips.

Stdlib only.

Examples
--------
  # Wait for open, submit, ping Discord (default 1s poll):
  ./auto_submit.py --id 24-22322-1 --email dwd@adwaa.com

  # Known open time -> ramp to 200ms polling in the last 30s before it:
  ./auto_submit.py --id 24-22322-1 --email dwd@adwaa.com \
      --open-at "2026-06-03 12:00" --fast-interval 0.2 --ramp-window 30

  # Multiple entries from CSV (id,email per row):
  ./auto_submit.py --batch students.csv

  # Just watch + report timing, never submit, never ping:
  ./auto_submit.py --id x --email y --dry-run
"""

import argparse
import csv
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
from datetime import datetime

# --- Form / endpoint config -------------------------------------------------
FORM_ID = "1FAIpQLSct2MGrn3rtz7ZHusAk1fBRyn-ZbfTZhEmPr0x0BpuBmI8AvQ"
FORM_HOST = "docs.google.com"
VIEW_PATH = f"/forms/d/e/{FORM_ID}/viewform"
SUBMIT_PATH = f"/forms/d/e/{FORM_ID}/formResponse"

ENTRY_ID = "entry.1220498033"     # Student ID (POST param name)
ENTRY_EMAIL = "entry.1729216927"  # Email (POST param name)
FIELD_ID = 1220498033             # numeric id of the Student ID question; our
                                  # OPEN sentinel — it only exists in the live
                                  # form payload, never on the placeholder page.

WEBHOOK_URL = ("https://discord.com/api/webhooks/1493697055695048866/"
               "7Piw9Rdsg2Oy7MtgZr5nlqEUS_N99IuXRwF1Leho0ii3vZYgD0H_Sw4viREyRM-4-Slr")

STATE_DIR = os.path.dirname(os.path.abspath(__file__))
DONE_FLAG = os.path.join(STATE_DIR, ".auto_submit_done")

BASE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


def now_ms():
    return datetime.now().strftime("%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}"


def log(msg):
    print(f"[{now_ms()}] {msg}", flush=True)


class KeepAlive:
    """A single reused HTTPS connection that reconnects on failure."""

    def __init__(self, host, timeout=15):
        self.host = host
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        self.conn = None

    def _ensure(self):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(
                self.host, timeout=self.timeout, context=self.ctx)
            self.conn.connect()  # pay TLS now, so polls are warm

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def request(self, method, path, body=None, headers=None):
        """Return (status, body_text). Retries once on a dropped connection."""
        last_err = None
        for attempt in (1, 2):
            try:
                self._ensure()
                self.conn.request(method, path, body=body, headers=headers or {})
                resp = self.conn.getresponse()
                data = resp.read()  # must drain to reuse the connection
                return resp.status, data.decode("utf-8", "ignore")
            except (http.client.HTTPException, OSError) as e:
                last_err = e
                self.close()  # force fresh connection on retry
        raise last_err


def is_open(body):
    """CONTENT-based open test. Status code is deliberately ignored.

    Open <=> the live form payload exists AND actually contains the Student ID
    question (numeric FIELD_ID). The "Token generation will start from
    mentioned time" placeholder is a real HTTP 200 page but carries no
    FB_PUBLIC_LOAD_DATA_ at all, so it fails here.
    """
    m = re.search(r"FB_PUBLIC_LOAD_DATA_ = (.*?);</script>", body, re.S)
    if not m:
        return False
    try:
        data = json.loads(m.group(1))
        questions = data[1][1] or []
    except (json.JSONDecodeError, IndexError, TypeError):
        # Payload present but unparseable: fall back to numeric-id substring.
        return str(FIELD_ID) in body
    for q in questions:
        fields = q[4] if len(q) > 4 and q[4] else []
        for f in fields:
            if f and f[0] == FIELD_ID:
                return True
    return False


BLOCK_SIGNATURES = (
    "our systems have detected unusual traffic",
    "unusual traffic from your computer network",
    "/sorry/index",
    "recaptcha",
    "captcha-form",
    "automated queries",
)


def is_blocked(status, body):
    """True if Google appears to be rate-limiting / blocking us.

    Covers explicit throttle status codes and the "unusual traffic" / captcha
    interstitial Google serves (often as HTTP 200 or via a /sorry/ redirect).
    """
    if status in (429, 403, 503):
        return True
    low = body[:5000].lower()
    return any(sig in low for sig in BLOCK_SIGNATURES)


def submit_recorded(reply_body):
    """True if the formResponse reply indicates the response was recorded."""
    m = re.search(r"FB_PUBLIC_LOAD_DATA_ = (.*?);</script>", reply_body, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            if len(data) > 1 and isinstance(data[1], list) and len(data[1]) == 0:
                return True
        except json.JSONDecodeError:
            pass
    return bool(re.search(r"response (has been|was) recorded", reply_body, re.I))


def discord(content):
    """Fire-and-report a Discord webhook message. Returns HTTP status or None."""
    try:
        u = urllib.parse.urlparse(WEBHOOK_URL)
        c = http.client.HTTPSConnection(u.netloc, timeout=10)
        c.request("POST", u.path, body=json.dumps({"content": content}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    except Exception as e:
        log(f"Discord error: {e}")
        return None


def parse_open_at(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"Could not parse --open-at {s!r} "
                     "(use e.g. '2026-06-03 12:00')")


def load_entries(args):
    entries = []
    if args.batch:
        with open(args.batch, newline="") as f:
            sample = f.read(2048)
            delim = "\t" if sample.count("\t") > sample.count(",") else ","
            f.seek(0)
            for row in csv.reader(f, delimiter=delim):
                if len(row) < 2 or not row[0].strip():
                    continue
                a, b = row[0].strip(), row[1].strip()
                if a.lower() in ("id", "student id", "studentid"):
                    continue
                entries.append((a, b))
    elif args.id and args.email:
        entries.append((args.id.strip(), args.email.strip()))
    return entries


def do_submissions(conn, entries, dry_run, no_discord):
    submit_headers = dict(BASE_HEADERS)
    submit_headers["Content-Type"] = "application/x-www-form-urlencoded"
    all_ok = True
    for sid, mail in entries:
        payload = urllib.parse.urlencode({
            ENTRY_ID: sid, ENTRY_EMAIL: mail, "fvv": "1", "pageHistory": "0"})
        if dry_run:
            log(f"[dry-run] would submit {sid} <{mail}>")
            continue
        t0 = time.monotonic()
        try:
            _, reply = conn.request("POST", SUBMIT_PATH, body=payload,
                                    headers=submit_headers)
            ok = submit_recorded(reply)
        except Exception as e:
            ok = False
            log(f"submit ERROR {sid}: {e}")
        dt = (time.monotonic() - t0) * 1000
        log(f"[{'OK' if ok else 'FAILED'}] {sid} <{mail}>  ({dt:.0f}ms)")
        all_ok = all_ok and ok
        if not no_discord:
            if ok:
                discord(f"✅ **Submitted** `{sid}` <{mail}> at {now_ms()} "
                        f"(round-trip {dt:.0f}ms). Form token generated.")
            else:
                discord(f"⚠️ **Submission FAILED** for `{sid}` <{mail}> "
                        f"at {now_ms()}. Check manually!")
    return all_ok


def main():
    ap = argparse.ArgumentParser(description="Be-first auto-submitter for the FST form.")
    ap.add_argument("--id")
    ap.add_argument("--email")
    ap.add_argument("--batch", help="CSV/TSV of 'id,email' rows")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="Normal poll interval seconds (default 1.0)")
    ap.add_argument("--fast-interval", type=float, default=0.2,
                    help="Poll interval during the pre-open ramp (default 0.2)")
    ap.add_argument("--open-at", help="Known open time, e.g. '2026-06-03 12:00' (local)")
    ap.add_argument("--ramp-window", type=float, default=30,
                    help="Seconds before --open-at to switch to fast polling (default 30)")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="Stop after N minutes (0 = run until open, default)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect open but never submit and never ping Discord")
    ap.add_argument("--no-discord", action="store_true",
                    help="Submit but do not send Discord alerts")
    ap.add_argument("--force", action="store_true",
                    help="Ignore the .auto_submit_done flag from a prior run")
    ap.add_argument("--block-alert-after", type=int, default=5,
                    help="Discord-warn after N consecutive blocked/failed polls "
                         "(default 5; 0 disables block alerts)")
    args = ap.parse_args()

    entries = load_entries(args)
    if not entries:
        ap.error("provide --id and --email, or --batch FILE")

    if os.path.exists(DONE_FLAG) and not args.force and not args.dry_run:
        log(f"Already submitted (flag {DONE_FLAG} exists). Use --force to re-run.")
        return 0

    open_at = parse_open_at(args.open_at) if args.open_at else None
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None

    conn = KeepAlive(FORM_HOST)
    log(f"Pre-warming connection to {FORM_HOST} ...")
    try:
        conn._ensure()
    except Exception as e:
        log(f"Initial connect failed (will retry in loop): {e}")

    if open_at:
        log(f"Target open time: {open_at} | ramp to {args.fast_interval}s "
            f"in last {args.ramp_window}s | normal {args.interval}s")
    else:
        log(f"Polling every {args.interval}s (content-based open check) ...")

    # Block/error alerting: warn on Discord once we hit N consecutive bad polls
    # (a block episode), and once more when polling recovers. Suppressed in
    # dry-run / --no-discord, and disabled entirely when --block-alert-after 0.
    block_alerts = args.block_alert_after > 0 and not args.dry_run and not args.no_discord
    consec_fail = 0
    block_alerted = False

    polls = 0
    while True:
        if deadline and time.monotonic() > deadline:
            log("Reached --max-minutes without the form opening. Stopping.")
            return 1

        interval = args.interval
        if open_at:
            secs_to_open = (open_at - datetime.now()).total_seconds()
            if secs_to_open <= args.ramp_window:
                interval = args.fast_interval

        bad = None  # reason string if this poll failed/blocked
        try:
            status, body = conn.request("GET", VIEW_PATH, headers=BASE_HEADERS)
            polls += 1
            if is_blocked(status, body):
                bad = f"blocked/throttled (http {status})"
            elif is_open(body):
                log(f"FORM OPEN detected after {polls} polls (http {status}). "
                    "Submitting NOW.")
                ok = do_submissions(conn, entries, args.dry_run, args.no_discord)
                if not args.dry_run and ok:
                    open(DONE_FLAG, "w").close()
                return 0 if ok else 1
            elif polls % 20 == 1:  # avoid log spam
                log(f"still closed (poll #{polls}, http {status})")
        except Exception as e:
            bad = f"poll error: {e}"

        if bad:
            consec_fail += 1
            log(f"{bad} (consecutive #{consec_fail})")
            if block_alerts and not block_alerted and consec_fail >= args.block_alert_after:
                discord(f"🚫 **Monitor may be BLOCKED** — {consec_fail} consecutive "
                        f"bad polls ({bad}) as of {now_ms()}. The watcher is still "
                        "retrying, but you may need to switch IP/VPN. It will NOT "
                        "detect the form opening while blocked.")
                block_alerted = True
        else:
            if block_alerted:
                discord(f"✅ **Monitor recovered** at {now_ms()} — polling normally "
                        "again after a block episode.")
            consec_fail = 0
            block_alerted = False

        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
