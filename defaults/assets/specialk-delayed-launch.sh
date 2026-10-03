#!/usr/bin/env bash
# Steam launch wrapper for Decky RenoDX's experimental delayed Special K injection.
# Usage (set by the plugin): bash specialk-delayed-launch.sh <appid> <delay> <injector.exe> -- %command%
set -u

if [ "$#" -lt 4 ]; then
  echo "usage: $0 <appid> <delay> <injector> -- <game command...>" >&2
  exit 1
fi
appid="$1"
delay="$2"
injector="$3"
shift 3
[ "${1:-}" = "--" ] && shift

log_dir="${XDG_DATA_HOME:-$HOME/.local/share}/decky-renodx/logs"
mkdir -p "$log_dir" 2>/dev/null
log="$log_dir/${appid:-unknown}.log"
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') specialk-delayed: $*" >> "$log" 2>/dev/null; }

if [ "$#" -eq 0 ]; then
  say "no game command was passed; check the launch options end with -- %command%"
  exit 1
fi

"$@" &
game_pid=$!

(
  injector_pid=""
  sleep_pid=""
  trap 'kill $sleep_pid $injector_pid 2>/dev/null; exit 0' TERM
  sleep "$delay" &
  sleep_pid=$!
  wait "$sleep_pid"
  if ! kill -0 "$game_pid" 2>/dev/null; then
    say "game exited before the ${delay}s delay; not injecting"
    exit 0
  fi
  proton=""
  for arg in "$@"; do
    case "$arg" in
      */proton) proton="$arg"; break ;;
    esac
  done
  if [ -z "$proton" ] || [ ! -f "$injector" ]; then
    say "missing Proton (${proton:-not found}) or injector ($injector); skipping"
    exit 0
  fi
  say "injecting Special K via $proton"
  "$proton" run "$injector" >> "$log" 2>&1 &
  injector_pid=$!
  wait "$injector_pid"
) &
watcher_pid=$!

wait "$game_pid"
status=$?
# Never outlive the game: stop the delay timer or the injector.
kill -TERM "$watcher_pid" 2>/dev/null
wait "$watcher_pid" 2>/dev/null
exit "$status"
