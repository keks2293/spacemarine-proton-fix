# Warhammer 40,000: Space Marine (55150) under Proton 11 (ARM64) + FEX — crash investigation

Warhammer 40,000: Space Marine (Steam appid 55150) launched with a live Steam client:
Steam → reaper → Proton 11.0 (ARM64) (`compatibilitytools.d/fex26111`) → wine host (ARM64) →
FEX wow64 (32-bit guest process). Deterministic crash (AV `A99EE7`) during RLink/Steam
registration.

**Status: investigation complete (2026-11-17); user decision — stop (do not apply the patch).**
Root cause (H2) confirmed: a deterministic out-of-bounds write in the game's own code — 18 counter
tasks compute a 32-bit hash index **without masking**; the hash input depends on the environment
(prefix/TID state), and in every environment tested (T1–T6) the index is wild, so the store
`val[idx]` is always out of bounds. The ready fix (E-17) lives in `scripts/patch_e17.py`
(dry-run green, not applied).

## Contents

| Path | What it is |
|---|---|
| `warhammer-40k-space-marine.md` | Main document: E-01…E-17, T1–T6, full crash mechanics (18 tasks/wrappers, fnptr tables, PE VA map, IDX map), env tests, variable search, pitfalls |
| `warhammer-40k-space-marine-bug-report.md` | Issue draft (Proton 11 ARM64 + FEX wow64) |
| `results/` | Disassembly of tasks/wrappers/readers; E-15 log (swap to FEX 2611.1) |
| `configs/Steam_55150_SpaceMarine.exe.json` | Per-game FEX config (copy in the repo; the symlink in `~/.config/fex-emu/AppConfig/` points here) |
| `config.vdf.bak-2008` | Steam `config.vdf` backup (artifact of the 11-15 session) |

### Scripts

| Script | Purpose |
|---|---|
| `envtest.sh <tag> [ENV=V …]` | Launch through the Steam path with environment variables + an environ/maps sampler of the live guest; `PFX` can be overridden from the environment |
| `mdmp.py` | Parser for the custom BugSplat dumps (version 0xA793, x86) |
| `find_rlink_refs.py` | Scans RLink strings and xrefs in `SpaceMarine.exe` |
| `patch_e17.py` | E-17: 18 IDX tasks → constant immediates; `--apply`/`--restore`, asserts on md5/rel32/tail; the exe stays pristine, `.orig` untouched |
| `launch-swap-test.sh` | Runs the game on a local FEX build (proton-patched tool) |

## Paths outside the repo

| Path | What it is |
|---|---|
| `~/.local/share/Steam/steamapps/common/Warhammer 40,000 Space Marine/` | The game (`SpaceMarine.exe` pristine, md5 `786fd60984609e8ad3fb46c4d0befe7f`, `.orig` in place) |
| `~/.local/share/Steam/steamapps/compatdata/55150` | Live prefix (dumps T1–T5) |
| `/var/home/armada/opencode/compatdata-fresh/55150` | Fresh prefix for T6 |
| `~/.local/share/Steam/compatibilitytools.d/fex26111` | FEX tool used by Steam |
| `../fex/` | FEX build and A/B tests (RE4/WNO) — sibling repo |

## If the patch is still needed

`python3 scripts/patch_e17.py --apply` (asserts run, `.orig` untouched; rollback —
`--restore`). Kill `BsSndRpt.exe` before launching, if it is alive.
