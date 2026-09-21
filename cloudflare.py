"""
cloudflare.py
=============
Modul untuk menjalankan Cloudflare Quick Tunnel secara otomatis & portable.
"""

import os
import sys
import shutil
import subprocess
import threading
import re

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BUNDLE_DIR = sys._MEIPASS
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

EXE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BUNDLE_DIR


def get_cloudflared_path() -> str:
    """Cari path executable cloudflared (prioritaskan folder bundle / lokal aplikasi)."""
    candidates = [
        os.path.join(BUNDLE_DIR, "cloudflared.exe"),
        os.path.join(BUNDLE_DIR, "bin", "cloudflared.exe"),
        os.path.join(EXE_DIR, "cloudflared.exe"),
        os.path.join(EXE_DIR, "bin", "cloudflared.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which("cloudflared")


def is_cloudflared_available() -> bool:
    return get_cloudflared_path() is not None


def start_tunnel(port: int, on_url_update=None):
    """
    Menjalankan Cloudflare Quick Tunnel.
    
    Args:
        port: Port lokal yang akan di-expose.
        on_url_update: Callback(url: str) yang dipanggil ketika URL publik Cloudflare siap.
    Returns:
        process: Subprocess dari cloudflared.
    """
    cloudflared_bin = get_cloudflared_path()
    if not cloudflared_bin:
        raise FileNotFoundError("Executable cloudflared.exe tidak ditemukan di folder aplikasi maupun PATH!")

    command = [cloudflared_bin, "tunnel", "--url", f"http://127.0.0.1:{port}"]

    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace"
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    process = subprocess.Popen(command, **kwargs)
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def monitor_output():
        for line in iter(process.stdout.readline, ''):
            match = url_pattern.search(line)
            if match:
                url = match.group(0)
                if on_url_update:
                    on_url_update(url)

    monitor_thread = threading.Thread(target=monitor_output, daemon=True)
    monitor_thread.start()

    return process