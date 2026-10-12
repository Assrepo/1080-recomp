# 1080° Snowboarding: Recompiled

Ziel: **1080° Snowboarding (N64)** per statischer Recompilation in nativen Code
übersetzen, sodass es ohne Emulator unter **Windows und Linux** läuft – mit
moderner Grafik über [RT64](https://github.com/rt64/rt64) (hohe Auflösungen,
Widescreen, MSAA, hohe Bildraten, später ggf. Texturpakete).

Technische Basis ist derselbe Stack wie bei
[Zelda 64: Recompiled](https://github.com/Zelda64Recomp/Zelda64Recomp):

| Komponente | Aufgabe |
|---|---|
| [N64Recomp](https://github.com/N64Recomp/N64Recomp) (`lib/N64Recomp`) | übersetzt den MIPS-Code des ROMs in C |
| [N64ModernRuntime](https://github.com/N64Recomp/N64ModernRuntime) (`lib/N64ModernRuntime`) | ersetzt libultra (Threads, DMA, Controller, Audio, Speicherstände) |
| [RT64](https://github.com/rt64/rt64) (`lib/rt64`) | Renderer für die N64-Displaylisten (D3D12 / Vulkan) |

> **Rechtliches:** Dieses Repository enthält **keinen** ROM und keinen
> Nintendo-Code. Jeder braucht einen eigenen, legal erstellten Dump seines
> Moduls. ROM-Dateien sind per `.gitignore` ausgeschlossen.

## Stand

Das Projekt ist in Phase 1. Weil es für 1080° **keine Dekompilierung** gibt,
fehlt das sonst übliche ELF mit Funktionssymbolen. Deshalb gibt es hier eigene
Werkzeuge, die diese Information direkt aus dem ROM gewinnen:

- [x] `tools/n64rom.py` – ROM-Format erkennen (.z64/.v64/.n64) und umwandeln,
      Header/Hashes anzeigen, RSP-Microcodes im ROM finden
- [x] `tools/find_functions.py` – Funktionserkennung im Hauptsegment (Einsprungpunkt,
      `jal`-Ziele, Tail-Calls, Jump-Tables, Funktionszeiger, Lückensuche) →
      Symboldatei für N64Recomp + Bericht über Hardwarezugriffe und Overlay-Aufrufe
- [x] `scripts/recompile.sh` – kompletter Ablauf ROM → Symbole → C-Code
- [x] Tests mit synthetischem ROM; End-to-End mit N64Recomp geprüft
- [ ] libultra-Funktionen im ROM identifizieren und benennen (`config/names.txt`)
- [ ] Overlays / per DMA nachgeladene Code-Segmente finden und als weitere Sections eintragen
- [ ] Host-Programm (CMake): N64ModernRuntime + RT64 + SDL2, Fenster, Eingabe, Audio
- [ ] Microcode prüfen und ggf. RT64 anpassen, Audio-Microcode (RSP) recompilieren
- [ ] Spielspezifische Patches: Widescreen, 60 FPS, Spiegelungen/Effekte

## Benutzung (Phase 1)

Voraussetzungen: Python ≥ 3.11, CMake ≥ 3.20, ein C++20-Compiler (clang, gcc oder MSVC).

```sh
git clone --recurse-submodules <dieses-repo>
cd 1080-recomp

# eigenen Dump angeben (.z64, .v64 oder .n64 – wird nach rom/1080.us.z64 konvertiert)
scripts/recompile.sh /pfad/zu/1080.z64
```

Ergebnis:

- `build/1080.us.syms.toml` – gefundene Funktionen
- `build/analyse.md` – Bericht: Funktionen mit Hardwarezugriff (Kandidaten für
  libultra), Aufrufe in Overlays, Warnungen
- `build/RecompiledFuncs/` – der recompilierte C-Code

Einzelwerkzeuge:

```sh
python3 tools/n64rom.py info  rom/1080.us.z64   # Header, SHA-1, Einsprungpunkt
python3 tools/n64rom.py ucode rom/1080.us.z64   # welcher Grafik-/Audio-Microcode?
python3 tools/find_functions.py rom/1080.us.z64 -o syms.toml --report analyse.md
python3 tests/test_tools.py                     # Tests (braucht clang/lld)
```

## Wie geht es weiter? (der schwierige Teil)

1. **libultra benennen.** N64ModernRuntime ersetzt libultra-Funktionen anhand
   ihres *Namens* (`osCreateThread`, `osPiStartDma`, `osViSwapBuffer`, …; vollständige
   Liste in `tools/data/n64recomp_symbols.txt`). Im ROM heißen sie aber nur
   `func_8000xxxx`. Der Analysebericht listet alle Funktionen, die COP0, Cache oder
   PI/VI/SP/AI/SI-Register anfassen – das sind fast ausschließlich libultra-Funktionen.
   Gefundene Namen kommen in `config/names.txt` (`0x80001234 osCreateThread`).
   Geplant: automatischer Abgleich gegen Signaturen aus
   [ultralib](https://github.com/decompals/ultralib).
2. **Overlays.** Der IPL3 lädt nur das erste MiB ab ROM-Offset `0x1000`. Weiterer
   Code wird vom Spiel per DMA nachgeladen; der Bericht zeigt Aufrufziele außerhalb
   des Hauptsegments als Hinweis darauf.
3. **Host-Programm** analog zu Zelda64Recomp `src/main` aufbauen.
4. **Grafik.** RT64 unterstützt F3D, F3DEX, F3DEX2 und S2DEX. `n64rom.py ucode`
   zeigt, welchen Microcode 1080° mitbringt; nutzt das Spiel eine eigene Variante,
   muss RT64 dafür erweitert werden.

Mit einem echten ROM-Dump lässt sich Phase 1 sofort lokal ausführen; die
Ergebnisse (`build/analyse.md`) sind die Grundlage für die nächsten Schritte.
