#!/bin/sh
# nodder installer, for people who would rather not have make.
#
#   git clone <repo> && cd nodder && ./install.sh
#
# Equivalent to `make install`. Set PREFIX to install somewhere other than
# ~/.local. Pass --service to also install and start the systemd user unit.

set -eu

PREFIX="${PREFIX:-$HOME/.local}"
BINDIR="$PREFIX/bin"
UNITDIR="$HOME/.config/systemd/user"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WANT_SERVICE=0

for arg in "$@"; do
    case "$arg" in
        --service) WANT_SERVICE=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null || die "$PYTHON not found"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
    || die "need python 3.10+, found $("$PYTHON" -V 2>&1)"
command -v herdr >/dev/null \
    || die "herdr not found on PATH. nodder drives the herdr CLI; see https://herdr.dev"

say "checking:  $("$PYTHON" -V 2>&1), $(herdr --version 2>&1)"

cd "$HERE"
say "testing:   running the suite"
"$PYTHON" -m unittest discover -s tests -t . -q >/dev/null \
    || die "tests failed; refusing to install"

say "building:  standalone executable"
rm -rf build/pkg
mkdir -p build/pkg
cp -r nodder build/pkg/
"$PYTHON" -m zipapp build/pkg \
    --main nodder.cli:main \
    --python "/usr/bin/env python3" \
    --output build/nodder \
    --compress
rm -rf build/pkg
chmod +x build/nodder

mkdir -p "$BINDIR"
cp build/nodder "$BINDIR/nodder"
chmod 755 "$BINDIR/nodder"
say "installed: $BINDIR/nodder"

if [ "$WANT_SERVICE" -eq 1 ]; then
    mkdir -p "$UNITDIR"
    sed "s|@BINDIR@|$BINDIR|" packaging/nodder.service > "$UNITDIR/nodder.service"
    systemctl --user daemon-reload
    systemctl --user enable --now nodder.service
    say "service:   nodder.service enabled and started"
fi

case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *) say ""
       say "note: $BINDIR is not on your PATH. Add this to your shell rc:"
       say "      export PATH=\"$BINDIR:\$PATH\"" ;;
esac

say ""
say "Next:  nodder --status      # what would be watched"
say "       nodder --dry-run -v  # decide, but press nothing"
say "       nodder -v            # run for real"
