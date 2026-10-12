#!/usr/bin/env bash
# Erzeugt aus dem eigenen ROM-Dump die Symboldatei und den recompilierten C-Code.
#   scripts/recompile.sh [pfad/zum/rom]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROM_IN="${1:-$ROOT/rom/1080.us.z64}"
ROM="$ROOT/rom/1080.us.z64"
BUILD="$ROOT/build"
mkdir -p "$BUILD" "$ROOT/rom"

if [[ "$(realpath "$ROM_IN")" != "$(realpath -m "$ROM")" ]]; then
    python3 "$ROOT/tools/n64rom.py" convert "$ROM_IN" "$ROM"
fi
python3 "$ROOT/tools/n64rom.py" info "$ROM"

NAMES=()
[[ -f "$ROOT/config/names.txt" ]] && NAMES=(--names "$ROOT/config/names.txt")
python3 "$ROOT/tools/find_functions.py" "$ROM" -o "$BUILD/1080.us.syms.toml" \
    --report "$BUILD/analyse.md" "${NAMES[@]}"

if [[ ! -x "$BUILD/N64Recomp/N64Recomp" ]]; then
    git -C "$ROOT" submodule update --init --recursive lib/N64Recomp
    cmake -S "$ROOT/lib/N64Recomp" -B "$BUILD/N64Recomp" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$BUILD/N64Recomp" --target N64Recomp -j
fi
mkdir -p "$BUILD/RecompiledFuncs"
"$BUILD/N64Recomp/N64Recomp" "$ROOT/config/1080.us.toml"
echo "Fertig: $BUILD/RecompiledFuncs (Bericht: $BUILD/analyse.md)"
