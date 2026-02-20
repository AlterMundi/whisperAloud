#!/usr/bin/env bash
set -euo pipefail

run_case() {
  local name="$1"
  shift
  local out
  out="$(./install.sh --dry-run "$@" 2>&1)"
  echo "[$name] ok"
  printf '%s\n' "$out"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  if ! grep -Fq "$needle" <<<"$haystack"; then
    echo "Expected output to contain: $needle" >&2
    exit 1
  fi
}

default_out="$(run_case default)"
assert_contains "$default_out" "Install system deps: yes"
assert_contains "$default_out" "Install user service: yes"
assert_contains "$default_out" "systemctl --user start whisper-aloud"

skip_system_out="$(run_case skip-system --skip-system)"
assert_contains "$skip_system_out" "Install system deps: no (--skip-system)"

skip_service_out="$(run_case skip-user-service --skip-user-service)"
assert_contains "$skip_service_out" "Install user service: no (--skip-user-service)"
assert_contains "$skip_service_out" "manual daemon start"

combined_out="$(run_case combined --skip-system --skip-user-service --no-cuda)"
assert_contains "$combined_out" "Install system deps: no (--skip-system)"
assert_contains "$combined_out" "Install user service: no (--skip-user-service)"
assert_contains "$combined_out" "CUDA mode: disabled"

echo "Install matrix validation passed"
