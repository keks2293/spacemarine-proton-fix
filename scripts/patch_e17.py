#!/usr/bin/env python3
"""E-17 patch for SpaceMarine.exe: P-B2 — fix of the 18 counter IDX tasks.

Root cause (H2): dispatcher A9A240 calls 18 wrappers; wrapper k calls IDX task k
(return = "slot+1", the wrapper subtracts 1), then stores val[idx]/ts[idx]. In our
environment the IDX functions (hash F65790 over .rdata data + caller RVA + TID
state) return a wild 32-bit hash (e.g. 0x31C33397 for counter 0) instead of the
expected small "slot+1" → idx = 0x31C33396 → a store ~3.3 GB past the array →
AV at A99EE7. The game does not mask the index (latent UB in the game code).

Patch: instead of computing the hash, each task returns a constant:
  - dword tasks k=0..7   @ VA 0xA99BB0 + k*0x20:
      E8 rel32 C3                      (call IDX_k; ret)
    → B8 imm32 C3                      (mov eax, k+1; ret)
      wrapper: mov esi,eax; dec esi → idx = k ✓
  - float tasks k=8..17  @ VA 0xA99CB0 + (k-8)*0x20 (13 bytes):
      E8 rel32 | 89 44 24 FC | D9 44 24 FC | C3
      (call IDX_k; mov [esp-4],eax; fld [esp-4]; ret → the result is in ST0)
    → B8 imm32 | 89 44 24 FC | D9 44 24 FC | C3   (only the first 5 B change)
      imm = bits of float (k-7).0f; wrapper: fsub 1.0f; cvttss2si → idx = k-8 ✓

Self-consistency: dword table A9B8828 [i]=wrapper_i (readers use literal
idx 3..6), float table A9B8848 [j]=wrapper_{8+j} (readers use idx 0..3) →
wrapper k must fill its own slot. The size of the ts blocks (+0x38: 8 dword,
+0x5C: 10 float) confirms 8+10 slots.

File offset for .text: file = VA - 0x400C00 (ImageBase 0x400000).
NOTE: the VA in the PE section table is an RVA; use objdump VMA only.

Usage:
  python3 patch_e17.py               # dry-run: verify the bytes, change nothing
  python3 patch_e17.py --apply       # apply in place (.orig is preserved)
  python3 patch_e17.py --restore     # restore the original exe from .orig
"""
import hashlib
import struct
import sys

EXE = "/var/home/armada/.local/share/Steam/steamapps/common/Warhammer 40,000 Space Marine/SpaceMarine.exe"
ORIG = EXE + ".orig"
PRISTINE_MD5 = "786fd60984609e8ad3fb46c4d0befe7f"
TEXT_OFF = 0x400C00  # file = VA - TEXT_OFF for .text

# IDX functions per counter (verified against the rel32 in the file;
# recomputed from the file on 11-17; the early sketch had the pairs (2,6)
# and (3,5) swapped — the file is authoritative)
IDX_TARGETS = {
    0: 0xF3CAE0, 1: 0xF3C860, 2: 0xF3C5E0, 3: 0xF3C380,
    4: 0xF3C100, 5: 0xF3BE80, 6: 0xF3BC20, 7: 0xF3BA60,
    8: 0xF3CD40, 9: 0xF3D030, 10: 0xF3D1F0, 11: 0xF3D3B0,
    12: 0xF3D550, 13: 0xF3D710, 14: 0xF3D8D0, 15: 0xF3DA70,
    16: 0xF3DC30, 17: 0xF3DDF0,
}


def va_file(va):
    assert 0x401000 <= va < 0x1537A60, f"VA {va:#x} outside .text"
    return va - TEXT_OFF


def sites():
    """(k, VA, old_bytes, new_bytes, comment)."""
    out = []
    for k in range(18):
        if k < 8:  # dword: 6 bytes E8 rel32 C3 → B8 imm32 C3
            va = 0xA99BB0 + k * 0x20
            imm = k + 1  # the wrapper does dec → idx = k
            imm_b = struct.pack("<I", imm)
            new_tail = b"\xc3"
            label = f"dword slot {k} (imm={imm})"
        else:      # float: 13 bytes, only the first 5 change
            va = 0xA99CB0 + (k - 8) * 0x20
            imm_b = struct.pack("<f", float(k - 7))  # (k-7).0f; fsub 1.0 → idx = k-8
            new_tail = b"\x89\x44\x24\xfc\xd9\x44\x24\xfc\xc3"
            label = f"float slot {k - 8} (imm={struct.unpack('<f', imm_b)[0]:.1f}f)"
        out.append((k, va, imm_b, new_tail, label))
    return out


def check(md5, apply=False):
    d = open(EXE, "rb").read()
    cur = hashlib.md5(d).hexdigest()
    if cur != PRISTINE_MD5:
        print(f"ERROR: exe md5 {cur} != pristine {PRISTINE_MD5}")
        print("  (the file is already patched or replaced — abort)")
        return 1
    o = open(ORIG, "rb").read()
    if hashlib.md5(o).hexdigest() != PRISTINE_MD5:
        print("ERROR: .orig md5 does not match the pristine one — abort (otherwise the original is lost)")
        return 1

    for k, va, imm_b, tail, label in sites():
        off = va_file(va)
        cur_b = d[off:off + 6]
        if cur_b[0] != 0xE8:
            print(f"ERROR: thunk {k} @ {va:#x}: first byte {cur_b[0]:#x} != E8")
            return 1
        rel = struct.unpack("<i", cur_b[1:5])[0]
        target = (va + 5 + rel) & 0xFFFFFFFF
        if target != IDX_TARGETS[k]:
            print(f"ERROR: thunk {k} @ {va:#x}: call {target:#x} != IDX {IDX_TARGETS[k]:#x}")
            return 1
        # tail: dword → C3; float → 89 44 24 FC (check the 13-byte pattern)
        exp_tail = b"\xc3" if k < 8 else b"\x89\x44\x24\xfc\xd9\x44\x24\xfc\xc3"
        if d[off + 5:off + 5 + len(exp_tail)] != exp_tail:
            print(f"ERROR: thunk {k} @ {va:#x}: tail {d[off+5:off+5+len(exp_tail)].hex()} != {exp_tail.hex()}")
            return 1
        new_b = b"\xb8" + imm_b + tail
        print(f"  thunk {k:2d} @ VA {va:#08x} (file {off:#08x})  {label}")
        print(f"    old: {' '.join('%02x' % c for c in d[off:off+len(new_b)])}")
        print(f"    new: {' '.join('%02x' % c for c in new_b)}")
        if apply:
            d = d[:off] + new_b + d[off + len(new_b):]
    if apply:
        with open(EXE, "wb") as f:
            f.write(d)
        newmd5 = hashlib.md5(d).hexdigest()
        print(f"\nAPPLIED. New md5: {newmd5}")
        print(f"Original preserved: {ORIG} (md5 {PRISTINE_MD5})")
        # read-back check
        chk = open(EXE, "rb").read()
        assert hashlib.md5(chk).hexdigest() == newmd5, "read-back mismatch!"
        assert len(chk) == len(d), "size changed!"
    return 0


def restore():
    o = open(ORIG, "rb").read()
    if hashlib.md5(o).hexdigest() != PRISTINE_MD5:
        print("ERROR: .orig md5 != pristine — refusing to restore")
        return 1
    d = open(EXE, "rb").read()
    print(f"Current md5:  {hashlib.md5(d).hexdigest()}")
    with open(EXE, "wb") as f:
        f.write(o)
    print(f"Restored from .orig, md5: {PRISTINE_MD5}")
    return 0


if __name__ == "__main__":
    if "--restore" in sys.argv:
        sys.exit(restore())
    sys.exit(check(PRISTINE_MD5, apply="--apply" in sys.argv))
