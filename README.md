# auto-token

Automates monitoring and submission of the **Summer 2025-26 Final Registration Token Generator (FST)** Google Form.

## Files

| File | Purpose |
|------|---------|
| `auto_submit.py` | **"Be first" auto-submitter** — keep-alive poll, content-based open detection, instant auto-submit, post-submit Discord ping |
| `form_monitor.sh` | One-shot monitor: checks if the form is open, fires a Discord webhook alert |

---

## auto_submit.py (recommended for "be first")

A single long-running process that removes the human from the loop: it watches
the form and **submits automatically the instant it opens**, then pings Discord
to tell you it's already done.

```bash
# Wait for open, submit, ping Discord (default 1s poll):
./auto_submit.py --id 24-22322-1 --email you@example.com

# Known open time -> ramp to 200ms polling in the last 30s before it:
./auto_submit.py --id 24-22322-1 --email you@example.com \
    --open-at "2026-06-03 12:00" --fast-interval 0.2 --ramp-window 30

# Multiple entries:
./auto_submit.py --batch students.csv --interval 0

# Watch + report timing only, never submit, never ping:
./auto_submit.py --id x --email y --dry-run

# Measure round-trip latency to Google (to pick the lowest-latency region/VPN):
./auto_submit.py --latency-test          # 10 warm requests
./auto_submit.py --latency-test 30        # 30 requests
```

| Flag | Default | Description |
|------|---------|-------------|
| `--id` / `--email` | — | Single submission |
| `--batch FILE` | — | CSV/TSV of `id,email` rows |
| `--interval N` | 1.0 | Normal poll interval (seconds) |
| `--fast-interval N` | 0.2 | Poll interval during the pre-open ramp |
| `--open-at "Y-m-d H:M"` | — | Known open time (local); enables the ramp |
| `--ramp-window N` | 30 | Seconds before `--open-at` to start fast polling |
| `--max-minutes N` | 0 | Stop after N minutes (0 = run until open) |
| `--block-alert-after N` | 5 | Discord-warn after N consecutive blocked/failed polls (0 disables) |
| `--no-discord` | off | Submit but don't ping Discord |
| `--dry-run` | off | Detect open but never submit / never ping |
| `--force` | off | Ignore the `.auto_submit_done` flag from a prior run |
| `--latency-test [N]` | — | Measure RTT to Google (N warm requests, default 10), print stats, exit |
| `--latency-get` | off | Use full GET instead of HEAD for the latency test |

### Measuring latency (which region is fastest)

`--latency-test` opens one keep-alive connection to `docs.google.com`, reports
the **connect time** (DNS + TCP + TLS handshake) once, then fires N warm
requests and prints `min / median / avg / p95 / max` in ms:

```
$ ./auto_submit.py --latency-test 20
[..] connect to docs.google.com: 36.8ms (DNS+TCP+TLS handshake)
[..] latency to docs.google.com over 20 warm HEADs: min 41 / median 55 / avg 58 / p95 92 / max 110 ms
```

Run it from each candidate vantage point (home, VPN exit, a cloud VM in `us`,
`eu`, `asia-south`, …) and pick the one with the **lowest median** — that's the
warm RTT every poll/submit pays, so it directly decides who's first. By default
it uses a cheap `HEAD` so the number reflects pure network round-trip; add
`--latency-get` to include the page download too.

**Why this design (and why not Rust):** the bottleneck for being first is
*detection latency* and *keeping a human out of the loop*, not language speed.
Your code spends <1ms per poll; the network round trip is tens of ms. So the
wins are: keep-alive connection (TLS handshake paid once), tight polling, an
optional ramp into a known open time, and the machine submitting — not you.
A cloud VM near Google's frontend (lower RTT) helps far more than any rewrite.

After a successful submit it writes `.auto_submit_done` so a re-run won't
double-submit. Re-run with `--force` to override.

### Block detection & alerting

Google can rate-limit aggressive polling (HTTP 429/403/503, or an "unusual
traffic" / captcha interstitial — sometimes served as HTTP 200). A block is
dangerous because the watcher would see no form payload and silently treat it
as "still closed", potentially **missing the open moment**.

To guard against this, the loop counts consecutive blocked/failed polls and, on
crossing `--block-alert-after`, fires a Discord warning (`🚫 Monitor may be
BLOCKED …`) so you can switch IP/VPN. It alerts **once per block episode** (no
spam) and sends a `✅ Monitor recovered` ping when polling resumes. To reduce
block risk in the first place: keep `--interval >= 0.2`, and use `--open-at` so
you only poll fast in the final window rather than all day.

## form_monitor.sh

Run on a schedule (e.g. via cron) to watch for the form opening and send a Discord webhook alert.

```bash
# Run manually
./form_monitor.sh

# Add to crontab (every minute)
* * * * * /path/to/form_monitor.sh >/dev/null 2>&1
```

- Writes status to `form_monitor.log`
- Once the alert fires it creates `.form_opened_alerted` to prevent duplicates
- To re-arm: `rm .form_opened_alerted`

### Configuration

Edit `form_monitor.sh` and set `WEBHOOK_URL` to your Discord webhook.

---

## Detection logic

**Do not rely on the HTTP status code.** When the form is not yet open, Google
serves a real HTTP 200 placeholder page ("Token generation will start from
mentioned time") — a status check would falsely report "open".

The reliable, content-based signal:

- **Open** ⇔ the page contains `FB_PUBLIC_LOAD_DATA_` *and* its questions array
  actually includes the Student ID field (numeric id `1220498033`). Note the
  literal string `entry.1220498033` is *not* in the server HTML — only the
  numeric id appears in the JSON payload; the `entry.` prefix is added by JS.
- **Closed / placeholder** ⇔ no `FB_PUBLIC_LOAD_DATA_` payload at all.

Submission success is detected when the `formResponse` endpoint returns an empty
questions array (`data[1] == []` in the embedded JSON) — the server-side
confirmation signature Google Forms uses.
