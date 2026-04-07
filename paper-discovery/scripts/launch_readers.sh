#!/usr/bin/env bash
# launch_readers.sh — batch AI session launcher with adapter support and timeout

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
ADAPTER="claude"
TIMEOUT_RAW="90m"
MAX_PARALLEL=10

PROMPTS_DIR="${PAPER_BANK:-${HOME}/Documents/paper-bank}/prompts"
LOGS_DIR="${PAPER_BANK:-${HOME}/Documents/paper-bank}/logs"
DONE_DIR="${PROMPTS_DIR}/done"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    # Basic validation for numeric args (after assignment)
    if [[ "$1" == "--max-parallel" ]]; then
        if [[ -z "${2:-}" ]] || ! [[ "$2" =~ ^[0-9]+$ ]] || [[ "$2" -lt 1 ]]; then
            echo "error: --max-parallel expects an integer >= 1" >&2
            exit 1
        fi
    fi
    case "$1" in
        --adapter)      ADAPTER="$2";     shift 2 ;;
        --timeout)      TIMEOUT_RAW="$2"; shift 2 ;;
        --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
        *) echo "error: unknown option '$1'" >&2; exit 1 ;;
    esac
done

# Validate adapter
case "$ADAPTER" in
    claude|codex) ;;
    *) echo "error: unknown adapter '${ADAPTER}'; valid: claude, codex" >&2; exit 1 ;;
esac

# Parse timeout string to seconds
parse_timeout_secs() {
    local raw="$1"
    case "$raw" in
        *h) echo $(( ${raw%h} * 3600 )) ;;
        *m) echo $(( ${raw%m} * 60  )) ;;
        *s) echo "${raw%s}" ;;
        *)  echo "$raw" ;;
    esac
}
TIMEOUT_SECS=$(parse_timeout_secs "$TIMEOUT_RAW")

mkdir -p "${LOGS_DIR}" "${DONE_DIR}"

# ── Heartbeat logger (time-based progress) ───────────────────────────────────
HEARTBEAT_SECS=60
heartbeat_pid=""
start_heartbeat() {
    (
        while true; do
            sleep "${HEARTBEAT_SECS}"
            local remaining=$(( total_count - ok_count - fail_count ))
            log_progress "heartbeat: ok=${ok_count} failed=${fail_count} remaining=${remaining} total=${total_count}"
        done
    ) &
    heartbeat_pid=$!
}
stop_heartbeat() {
    if [[ -n "${heartbeat_pid}" ]]; then
        kill "${heartbeat_pid}" 2>/dev/null || true
        wait "${heartbeat_pid}" 2>/dev/null || true
    fi
}
trap stop_heartbeat EXIT


ok_count=0
fail_count=0

log_progress() { echo "[launch] $*"; }

run_session() {
    case "$ADAPTER" in
        claude) cat "$1" | claude --print --dangerously-skip-permissions ;;
        codex)  cat "$1" | codex --dangerously-bypass-approvals-and-sandbox exec ;;
    esac
}

# ── Collect prompt files ──────────────────────────────────────────────────────
prompt_files=()
while IFS= read -r line; do
    prompt_files+=("$line")
done < <(find "${PROMPTS_DIR}" -maxdepth 1 -name '*-prompt.md' -type f | sort)

total_count=${#prompt_files[@]}

if [[ $total_count -eq 0 ]]; then
    log_progress "No prompt files found in ${PROMPTS_DIR}"
    log_progress "batch complete: ok=0 failed=0 total=0"
    exit 0
fi

log_progress "adapter=${ADAPTER} prompts=${total_count} max_parallel=${MAX_PARALLEL} timeout=${TIMEOUT_RAW} logs=${LOGS_DIR}"
start_heartbeat

# ── Batch tracking arrays ─────────────────────────────────────────────────────
declare -a batch_pids=()
declare -a batch_watchdog_pids=()
declare -a batch_cite_keys=()
declare -a batch_log_files=()

flush_batch() {
    local i exit_code
    for i in "${!batch_pids[@]}"; do
        local pid="${batch_pids[$i]}"
        local wpid="${batch_watchdog_pids[$i]}"
        local cite_key="${batch_cite_keys[$i]}"
        local log_file="${batch_log_files[$i]}"
        local prompt_file="${PROMPTS_DIR}/${cite_key}-prompt.md"

        exit_code=0
        wait "${pid}" || exit_code=$?

        kill "$wpid" 2>/dev/null || true
        wait "$wpid" 2>/dev/null || true

        if [[ $exit_code -eq 143 ]] || [[ $exit_code -eq 137 ]]; then
            log_progress "result=timeout cite_key=${cite_key}"
            (( fail_count++ )) || true
        elif [[ $exit_code -eq 0 ]]; then
            log_progress "result=ok cite_key=${cite_key}"
            mv "${prompt_file}" "${DONE_DIR}/${cite_key}-prompt.md"
            (( ok_count++ )) || true
        else
            log_progress "result=failed cite_key=${cite_key} exit_code=${exit_code}"
            echo "--- last 20 lines of ${log_file} ---"
            tail -n 20 "${log_file}"
            echo "---"
            (( fail_count++ )) || true
        fi
    done

    batch_pids=()
    batch_watchdog_pids=()
    batch_cite_keys=()
    batch_log_files=()

    local remaining=$(( total_count - ok_count - fail_count ))
    log_progress "progress: ok=${ok_count} failed=${fail_count} remaining=${remaining}"
}

# ── Main loop ─────────────────────────────────────────────────────────────────
for prompt_file in "${prompt_files[@]}"; do
    filename="$(basename "${prompt_file}")"
    cite_key="${filename%-prompt.md}"
    log_file="${LOGS_DIR}/${cite_key}-session.log"

    log_progress "spawning cite_key=${cite_key} log=${log_file}"

    run_session "${prompt_file}" > "${log_file}" 2>&1 &
    session_pid=$!

    ( sleep "$TIMEOUT_SECS"; kill "$session_pid" 2>/dev/null ) &
    watchdog_pid=$!

    batch_pids+=("$session_pid")
    batch_watchdog_pids+=("$watchdog_pid")
    batch_cite_keys+=("${cite_key}")
    batch_log_files+=("${log_file}")

    if [[ ${#batch_pids[@]} -ge ${MAX_PARALLEL} ]]; then
        flush_batch
    fi
done

if [[ ${#batch_pids[@]} -gt 0 ]]; then
    flush_batch
fi

log_progress "batch complete: ok=${ok_count} failed=${fail_count} total=${total_count}"
log_progress "logs dir: ${LOGS_DIR}"
