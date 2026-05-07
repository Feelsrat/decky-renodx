#!/usr/bin/env bash
set -u

appid="$1"
delay="$2"
specialk_exe="$3"
shift 3

log="${HOME}/homebrew/logs/hdr-plugin/${appid}.log"
mkdir -p "$(dirname "$log")"

echo "$(date '+%Y-%m-%d %H:%M:%S') Special K delayed launch: delay=${delay}, exe=${specialk_exe}" >> "$log"

"$@" &
game_pid=$!

(
  sleep "$delay"
  proton_bin=""
  for arg in "$@"; do
    case "$arg" in
      *proton*|*Proton*) proton_bin="$arg"; break ;;
    esac
  done
  if [ -z "$proton_bin" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Special K delayed launch: could not find Proton command in Steam launch argv." >> "$log"
    exit 0
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') Special K delayed launch: starting global injector through ${proton_bin}" >> "$log"
  "$proton_bin" run "$specialk_exe" >> "$log" 2>&1 || true
) &

wait "$game_pid"
