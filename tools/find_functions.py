#!/usr/bin/env python3
"""Funktionserkennung für N64-ROMs ohne Dekompilierung.

N64Recomp braucht für jedes Code-Segment eine Liste aller Funktionen (Adresse + Größe).
Für 1080° Snowboarding gibt es keine Dekompilierung und damit kein ELF mit Symbolen.
Dieses Werkzeug erzeugt die Liste heuristisch direkt aus dem ROM:

  1. Start am Einsprungpunkt aus dem ROM-Header (oder an --seed-Adressen).
  2. Jede Funktion wird linear durchlaufen, bis ein `jr $ra` erreicht ist, hinter dem
     kein offenes Sprungziel mehr liegt (Branches, Jump-Tables und `j` werden verfolgt).
  3. Alle `jal`-Ziele, Tail-Calls und per lui/addiu gebildete Funktionszeiger, die auf
     einen Funktionsprolog zeigen, werden als neue Funktionen eingereiht.
  4. Lücken zwischen gefundenen Funktionen werden nach Prologen durchsucht.
  5. Bis zum Fixpunkt wiederholen.

Ausgabe ist eine Symboldatei im Format, das N64Recomp über `symbols_file_path` liest.
Zusätzlich entsteht ein Bericht mit Funktionen, die direkt Hardware ansprechen (COP0,
Cache, PI/VI/SP/AI/SI-Register) - genau diese müssen meist als libultra-Funktionen
benannt werden, damit die N64ModernRuntime sie durch eigene Implementierungen ersetzt.
"""
import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n64rom import load_rom, parse_header, to_z64  # noqa: E402

REG_SP, REG_RA, REG_ZERO = 29, 31, 0

HW_REGIONS = {
    0xA400: "SP (RSP)", 0xA404: "SP-Register", 0xA408: "SP-PC", 0xA410: "DP (RDP)",
    0xA430: "MI", 0xA440: "VI", 0xA450: "AI", 0xA460: "PI", 0xA470: "RI", 0xA480: "SI",
    0xBFC0: "PIF",
}


def s16(x: int) -> int:
    return x - 0x10000 if x & 0x8000 else x


@dataclass
class Func:
    start: int
    end: int = 0
    calls: set = field(default_factory=set)
    pointers: set = field(default_factory=set)
    hardware: set = field(default_factory=set)
    warnings: list = field(default_factory=list)


class Segment:
    def __init__(self, rom: bytes, rom_start: int, vram: int, size: int):
        self.rom = rom
        self.rom_start = rom_start
        self.vram = vram
        self.size = min(size, len(rom) - rom_start)
        self.vram_end = vram + self.size

    def contains(self, addr: int) -> bool:
        return self.vram <= addr < self.vram_end

    def word(self, addr: int) -> int:
        return struct.unpack_from(">I", self.rom, self.rom_start + addr - self.vram)[0]


def is_prologue(w: int) -> bool:
    """addiu $sp, $sp, -N"""
    return (w >> 26) == 9 and ((w >> 21) & 31) == REG_SP and ((w >> 16) & 31) == REG_SP and s16(w & 0xFFFF) < 0


def is_valid_insn(w: int) -> bool:
    """Grobe Plausibilitätsprüfung - fängt vor allem Daten ab, die als Code gelesen würden."""
    op = w >> 26
    if op in (0x13, 0x1C, 0x1D, 0x1E, 0x1F, 0x3B):
        return False
    if op == 0:
        funct = w & 63
        return funct not in (0x01, 0x05, 0x0A, 0x0B, 0x0E, 0x15, 0x28, 0x29, 0x35, 0x37, 0x39, 0x3D)
    return True


def branch_info(pc: int, w: int):
    """Liefert (ziel, unbedingt, ist_call) für Branches, sonst None."""
    op = w >> 26
    rs, rt = (w >> 21) & 31, (w >> 16) & 31
    target = (pc + 4 + (s16(w & 0xFFFF) << 2)) & 0xFFFFFFFF
    if op in (4, 20):  # beq, beql
        return target, rs == REG_ZERO and rt == REG_ZERO, False
    if op in (5, 6, 7, 21, 22, 23):  # bne, blez, bgtz (+likely)
        return target, False, False
    if op == 1:  # REGIMM
        if rt in (0, 1, 2, 3):  # bltz, bgez, bltzl, bgezl
            return target, rt in (1, 3) and rs == REG_ZERO, False
        if rt in (16, 17, 18, 19):  # bltzal, bgezal, ... (bal = bgezal $zero)
            return target, False, True
    if op == 17 and rs == 8:  # bc1f/bc1t/bc1fl/bc1tl
        return target, False, False
    return None


class Analyzer:
    def __init__(self, seg: Segment, max_table: int = 512):
        self.seg = seg
        self.funcs: dict[int, Func] = {}
        self.max_table = max_table
        self.external_calls: dict[int, int] = {}

    def analyze(self, start: int, known: list[int]) -> Func:
        seg = self.seg
        f = Func(start)
        pending: set[int] = set()
        hi: dict[int, int] = {}        # Register -> per lui geladener oberer Teil
        addr_regs: dict[int, int] = {}  # Register -> vollständig gebildete Adresse
        loaded_from: dict[int, int] = {}  # Register -> Adresse, aus der per lw geladen wurde
        next_known = min((k for k in known if k > start), default=seg.vram_end)
        pc = start
        end_after_delay = None

        while pc < next_known:
            w = seg.word(pc)
            if end_after_delay is not None and pc == end_after_delay:
                break
            if not is_valid_insn(w):
                f.warnings.append(f"ungültige Instruktion 0x{w:08X} bei 0x{pc:08X}")
                f.end = pc
                return f

            op = w >> 26
            rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
            imm = w & 0xFFFF
            forward_open = any(t > pc + 4 for t in pending)

            # Hardwarezugriffe / Spezialinstruktionen für den Bericht merken
            if op == 15 and (imm in HW_REGIONS or (imm & 0xFFF0) in HW_REGIONS):
                f.hardware.add(HW_REGIONS.get(imm, HW_REGIONS.get(imm & 0xFFF0)))
            elif op == 16 and rs in (0, 4):
                f.hardware.add("COP0")
            elif op == 16 and (w & 0x3F) == 0x18 and rs == 16:
                f.hardware.add("ERET")
            elif op == 47:
                f.hardware.add("CACHE")

            # Konstanten-Tracking für Funktionszeiger und Jump-Tables
            funct = w & 63
            is_addu = op == 0 and funct == 0x21
            base_hi = hi.get(rs)
            if is_addu and base_hi is None:
                base_hi = hi.get(rt)
            dest = None
            if op == 0 and funct not in (0x08, 0x09, 0x18, 0x19, 0x1A, 0x1B):
                dest = rd
            elif 8 <= op <= 15 or 32 <= op <= 39:
                dest = rt
            if dest is not None:
                for table in (hi, addr_regs, loaded_from):
                    table.pop(dest, None)

            if op == 15:  # lui
                hi[rt] = imm << 16
            elif op in (9, 13) and base_hi is not None:  # addiu / ori
                addr = (base_hi + (s16(imm) if op == 9 else imm)) & 0xFFFFFFFF
                addr_regs[rt] = addr
                if seg.contains(addr) and addr % 4 == 0 and is_prologue(seg.word(addr)):
                    f.pointers.add(addr)
            elif is_addu and base_hi is not None:  # addu: Tabellenbasis + Index
                hi[rd] = base_hi
            elif op == 35 and base_hi is not None:  # lw rt, lo(base)
                loaded_from[rt] = (base_hi + s16(imm)) & 0xFFFFFFFF

            br = branch_info(pc, w)
            if br:
                target, unconditional, is_call = br
                if is_call:
                    self._add_call(f, target)
                elif target > pc:
                    pending.add(target)
                if unconditional and not is_call and not any(t > pc + 4 for t in pending - {target}) and target <= pc:
                    end_after_delay = pc + 8
            elif op == 3:  # jal
                self._add_call(f, ((pc + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2))
            elif op == 2:  # j
                target = ((pc + 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
                if start <= target <= pc:
                    if not forward_open:
                        end_after_delay = pc + 8
                elif pending and pc < target <= max(pending):
                    pass  # Sprung in einen Bereich, der ohnehin zur Funktion gehört
                else:
                    self._add_call(f, target)  # Tail-Call
                    if not forward_open:
                        end_after_delay = pc + 8
            elif op == 0 and (w & 63) == 8:  # jr
                if rs == REG_RA:
                    if not forward_open:
                        end_after_delay = pc + 8
                elif rs in loaded_from:
                    table = loaded_from[rs]
                    added = self._read_jump_table(table, pc, next_known, pending)
                    if not added:
                        f.warnings.append(f"jr ${rs} bei 0x{pc:08X}: Jump-Table 0x{table:08X} nicht lesbar")
                else:
                    f.warnings.append(f"jr ${rs} bei 0x{pc:08X} ohne erkennbare Jump-Table")
            elif op == 0 and (w & 63) == 9:  # jalr - Ziel nur zur Laufzeit bekannt
                pass

            pc += 4

        f.end = min(pc, next_known)
        if end_after_delay is None and pc >= next_known and next_known == seg.vram_end:
            f.warnings.append("Segmentende erreicht, ohne Funktionsende zu finden")
        return f

    def _read_jump_table(self, table: int, pc: int, limit: int, pending: set) -> bool:
        if not self.seg.contains(table) or table % 4:
            return False
        added = False
        for i in range(self.max_table):
            addr = table + i * 4
            if not self.seg.contains(addr):
                break
            entry = self.seg.word(addr)
            if not (self.seg.vram <= entry < limit) or entry % 4:
                break
            if entry > pc:
                pending.add(entry)
            added = True
        return added

    def _add_call(self, f: Func, target: int):
        if self.seg.contains(target):
            f.calls.add(target)
        else:
            self.external_calls[target] = self.external_calls.get(target, 0) + 1

    def run(self, seeds: list[int], scan_gaps: bool = True) -> None:
        starts = {s for s in seeds if self.seg.contains(s)}
        while True:
            self.external_calls.clear()
            known = sorted(starts)
            self.funcs = {s: self.analyze(s, known) for s in known}
            new = set()
            for f in self.funcs.values():
                new |= f.calls | f.pointers
            if scan_gaps:
                new |= self._gap_candidates()
            new = {n for n in new if n % 4 == 0} - starts
            if not new:
                break
            starts |= new

    def _gap_candidates(self) -> set:
        """Sucht in Lücken zwischen Funktionen (innerhalb des Codebereichs) nach Prologen."""
        found = set()
        ordered = sorted(self.funcs.values(), key=lambda f: f.start)
        bounds = [f.start for f in ordered[1:]] + [self.seg.vram_end]
        for a, limit in zip(ordered, bounds):
            addr = a.end
            while addr < limit:
                w = self.seg.word(addr)
                if w == 0:
                    addr += 4
                    continue
                if is_prologue(w):
                    found.add(addr)
                break
        return found


def parse_names(path: str) -> dict[int, str]:
    """Format: eine Zeile pro Funktion, `0x80001234 osCreateThread`, Kommentare mit #."""
    names = {}
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path}:{lineno}: erwartet '<adresse> <name>'")
        names[int(parts[0], 0)] = parts[1]
    return names


def write_syms(path: str, section_name: str, seg: Segment, funcs: list[Func], names: dict[int, str],
               entrypoint: int) -> None:
    text_end = max(f.end for f in funcs)
    lines = [
        "# Automatisch erzeugt von tools/find_functions.py - nicht von Hand bearbeiten.",
        "# Namen werden über config/names.txt vergeben.",
        "",
        "[[section]]",
        f'name = "{section_name}"',
        f"rom = 0x{seg.rom_start:08X}",
        f"vram = 0x{seg.vram:08X}",
        f"size = 0x{text_end - seg.vram:X}",
        "",
        "functions = [",
    ]
    for f in funcs:
        name = names.get(f.start) or ("entrypoint" if f.start == entrypoint else f"func_{f.start:08X}")
        lines.append(f'    {{ name = "{name}", vram = 0x{f.start:08X}, size = 0x{f.end - f.start:X} }},')
    lines += ["]", ""]
    Path(path).write_text("\n".join(lines))


def write_report(path: str, analyzer: Analyzer, funcs: list[Func], names: dict[int, str]) -> None:
    out = ["# Analysebericht", ""]
    out.append(f"Funktionen gefunden: {len(funcs)}")
    out.append(f"Codebereich: 0x{funcs[0].start:08X} - 0x{max(f.end for f in funcs):08X}")
    out.append("")
    out.append("## Funktionen mit direktem Hardwarezugriff")
    out.append("Kandidaten für libultra-Funktionen, die benannt werden müssen (siehe tools/data/n64recomp_symbols.txt).")
    out.append("")
    for f in funcs:
        if f.hardware:
            name = names.get(f.start, f"func_{f.start:08X}")
            out.append(f"- 0x{f.start:08X} {name} (0x{f.end - f.start:X} Bytes): {', '.join(sorted(f.hardware))}")
    out.append("")
    out.append("## Aufrufe außerhalb des Segments (Hinweis auf Overlays)")
    out.append("")
    for target, count in sorted(analyzer.external_calls.items()):
        out.append(f"- 0x{target:08X}: {count}x")
    out.append("")
    out.append("## Warnungen")
    out.append("")
    for f in funcs:
        for w in f.warnings:
            out.append(f"- func_{f.start:08X}: {w}")
    Path(path).write_text("\n".join(out) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("rom", help="ROM (.z64/.v64/.n64)")
    p.add_argument("-o", "--out", default="1080.syms.toml", help="Ausgabe-Symboldatei")
    p.add_argument("--report", default=None, help="Analysebericht (Markdown)")
    p.add_argument("--rom-start", type=lambda x: int(x, 0), default=0x1000,
                   help="ROM-Offset des Segments (Standard: 0x1000, Hauptsegment)")
    p.add_argument("--vram", type=lambda x: int(x, 0), default=None,
                   help="VRAM-Adresse des Segments (Standard: Einsprungpunkt aus dem Header)")
    p.add_argument("--size", type=lambda x: int(x, 0), default=0x100000,
                   help="Maximale Segmentgröße (Standard: 1 MiB, so viel lädt der IPL3)")
    p.add_argument("--section-name", default=".main")
    p.add_argument("--seed", type=lambda x: int(x, 0), action="append", default=[],
                   help="Zusätzliche bekannte Funktionsadresse (mehrfach möglich)")
    p.add_argument("--names", default=None, help="Namensdatei ('<adresse> <name>' pro Zeile)")
    p.add_argument("--no-gap-scan", action="store_true", help="Lücken nicht nach Prologen durchsuchen")
    args = p.parse_args()

    try:
        rom = to_z64(load_rom(args.rom))
        names = parse_names(args.names) if args.names else {}
    except (OSError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    entrypoint = parse_header(rom)["entrypoint"]
    vram = args.vram if args.vram is not None else entrypoint
    seg = Segment(rom, args.rom_start, vram, args.size)
    analyzer = Analyzer(seg)
    analyzer.run([entrypoint, *args.seed, *names.keys()], scan_gaps=not args.no_gap_scan)

    funcs = sorted((f for f in analyzer.funcs.values() if f.end > f.start), key=lambda f: f.start)
    if not funcs:
        print("Fehler: keine Funktionen gefunden - stimmen --rom-start/--vram?", file=sys.stderr)
        return 1
    write_syms(args.out, args.section_name, seg, funcs, names, entrypoint)
    if args.report:
        write_report(args.report, analyzer, funcs, names)

    hw = sum(1 for f in funcs if f.hardware)
    warn = sum(len(f.warnings) for f in funcs)
    print(f"{len(funcs)} Funktionen -> {args.out}  "
          f"(Code bis 0x{funcs[-1].end:08X}, {hw} mit Hardwarezugriff, {warn} Warnungen, "
          f"{len(analyzer.external_calls)} externe Aufrufziele)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
