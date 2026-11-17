#!/usr/bin/env python3
"""Find RLink strings in SpaceMarine.exe + xrefs to them in .text (32-bit absolute refs)."""
import struct, sys

EXE = "/var/home/armada/.local/share/Steam/steamapps/common/Warhammer 40,000 Space Marine/SpaceMarine.exe"
data = open(EXE, "rb").read()

# --- PE parse ---
pe_off = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe_off:pe_off+4] == b"PE\0\0"
nsec, = struct.unpack_from("<H", data, pe_off+6)
opt_off = pe_off + 24
magic, = struct.unpack_from("<H", data, opt_off)
is64 = magic == 0x20B
optsize, = struct.unpack_from("<H", data, pe_off+20)
sec_off = opt_off + optsize
imgbase, = struct.unpack_from("<I", data, opt_off + (32 if is64 else 28))
sections = []
for i in range(nsec):
    o = sec_off + i*40
    name = data[o:o+8].rstrip(b"\0").decode("latin-1")
    vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, o+8)
    sections.append((name, vaddr, vsize, raddr, rsize))
print(f"ImageBase=0x{imgbase:X} sections:")
for s in sections: print(f"  {s[0]:8} VA=0x{s[1]:08X} vsize=0x{s[2]:06X} raddr=0x{s[3]:08X}")

def va2off(va):
    for name, vaddr, vsize, raddr, rsize in sections:
        if vaddr <= va < vaddr + max(vsize, rsize):
            return raddr + (va - vaddr)
    return None

# --- find strings (try ASCII + UTF-16LE) ---
targets = ["Found %d profiles for account %ws", "Found profile: %ws",
           "unknown steam user", "GenerateToken", "GetAuthToken state ID",
           "SteamAPI_Init() failed", "Found 0 profiles", "WorldwideLoginService"]
found = {}
for t in targets:
    enc_hits = []
    for enc in ("latin-1", "utf-16-le"):
        pat = t.encode(enc)
        off = 0
        while True:
            off = data.find(pat, off)
            if off < 0: break
            va = None
            for name, vaddr, vsize, raddr, rsize in sections:
                if raddr <= off < raddr + rsize:
                    va = vaddr + (off - raddr)
            enc_hits.append((off, va, enc))
            off += 1
    if not enc_hits:
        print(f"  NOT FOUND: {t!r}")
        continue
    for off, va, enc in enc_hits[:3]:
        print(f"  {t!r} [{enc}]: fileoff=0x{off:X} VA=0x{va:X}")
    found[t] = (enc_hits[0][0], enc_hits[0][1])

# --- scan .text for absolute refs to each target VA ---
text = [s for s in sections if s[0] == ".text"]
if not text:
    print("NO .text section?!"); sys.exit(1)
name, vaddr, vsize, raddr, rsize = text[0]
tdata = data[raddr:raddr+rsize]
print(f"\nScanning .text ({len(tdata)} bytes) for absolute refs...")
for t, (off, va) in found.items():
    if va is None: continue
    pat = struct.pack("<I", va)
    refs = []
    start = 0
    while True:
        i = tdata.find(pat, start)
        if i < 0: break
        refs.append(vaddr + i)
        start = i + 1
    print(f"  {t!r} (VA 0x{va:X}): {len(refs)} refs -> " + ", ".join(f"0x{r:X}" for r in refs[:8]))
