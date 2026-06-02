# auto-token

Automates monitoring and submission of the **Summer 2025-26 Final Registration Token Generator (FST)** Google Form.

## Files

| File | Purpose |
|------|---------|
| `submit_form.py` | Submit the form (single, batch, or wait-then-submit) |
| `form_monitor.sh` | One-shot monitor: checks if the form is open, fires a Discord webhook alert |

---

## submit_form.py

Submits the token form automatically. Zero external dependencies (stdlib only).

### Usage

```bash
# Single submission
./submit_form.py --id 24-22322-1 --email you@example.com

# Wait until form is open, then submit immediately
./submit_form.py --id 24-22322-1 --email you@example.com --wait

# Batch submit from CSV (format: id,email — header row optional)
./submit_form.py --batch students.csv

# Dry run — validates without actually posting
./submit_form.py --id 24-22322-1 --email you@example.com --dry-run
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--id` | — | Student ID |
| `--email` | — | Email address |
| `--batch FILE` | — | CSV/TSV file of `id,email` rows |
| `--wait` | off | Poll until the form is open, then submit |
| `--interval N` | 5 | Poll interval in seconds (used with `--wait`) |
| `--dry-run` | off | Print the request without POSTing |

Exit code `0` = all submissions recorded, `1` = at least one failure.

---

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

- **Closed:** `GET /viewform` redirects to `/closedform`
- **Open:** final URL is not `/closedform` and page contains `FB_PUBLIC_LOAD_DATA_`

Submission success is detected when the `formResponse` endpoint returns an empty questions array (`data[1] == []` in the embedded JSON) — this is the server-side confirmation signature Google Forms uses.
