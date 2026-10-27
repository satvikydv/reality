#!/usr/bin/env bash
# Forward the phone's localhost:8000 to this machine's server over USB (adb reverse).
# Usage: scripts/usb_connect.sh [adb-serial]   (or set ANDROID_SERIAL)
set -euo pipefail

PORT=8000
SERIAL="${1:-${ANDROID_SERIAL:-}}"

fail() { echo "error: $*" >&2; exit 1; }

command -v adb >/dev/null 2>&1 || fail "adb not found on PATH. Install Android platform-tools and add the folder to PATH (see README: Phone over USB)."

lines="$(adb devices | tail -n +2 | tr -d '\r' | sed '/^[[:space:]]*$/d')"
mapfile -t ready < <(printf '%s\n' "$lines" | awk '$2=="device"{print $1}')

if [ "${#ready[@]}" -eq 0 ]; then
  if printf '%s\n' "$lines" | grep -q 'unauthorized'; then
    fail "Phone is connected but not authorized. Unlock it and accept the 'Allow USB debugging?' prompt, then re-run."
  elif printf '%s\n' "$lines" | grep -q 'offline'; then
    fail "Phone shows as offline. Unplug/replug the cable, or run 'adb kill-server', then re-run."
  fi
  fail "No device listed by 'adb devices'. Connect the phone by USB, enable USB debugging, and use a data-capable cable."
fi

if [ -z "$SERIAL" ]; then
  [ "${#ready[@]}" -eq 1 ] || fail "Multiple devices authorized (${ready[*]}). Re-run with the serial as an argument."
  SERIAL="${ready[0]}"
else
  printf '%s\n' "${ready[@]}" | grep -qx "$SERIAL" || fail "Device '$SERIAL' is not in the authorized list (${ready[*]})."
fi

adb -s "$SERIAL" reverse "tcp:$PORT" "tcp:$PORT" || fail "adb reverse failed."

echo "Forwarding phone localhost:$PORT -> this machine's localhost:$PORT (device $SERIAL)"
echo "Start the server:  PLAIN_HTTP=1 python backend/run.py"
echo "Open on the phone: http://localhost:$PORT"
