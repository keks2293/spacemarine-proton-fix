#!/usr/bin/env python3
"""Parser for BugSplat minidumps (MDMP version 0xA793), 32-bit x86 guest.

The format is custom (SpaceMarine.exe + BugSplat.dll): the stream directory is
NON-STANDARD (the RVAs point into the directory itself / are duplicated), so the
contexts are recovered by a content scan against the STRICT signature of the
standard Windows CONTEXT.

Key fact (anchor): in the Windows CONTEXT the register block is at offset 0x84,
Eip at offset 0xB0 (176) from the start of CONTEXT. Block layout (relative to Eip):
    SegGs = Eip-44, SegFs = Eip-40, SegEs = Eip-36, SegDs = Eip-32,
    Edi = Eip-28, Esi = Eip-24, Ebx = Eip-20, Edx = Eip-16,
    Ecx = Eip-12, Eax = Eip-8, Ebp = Eip-4, Eip = Eip,
    SegCs = Eip+4, EFlags = Eip+8, Esp = Eip+12, SegSs = Eip+16.

Strict signature (anchor = position p of the Eip value):
    u32(p-32) == 0x2b          (SegDs)
    u32(p-36) == 0x2b          (SegEs)
    u32(p+4)  == 0x23          (SegCs)
    u32(p+16) == 0x2b          (SegSs)
    u32(p+8)  & 0x200          (EFlags: IF)
    u32(p)     = code address   (Eip)
    u32(p+12)  = stack address  (Esp)
This filters out almost all noise: there is one real guest CONTEXT in a dump.

Usage:
  python3 mdmp.py <dump.dmp>
  python3 mdmp.py <dump.dmp> --eip 0xA99EE7   # only contexts with this Eip
  python3 mdmp.py <dump.dmp> --dump 0xf8c4    # parse the context from its start
"""
import re
import struct
import sys

def u32(d, o): return struct.unpack_from("<I", d, o)[0]

# offsets from context_start (eip_hit - 176) — standard Windows CONTEXT
CTX = {
    "SegGs": 132, "SegFs": 136, "SegEs": 140, "SegDs": 144,
    "Edi": 148, "Esi": 152, "Ebx": 156, "Edx": 160, "Ecx": 164, "Eax": 168,
    "Ebp": 172, "Eip": 176, "SegCs": 180, "EFlags": 184, "Esp": 188,
    "SegSs": 192,
}
REG_ORDER = ["Eip", "Eax", "Ecx", "Edx", "Ebx", "Esp", "Ebp", "Esi", "Edi",
             "EFlags", "SegGs", "SegFs", "SegEs", "SegDs", "SegCs", "SegSs"]

EXC_CODES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xE06D7363: "MSVC_CXX_EXCEPTION",
    0x80000003: "EXCEPTION_BREAKPOINT",
    0x40010005: "WINE_AV",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000008: "DAV",
    0x00000896: "STATUS_HEAP_CORRUPTION",
}

def is_code(v):
    return (0x400000 <= v < 0x2000000) or (0x700000 <= v < 0x800000) \
        or (0x10000000 <= v < 0x30000000)

def is_stack(v):
    return 0x40000 <= v < 0x90000000

def read_ctx(d, start):
    if start < 0 or start + 196 > len(d):
        return None
    r = {n: u32(d, start + o) for n, o in CTX.items()}
    return r

def check_sig(d, p):
    """Strict signature with anchor p (the Eip position). Returns True/False."""
    if p < 44 or p + 16 >= len(d):
        return False
    if u32(d, p - 32) != 0x2b:   # SegDs
        return False
    if u32(d, p - 36) != 0x2b:   # SegEs
        return False
    if u32(d, p + 4) != 0x23:    # SegCs
        return False
    if u32(d, p + 16) != 0x2b:   # SegSs
        return False
    fl = u32(d, p + 8)
    if not (fl & 0x200) or fl >= 0x100000:  # EFlags: IF set, sane range
        return False
    if not is_code(u32(d, p)):              # Eip
        return False
    if not is_stack(u32(d, p + 12)):        # Esp
        return False
    return True

def scan_contexts(d, only_eip=None):
    found = {}
    for p in range(44, len(d) - 16, 4):
        if only_eip is not None and u32(d, p) != only_eip:
            continue
        if not check_sig(d, p):
            continue
        start = p - 176
        r = read_ctx(d, start)
        found[p] = (start, r)
    return found

def fmt_regs(r):
    return " ".join(f"{k}={r[k]:#x}" for k in REG_ORDER)

def scan_exc_records(d):
    out = []
    for off in range(0, len(d) - 24, 4):
        code = u32(d, off)
        if code not in EXC_CODES:
            continue
        addr = u32(d, off + 12)
        nparams = u32(d, off + 16)
        if nparams > 15:
            continue
        if not (is_code(addr) or addr == 0):
            continue
        params = [u32(d, off + 20 + i * 4) for i in range(min(nparams, 6))]
        out.append((off, code, addr, nparams, params))
    return out

def scan_modules(d):
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{3,64}\.(?:exe|dll|so)\b", d):
        name = m.group(0).decode("latin-1")
        s, e = m.start(), m.end()
        out.append((s, name, d[max(0, s - 16):s], d[e:e + 16]))
    return out

def scan_stacks(d):
    """Runs of >=4 consecutive exe-range code pointers (return addresses)."""
    runs = []
    cur = []
    for off in range(0, len(d) - 4, 4):
        v = u32(d, off)
        if 0x400000 <= v < 0x2000000:
            cur.append((off, v))
        else:
            if len(cur) >= 4:
                runs.append(cur)
            cur = []
    if len(cur) >= 4:
        runs.append(cur)
    return runs

def main():
    args = [a for a in sys.argv[1:]]
    only_eip = None
    dump_start = None
    if "--eip" in args:
        i = args.index("--eip"); only_eip = int(args[i + 1], 16); del args[i:i + 2]
    if "--dump" in args:
        i = args.index("--dump"); dump_start = int(args[i + 1], 16); del args[i:i + 2]
    path = args[0]
    d = open(path, "rb").read()
    print(f"=== {path}: {len(d)} B")
    assert d[:4] == b"MDMP", "not a minidump"
    version, nstreams, dir_rva = u32(d, 4), u32(d, 8), u32(d, 12)
    print(f"version=0x{version:x} streams={nstreams} dir_rva=0x{dir_rva:x}")

    if dump_start is not None:
        r = read_ctx(d, dump_start)
        if r:
            print(f"\n=== context from start 0x{dump_start:x} (eip_hit@0x{dump_start+176:x}):")
            print("  " + fmt_regs(r))
        return

    print("\n=== CONTEXT (strict signature):")
    ctxs = scan_contexts(d, only_eip)
    if not ctxs:
        print("  (not found)")
    for p in sorted(ctxs):
        start, r = ctxs[p]
        print(f"  ctx@0x{start:x} (eip_hit@0x{p:x}): {fmt_regs(r)}")

    print("\n=== EXCEPTION_RECORD scan:")
    recs = scan_exc_records(d)
    if not recs:
        print("  (not found)")
    for off, code, addr, nparams, params in recs:
        print(f"  @0x{off:x}: code=0x{code:x} ({EXC_CODES[code]}) addr=0x{addr:x} "
              f"n={nparams} params={['%#x' % p for p in params]}")

    print("\n=== Modules (name strings):")
    for s, name, pre, post in scan_modules(d):
        print(f"  @0x{s:x}: {name!r}  pre={' '.join('%02x' % b for b in pre)}  "
              f"post={' '.join('%02x' % b for b in post)}")

    print("\n=== Stacks (>=4 consecutive exe code addresses):")
    runs = scan_stacks(d)
    if not runs:
        print("  (not found — the dump has no stack memory)")
    for run in runs[:15]:
        vals = " ".join(f"{v:#x}" for _, v in run[:16])
        more = " ..." if len(run) > 16 else ""
        print(f"  @0x{run[0][0]:x} len={len(run)}: {vals}{more}")

if __name__ == "__main__":
    main()
