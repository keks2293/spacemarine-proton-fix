# Warhammer 40,000: Space Marine (appid 55150) — Launch & Crash Investigation

> **Status (2026-11-17): investigation complete.**
> **Root cause (H2) confirmed: a deterministic out-of-bounds write in the game's own code.**
> `SpaceMarine.exe` at `0xA99EE7` executes `mov [eax+edi*4],ecx` with an unmasked 32-bit hash
> index (`0x31C33396` in the live prefix, `0x71F3EFCF` in a fresh prefix). The trigger is
> RLink/Steam registration, which only runs with a **live Steam client** (H11).
> **Fix E-17 (P-B2) is ready but NOT applied — user decision: stop.**
> The environment axis is exhausted (T1–T6 + variable search); the FEX build is not the variable.

## TL;DR

- **Symptom:** the game crashes ~32 s after the RLink network thread starts, with a silent AV
  (`0xC0000005`, write) at guest address `0xA99EE7`.
- **Root cause:** the game's counter subsystem (18 IDX tasks) computes a 32-bit hash and uses it
  as an array index without masking or bounds checking. Under FEX the hash is always wild
  (environment-dependent), so the store `val[idx]` is always out of bounds.
- **Not FEX:** FEX-2611.1 (E-15) "fixed" the crash only because the Steam client was off — the
  game took the login-failure path and exited before reaching the crashing code. With a live
  client the same crash returns (E-16).
- **Not environment:** LAA, heap layout/perturbation, sync (fsync/ntsync/server-side), and a
  fresh prefix do not change the crash (T1–T6). The index is deterministic within one
  environment but environment-dependent across prefixes.
- **Not config:** the whole TSO × Multiblock matrix crashes identically (E-05, E-07, E-09).
- **Not DRM:** live capture shows `.text` is byte-identical to disk throughout the process (E-08).
- **Fix:** E-17/P-B2 patches the 18 IDX tasks to return their slot constant; reversible via
  `.orig`. Dry-run green, not applied.

## Table of Contents

1. [Environment](#environment)
2. [Launch stack](#launch-stack)
3. [Timeline](#timeline)
4. [Crash signature (deterministic, 6/6)](#crash-signature-deterministic-66)
5. [FEX config channel](#fex-config-channel-important-for-tests)
6. [Manual launch (without Steam UI)](#manual-launch-without-steam-ui)
7. [Test plan](#test-plan)
8. [Session 2026-11-16: live `.text` capture + authoritative disassembly](#session-2026-11-16-live-text-capture--authoritative-disassembly)
9. [Files](#files)
10. [Experiment log](#experiment-log-setup--result--conclusion)
11. [Pitfalls](#pitfalls-recurring-traps)
12. [Hypotheses tracker](#hypotheses-status--tracker)
13. [Session 2026-11-16 (evening): fact-check, B1/B2, Proton #934](#session-2026-11-16-evening-fact-check-b1b2-proton-934)
14. [Session 2026-11-17: full mechanics, env tests, E-17](#session-2026-11-17-full-crash-mechanics--env-tests--e-17-patch)

## Environment

| Item | Value |
|---|---|
| Device | Retroid Pocket 6, RK3566, aarch64, 6 GB RAM |
| OS | Armada **20261109.dfed5d6** (Fedora 44, kernel 7.2.6) |
| x86 game stack | "Proton 11.0 (ARM64)" (Steam appid 4628740) = **proton-cachyos**, branch `cachyos_11.0_20261001/main` |
| **FEX engine (important)** | **NOT** the system `fex-emu 2610` (that is a Linux ELF interpreter — a red herring). The engine is **statically baked into `libwow64fex.dll`** (5 MB, aarch64 Windows PE) inside the Proton tool; `libwow64fex.so` (71 KB) is a thin unixlib bridge (FEXUnixLib_* handlers). |
| **FEX pin in the tool** | `FEX-Emu/FEX @ 0df84d3844` (2026-09-28, between 2608 and 2609; 214 commits behind 2611.1) |
| Game | 32-bit x86, Relic engine, **D3D9** (d3dx9_37) → Wine d3d9 → DXVK → Vulkan/Turnip; ~11.5 GB, Russian localization |

Two Proton tool copies on the system:

- **Depot** (used by the game): `~/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/`
  (updated by Steam on 2026-11-15 10:04, buildid 25833186). **Prefix symlinks point HERE**
  (`pfx/drive_c/windows/syswow64/libwow64fex.dll → depot`), i.e. all crashes happen on the depot FEX.
- **OS copy**: `/usr/share/steam/compatibilitytools.d/proton-cachyos-11.0-arm64/` — FEX ≥2609
  (fingerprint: `NtRaiseException` ×2); the game does not use it.

DLL fingerprints (strings in the binary): depot = 0× `NtRaiseException` (pre-2609), OS = 2×
(≥2609), 0× `FEX: Forced assert` in both (cf2f831 absent).

Game path: `~/.local/share/Steam/steamapps/common/Warhammer 40,000 Space Marine/`
Prefix: `~/.local/share/Steam/steamapps/compatdata/55150/` (version `11.0-100`)

## Launch stack

```text
Steam client (IPC alive?)
 └─ ~/.local/share/Steam/linuxarm64/steam-launch-wrapper --oom-score-adjust 900
     └─ ~/.local/share/Steam/steamrtarm64/reaper  SteamLaunch AppId=55150
         └─ SteamLinuxRuntime_4-arm64/_v2-entry-point --verb=waitforexitandrun
             └─ Proton 11.0 (ARM64)/proton  waitforexitandrun SpaceMarine.exe
                 └─ wine host (aarch64: bin-arm64/wine + wineserver)
                     └─ FEX wow64: libwow64fex.dll (guest CPU emulation)
                         └─ SpaceMarine.exe  (32-bit x86 guest; D3D9 → DXVK → Turnip)
```

`FEX_APP_CONFIG` is the only config channel the wow64 FEX reads (see below).

## Timeline

1. exFAT → internal btrfs: the prefix started building (done by the user).
2. 6 crashes (11:41, 12:03, 12:28, 13:57, 14:43, 15:44) — same signature, deterministic.
3. Armada update to 20261109 → crash unchanged.
4. **Run 4 (15:44) with `SilentLog: "0"`** — the FEX log turned on (2062 lines `L TID msg` in
   `game-run4.log`); the crash was the same, but now visible: **no `A `/`E ` lines**
   (asserts/errors were not logged) and **`D E0 Call-ret stack inbalance`** in the game
   process right before death.

## Crash signature (deterministic, 6/6)

Dumps (all in `.../pfx/drive_c/users/steamuser/AppData/Local/Temp/`):

- `SpaceMarine2SR90OI3.dmp` — 11:41
- `SpaceMarine97353B42.dmp` — 12:03
- `SpaceMarineOK4T1EA5.dmp` — 12:28
- `SpaceMarine2BKL16I6.dmp` — 13:57
- `SpaceMarineDLTT48I0.dmp` — 14:43 (compatible profile)
- `SpaceMarineLF8D6CC4.dmp` — 15:44 (run 4, SilentLog=0)

Every crash follows the same sequence:

```text
C++ exception (e06d7363)  →  EXCEPTION_BREAKPOINT (0x80000003)  →  AV (0xC0000005)
backtrace: libwow64fex.dll+0x3650 → wow64.dll+0x1C85C
```

- `libwow64fex.dll` = FEX engine (aarch64 Windows PE, baked into the depot tool);
  `wow64.dll` = wine shim.
- Crash threads: `IntegrityManagerThread` (run 1), `Task worker 0` (run 2, right after
  `NetworkManager::Create`).
- Point: ~3–4 s after `RLink::...::ThreadFunction - Entering network thread function`
  (RLink/Steamworks init; the SM online services are dead).
- Dump format: custom BugSplat (version 0xA793; 71–82 KB). The stream directory is
  non-standard (RVAs into the directory itself) → content scan; context anchor Eip@176.
  Parser: `scripts/mdmp.py` (this repo).

## FEX config channel (important for tests)

- `armada-game-launch` applies the per-game config **only via the env `FEX_APP_CONFIG`** (the
  only knob read by FEX arm64ec/wow64; on-disk FEX configs are invisible to them). It generates
  `~/.cache/armada-fex/{appid}.{pid}.json` from the game-tweaks profile.
- Game-tweaks: `/usr/share/armada/game-tweaks.json` (defaults; global `fexProfile: default`) +
  `/etc/armada/game-tweaks.json` (override, **root**). Per-game `env`-dict is also supported.
- **Without sudo:** set `FEX_APP_CONFIG` yourself and **skip `armada-game-launch`** (it
  overwrites the variable).
- **FEX log**: key `SilentLog: "0"` in `FEX_APP_CONFIG` (default "true" = silent;
  `FEXCore/Interface/Config/Config.json.in:519`, read in `Source/Common/Config.cpp:512`). Log
  line format of the Windows build: `L TID msg` (L ∈ A/E/D/I) — they go to our catch log
  without the WINE prefix; assert lines would be `A ...`. The log file is NOT created in the
  prefix (WineDbgOut path). Config with logging: `/tmp/opencode/fex-cfg/fex-log.json`.
- ⚠ `FEX_*` env variables are not read by wow64-FEX; only JSON via `FEX_APP_CONFIG`.

## Manual launch (without Steam UI)

Same chain as Steam, but without `armada-game-launch` (otherwise it overwrites
`FEX_APP_CONFIG`):

```bash
cd "$HOME/.local/share/Steam/steamapps/common/Warhammer 40,000 Space Marine"
CFG=/tmp/opencode/fex-cfg/fex-log.json   # default profile + SilentLog=0
env SteamAppId=55150 \
    STEAM_COMPAT_APP_ID=55150 \
    STEAM_COMPAT_DATA_PATH="$HOME/.local/share/Steam/steamapps/compatdata/55150" \
    STEAM_COMPAT_TOOL_PATHS="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)" \
    STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam" \
    FEX_APP_CONFIG="$CFG" \
    WINEDEBUG=warn+all \
  "$HOME/.local/share/Steam/linuxarm64/steam-launch-wrapper" --oom-score-adjust 900 -- \
  "$HOME/.local/share/Steam/steamrtarm64/reaper" SteamLaunch AppId=55150 -- \
  "$HOME/.local/share/Steam/steamapps/common/SteamLinuxRuntime_4-arm64"/_v2-entry-point \
    --verb=waitforexitandrun -- \
  "$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)"/proton \
    waitforexitandrun SpaceMarine.exe \
  > /tmp/opencode/game-run-N.log 2>&1
```

For the swap test: `STEAM_COMPAT_TOOL_PATHS` → a copy of the tool with replaced
`libwow64fex.dll/.so`; `STEAM_COMPAT_DATA_PATH` → a **fresh prefix** (the old one is symlinked
to the depot directory). Check the config of a live process:
`tr '\0' '\n' < /proc/PID/environ | grep FEX_APP_CONFIG`.
⚠ `pgrep -f SpaceMarine` matches our wrapper bash (false "alive"); check `/proc/PID/comm`.

## Test plan

1. [x] compatible profile via `FEX_APP_CONFIG` (no sudo) — `game-run3.log`: window, DXVK,
   RLink; **the crash did not go away** (dump DLTT48I0, 14:43). The profile does not fix it.
2. [x] Run 4 with `SilentLog=0` — FEX log obtained (2062 lines), no assert lines,
   `Call-ret stack inbalance` in E0/1D8 before the crash (dump LF8D6CC4, 15:44).
3. [ ] (optional) pin the profile permanently, sudo:
   `echo '{"games":{"55150":{"fexProfile":"compatible"}}}' | sudo tee /etc/armada/game-tweaks.json`
   (only if it still makes sense after the crash fix — the profile alone does not fix the crash).
4. [ ] (optional) remove the orphan of the old external prefix
   `/run/media/armada/Slim/SteamLibrary/steamapps/compatdata/55150` (disk space).

## Session 2026-11-16: live `.text` capture + authoritative disassembly

### Decisive experiment: in-memory `.text` == on-disk

Scripts `/tmp/opencode/{capture_text.py, oneshot_diff.py, sample_until_crash.py}`: while
`SpaceMarine.exe` is alive, every 5 s read `/proc/<pid>/mem` at mapping `0x401000–0x1538000`
(the whole `.text`, 0x1136A60 bytes) and byte-diff it against the on-disk section.

Result (6 samples, 1.1 s → 31.8 s, until process death): **DIFFS = 0 on every sample**. ROI
areas (thunk `A99ED8`, caller `A99F7A`, `F3CAE0`, `F3CF60`, `F520F0/F52100`, `F65230`,
`F65790`, reg-block `A9A240`) — identical.

**Conclusion: the hypothesis "DRM/CEG patches `.text` at runtime" is dead.** The code FEX
executes is exactly the shipped binary. There is no change in `.text` during the process
lifetime.

### Authoritative disassembly (objdump, `call *IAT` resolved to imports)

Thunk `0xA99ED8` (earlier some movs were read mirrored — the "anomalies" are gone):

```asm
a99ed8: push esi
a99ed9: mov  esi, ecx          ; esi = Z (entry, from ecx)
a99edb: mov  eax, [esi+0x34]   ; eax = buffer
a99ede: mov  ecx, [esp+0xC]    ; ecx = arg2 = Y
a99ee2: push edi
a99ee3: mov  edi, [esp+0xC]    ; edi = arg1 = X-1
a99ee7: mov  [eax+edi*4], ecx  ; buffer[X-1] = Y   <- CRASH (OOB write)
a99eea: call *0x153a90c        ; = WINMM.timeGetTime
```

There is **no** `and $0x0C,%al` in the thunk — the earlier "anomaly #1" was an artifact of
reading the bytes `8B 4C 24 0C` mirrored (= `mov edi,[esp]`, not `mov [esp],edi`). The crash
context is fully consistent with the on-disk code → **no FEX JIT/exception-context anomalies**.

Registration-block wrappers: `A99BB0 = call F3CAE0` (→ X), `A99BA0 = call F3CF60` (→ Y = 0x2A),
`A99DE0 = mov eax,[0x1D417E8]` (→ Z). Crash: `buffer[X-1] = 0x2A`, X-1 = 0x31C33396.

**Key:** the "hash" in `F52100` is **`GetCurrentThreadId()`** (KERNEL32, IAT `0x153A118`):

```asm
f52100: slot[0] = 0xFFFFFFFF
f52109: call GetCurrentThreadId
f5210f: mov $37,ecx ; xor edx,edx ; div ecx
f5211d: ecx = table[edx]        ; table[tid%37]  (table @ 0x19C34EC, 37 slots)
f52120: slot[1] = table[tid%37]
```

That is, registration is a **thread-ID-bucketed** system (37 slots). The OOB index comes from
`local[0]` inside `F3CAE0`, which is filled in a loop over 708 (0x2C4) records via
`F65230`/`F52DE0` (`F52DE0` = byte-wise table-driven XOR string hash).
`F65790 = (a+0x75)^0x16E2A14`. In FEX, `GetCurrentThreadId() = TEB->ClientId.UniqueThread`
(the TEB is filled by the wine side of wow64 thread creation; FEX only reads
`Info.TebBaseAddress`).
⚠ **Correction (11-16, evening):** the SPDIAG log (`sdwh-maps2.log`) showed that the guest TID
is **small wine handles** (`tid=0x24`, `tid=0xE4`; crash thread `0x208`=520), NOT the Linux
TID. The assumption "≈ Linux TID" is wrong; "TID is an emulation-dependent input" is weakened
but not refuted (the values still differ from native Windows). See
[Hypotheses tracker](#hypotheses-status--tracker).

### Settings tests (continued)

`TSOEnabled=0` (+`Multiblock=0`) — launched; the same deterministic crash in the same ~32 s
window. New BugSplat dumps were not written (the direct-launch path does not write to
AppData/Temp; see below). Meaningful FEX toggles exhausted: X87ReducedPrecision, Multiblock,
TSOEnabled — no effect.

### "Is there a Proton workaround?"

**No, there is no generic Proton setting for an OOB crash in the game's own CPU code.** Proton
env knobs (PROTON_USE_WINED3D11/NO_ESYNC/USE_NTSYNC/…) control graphics/sync/latency, not the
CPU logic where the crash happens. The only relevant knobs are the per-game FEX config, and
all meaningful ones have been tested. The only known workaround for this game — **Valve Proton
issue #934**: "replacing SpaceMarine.exe (1.0.165) with an unofficial fixed one helped", i.e. a
binary patch/replacement of the game exe, not a Proton setting.

### Direct launch without the Steam client (the client swallows `-applaunch`)

Working recipe (the client does not have to be focused):

```bash
DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
 STEAM_COMPAT_CLIENT_INSTALL_PATH=~/.local/share/Steam \
 STEAM_COMPAT_DATA_PATH=~/.local/share/Steam/steamapps/compatdata/55150 \
 STEAM_COMPAT_APP_ID=55150 STEAM_APP_ID=55150 \
 STEAM_COMPAT_TOOL_PATH=~/.local/share/Steam/compatibilitytools.d/cachyos-261015 \
 /usr/libexec/armada/armada-game-launch .../steam-launch-wrapper --oom-score-adjust 900 -- \
   .../steamrtarm64/reaper SteamLaunch AppId=55150 -- \
   .../SteamLinuxRuntime_4-arm64/_v2-entry-point --verb=waitforexitandrun -- \
   .../cachyos-261015/proton waitforexitandrun '.../SpaceMarine.exe'
```

Without `STEAM_COMPAT_DATA_PATH` Proton fails with "No compat data path?".

## Files

| Item | Path |
|---|---|
| Run 1 log (default, WINEDEBUG=warn+all) | `/tmp/opencode/game-run.log` |
| Run 3 log (compatible via FEX_APP_CONFIG) | `/tmp/opencode/game-run3.log` |
| Run 4 log (SilentLog=0, FEX log 2062 lines) | `/tmp/opencode/game-run4.log` |
| FEX config with logging | `/tmp/opencode/fex-cfg/fex-log.json` |
| Steam logs | `~/.local/share/Steam/logs/{console_log,gameprocess_log,compat_log}.txt` |
| Dump parser (early incomplete copy) | `/tmp/opencode/mdmp.py` |
| FEX profile configs | `/usr/share/armada/fex-profiles.json` |
| Config generator | `../fex/scripts/make-fex-config.py` |
| Issue draft (Proton 11 ARM64 + FEX wow64) | `warhammer-40k-space-marine-bug-report.md` |
| Swap-test script (ready) | `scripts/launch-swap-test.sh` (this repo) |

## Experiment log (setup → result → conclusion)

| ID | Date/time | Setup (tool / profile config / prefix) | Result | Conclusion |
|---|---|---|---|---|
| E-01 | 11-15 11:41 | depot / default (TSO1/MB1) / live 55150 | crash ~32 s; dump `2SR90OI3` | baseline signature |
| E-02 | 11-15 12:03 | depot / default | crash; dump `97353B42` | deterministic |
| E-03 | 11-15 12:28 | depot / default | crash; dump `OK4T1EA5` | deterministic |
| E-04 | 11-15 13:57 | depot / default | crash; dump `2BKL16I6` | deterministic |
| E-05 | 11-15 14:43 | depot / **compatible (TSO1/MB0)** | crash; dump `DLTT48I0` | TSO1/MB0 does not fix it (H4) |
| E-06 | 11-15 15:44 | depot / default + SilentLog=0 | crash; dump `LF8D6CC4`; FEX log 2062 lines, `Call-ret stack inbalance`, 0 assert lines | hypothesis H1 (exception path) |
| E-07 | 11-15 20:05 → 11-16 02:14 | proton-patched (builds 2609 and 2610-25 — **byte-identical**) / mixed (cap1=compatible, fast1-3=fast, full1) / 18 fresh prefixes | same crash 0xA99EE7 (logs lost on reboot — per the summary) | 2609/2610-25 do not fix it (H1-partial); TSO0/MB1 does not fix it |
| E-08 | 11-16 morning | depot / live prefix / live `.text` capture (6 samples, 1.1→31.8 s) | 0 diffs | H6 refuted (DRM does not patch `.text`) |
| E-09 | 11-16 morning | depot / manual `FEX_APP_CONFIG` TSO0/MB0 | same crash ~32 s (direct launch writes no dumps) | TSO0/MB0 does not fix it (H4) |
| E-13 | 11-16 evening | exe fact-check (md5/cmp/COFF) | exe == `.orig` (`786fd609…`), Characteristics=0x0123 | **H7 withdrawn** — the game runs on an untouched exe |
| E-14 | 11-16 evening | audit of Proton #934 (web) | fixed-exe 1.0.165 (DRM suspicion) — source not public; "2 files missing" on a fresh install → Verify Integrity; Proton7+WINED3D11 (x86 PCs only) | (a) unavailable; (b) testable; (c/d) not applicable to ARM |
| E-15 | 11-16 19:47–19:48 | **proton-patched 2611.1** (md5 `631f362b…`, NtRaiseException=2) / default+SilentLog=0 (TSO1/MB1) / fresh prefix run 19 / **Steam client NOT running** | **CRASH GONE.** rc=0, no minidump, the process lived ~34–40 s (gamePC.cfg 19:47:50.67 → exit 19:48:24.93) — past the old 32-s point. RLink window 19:48:22.6→19:48:24.7 = exactly the 32nd–34th second. Instead of an AV the game handles the failure: `SteamAPI_Init() failed` → `GenerateToken - failed, unknown steam user` → `Found 0 profiles for account ` → graceful shutdown (SessionManager/Transport/WorldwideLoginService uninit, "Terminating 0x164/0x160") | **Pre-registration (before launch):** the only variable vs E-07 is the FEX build (2609→2611.1: CHPEv2 exception-path rewrite + dbbda7b2 call-ret stack reset). If the crash is from the exception path (H1) → 2611.1 passes 32 s, no minidump. If the same `0xA99EE7` → H1 refuted. **Outcome: branch 1 materialized — H1 confirmed as the cause of the crash.** Sub-criterion "gamePC.cfg non-zero" is wrong: the file is 0 B in ALL runs (including the first ones) — the game does not write it before save/exit. ⚠ Nuance: the client was off → `SteamAPI_Init() failed` is guaranteed; a clean exit = the login-fail path (H11) |
| E-16 | 11-16 20:23–20:24 | compat tool `fex26111` (FEX-2611.1, md5 `631f362b…`) **via Steam UI**, client **alive** (IPC), live prefix 55150, default TSO1/MB1 | **CRASH RETURNED.** Silently (user: "it closed by itself without an error"); new dump `SpaceMarineS8IH0NM4.dmp` (81 748 B, 20:24:29). **Same signature:** Eip `0xA99EE7`, wild index `0x31C33396` (true Edi), value `0x2A`, `0x900`, ret `0xA99F96`; buffer pointers 0x1063xxxx slightly different from Q84D1V03. Process: PID 1223xxx (20:24:19) → "exit code -1" (20:24:32) — the old ~32-s point | **Pre-registration (1) "the crash will not return" — REFUTED.** The E-15 "fix" = flow illusion: without a client the game exits early on the login-fail path (`SteamAPI_Init() failed`) and **never reaches the crash code**; with a live SteamAPI the RLink registration runs → wild index → AV. Conclusions: **H1 demoted** (the same 2611.1 crashes with the client and does not crash without it — the FEX build is not the variable), **H2 promoted to primary** (deterministic OOB in the game code — the index is identical across builds/runs), **H11** = the client-state splitting mechanism. Details: the "E-16" section below |
| T1–T3 | 11-17 00:43–01:35 | fex26111 / Steam path (steam-launch-wrapper, `scripts/envtest.sh` recipe), client **alive**, RLink confirmed; env tests: T1 `WINE_LARGE_ADDRESS_AWARE=0` (+ no-op flag `PROTON_FORCE_LARGE_ADDRESS_AWARE=0`), T2 `GLIBC_TUNABLES=glibc.malloc.perturb=255 _NO_DEBUG_HEAP=1`, T3 `PROTON_NO_ESYNC=1 PROTON_NO_FSYNC=1` | **All: the same crash `A99EE7`, the same index `0x31C33396`, context byte-identical across runs** (T1=T1b=T2: Eax=0x10633690, Esp=0x0827FED8, fault 0xD77004E8 = Eax+idx·4). The env variables provably reached the guest (`/proc/<pid>/environ` sample) | The index depends on neither LAA/heap layout, nor perturbation, nor the sync mechanism → not heap-derived, not layout-dependent, not a race. LAA is invisible in FEX wow64 (the arena always extends to 0xFFFF4000). Details: the "Session 2026-11-17" section |

## Pitfalls (recurring traps)

- **`/tmp` is volatile** (tmpfs): the 11-15→11-16 reboot burned all logs/configs/builds from
  11-15. Long-lived artifacts go to home (the repo, `proton-patched/`, prefixes
  `compatdata-swap/*`).
- **`pkill -f` sets your own shell on fire**: the pattern also matches the bash cmdline (today
  rc=143: the word "wineserver" in the command text matched `FEX.*wineserv[e]r`; the bracket
  trick does not help if the same word is in the same command). Kill by PID.
- **Config channel**: wow64-FEX in the Proton chain reads ONLY `FEX_APP_CONFIG` (JSON); on-disk
  `~/.config/fex-emu/AppConfig/*` is invisible; `armada-game-launch` overwrites
  `FEX_APP_CONFIG` → manual launches are without it, following the steam-launch-wrapper recipe.
- `pgrep -f SpaceMarine` matches the wrapper (false "alive") → check `/proc/PID/comm`.
- **A conversation summary ≠ a repo document**: the "DLL bit" (H7) lived only in a compressed
  summary and was not confirmed by artifacts. The truth is the files on disk (md5, dumps, logs).
- The Steam client swallows `-applaunch` → launch manually through steam-launch-wrapper.
- `gamePC.cfg` is created **empty** early (0 bytes since 11:41) → the success criterion is a
  **non-zero** file.
- **Context anchor in BugSplat dumps (version 0xA793):** the stream directory is non-standard
  (RVAs point into the directory itself) → parse by content (content scan by Eip/patterns).
  x86 CONTEXT: Eip at offset **176 (0xB0)** in the block, `context_start = eip_hit − 176`; the
  block is the standard layout (…Ebp, Eip, SegCs, EFlags, Esp, SegSs). The old table (Eip=192)
  was shifted by 16 B — "SegDs" is actually the true Edi. Self-consistency check: SegCs=0x23,
  EFlags=0x212, SegSs=0x2B, Esp in the wine region.
- Profiles (`fex-profiles.json`): default=TSO1/MB1, fast=TSO0/MB1, compatible=TSO1/MB0 — when
  calling a run "TSO run", always record both bits.
- **`BsSndRpt.exe` blocks the next launch (11-17):** after every crash BugSplat starts
  `BsSndRpt.exe` (a GUI crash reporter) that lives indefinitely, holding the
  reaper→srt-bwrap→pv-adverb tree AND the Steam session for 55150 → the next game launch does
  not start (reaper waits, no new session in compat_log). **Kill the `BsSndRpt.exe` PID between
  crash runs.** Happened 3× (pids 1641965, 1702977, 1708665 — all killed).
- **PE section VAs = RVA, objdump prints VMA (11-17):** the section table stores RVAs; compute
  file offsets from the objdump VMA (see the VA map in the 11-17 section). Using RVA directly →
  all offsets shift by 0x400000.
- **Read 4-byte windows as LE u32 (11-17):** `e8 04 70 d7` = `0xD77004E8` (once read as
  `0xD70400E8` → a false "the formula does not add up").

## Hypotheses (status) — tracker

| # | Hypothesis | Status | Evidence / artifact |
|---|---|---|---|
| H1 | FEX engine regression in the depot (exception path, call-ret desync) | **REFUTED** | The game runs on Steam Deck (x86, Proton 8) — there is no FEX regression window; the crash is specific to the ARM64 + FEX wow64 environment. On this device the FEX build is not the variable: E-15 (no client, 2611.1) exited cleanly only because the game took the login-fail path (H11) and never reached the crash code; E-16 (client alive, same 2611.1) reproduced the same crash `0xA99EE7`/index `0x31C33396`. Root cause — H2 |
| H2 | OOB in the game's own code (thread-bucketed hash, unmasked index) | **ROOT CAUSE (confirmed)** | Thunk `A99ED8` (`mov [eax+edi*4],ecx`) disassembled from the on-disk `.text`; live capture: 6 samples until death, 0 diffs (H6). **E-16:** the wild index `0x31C33396` (Edi) is **identical** in the depot runs (11-15 dumps, E-01..06) and in 2611.1 (S8IH0NM4), and across runs in general — deterministic, data-driven. **11-17 (T1–T6):** LAA/heap/sync have no effect; **a fresh prefix (T6) yields a DIFFERENT index `0x71F3EFCF` + a different value `Ecx=0xEE82005C`** (with byte-identical heap) → the hash input includes environment state (prefix/TID state), not just .rdata+RVA; in any of our environments the index is wild (in-range is unreachable by env tuning). Trigger: RLink/Steam registration (runs only with a live SteamAPI — H11). **Conclusion: the game does not mask the index → OOB is guaranteed in any environment where the hash is wild; the reliable fix = E-17 (constant tasks)** |
| H3 | TID — an emulation-dependent input (Linux TID in wine/FEX) | **weakened** | SPDIAG: guest TID = small wine handles (0x24, 0xE4, crash 0x208); same order of magnitude as Windows, but different values |
| H4 | Memory visibility (TSO) | **refuted** | The whole TSO×Multiblock matrix: 1/1 (default, E-01..E-06), 1/0 (compatible, E-05), 0/0 (E-09), 0/1 (fast1-3, E-07) — the same crash everywhere. The config axis does not fix it |
| H5 | Config toggles (compatible, Multiblock, X87ReducedPrecision) | **refuted** | run 3 (compatible) — the crash did not go away; the others — no effect |
| H6 | DRM patches `.text` at runtime | **refuted** | Live capture of `/proc/<pid>/mem`: 0 diffs on all 6 samples (1.1 s→31.8 s) |
| H7 | "IMAGE_FILE_DLL bit was missing, exe rebuilt with /dll" | **NOT CONFIRMED — withdrawn** | `SpaceMarine.exe` is **byte-identical to `.orig`** (md5 `786fd609…`, 25 153 592 B, diff=0). Bit 0x2000 is not required for a normal exe (Characteristics=0x0123 — standard; on Windows the game works precisely because the exe is normal). No modified copy on disk. Not mentioned anywhere in the 11-15 session doc. The claim lives only in the compressed conversation summary |
| H10 | Known world fix: "fixed exe 1.0.165" | **unavailable** | Proton #934 (open since 2018, label "Unofficial"): the fix-exe author does not share the source ("the emperor does not approve"). See below |
| H11 | Steam client state = flow fork: no client → login-fail path (clean exit); client alive → RLink registration → crash | **confirmed as the mechanism (E-15 + E-16)** | E-15 (no client): `SteamAPI_Init() failed` → `GenerateToken - failed, unknown steam user` → `Found 0 profiles for account ` (24.577) → graceful uninit → exit 0. **E-16 (client alive, same 2611.1): crash `A99EE7`/`0x31C33396`** — with a live SteamAPI the game does NOT take the login-fail path; it reaches registration and AVs on the wild index. This is exactly what the "fixed exe" from #934 bypasses (it probably silences the registration/check). Open: the origin of the wild index (H2); what is on screen (E-16: "closed by itself without an error" — silent, BugSplat dump) |

## Session 2026-11-16 (evening): fact-check, Proton #934

### "DLL bit" fact-check (per the user's question)

- `md5sum`: `SpaceMarine.exe` == `SpaceMarine.exe.orig` = `786fd60984609e8ad3fb46c4d0befe7f`;
  `cmp` — 0 differences; both 25 153 592 B, mtime 2026-11-15 11:39:16. **The game runs on an
  untouched exe.** Looking for a "rebuilt" copy is pointless — there is none.
- COFF `Characteristics=0x0123` (EXECUTABLE_IMAGE | 32BIT_MACHINE | …): **a normal exe does not
  contain `IMAGE_FILE_DLL` (0x2000)** — that is the DLL bit. The claim "the DLL bit was missing →
  /dll in lld-link" as a root cause is withdrawn (H7).
- Lesson for trusting notes: a conversation summary ≠ a repo document; the truth is the
  artifacts on disk (md5, dumps, logs, disassembly).

### TID fact (SPDIAG)

`/tmp/opencode/sdwh-maps2.log` (patched-tool run, 11-16 09:38): `[SPDIAG] BTCpuThreadInit ENTER
tid=24`, `tid=E4` — guest TIDs are small wine handles. The crash thread `0x208` (520) is of the
same order. H3 weakened.

### /tmp was wiped (reboot overnight 11-15→11-16)

The reboot happened between 02:14 and 09:38 on 11-16 (11-15 files lost; dnf --transient packages
reinstalled today). Lost: `game-run*.log`, `mdmp.py`, `fex-cfg/`, build trees, swap logs
(swap-run results only per the summary: the same 0xA99EE7). Survived (home, not tmpfs):
`proton-patched/`, `compatdata-swap/*`, dumps in the prefixes.

### Critical TSO clarification (who actually ran what)

`/usr/share/armada/fex-profiles.json`: **default = TSOEnabled=1, Multiblock=1**;
**fast = TSO=0, MB=1**; **compatible = TSO=1, MB=0**. `armada-game-launch` generates
`FEX_APP_CONFIG` from the profile (default by default) → **the 11-15 crashes through the Steam
UI ran with TSO=1 + MB=1**. The on-disk per-game AppConfig (`~/.config/fex-emu/AppConfig/…`) is
**not read** by wow64-FEX (only `FEX_APP_CONFIG`) — it was a red herring. **The whole
TSO×Multiblock matrix is covered:** 1/1 (default, E-01..E-06), 1/0 (compatible, E-05), 0/0
(manual, E-09), 0/1 (fast1-3 swap runs, E-07) — **all give the same crash**. The config axis does
not fix it (H4 closed as refuted).

### Success criterion — clarification

`gamePC.cfg` in the live prefix — **0 bytes**, created 11-15 11:41:12 (the first run). The game
creates an empty file early; "gamePC.cfg written" as a criterion = a **non-zero** file.

### Proton issue #934 (checked 11-16)

- Open since **2018**, label `Game compatibility - Unofficial` ("not expected to work without
  issues").
- (a) kneo (09-2024): "It seems to be a **DRM issue**, as replacing the SpaceMarine.exe
  (Version 1.0.165) with an *unofficial fixed one*, did the trick" — the source is not published
  ("the emperor does not approve such heresy"), details only in private.
- (b) Fresh install: "bug splash + **2 files missing**" → fixed by Steam "Verify Integrity".
- (c) mathew2214 (07-2024): on x86 PCs, Proton 7 + `PROTON_USE_WINED3D11=1` — it starts (audio
  underruns). (d) MRDGH2821 (06-2025): Heroic + GE-Proton 9-27/8-32 — 70+ FPS (x86 PCs).
- For our ARM situation (c)/(d) are not applicable; (a) is the only direct workaround, but the
  file is unavailable; (b) is testable: verify the installation files' integrity.

### E-15: FEX-2611.1 — the crash went away (11-16 19:47–19:48)

- Launch: `scripts/launch-swap-test.sh 19`, tool `proton-patched` (2611.1, md5 `631f362b…`),
  config default+SilentLog=0 (TSO1/MB1 — the same profile that crashes through Steam), fresh
  prefix `compatdata-swap/19`. The only variable vs E-07: the FEX build.
- **Result: rc=0, no minidump.** Process: gamePC.cfg created 19:47:50.67 (the game writes it
  early, 0 B), exit 19:48:24.93 → lifetime ~34–40 s. The old crash point (~32 s, RLink/Steamworks
  init) was passed: RLink debug 19:48:22.6→19:48:24.7.
- Instead of an AV (`A99ED8`, bucketed hash) the game **handles the failure correctly**:
  `SteamAPI_Init() failed` → RLink init (NetworkManager, WinTransport broadcast :6112,
  SessionManager, Automatch) → `NetworkManager::Destroy` → `GenerateToken - failed, unknown steam
  user` → `GetAuthToken state ID : 4` → `Found 0 profiles for account ` → graceful shutdown
  (CallManager, channels, "Fade to black", SessionManager/Transport/WorldwideLoginService uninit,
  "Terminating 0x164/0x160").
- Conclusions (11-16 19:48, partially revised by E-16): (1) **H1 confirmed** — *revised by E-16:
  "the crash went away" = flow illusion (the client was off → login-fail path, the game never
  reached the crash code); H1 demoted to a secondary factor (no-client flow only).* (2) New
  blocker — H11: the RLink authentication failure itself (dead THQ services) makes the game exit
  cleanly. (3) The sub-criterion "gamePC.cfg non-zero" is wrong — the file is 0 B in all runs,
  including the first ones.
- Artifacts: log `/tmp/opencode/game-swap19.log` (283 KB, 3192 lines), prefix
  `compatdata-swap/19`. ⚠ /tmp is volatile — a copy of the log is in the repo before the commit.

### E-16: the crash returned with a live client (11-16 20:23–20:24)

- Launch: **via Steam UI**, tool `fex26111` (compat_log 20:23:49: "FEX 2611.1 (SpaceMarine fix)"),
  client **alive** (IPC: `GetIPCConnectionDetails` in console_log), live prefix 55150, default
  TSO1/MB1. User observation: **"it closed by itself without an error"** — silent.
- **Result: CRASH RETURNED.** New dump `SpaceMarineS8IH0NM4.dmp` (81 748 B, 20:24:29).
  gameprocess_log: PIDs 1223900–1224173 added 20:24:19, "exit code -1" 20:24:32 — the old ~32-s
  point. gamePC.cfg in the live prefix untouched (0 B since 11-15 11:41 — as always).
- **Signature identical to the old one** (dump Q84D1V03, 10:28, depot/earlier):
  - Eip `0xA99EE7` (4 occurrences in the file: 0xf878/0xf974/0x13bf8/0x13d40),
  - wild index **`0x31C33396`** — the same as in all 11-15 crashes and in Q84D1V03;
  - store value `0x2A`, constant `0x900`, ret `0xA99F96` (handler context Eip=0xA9A8A6,
    Esi=0xA99F96 — the same return point).
- **Crash context (after the anchor fix, see below):** true registers —
  `Edi=0x31C33396 Esi=0x10632FE0 Ebx=0x106345B0 Edx=0x900 Ecx=0x2A Eax=0x106332A0
  Ebp=0x767FF24 Eip=0xA99EE7 Esp=0x767FED8`; SegCs=0x23, EFlags=0x212, SegSs/SegDs/SegEs=0x2B
  (the context is self-consistent). The faulty store: `mov [eax+edi*4],ecx` →
  0x106332A0 + 0x31C33396·4 ≈ **0x176FFF98** — unmapped → AV 0xC0000005 (present in the dump).
- **Diff old (Q84D1V03) vs new (S8IH0NM4):**
  - **identical:** Eip, wild index `0x31C33396`, `0x2A`, `0x900`, ret `0xA99F96`;
  - **different:** buffer-area pointers (Eax 0x10633690→0x106332A0, Esi 0x106333D0→0x10632FE0,
    Ebx 0x106349A0→0x106345B0 — a shift of hundreds of bytes), stack (Ebp 0x6C2FF24→0x767FF24).
  - **Conclusion: the wild index is deterministic** — data-driven, not an address, not
    randomness; the same across FEX builds (depot and 2611.1) and across runs.
- **Method — context anchor (important for all dumps):** the BugSplat format (version 0xA793) is
  non-standard: the stream directory points RVAs into the directory itself → parse by content
  (content scan). x86 CONTEXT anchor: Eip at offset **176 (0xB0)** in the block, i.e.
  `context_start = eip_hit − 176`; the block is the standard layout (…Ebp, Eip, SegCs, EFlags,
  Esp, SegSs). The old table (Eip=192) was shifted by 16 B: the "SegDs slot" is actually the true
  **Edi**. Check: SegCs=0x23, EFlags=0x212, Esp in the wine region, SegSs=0x2B.
- **Conclusions:** (1) pre-registration (1) "the crash 0xA99EE7 will not return" — **REFUTED**.
  (2) The E-15 "fix" = **flow illusion**: without a client the game exits early (login-fail path,
  `SteamAPI_Init() failed`) and does not reach the crash code; with a live SteamAPI the RLink
  registration runs → wild index → AV. (3) **H1 demoted** (the FEX build is not the variable of
  the with-client crash; the exception path affects only the no-client flow), **H2 promoted to
  primary** (root cause — a deterministic OOB in the game code), **H11** = the splitting
  mechanism.
- Artifacts: dump `SpaceMarineS8IH0NM4.dmp` (prefix 55150, Temp), compat_log /
  gameprocess_log / console_log (20:23–20:24).

### Dump analysis (Q84D1V03 vs S8IH0NM4) — parser `scripts/mdmp.py`

Parser: strict 32-bit CONTEXT signature (anchor Eip@176; SegDs/SegEs/SegSs=0x2b, SegCs=0x23,
EFlags with IF) — finds exactly 2 copies of the crash context in each dump (the same registers,
saved twice). The dump has **no stack/heap memory** (only EXCEPTION_RECORD + CONTEXT + module
names + strings) → the index origin cannot be extracted from the dump; the exe disassembly is
needed (E-17).

EXCEPTION_RECORD (both): `code=0xC0000005 (AV) addr=0xA99EE7 n=2 params=[0x1, fault-addr]`
→ a **WRITE** fault (param0=1) at param1.

Exact diff (the register set is self-consistent in both dumps):

| Reg | OLD Q84D1V03 | NEW S8IH0NM4 | Δ |
|---|---|---|---|
| Eip | `0xA99EE7` | `0xA99EE7` | = |
| **Edi (index)** | **`0x31C33396`** | **`0x31C33396`** | **= (not a single bit changed)** |
| Ecx (value) | `0x2A` | `0x2A` | = |
| Edx | `0x900` | `0x900` | = |
| Eax (base) | `0x10633690` | `0x106332A0` | **−0x3F0** |
| Esi | `0x106333D0` | `0x10632FE0` | **−0x3F0** |
| Ebx | `0x106349A0` | `0x106345B0` | **−0x3F0** |
| Ebp | `0x6C2FF24` | `0x767FF24` | +0xAA0000 (stack) |
| Esp | `0x6C2FED8` | `0x767FED8` | +0xAA0000 (stack) |

**Formula check (Python, exact match in both):** `fault-addr = Eax + Edi·4` (displacement = 0).
OLD: 0x10633690+0x31C33396·4 = `0xD77004E8` (= AV param1 ✓). NEW: 0x106332A0+0x31C33396·4 =
`0xD77000F8` (✓).
→ The crashing instruction = **`mov [eax+edi*4], ecx`** (store `0x2A`), thunk `A99ED8`.

**Conclusions from the diff:**

1. **The wild index `0x31C33396` is deterministic and NOT address-derived**: the buffer area
   (Eax/Esi/Ebx) shifted by exactly **−0x3F0** (heap layout), the stack by +0xAA0000, while the
   index is **unchanged**. If the index were derived from a pointer/address, it would shift with
   the buffer. → it is a pure **data value** (a hash output of some input).
2. The index does not depend on the FEX build (depot vs 2611.1) or on the run — always the same.
3. Crash thread TID: in OLD = `0x208` (520), bucket = 520%37 = **2** (per the earlier analysis).
   In NEW it cannot be reliably extracted (non-standard stream directory); a 0x208 pattern near
   the new dump's context is not confirmed. → the bucket may have shifted, but that is secondary.

## Session 2026-11-17: full crash mechanics + env tests + E-17 patch

### PE VA map (authoritative; objdump prints VMA = ImageBase + RVA)

- `pe_off=0x150`, `SizeOfOptionalHeader=0xE0`, **Characteristics=0x123 — the LAA bit (0x20) is
  SET** (32-bit exe, 4 GB).
- Sections → file offsets (VMA → file):
  - `.text` VA `0x401000..0x1537A60` → `file = VA − 0x400C00`
  - `.rdata` VA `0x153A000..` → `file = VA − 0x401000`
  - `.data` VA `0x1964000..0x1BCB600` → `file = VA − 0x401600`
  - BSS `0x1BCB600..0x2097C10` (occupies no space in the file)
- ⚠ The PE section table stores RVAs; objdump output shows VMAs. Mixing them → a 0x400000 shift.

### Full counter mechanics (decoded from the file, all links verified)

- **Counter-manager singleton** @ VA `0x1D417E8` (getter `A99DE0`), allocation 0x128:
  - `+0x34` ptr → dword val array (heap); `+0x38..+0x57` inline dword ts (8 slots)
  - `+0x58` ptr → float val array; `+0x5C..+0x83` inline float ts (10 slots)
  - → **8 dword + 10 float counters** (confirmed by the size of the inline ts blocks).
- **Dispatcher `A9A240`** → 18 wrappers; wrapper k → IDX task k → store `val[idx]/ts[idx]`.
- **IDX tasks** `A99BB0 + k*0x20` (k=0..17). Two layouts:
  - **dword k=0..7 — 6 B:** `E8 rel32 C3` (call IDX_k; ret)
  - **float k=8..17 — 13 B:** `E8 rel32 | 89 44 24 FC | D9 44 24 FC | C3`
    (call IDX_k; mov [esp−4],eax; fld [esp−4]; ret → the result is in **ST0**, not EAX;
    `[esp−4]` is a safe scratch below the return address; the caller does not re-read it)
- IDX functions return float bits in **EAX** (the `mov esi,eax; ret` epilogue verified for all) —
  the float task converts EAX→ST0 via spill.
- **dword wrappers** `A99F7A + k*0x1E`: `call IDX; mov esi,eax; dec esi` → `idx = ret−1`.
- **float wrappers** `A9A06A + j*0x2F` (j=k−8): `call IDX-task; fsub [0x17FC074](=1.0f);
  cvttss2si` → `idx = (int)(float−1.0)`.
- **All 18 wrapper→thunk references verified against the file bytes.**
- **fnptr tables** (file-verified):
  - dword: VA `0x19B8828` (file `0x15B7228`): 18 entries `[i] = wrapper_i`
  - float: VA `0x19B8848` (file `0x15B7248`): 10 entries `[j] = wrapper_{8+j}`
    (`[10]=0x171C8FC` unused, `[11]=0`)
  - readers: dword — literal idx 3..6; float — literal idx 0..3; refresh through the tables →
    self-consistency requires wrapper k to fill **its own** slot.
- **IDX target map (corrected)** — in the early sketch the pairs (2,6)/(3,5) were swapped; the
  file is authoritative:

  | k | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | VA | F3CAE0 | F3C860 | F3C5E0 | F3C380 | F3C100 | F3BE80 | F3BC20 | F3BA60 | F3CD40 | F3D030 | F3D1F0 | F3D3B0 | F3D550 | F3D710 | F3D8D0 | F3DA70 | F3DC30 | F3DDF0 |

- **imm for the patch:** dword k=0..7 → `k+1` (1..8); float k=8..17 → `(k−7).0f`
  = 1.0f..10.0f (bits `0x3F800000 … 0x41200000`).

### Env tests (all with a live client, RLink markers in the log, guest environ sampled)

Recipe: `scripts/envtest.sh <tag> <ENVVAR=VAL…>` (steam-launch-wrapper + reaper +
`_v2-entry-point` + proton waitforexitandrun; the sampler `/tmp/opencode/sample_game.py` reads
`/proc/<pid>/{environ,maps}` of the live guest).

| Test | Env (proven in the guest) | Crash | Index | Dump |
|---|---|---|---|---|
| T1 (00:43) | `PROTON_FORCE_LARGE_ADDRESS_AWARE=0` + `WINE_LARGE_ADDRESS_AWARE=0` | `A99EE7` | **`0x31C33396`** | `SpaceMarine8TU51TQ0.dmp` |
| T1b (01:22) | same as T1 (repeat + sampler) | `A99EE7` | **`0x31C33396`** | `SpaceMarineT7N50555.dmp` |
| T2 (01:26) | `GLIBC_TUNABLES=glibc.malloc.perturb=255`, `_NO_DEBUG_HEAP=1` | `A99EE7` | **`0x31C33396`** | `SpaceMarinePPHL14H1.dmp` |
| T3 (01:33) | `PROTON_NO_ESYNC=1 PROTON_NO_FSYNC=1` | `A99EE7` | **`0x31C33396`** | `SpaceMarineNTML5611.dmp` |

- **Context:** the heap registers `Eip=A99EE7 Edi=0x31C33396 Ecx=0x2A Edx=0x900
  Eax=0x10633690 Esi=0x106333D0 Ebx=0x106349A0` are **byte-identical in all 4 runs** (T1, T1b, T2,
  T3); fault = `Eax + idx·4 = 0xD77004E8` (exact match with EXCEPTION_RECORD). The stack base
  varies from run to run (T1–T2: Esp=0x0827FED8, T3: Esp=0x60DFED8) — normal stack placement
  nondeterminism, carries no meaning.
- **T3 guest environ sample:** `PROTON_NO_ESYNC=1 | PROTON_NO_FSYNC=1` present, `WINEFSYNC`
  **absent** (the default `=1` removed — NO_FSYNC worked; NO_ESYNC is not handled by this Proton
  at all — esync is off anyway, ntsync runs).
- The E-16 baseline differs (Eax 0x106332A0, Esp 0x767FED8, fault 0xD77000F8) — heap shift −0x3F0
  and stack; the index is identical. The reason for the difference vs E-16 is not LAA (see
  below); it is not material for the conclusions.

### LAA: what the flag does and why it has no effect

- **`PROTON_FORCE_LARGE_ADDRESS_AWARE=0` is a no-op in this Proton (11.0 ARM64):**
  `check_environment` with `=0` only does `discard("forcelgadd")`; `"noforcelgadd"` is set only
  from compat_config for three specific appids (not 55150) → `WINE_LARGE_ADDRESS_AWARE` is not
  set at all → wine looks at the PE LAA bit (set) → **still 4 GB**. The real lever is
  `WINE_LARGE_ADDRESS_AWARE=0` directly (read only in `ntdll.so`).
- **But there is no real lever either:** T1b (LAA=0) and T2 (LAA=1) give a **byte-identical**
  context → `WINE_LARGE_ADDRESS_AWARE` **does not affect the guest layout in FEX wow64**: the
  32-bit guest arena is always mapped up to `0xFFFF4000` (maps: `max_low4G_end=0xffff4000`,
  4 GB — with both `=1` and `=0`). FEX wow64 does not implement a 2-GB mode based on the LAA flag.
- Conclusion: the LAA axis is exhausted; 2 GB on this stack is unreachable via env variables.

### Sync: what the flags do

- `PROTON_NO_ESYNC=1` — **not handled** by this Proton script at all (esync is off by default
  here; ntsync runs).
- `PROTON_NO_FSYNC=1` — the only real effect: `env.pop("WINEFSYNC")` (the script's default is
  `WINEFSYNC=1`).
- The crash is deterministic (one index in 6+6 runs) → it does not look like a race; T3 is a
  control.

### Additional variable search (11-17, after T3)

**Excluded with evidence (do not test):**

| Variable | Why it is not needed |
|---|---|
| `WINEDLLOVERRIDES="msvcrt=n,b"` | The game directory has **no** `msvcrt.dll` (ls + load log: the guest loads the builtin msvcrt, FEX hash `2361ef…`). There is nothing to switch to — no-op |
| `PROTON_NO_ESYNC` / `WINEESYNC` | This wine build has **no esync** (0 esync strings in ntdll.so/wineserver) |
| `WINE_NTSYNC=0` | The build does not read it; the sync choice is in `wineserver` (`/dev/ntsync`, `WINEFSYNC`, `PROTON_NO_NTSYNC`) |
| `PROTON_USE_NTSYNC` | A Proton-CachyOS variable; our script does not handle it (not a single ntsync line in the script) |
| `FEX_TSOENABLED`/`FEX_MULTIBLOCK`/… | The same axis as the TSO×MB config matrix (E-05/07/09) — nothing fixes it; `FEX_X87REDUCEDPRECISION` is already =1 in the profile |
| glibc tunables (mmap_threshold etc.) | The index is data-driven, not heap-derived (T2) — changing the heap layout is pointless |

**New findings:**

- `wineserver` reads **`PROTON_NO_NTSYNC`**: the sync logic — `WINEFSYNC=1` → fsync (default;
  T1–T2); otherwise `/dev/ntsync` → ntsync (T3); `PROTON_NO_NTSYNC=1` → "wineserver: using
  server-side synchronization." (no fast sync at all) → **T5**.
- FEX: the env variables `FEX_<OPTION>` = the same config keys (full list: `man FEX`,
  ENVIRONMENT). Plausible untested ones: `FEX_SOFTWARERNG` (RDRAND→software; unlikely — the index
  is deterministic, no RNG input visible), `FEX_STARTUPSLEEP` (startup delay → thread order;
  unlikely — the order is deterministic anyway).
- **The CRT axis was closed in advance:** `redist/vcredist_x86.exe` = VC++ 2005 SP1 (the game
  imports MSVCR90/MSVCP90). Installed into the prefix (winsxs
  `x86_microsoft.vc90.crt_…9.0.30729.6161`), BUT: `syswow64/msvcr90.dll` (what the 32-bit guest
  loads) is **already byte-for-byte the same real MSVC9 SP1** (md5 `51120407…` = the file from
  vcredist; wine bundles the original MS binaries for VC9). `system32/msvcr90.dll` is the ARM64
  variant (machine 0xAA64) for 64-bit guests and does not affect the 32-bit guest. A T4 run would
  be uninformative — the DLLs would not change.

**New tests:**

| Test | Env | Axis | Result |
|---|---|---|---|
| T5 (10:07) | `PROTON_NO_NTSYNC=1 PROTON_NO_FSYNC=1` | sync: server-side (no fast sync) | **`wineserver: using server-side synchronization.`** (the switch confirmed in the log). The same crash `A99EE7`, **the same index `0x31C33396`**, heap context byte-identical (dump `SpaceMarineJKSP03D2.dmp`, fault 0xD77004E8 = Eax+idx·4). **The whole sync matrix is exhausted: fsync (T1–T2) → ntsync (T3) → server-side (T5) — identical** |
| T6 (10:19) | (none) + **fresh prefix** (`PFX=/var/home/armada/opencode/compatdata-fresh/55150`) | prefix state (registry/"steamuser" profile) | **THE SAME CRASH (`A99EE7`), BUT A DIFFERENT INDEX: `Edi=0x71F3EFCF`** (the live prefix always gives `0x31C33396`). Also: `Ecx=0xEE82005C` (the store value; `0x2A` in the live prefix) — i.e. the "value" function is environment-dependent too. The heap registers (Eax/Esi/Ebx) are **byte-identical** to all previous runs; `Edx=0x900` the same; fault `0xD832F5CC = Eax+idx·4` ✓. Dump `SpaceMarineS4G547N4.dmp` (80 492 B, in the fresh prefix's Temp). **Conclusion: the index is NOT one fixed constant but a function of environment state (prefix/TID state); deterministic within a given environment. Neither LAA, nor heap, nor sync, nor a fresh prefix yields an in-range index → the env axis is exhausted; the reliable fix = patching the tasks (E-17)** |

### E-17: binary patch `SpaceMarine.exe` — P-B2 (script ready, dry-run green)

`scripts/patch_e17.py` (dry-run / `--apply` / `--restore`; `.orig` = pristine md5
`786fd60984609e8ad3fb46c4d0befe7f`):

- Essence: instead of computing the wild hash, each of the 18 IDX tasks returns the **constant**
  of its slot → `idx` is always correct (0..17) → stores go to their own slots, the OOB
  disappears. The game does not mask the index — a latent UB in its code; on Windows the
  "correct" hash returns slot+1, on our side it returns a wild 32-bit hash.
- **dword sites** `A99BB0..A99C90 step 0x20`: `E8 rel32 C3` → `B8 imm32 C3`
  (imm = k+1 = 1..8; the wrapper does `dec esi` → idx = k).
- **float sites** `A99CB0..A99DD0 step 0x20`: **only the first 5 B** change
  `E8 rel32` → `B8 imm32` (imm = bits of (k−7).0f); spill+fld+ret are preserved
  (the result is still in ST0, callers untouched).
- Dry-run asserts (all passed): md5 of exe and `.orig` == pristine; the first byte of the site =
  E8; rel32 → the target IDX function (table above); the tail (C3 / 89 44 24 FC D9 44 24 FC C3).
- An alternative without touching the exe (if the user objects): a preload-DLL shim
  (a 32-bit x86 DLL that patches the same 18 tasks on PROCESS_ATTACH; the exe stays
  byte-identical).

### Session artifacts

- `scripts/patch_e17.py`, `scripts/envtest.sh` (repo); `/tmp/opencode/sample_game.py`
  (sampler, volatile), logs `/tmp/opencode/envtest{1,1b,2,3}*.log`, samples
  `/tmp/opencode/sample-t{1,2,3}.txt`.
- Dumps: `SpaceMarine8TU51TQ0` (T1), `SpaceMarineT7N50555` (T1b), `SpaceMarinePPHL14H1` (T2) —
  Temp of prefix 55150.

### Next steps (updated 11-17, T1–T6 closed)

0. ✅ **T3 (01:33):** the same crash/index; `WINEFSYNC` absent in the guest (NO_FSYNC worked).
   ✅ **T5 (10:07):** server-side sync (confirmed in the log) — the same crash/index.
   ✅ **T6 (10:19):** fresh prefix + client — **the same crash, a DIFFERENT index `0x71F3EFCF`**
   (and a different value `Ecx=0xEE82005C`), heap byte-identical → the index is
   environment-dependent (prefix/TID), in-range is unreachable by tuning. **The env axis is
   exhausted (T1–T6 + variable search: msvcrt/esync/ntsync/CRT/FEX — everything excluded with
   evidence).**
1. **User decision (2026-11-17): stop.** The E-17 P-B2 patch is ready (dry-run green) but NOT
   applied; the game stays broken through the Steam client. If the decision changes:
   `python3 scripts/patch_e17.py --apply` (asserts run; `.orig` untouched) → launch through the
   Steam path with a live client (kill BsSndRpt first) → expect to pass the 32-s point → menu.
   Alternatives: a preload-DLL shim (the exe stays byte-identical).
