#!/usr/bin/env python3
"""N64-ROM-Hilfswerkzeug für das 1080°-Recomp-Projekt.

Unterbefehle:
  info      Header, Byte-Reihenfolge, Hashes und Einsprungpunkt anzeigen
  convert   ROM in das big-endian .z64-Format umwandeln (wird von N64Recomp erwartet)
  ucode     ROM nach RSP-Microcode-Kennungen durchsuchen (wichtig für RT64)
"""
import argparse
import hashlib
import re
import struct
import sys
from pathlib import Path

# Die ersten vier Bytes jedes N64-ROMs (PI-Domain-Konfiguration) in den drei üblichen Formaten.
MAGIC_Z64 = bytes.fromhex("80371240")  # big endian (nativ)
MAGIC_V64 = bytes.fromhex("37804012")  # 16-Bit byteswapped
MAGIC_N64 = bytes.fromhex("40123780")  # 32-Bit little endian


def detect_format(rom: bytes) -> str:
    head = rom[:4]
    if head == MAGIC_Z64:
        return "z64"
    if head == MAGIC_V64:
        return "v64"
    if head == MAGIC_N64:
        return "n64"
    raise ValueError(f"Unbekanntes ROM-Format (erste Bytes: {head.hex()})")


def to_z64(rom: bytes) -> bytes:
    fmt = detect_format(rom)
    if fmt == "z64":
        return rom
    if len(rom) % 4:
        raise ValueError("ROM-Größe ist kein Vielfaches von 4")
    if fmt == "v64":
        out = bytearray(len(rom))
        out[0::2] = rom[1::2]
        out[1::2] = rom[0::2]
        return bytes(out)
    # n64: jedes 32-Bit-Wort umdrehen
    words = struct.unpack(f"<{len(rom) // 4}I", rom)
    return struct.pack(f">{len(words)}I", *words)


def parse_header(rom: bytes) -> dict:
    """Erwartet ein ROM im .z64-Format."""
    return {
        "clock_rate": struct.unpack_from(">I", rom, 0x04)[0],
        "entrypoint": struct.unpack_from(">I", rom, 0x08)[0],
        "libultra_rev": struct.unpack_from(">I", rom, 0x0C)[0],
        "crc1": struct.unpack_from(">I", rom, 0x10)[0],
        "crc2": struct.unpack_from(">I", rom, 0x14)[0],
        "title": rom[0x20:0x34].decode("ascii", "replace").rstrip(" \0"),
        "game_code": rom[0x3B:0x3F].decode("ascii", "replace"),
        "version": rom[0x3F],
    }


REGIONS = {"E": "USA (NTSC)", "J": "Japan (NTSC)", "P": "Europa (PAL)", "D": "Deutschland (PAL)",
           "U": "Australien (PAL)", "F": "Frankreich (PAL)"}


def load_rom(path: str) -> bytes:
    data = Path(path).read_bytes()
    if len(data) < 0x101000:
        raise ValueError(f"{path}: Datei ist zu klein für ein N64-ROM ({len(data)} Bytes)")
    return data


def cmd_info(args) -> int:
    raw = load_rom(args.rom)
    fmt = detect_format(raw)
    rom = to_z64(raw)
    h = parse_header(rom)
    print(f"Datei:        {args.rom}")
    print(f"Format:       .{fmt}" + ("" if fmt == "z64" else "  (mit 'convert' nach .z64 umwandeln)"))
    print(f"Größe:        {len(rom)} Bytes ({len(rom) / (1024 * 1024):.1f} MiB)")
    print(f"Titel:        {h['title']}")
    code = h["game_code"]
    print(f"Spielcode:    {code}  -> Region: {REGIONS.get(code[3:], 'unbekannt')}, Revision {h['version']}")
    print(f"Einsprung:    0x{h['entrypoint']:08X}")
    print(f"CRC1/CRC2:    0x{h['crc1']:08X} / 0x{h['crc2']:08X}")
    print(f"SHA-1 (z64):  {hashlib.sha1(rom).hexdigest()}")
    print(f"MD5:          {hashlib.md5(rom).hexdigest()}")
    if code[:3] != "NTE":
        print("WARNUNG: Spielcode beginnt nicht mit 'NTE' - ist das wirklich 1080° Snowboarding?",
              file=sys.stderr)
    return 0


def cmd_convert(args) -> int:
    raw = load_rom(args.rom)
    rom = to_z64(raw)
    Path(args.out).write_bytes(rom)
    print(f"{args.out} geschrieben ({detect_format(raw)} -> z64)")
    return 0


# Kennstrings, die in den Datensegmenten von Nintendo-/SGI-Microcodes stehen.
UCODE_PATTERN = re.compile(rb"RSP (?:Gfx|SW|Audio)[ -~]{4,80}")
KNOWN_UCODES = {
    b"F3DEX2": "F3DEX2 (von RT64 unterstützt)",
    b"F3DLX2": "F3DLX2 (F3DEX2-Familie, von RT64 unterstützt)",
    b"F3DZEX": "F3DZEX (F3DEX2-Familie, von RT64 unterstützt)",
    b"F3DEX": "F3DEX 1.x (von RT64 unterstützt)",
    b"F3DLX": "F3DLX 1.x (F3DEX-Familie)",
    b"F3DLP": "F3DLP (F3DEX-Familie)",
    b"S2DEX": "S2DEX (2D-Sprites, von RT64 unterstützt)",
    b"L3DEX": "L3DEX (Linien)",
    b"Fast3D": "Fast3D/F3D (von RT64 unterstützt)",
}


def cmd_ucode(args) -> int:
    rom = to_z64(load_rom(args.rom))
    found = False
    for m in UCODE_PATTERN.finditer(rom):
        text = m.group(0).decode("ascii", "replace").strip()
        family = next((desc for key, desc in KNOWN_UCODES.items() if key in m.group(0)), "unbekannt")
        print(f"0x{m.start():08X}  {text:<70}  [{family}]")
        found = True
    if not found:
        print("Keine Standard-Microcode-Kennungen gefunden. Das Spiel nutzt eventuell einen "
              "eigenen/komprimierten Microcode - dann muss er zur Laufzeit identifiziert werden "
              "(RT64-Log beim ersten osSpTaskLoad prüfen).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("info", help="ROM-Informationen anzeigen")
    s.add_argument("rom")
    s.set_defaults(func=cmd_info)
    s = sub.add_parser("convert", help="ROM nach .z64 umwandeln")
    s.add_argument("rom")
    s.add_argument("out")
    s.set_defaults(func=cmd_convert)
    s = sub.add_parser("ucode", help="RSP-Microcodes im ROM finden")
    s.add_argument("rom")
    s.set_defaults(func=cmd_ucode)
    args = p.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
