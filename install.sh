#!/usr/bin/env bash
#
# Universal installer for Magpie CLI.
# Requires Python 3 (>= 3.8). Works without sudo (user-space aria2c + bundled ffmpeg).
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "          Installing Magpie CLI 🐦"
echo "=================================================="

# 1. Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "[-] FATAL: Python 3 is required but not installed."
    echo "    Please install Python 3 (>= 3.8) and try again."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[+] Detected Python $PY_VER"

# --- privilege helpers: never require sudo -------------------------------------
ORIG_PATH="$PATH"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
export PATH="$LOCAL_BIN:$PATH"

SUDO=""
CAN_PKG=0   # can we use the system package manager?
IS_TERMUX=0
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ] && command -v pkg >/dev/null 2>&1; then
    IS_TERMUX=1
    CAN_PKG=1
elif [ "$(id -u)" -eq 0 ]; then
    CAN_PKG=1
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    SUDO="sudo"; CAN_PKG=1       # passwordless sudo only; never prompt/hang
fi
if command -v brew >/dev/null 2>&1; then CAN_BREW=1; else CAN_BREW=0; fi
[ $CAN_PKG -eq 1 ] && echo "[+] Package manager access: yes" || echo "[i] No root/sudo: using user-space installs (~/.local/bin)"

pkg_install() {  # $1 = package name; returns 0 on success
    if [ "$IS_TERMUX" -eq 1 ] && command -v pkg >/dev/null 2>&1; then
        echo "[*] Using Termux pkg to install $1..."
        pkg install -y "$1" >/dev/null 2>&1 && return 0
    fi
    [ "$CAN_BREW" -eq 1 ] && { brew install "$1" >/dev/null 2>&1 && return 0; }
    [ "$CAN_PKG" -eq 1 ] || return 1
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$1" >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then $SUDO dnf install -y "$1" >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then $SUDO yum install -y "$1" >/dev/null 2>&1
    elif command -v pacman >/dev/null 2>&1; then $SUDO pacman -Sy --noconfirm "$1" >/dev/null 2>&1
    elif command -v apk >/dev/null 2>&1; then $SUDO apk add --no-cache "$1" >/dev/null 2>&1
    else return 1; fi
}

# 2. aria2c (fast multi-connection downloads)
echo "[*] Verifying aria2c installation..."
if ! command -v aria2c >/dev/null 2>&1; then
    echo "[*] aria2c not found. Trying system package..."
    pkg_install aria2 || true
fi
if ! command -v aria2c >/dev/null 2>&1 && [ "$(uname -s)" = "Linux" ]; then
    echo "[*] Installing static aria2c into $LOCAL_BIN (no sudo needed)..."
    case "$(uname -m)" in
        x86_64|amd64)  A2="x86_64-linux-musl" ;;
        aarch64|arm64) A2="aarch64-linux-musl" ;;
        armv7*)        A2="armv7-linux-musleabihf" ;;
        i?86)          A2="i686-linux-musl" ;;
        *)             A2="" ;;
    esac
    if [ -n "$A2" ]; then
        TMPD="$(mktemp -d)"
        URL="https://github.com/abcfy2/aria2-static-build/releases/latest/download/aria2-${A2}_static.zip"
        if { curl -fsSL "$URL" -o "$TMPD/a.zip" 2>/dev/null || wget -qO "$TMPD/a.zip" "$URL" 2>/dev/null; } && \
           python3 - "$TMPD/a.zip" "$LOCAL_BIN" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); z.extract("aria2c", sys.argv[2])
PY
        then chmod +x "$LOCAL_BIN/aria2c"; fi
        rm -rf "$TMPD"
    fi
fi
if command -v aria2c >/dev/null 2>&1; then
    echo "[+] aria2c ready: $(aria2c -v | head -n 1)"
else
    echo "[!] aria2c could not be installed. Magpie will use its built-in resumable downloader"
    echo "    (works, but slower). Install aria2 later for max speed."
fi

# 2b. ffmpeg (sources are often Matroska and must be converted to real MP4)
echo "[*] Verifying ffmpeg installation..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    pkg_install ffmpeg || true
fi
if command -v ffmpeg >/dev/null 2>&1; then
    echo "[+] ffmpeg ready: $(ffmpeg -version | head -n 1)"
else
    echo "[i] No system ffmpeg; Magpie will use the bundled static ffmpeg (pip: imageio-ffmpeg)."
fi

# 3. Install Python dependencies and package
echo "[*] Installing Magpie CLI Python package..."

INSTALL_SUCCESS=0

# Strategy A: Standard pip install
if python3 -m pip install -e . >/dev/null 2>&1; then
    INSTALL_SUCCESS=1
fi

# Strategy B: PEP 668 managed environment override
if [ $INSTALL_SUCCESS -eq 0 ]; then
    if python3 -m pip install --break-system-packages -e . >/dev/null 2>&1; then
        INSTALL_SUCCESS=1
    fi
fi

# Strategy C: User space install
if [ $INSTALL_SUCCESS -eq 0 ]; then
    if python3 -m pip install --user -e . >/dev/null 2>&1; then
        INSTALL_SUCCESS=1
    fi
fi

# Strategy D: Direct setup.py install
if [ $INSTALL_SUCCESS -eq 0 ]; then
    if python3 setup.py install >/dev/null 2>&1; then
        INSTALL_SUCCESS=1
    fi
fi

# Strategy E: Direct wrapper executable fallback
BIN_TARGET="/usr/local/bin/magpie"
if [ "$EUID" -ne 0 ] && [ ! -w "/usr/local/bin" ]; then
    BIN_TARGET="$HOME/.local/bin/magpie"
    mkdir -p "$HOME/.local/bin"
fi

if ! command -v magpie >/dev/null 2>&1; then
    echo "[*] Creating entrypoint wrapper at $BIN_TARGET..."
    {
        echo '#!/usr/bin/env bash'
        echo "PYTHONPATH=\"$SCRIPT_DIR:\$PYTHONPATH\" exec python3 -m magpie_cli.main \"\$@\""
    } > "$BIN_TARGET"
    chmod +x "$BIN_TARGET"
fi

if ! echo "$ORIG_PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
    echo "[i] Add ~/.local/bin to your PATH: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
fi

# 4. Verify installation
echo "[*] Verifying Magpie CLI installation..."
if command -v magpie >/dev/null 2>&1; then
    echo "[+] magpie binary found at: $(command -v magpie)"
    magpie --device
    echo ""
    echo "=================================================="
    echo " [✔] Magpie CLI successfully installed!"
    echo " Run 'magpie --help' or 'magpie --download \"breaking bad\"'"
    echo "=================================================="
else
    echo "[-] Failed to find 'magpie' on PATH."
    echo "    Try running: python3 $SCRIPT_DIR/magpie_cli/main.py --help"
    exit 1
fi
