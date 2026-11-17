#!/bin/bash
# Swap test: run Space Marine (55150) on a REBUILT Proton tool
# (libwow64fex.dll/.so from a local FEX build) with a FRESH prefix.
#
# Bypasses the Steam UI and armada-game-launch (otherwise it overwrites FEX_APP_CONFIG).
#
# Usage:
#   ./scripts/launch-swap-test.sh [run-N]
# Log: /tmp/opencode/game-swap<N>.log
#
# Success criterion: pass the RLink stalls (>5–10 min without a minidump), gamePC.cfg
# written, no dump in the log. Check:
#   grep -E '^[A-Z] [0-9A-F]{2,4} ' /tmp/opencode/game-swap<N>.log | tail   # FEX lines
#   ls "$PFX/pfx/drive_c/users/steamuser/AppData/Local/Temp/"               # dumps?

set -u
RUN="${1:-1}"
STEAM="$HOME/.local/share/Steam"
GAME_DIR="$STEAM/steamapps/common/Warhammer 40,000 Space Marine"
TOOL="/var/home/armada/opencode/proton-patched"
PFX="/var/home/armada/opencode/compatdata-swap/$RUN"
CFG="${FEX_APP_CONFIG:-/tmp/opencode/fex-cfg/fex-log.json}"   # default + SilentLog=0

if [ ! -x "$TOOL/proton" ]; then echo "NO TOOL: $TOOL/proton"; exit 1; fi
if [ ! -f "$CFG" ]; then echo "NO FEX CONFIG: $CFG"; exit 1; fi

# Stale process check (pgrep -f also matches this wrapper — look at /proc/PID/comm)
for pid in $(pgrep -f 'SpaceMarine' 2>/dev/null); do
  comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
  if [ "$comm" = "SpaceMarine.exe" ]; then
    echo "STALE GAME PROCESS: $pid (SpaceMarine.exe) — kill it before launching"
    exit 1
  fi
done

mkdir -p "$PFX"
cd "$GAME_DIR" || exit 1

echo "== swap-test run $RUN =="
echo "TOOL: $TOOL"
echo "PFX:  $PFX"
echo "LOG:  /tmp/opencode/game-swap$RUN.log"

env SteamAppId=55150 \
    STEAM_COMPAT_APP_ID=55150 \
    STEAM_COMPAT_DATA_PATH="$PFX" \
    STEAM_COMPAT_TOOL_PATHS="$TOOL" \
    STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM" \
    FEX_APP_CONFIG="$CFG" \
    WINEDEBUG=warn+all \
  "$STEAM/linuxarm64/steam-launch-wrapper" --oom-score-adjust 900 -- \
  "$STEAM/steamrtarm64/reaper" SteamLaunch AppId=55150 -- \
  "$STEAM/steamapps/common/SteamLinuxRuntime_4-arm64/_v2-entry-point" \
    --verb=waitforexitandrun -- \
  "$TOOL/proton" waitforexitandrun SpaceMarine.exe \
  > "/tmp/opencode/game-swap$RUN.log" 2>&1
rc=$?
echo "== exit code: $rc =="
echo "dumps:"; ls "$PFX/pfx/drive_c/users/steamuser/AppData/Local/Temp/" 2>/dev/null | grep -i dmp || echo "  (none)"
echo "FEX lines (tail):"; grep -E '^[A-Z] [0-9A-F]{2,4} ' "/tmp/opencode/game-swap$RUN.log" 2>/dev/null | tail -5
