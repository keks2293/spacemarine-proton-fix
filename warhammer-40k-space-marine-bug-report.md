# [BUG] Warhammer 40,000: Space Marine (55150): deterministic crash under Proton 11.0 (ARM64) + FEX wow64

## Device / software

| Item | Value |
|---|---|
| Device | Retroid Pocket 6, Rockchip RK3566 (Cortex-A55/A72), aarch64, 8 cores, 6 GB RAM |
| OS | Armada OS 20261109.dfed5d6 (Fedora 44, kernel 7.2.6) |
| Proton tool | "Proton 11.0 (ARM64)" (Steam appid 4628740) — proton-cachyos branch `cachyos_11.0_20261001/main` |
| Guest architecture | 32-bit x86 via wow64 (`libwow64fex.dll`, 5,079,040 B aarch64 Windows PE; FEX-Emu/FEX @ `0df84d3844`, 2026-09-28, between FEX-2608 and FEX-2609) |
| Game | Warhammer 40,000: Space Marine (55150), 32-bit x86, Relic engine, D3D9/d3dx9_37 → Wine d3d9 → DXVK → Turnip |
| Works on | x86 PCs, including Steam Deck with Proton 8 — the crash is specific to the ARM64 + FEX wow64 stack |

## Repro

1. Launch the game (32-bit) through the Proton tool above **with the Steam client running**.
2. The game boots fully: engine starts, window created, DXVK/Turnip initialized, Steamworks/RLink starts.
3. ~32 s after start — **3–4 s after** `RLink::...::ThreadFunction - Entering network thread function`
   (the online services are dead, login fails) — the game process dies silently.
   **100% reproducible, identical signature in every run** — 6/6 baseline runs (2 OS versions,
   6 FEX/config variants) and all T1–T6 environment tests.

Without a running Steam client the game does **not** crash: `SteamAPI_Init() failed` →
`GenerateToken - failed, unknown steam user` → `Found 0 profiles for account ` → clean exit (rc=0).
The crash requires the registration path, i.e. a live SteamAPI.

## Crash signature (identical in all minidumps)

```
EXCEPTION_ACCESS_VIOLATION (0xC0000005), write (param0=1)
  faulting instruction: 0xA99EE7   mov [eax+edi*4], ecx
  fault address        = eax + edi*4
```

| Run | Eip | Index (Edi) | Value (Ecx) | Eax | Fault address | Dump |
|---|---|---|---|---|---|---|
| Live prefix | `0xA99EE7` | `0x31C33396` | `0x2A` | `0x10633690` | `0xD77004E8` | `SpaceMarine2SR90OI3.dmp` … |
| Fresh prefix | `0xA99EE7` | `0x71F3EFCF` | `0xEE82005C` | `0x106332A0` | `0xD832F5CC` | `SpaceMarineS4G547N4.dmp` |

The heap registers are byte-identical across runs in the same prefix; the stack base varies
(stack placement nondeterminism). The index is deterministic within one environment and depends on
the environment (prefix/TID state), but is **always** wild — no tested environment yields an
in-range index.

## Root cause

The game's counter subsystem stores 8 dword + 10 float counters behind 18 wrapper functions
(dispatcher `0xA9A240` → IDX tasks `0xA99BB0 + k*0x20`). Each task computes a 32-bit hash and
returns it as the counter index; the wrappers only do `dec esi` (dword) or
`(int)(float − 1.0f)` (float) — **there is no mask and no bounds check**, and the callers do not
validate the result either. On Windows/x86 the hash happens to return `slot+1`; under FEX wow64 it
returns an arbitrary 32-bit value, so `val[idx] = value` is a wild write.

The store value is environment-dependent as well (`0x2A` in the live prefix, `0xEE82005C` in a
fresh prefix), i.e. the hash input comes from prefix/thread state, not from a constant.

Full mechanics (18 tasks, wrapper layouts, fnptr tables, PE VA map, IDX map): see the companion
investigation document `warhammer-40k-space-marine.md` in this repository.

## Why this is not an FEX build regression

- The game runs on x86 (Steam Deck, Proton 8) — there is no FEX in that path at all.
- On this device the FEX build is not the variable: FEX-2611.1 "fixed" the crash only in the
  no-client flow (the game exits before reaching the crashing code); with a live client the same
  crash returns at the same instruction with the same index.
- The whole TSO × Multiblock matrix (1/1, 1/0, 0/0, 0/1) crashes identically.
- FEX logging (`SilentLog: "0"`) shows **zero `A ` (assert) and `E ` (error) lines** before the
  fault; `D E0 Call-ret stack inbalance` appears in the game process shortly before death, but the
  crash reproduces across FEX versions with the same signature, so it is a symptom, not the cause.

## Environment axes tested (all negative)

| Axis | Test | Result |
|---|---|---|
| Large address aware | `PROTON_FORCE_LARGE_ADDRESS_AWARE=0` + `WINE_LARGE_ADDRESS_AWARE=0` | same crash, same index |
| Heap layout | `GLIBC_TUNABLES=glibc.malloc.perturb=255`, `_NO_DEBUG_HEAP=1` | same crash, same index, byte-identical context |
| Sync | `PROTON_NO_ESYNC=1 PROTON_NO_FSYNC=1`; `PROTON_NO_NTSYNC=1 PROTON_NO_FSYNC=1` | same crash, same index (fsync → ntsync → server-side) |
| Prefix state | fresh prefix (`compatdata-fresh/55150`) | same crash, different index (`0x71F3EFCF`) |
| CRT | VC++ 2005 SP1 redist installed | no-op: `syswow64/msvcr90.dll` is already the real MSVC9 SP1 binary |
| Game files | exe vs `.orig`: md5 `786fd60984609e8ad3fb46c4d0befe7f`, `cmp` — 0 differences | the game runs on an untouched exe |

## Suggested actions

1. **Workaround (verified by dry run, not applied):** patch the 18 IDX tasks to return their slot
   constant — `scripts/patch_e17.py --apply` (`--restore` to roll back). The exe stays pristine
   (`.orig` untouched), the patch is reversible. An alternative that keeps the exe byte-identical is
   a 32-bit preload DLL that patches the same 18 sites on `PROCESS_ATTACH`.
2. **For the wow64/Proton side:** it would help to know whether the hash routine used by the game
   (counter subsystem, `0xA9A240` dispatcher) is expected to produce different results under FEX
   wow64 than on native x86 — the game's own missing bounds check is the latent defect, but the
   divergence in hash output is what exposes it.

## Attachments

- 14 minidumps (custom BugSplat layout, version 0xA793, 71–82 KB): `SpaceMarine2SR90OI3.dmp`,
  `SpaceMarine97353B42.dmp`, `SpaceMarineOK4T1EA5.dmp`, `SpaceMarine2BKL16I6.dmp`,
  `SpaceMarineDLTT48I0.dmp`, `SpaceMarineLF8D6CC4.dmp`, `SpaceMarineQ84D1V03.dmp`,
  `SpaceMarineS8IH0NM4.dmp`, `SpaceMarine8TU51TQ0.dmp`, `SpaceMarineT7N50555.dmp`,
  `SpaceMarinePPHL14H1.dmp`, `SpaceMarineNTML5611.dmp`, `SpaceMarineJKSP03D2.dmp`,
  `SpaceMarineS4G547N4.dmp`
- full `WINEDEBUG=warn+all` + FEX log (`SilentLog=0`) capture of a crash: `game-run4.log`
  (~9000 lines; FEX lines have the `L TID msg` format without a WINE prefix)
- `bugsplat.log` tail: "Entered Unhandled Exception Filter" 15:44:21 → minidump saved → BsSndRpt launched
- disassembly of the tasks/wrappers/readers: `results/disasm-*.txt`

## Ruled out

- OOM / kernel oom-killer (tight memory, no kills logged)
- GPU/Turnip fault (clean kernel log)
- Filesystem (repro on internal btrfs; exFAT is a separate, fixed prefix-creation issue)
- FEX profile (TSO/X87-precision/multiblock variants via FEX_APP_CONFIG — crash unchanged)
- DRM / modified exe (live `.text` capture is byte-identical to disk throughout the process)
- `Steam2.dll`/`lsteamclient.dll` load failures (structural; host is an arm64-native client; Wine
  builtin lsteamclient is the intended path)
