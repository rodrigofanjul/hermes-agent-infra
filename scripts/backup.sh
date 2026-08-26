#!/usr/bin/env bash
# Backs up the hermes-data Docker volume to a restic repository
# (Backblaze B2 in production) and applies the retention policy. Runs
# entirely via the official restic/restic image — no host install of
# restic required.
#
# Required environment (see .env.example for the full reference):
#   RESTIC_REPOSITORY   e.g. b2:your-bucket-name:omniroute-backups
#                        (reuses the same bucket as ai-gateway-infra's
#                        OmniRoute backups — restic distinguishes
#                        snapshots by --host/--tag, see below)
#   RESTIC_PASSWORD     encrypts the repository — losing it means losing the backups
#   B2_ACCOUNT_ID
#   B2_ACCOUNT_KEY
#
# Optional environment:
#   HERMES_DATA_VOLUME   Docker volume to back up (default: hermes-data)
#   RESTIC_CACHE_VOLUME  Docker volume for restic's local cache (default: restic-cache)
#   RESTIC_IMAGE         restic image:tag to run (default: restic/restic:0.18.1)
#
# --host is pinned to "hermes-agent-backup" on every restic call below.
# This is required because each `docker run` gets a random container
# hostname, and restic groups retention (forget --keep-daily/--keep-weekly)
# by host — without a pinned host, forget would silently never prune
# anything across separate cron runs (same reasoning as
# ai-gateway-infra/scripts/backup.sh, which uses "omniroute-backup" for the
# same reason on the same repository).

set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set (e.g. b2:bucket-name:omniroute-backups)}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set}"
: "${B2_ACCOUNT_ID:?B2_ACCOUNT_ID must be set}"
: "${B2_ACCOUNT_KEY:?B2_ACCOUNT_KEY must be set}"

DATA_VOLUME="${HERMES_DATA_VOLUME:-hermes-data}"
CACHE_VOLUME="${RESTIC_CACHE_VOLUME:-restic-cache}"
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.18.1}"

run_restic() {
  docker run --rm \
    -v "${CACHE_VOLUME}:/root/.cache/restic" \
    -e RESTIC_REPOSITORY \
    -e RESTIC_PASSWORD \
    -e B2_ACCOUNT_ID \
    -e B2_ACCOUNT_KEY \
    "${RESTIC_IMAGE}" "$@"
}

echo "==> Backing up volume '${DATA_VOLUME}'"
docker run --rm \
  -v "${DATA_VOLUME}:/data:ro" \
  -v "${CACHE_VOLUME}:/root/.cache/restic" \
  -e RESTIC_REPOSITORY \
  -e RESTIC_PASSWORD \
  -e B2_ACCOUNT_ID \
  -e B2_ACCOUNT_KEY \
  "${RESTIC_IMAGE}" backup /data --tag hermes-data --host hermes-agent-backup

echo "==> Applying retention policy (7 daily, 4 weekly)"
run_restic forget --keep-daily 7 --keep-weekly 4 --prune --tag hermes-data --host hermes-agent-backup

echo "==> Current snapshots"
run_restic snapshots --tag hermes-data --host hermes-agent-backup
