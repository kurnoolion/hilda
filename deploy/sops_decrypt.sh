#!/usr/bin/env bash
# =====================================================================================
# deploy/sops_decrypt.sh -- decrypt customizations/credentials/*.sops.yaml on boot
# =====================================================================================
#
# Per [D-038] sops-encrypted credentials policy + [D-125] Point 3 (real creds LOCAL):
# 1. Production credential files live at customizations/credentials/*.sops.yaml
# 2. They are sops-encrypted at rest -- LOCAL on the Linux deployment box only;
#    NEVER committed to public github (covered by customizations/.gitignore).
# 3. This script decrypts each .sops.yaml -> .yaml just before `podman-compose up`.
# 4. The decrypted .yaml lives only in a tmpfs ramdisk that's wiped on host reboot.
#
# Usage:
#   1. Ensure SOPS_KEY_FILE is set in deploy/.env
#   2. Run: bash deploy/sops_decrypt.sh
#   3. Then: podman-compose --env-file deploy/.env up -d
#
# Exit codes:
#   0 = all .sops.yaml files decrypted successfully (or none to decrypt)
#   1 = sops binary missing
#   2 = SOPS_KEY_FILE missing or unreadable
#   3 = decryption failed for one or more files
# =====================================================================================

set -eu -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREDENTIALS_DIR="${REPO_ROOT}/customizations/credentials"
RAMDISK_DIR="${RAMDISK_DIR:-/var/run/hilda-creds}"   # tmpfs preferred for decrypted output


# ----- preflight checks -----
if ! command -v sops >/dev/null 2>&1; then
    echo "ERROR: 'sops' binary not found in PATH." >&2
    echo "Install: https://github.com/getsops/sops/releases" >&2
    exit 1
fi

if [[ -z "${SOPS_KEY_FILE:-}" ]]; then
    echo "ERROR: SOPS_KEY_FILE not set (expected path to private GPG/age key)." >&2
    echo "Set in deploy/.env or export before running this script." >&2
    exit 2
fi

if [[ ! -r "${SOPS_KEY_FILE}" ]]; then
    echo "ERROR: SOPS_KEY_FILE not readable: ${SOPS_KEY_FILE}" >&2
    exit 2
fi


# ----- prepare ramdisk for decrypted creds -----
if [[ ! -d "${RAMDISK_DIR}" ]]; then
    sudo mkdir -p "${RAMDISK_DIR}"
    sudo mount -t tmpfs -o size=16m,mode=0700,uid=10001,gid=10001 tmpfs "${RAMDISK_DIR}" || {
        echo "WARN: tmpfs mount failed; falling back to regular dir (less secure)." >&2
    }
fi


# ----- decrypt each .sops.yaml -----
exit_code=0
shopt -s nullglob

cd "${CREDENTIALS_DIR}"

for sops_file in *.sops.yaml *.sops.json; do
    [[ -f "${sops_file}" ]] || continue

    # Strip ".sops" from middle: foo.sops.yaml -> foo.yaml
    plain_name="${sops_file/.sops./.}"
    plain_path="${RAMDISK_DIR}/${plain_name}"

    echo "Decrypting ${sops_file} -> ${plain_path}"

    if SOPS_AGE_KEY_FILE="${SOPS_KEY_FILE}" \
       GPG_PRIVATE_KEY_FILE="${SOPS_KEY_FILE}" \
       sops --decrypt "${sops_file}" > "${plain_path}"; then
        chmod 0400 "${plain_path}"
        echo "  -> OK"
    else
        echo "  -> FAILED" >&2
        exit_code=3
    fi
done


# ----- summary -----
if (( exit_code == 0 )); then
    echo ""
    echo "All sops bundles decrypted to ${RAMDISK_DIR}"
    echo "Compose services should bind-mount ${RAMDISK_DIR} (read-only)."
    echo "After 'podman-compose down', ${RAMDISK_DIR} can be unmounted to wipe."
fi

exit "${exit_code}"
