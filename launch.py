"""
launch.py
=========
Peluncur cerdas untuk Balinese Whisper ASR.
Menjalankan server lokal dan membuka jendela aplikasi mandiri (seperti aplikasi native Windows).
"""

import os
import sys
import time
import socket
import tempfile
import threading
import webbrowser
import subprocess
import urllib.request
from http.server import ThreadingHTTPServer

# Safe logging for GUI / windowed mode
class SafeStream:
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
    def flush(self):
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass

sys.stdout = SafeStream(sys.stdout)
sys.stderr = SafeStream(sys.stderr)

# Impor backend server lokal
if getattr(sys, 'frozen', False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import server as bw_server


def show_error_box(title: str, message: str):
    """Tampilkan popup dialog pesan error Windows jika terjadi masalah."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"[{title}] {message}")


def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
    """Cari port yang belum dipakai."""
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def create_desktop_shortcut() -> bool:
    """Buat shortcut di Desktop Windows jika belum ada."""
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            return False
        shortcut_path = os.path.join(desktop, "Balinese Whisper.lnk")
        if os.path.exists(shortcut_path):
            return True

        exe_path = os.path.join(ROOT, "BalineseWhisper.exe")
        target = exe_path if os.path.isfile(exe_path) else os.path.join(ROOT, "Balinese-Whisper.bat")
        icon = os.path.join(ROOT, "app.ico")
        
        ps_cmd = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("{shortcut_path}")
$s.TargetPath = "{target}"
$s.WorkingDirectory = "{ROOT}"
$s.IconLocation = "{icon}"
$s.Description = "Aplikasi Balinese Whisper ASR"
$s.Save()
"""
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10
        )
        return res.returncode == 0
    except Exception:
        return False


def find_app_browser() -> tuple:
    """
    Cari browser yang mendukung mode --app (Edge atau Chrome).
    Kembalikan (nama_browser, path_executable).
    """
    candidates = [
        ("Microsoft Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("Microsoft Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ("Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")),
    ]
    for name, path in candidates:
        if os.path.isfile(path):
            return name, path
    return None, None


def open_ui(url: str):
    """
    Buka UI dalam jendela aplikasi mandiri (tanpa tab/address bar).
    Jika browser tidak mendukung --app, gunakan browser default.
    """
    browser_name, browser_path = find_app_browser()
    if browser_path:
        profile_dir = os.path.join(tempfile.gettempdir(), "balinese-whisper-app-profile")
        cmd = [
            browser_path,
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--window-size=1000,850",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            print(f"  [UI] Membuka jendela aplikasi dengan {browser_name}...")
            proc = subprocess.Popen(cmd)
            return proc
        except Exception as e:
            print(f"  [UI] Gagal membuka {browser_name}: {e}")

    print("  [UI] Membuka di browser default...")
    webbrowser.open(url)
    return None


def wait_for_server(url: str, timeout: float = 6.0) -> bool:
    """Tunggu hingga HTTP server merespons 200 sebelum membuka antarmuka."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/api/heartbeat", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    print("=" * 60)
    print("      🎙️ BALINESE WHISPER — APLIKASI TRANSLASI SUARA")
    print("=" * 60)

    # 1. Buat desktop shortcut otomatis jika belum ada
    if sys.platform == "win32":
        if create_desktop_shortcut():
            print("  [✓] Shortcut Desktop terpasang: 'Balinese Whisper'")

    # 2. Cek Model
    models = bw_server.list_models()
    if not models:
        msg = "Folder 'models/' kosong!\nSilakan letakkan berkas model (.bin) di dalam folder models/"
        print(f"\n  [PERINGATAN] {msg}")
        show_error_box("Balinese Whisper — Model Tidak Ditemukan", msg)
    else:
        print(f"  [✓] Model ditemukan ({len(models)}): {', '.join(models)}")

    # 3. Cek Whisper CLI
    if bw_server.HAS_WHISPER_CLI:
        print("  [✓] Mesin Whisper CLI siap")
    else:
        msg = "whisper-cli.exe tidak ditemukan di folder bin/!\nPastikan berkas bin/whisper-cli.exe ada."
        print(f"  [!] {msg}")
        show_error_box("Balinese Whisper — Engine Tidak Ditemukan", msg)

    # 4. Tentukan Port
    port = find_free_port(8000)
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    # 5. Jalankan server di thread daemon
    httpd = ThreadingHTTPServer((host, port), bw_server.Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"  [✓] Server aktif di: {url}")
    print("=" * 60)

    # 6. Tunggu server siap dan buka UI
    wait_for_server(url)
    app_proc = open_ui(url)

    print("\n  Aplikasi sedang berjalan.")
    if app_proc:
        print("  (Tutup jendela aplikasi untuk mematikan server)")
    else:
        print("  (Tekan Ctrl+C di jendela ini untuk berhenti)")
    print("=" * 60)

    # 7. Tunggu sampai jendela aplikasi ditutup atau Ctrl+C
    try:
        if app_proc:
            app_proc.wait()
            print("\n  Jendela aplikasi ditutup oleh pengguna.")
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Menghentikan aplikasi...")
    finally:
        print("  Membersihkan proses...")
        bw_server.kill_all_procs()
        httpd.shutdown()
        httpd.server_close()
        print("  Aplikasi selesai. Sampai jumpa!")
        time.sleep(1)


if __name__ == "__main__":
    main()
