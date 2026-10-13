"""
Device Identification & Fingerprinting for Magpie Terminal CLI.

Provides durable, deterministic hardware-level device identification
and session persistence in terminal environments across Linux, macOS,
and Windows.
"""

import os
import sys
import uuid
import socket
import platform
import hashlib
import getpass
import json
from pathlib import Path
from typing import Dict, Any, Optional

CONFIG_DIR = Path.home() / ".config" / "magpie"
SESSION_FILE = CONFIG_DIR / "session.json"


def _read_file_stripped(path_str: str) -> Optional[str]:
    """Read a system file cleanly if available and readable."""
    try:
        p = Path(path_str)
        if p.is_file() and os.access(p, os.R_OK):
            content = p.read_text(encoding="utf-8", errors="ignore").strip()
            if content and content != "None" and "00000000" not in content.lower():
                return content
    except Exception:
        pass
    return None


def get_hardware_identifiers() -> Dict[str, str]:
    """Collect system and hardware level identifiers for terminal fingerprinting."""
    info = {
        "system": platform.system().lower(),
        "machine": platform.machine().lower(),
        "node": socket.gethostname().strip().lower(),
        "user": getpass.getuser().strip(),
        "mac": hex(uuid.getnode()),
    }

    # Linux machine-id
    machine_id = _read_file_stripped("/etc/machine-id") or _read_file_stripped("/var/lib/dbus/machine-id")
    if machine_id:
        info["machine_id"] = machine_id

    # Product UUID (BIOS/DMI)
    product_uuid = _read_file_stripped("/sys/class/dmi/id/product_uuid")
    if product_uuid:
        info["product_uuid"] = product_uuid

    # Board serial if accessible
    board_serial = _read_file_stripped("/sys/class/dmi/id/board_serial")
    if board_serial:
        info["board_serial"] = board_serial

    return info


def generate_device_fingerprint() -> str:
    """Generate a stable, deterministic hardware-based device fingerprint.

    This survives CLI restarts and produces identical results on the same
    machine even if configuration files are temporarily cleared.
    """
    hw = get_hardware_identifiers()
    # Primary hardware seed
    raw_seed = ":".join([
        hw.get("machine_id", ""),
        hw.get("product_uuid", ""),
        hw.get("mac", ""),
        hw.get("system", ""),
        hw.get("machine", ""),
        hw.get("node", ""),
    ])

    digest = hashlib.sha256(f"magpie_cli_fingerprint:{raw_seed}".encode("utf-8")).hexdigest()
    return f"cli_{digest[:28]}"


def ensure_config_dir() -> Path:
    """Ensure the config directory exists with safe permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except Exception:
        pass
    return CONFIG_DIR


def load_session() -> Dict[str, Any]:
    """Load cached session information (device_id, user_id, token, etc.)."""
    ensure_config_dir()
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("device_id"):
                    return data
        except Exception:
            pass

    # No saved session or invalid: generate deterministic hardware fingerprint
    device_id = generate_device_fingerprint()
    session = {
        "device_id": device_id,
        "user_id": None,
        "token": None,
        "server_url": None,
    }
    save_session(session)
    return session


def save_session(session: Dict[str, Any]) -> None:
    """Save session data to disk securely."""
    ensure_config_dir()
    try:
        temp_file = CONFIG_DIR / "session.json.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)
        os.replace(temp_file, SESSION_FILE)
        try:
            os.chmod(SESSION_FILE, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Warning: Failed to persist session: {e}", file=sys.stderr)


def get_device_summary() -> Dict[str, str]:
    """Return a human-readable dictionary of device fingerprint details."""
    session = load_session()
    hw = get_hardware_identifiers()
    return {
        "device_id": session.get("device_id", "unknown"),
        "platform": f"{hw.get('system', '')} ({hw.get('machine', '')})",
        "hostname": hw.get("node", ""),
        "mac_node": hw.get("mac", ""),
        "has_machine_id": "yes" if "machine_id" in hw else "no",
        "session_file": str(SESSION_FILE),
    }
