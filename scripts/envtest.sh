#!/bin/bash
# Launch Space Marine (55150) through the Steam client (compat path, same as the
# launch options) with EXTRA env variables for environment tests + a sampler of the
# live process (environ + maps).
#
# Usage: ./envtest.sh <TAG> <ENVVAR=VAL ...>
#   ./envtest.sh t2 GLIBC_TUNABLES=glibc.malloc.perturb=255 _NO_DEBUG_HEAP=1
#   ./envtest.sh t3 PROTON_NO_ESYNC=1 PROTON_NO_FSYNC=1
# Log:    /tmp/opencode/envtest-<TAG>.log
# Sample: /tmp/opencode/sample-<TAG>.txt
set -u
TAG="$1"; shift
STEAM="$HOME/.local/share/Steam"
GAME_DIR="$STEAM/steamapps/common/Warhammer 40,000 Space Marine"
TOOL="$STEAM/compatibilitytools.d/fex26111"
# PFX can be overridden from outside (T6: fresh prefix)
PFX="${PFX:-$STEAM/steamapps/compatdata/55150}"
TEMP="$PFX/pfx/drive_c/users/steamuser/AppData/Local/Temp"
CFG="/tmp/opencode/fex-cfg/fex-log.json"
LOG="/tmp/opencode/envtest-$TAG.log"
SAMPLE="/tmp/opencode/sample-$TAG.txt"

# stale game processes
for pid in $(pgrep -f 'SpaceMarine' 2>/dev/null); do
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  if [ "$comm" = "SpaceMarine.exe" ]; then
    echo "STALE GAME PROCESS: $pid — kill it before launching"; exit 1
  fi
done
[ -f "$CFG" ] || { echo "NO FEX CONFIG: $CFG"; exit 1; }

rm -f "$SAMPLE"
python3 /tmp/opencode/sample_game.py "$SAMPLE" 170 &
SAMPLER=$!

cd "$GAME_DIR" || exit 1
ls "$TEMP" | sort > "/tmp/opencode/envtest-$TAG-before.txt"
date; echo "== $TAG start; extra env: $*"
env SteamAppId=55150 \
    STEAM_COMPAT_APP_ID=55150 \
    STEAM_COMPAT_DATA_PATH="$PFX" \
    STEAM_COMPAT_TOOL_PATHS="$TOOL" \
    STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM" \
    FEX_APP_CONFIG="$CFG" \
    WINEDEBUG=warn+all \
    "$@" \
  "$STEAM/linuxarm64/steam-launch-wrapper" --oom-score-adjust 900 -- \
  "$STEAM/steamrtarm64/reaper" SteamLaunch AppId=55150 -- \
  "$STEAM/steamapps/common/SteamLinuxRuntime_4-arm64/_v2-entry-point" \
    --verb=waitforexitandrun -- \
  "$TOOL/proton" waitforexitandrun SpaceMarine.exe \
  > "$LOG" 2>&1
rc=$?
echo "== $TAG exit code: $rc"
ls "$TEMP" | sort > "/tmp/opencode/envtest-$TAG-after.txt"
echo "== new dumps:"
comm -13 "/tmp/opencode/envtest-$TAG-before.txt" "/tmp/opencode/envtest-$TAG-after.txt"
wait "$SAMPLER" 2>/dev/null
echo "== sampler (first lines):"
head -20 "$SAMPLE" 2>/dev/null
echo "== RLink markers in the log:"
grep -c 'RLink::' "$LOG" 2>/dev/null || echo 0
echo "== eip from the FEX log:"
grep -E 'eip:' "$LOG" | tail -3
