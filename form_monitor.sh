#!/usr/bin/env bash
# Monitors a Google Form that is currently CLOSED and fires a Discord webhook
# alert the moment it opens (starts accepting responses).
#
# Detection logic:
#   - When closed: /viewform redirects to /closedform
#   - When open:   /viewform serves the form (no redirect to closedform, and
#                  the page contains the FB_PUBLIC_LOAD_DATA_ form payload)
#
# Designed to be run repeatedly (e.g. from cron). It is idempotent: once the
# form is detected open and the alert is sent, a flag file prevents re-alerting.

set -u

VIEWFORM_URL="https://docs.google.com/forms/d/e/1FAIpQLSct2MGrn3rtz7ZHusAk1fBRyn-ZbfTZhEmPr0x0BpuBmI8AvQ/viewform"
OPEN_URL="$VIEWFORM_URL"
WEBHOOK_URL="https://discord.com/api/webhooks/1493697055695048866/7Piw9Rdsg2Oy7MtgZr5nlqEUS_N99IuXRwF1Leho0ii3vZYgD0H_Sw4viREyRM-4-Slr"

STATE_DIR="$HOME/auto-token"
ALERTED_FLAG="$STATE_DIR/.form_opened_alerted"
LOG_FILE="$STATE_DIR/form_monitor.log"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >>"$LOG_FILE"; }

# If we've already alerted, do nothing further.
if [[ -f "$ALERTED_FLAG" ]]; then
  exit 0
fi

# Follow redirects; capture both the final effective URL and the body.
TMP_BODY="$(mktemp)"
trap 'rm -f "$TMP_BODY"' EXIT

FINAL_URL="$(curl -sL --max-time 30 \
  -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" \
  -o "$TMP_BODY" -w '%{url_effective}' "$VIEWFORM_URL")"
CURL_RC=$?

if [[ $CURL_RC -ne 0 ]]; then
  log "curl failed (rc=$CURL_RC); will retry next run"
  exit 0
fi

# Form is OPEN if we did NOT land on the closedform page AND the body looks like
# a real form payload.
if [[ "$FINAL_URL" != *"/closedform"* ]] && grep -q "FB_PUBLIC_LOAD_DATA_" "$TMP_BODY"; then
  log "FORM OPEN detected (final_url=$FINAL_URL). Sending Discord alert."

  PAYLOAD=$(cat <<JSON
{
  "content": "🚨 **FORM IS NOW OPEN!** 🚨",
  "embeds": [
    {
      "title": "Google Form is now accepting responses",
      "description": "The monitored form has opened. Submit now!",
      "url": "$OPEN_URL",
      "color": 3066993,
      "fields": [
        { "name": "Form link", "value": "$OPEN_URL" }
      ]
    }
  ]
}
JSON
)

  HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Content-Type: application/json" \
    -X POST -d "$PAYLOAD" "$WEBHOOK_URL")"

  if [[ "$HTTP_CODE" =~ ^2 ]]; then
    log "Discord alert sent OK (http=$HTTP_CODE). Setting alerted flag."
    touch "$ALERTED_FLAG"
  else
    log "Discord alert FAILED (http=$HTTP_CODE). Will retry next run."
  fi
else
  log "Still closed (final_url=$FINAL_URL)."
fi

exit 0
