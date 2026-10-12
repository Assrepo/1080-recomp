#!/usr/bin/env python3
"""Tests für tools/ mit einem synthetischen ROM (benötigt clang + ld.lld + llvm-objcopy).

Aufruf: python3 tests/test_tools.py
"""
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import find_functions  # noqa: E402
import n64rom  # noqa: E402

VRAM = 0x80000400


def build_rom(tmp: Path) -> tuple[bytes, dict]:
    obj, elf, binf = tmp / "f.o", tmp / "f.elf", tmp / "f.bin"
    subprocess.run(["clang", "--target=mips-linux-gnu", "-march=mips3", "-mabi=32", "-mno-abicalls",
                    "-fno-pic", "-c", str(ROOT / "tests/fixture.s"), "-o", str(obj)], check=True)
    subprocess.run(["ld.lld", "-T", str(ROOT / "tests/fixture.ld"), str(obj), "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", "-j", ".text", "-j", ".data", str(elf), str(binf)], check=True)
    nm = subprocess.run(["llvm-nm", str(elf)], check=True, capture_output=True, text=True).stdout
    syms = {line.split()[2]: int(line.split()[0], 16) for line in nm.splitlines() if len(line.split()) == 3}

    rom = bytearray(0x101000 + 0x1000)
    rom[0:4] = n64rom.MAGIC_Z64
    struct.pack_into(">I", rom, 0x08, VRAM)
    rom[0x20:0x34] = b"1080 SNOWBOARDING   "
    rom[0x3B:0x3F] = b"NTEE"
    code = binf.read_bytes()
    rom[0x1000:0x1000 + len(code)] = code
    return bytes(rom), syms


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"):
            if not shutil.which(tool):
                raise unittest.SkipTest(f"{tool} fehlt")
        cls.tmp = Path(tempfile.mkdtemp())
        cls.rom, cls.syms = build_rom(cls.tmp)

    def test_byteorder_roundtrip(self):
        rom = self.rom
        v64 = bytearray(len(rom))
        v64[0::2], v64[1::2] = rom[1::2], rom[0::2]
        n64 = struct.pack(f"<{len(rom) // 4}I", *struct.unpack(f">{len(rom) // 4}I", rom))
        self.assertEqual(n64rom.detect_format(bytes(v64)), "v64")
        self.assertEqual(n64rom.detect_format(n64), "n64")
        self.assertEqual(n64rom.to_z64(bytes(v64)), rom)
        self.assertEqual(n64rom.to_z64(n64), rom)

    def test_header(self):
        h = n64rom.parse_header(self.rom)
        self.assertEqual(h["entrypoint"], VRAM)
        self.assertEqual(h["game_code"], "NTEE")

    def test_function_discovery(self):
        seg = find_functions.Segment(self.rom, 0x1000, VRAM, 0x100000)
        an = find_functions.Analyzer(seg)
        an.run([VRAM])
        s = self.syms
        expected = {
            "entry": s["main"],
            "main": s["leaf"],
            "leaf": s["switcher"],
            "switcher": s["tail_target"],  # Jump-Table-Fälle gehören zur Funktion
            "tail_target": s["thread_fn"],
            "thread_fn": s["thread_fn"] + 5 * 4,
            "orphan": s["orphan"] + 3 * 4,
        }
        found = {f.start: f for f in an.funcs.values()}
        self.assertEqual(sorted(found), sorted(s[n] for n in expected))
        for name, end in expected.items():
            self.assertEqual(found[s[name]].end, end, name)
        self.assertIn("COP0", found[s["tail_target"]].hardware)
        self.assertIn("PI", found[s["thread_fn"]].hardware)
        self.assertFalse(any(f.warnings for f in found.values()), [f.warnings for f in found.values()])

    def test_syms_output(self):
        out = self.tmp / "out.toml"
        names = self.tmp / "names.txt"
        names.write_text(f"0x{self.syms['thread_fn']:08X} gameThread  # Kommentar\n")
        rom_path = self.tmp / "rom.z64"
        rom_path.write_bytes(self.rom)
        r = subprocess.run([sys.executable, str(ROOT / "tools/find_functions.py"), str(rom_path), "-o", str(out),
                            "--names", str(names), "--report", str(self.tmp / "r.md")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = out.read_text()
        self.assertIn('name = "entrypoint", vram = 0x80000400', text)
        self.assertIn('name = "gameThread"', text)
        import tomllib
        data = tomllib.loads(text)
        self.assertEqual(len(data["section"][0]["functions"]), 7)


if __name__ == "__main__":
    unittest.main()
