#!/bin/bash
# vexp-guard: block Grep/Glob/Read when the vexp daemon is running AND healthy.
# Cross-platform: Unix/macOS use the domain socket file (.vexp/daemon.sock);
# Windows (Git Bash) has no socket file, so the daemon writes .vexp/daemon.pipe.
VEXP_DIR="${CLAUDE_PROJECT_DIR:-.}/.vexp"
SOCK="$VEXP_DIR/daemon.sock"
PIPE="$VEXP_DIR/daemon.pipe"
HEALTHY="$VEXP_DIR/healthy"
PID_FILE="$VEXP_DIR/daemon.pid"

vexp_deny() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"vexp daemon is running. Use run_pipeline instead of Grep/Glob/Read."}}'
  exit 0
}
vexp_allow() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"vexp index not ready, allowing direct search fallback."}}'
  exit 0
}

# Healthy marker is the portable "daemon is up" signal; the daemon removes it on
# graceful shutdown. Required on every platform — if it's missing, fail open.
[ -f "$HEALTHY" ] || vexp_allow

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*|Windows*)
    # Windows: no Unix socket to stat, and `kill -0` on a native Windows PID is
    # unreliable under Git Bash. The pipe marker + healthy marker are the signal.
    [ -f "$PIPE" ] && vexp_deny
    vexp_allow
    ;;
  *)
    # Unix/macOS: require the live socket AND a live PID (the PID check catches
    # stale files left behind after `kill -9`).
    [ -S "$SOCK" ] || vexp_allow
    [ -f "$PID_FILE" ] || vexp_allow
    kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null && vexp_deny
    vexp_allow
    ;;
esac
