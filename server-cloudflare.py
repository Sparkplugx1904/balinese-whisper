"""
server-cloudflare.py
====================
Menjalankan Balinese Whisper server + Cloudflare Quick Tunnel
sehingga aplikasi dapat diakses publik dari internet (HP/laptop lain
bisa scan QR atau buka URL publik tanpa perlu satu jaringan WiFi).

Arsitektur:
   +--------------------+        +-----------------------+
   |  Balinese Whisper  |  --->  |   Cloudflare Tunnel   |  --->  https://xxx.trycloudflare.com
   |  HTTP @ 127.0.0.1  |        |  (cloudflared CLI)    |
   +--------------------+        +-----------------------+

Kebutuhan:
  1. Python 3.8+  (stdlib saja, TIDAK perlu pip install)
  2. cloudflared CLI di PATH:
       Windows : winget install Cloudflare.cloudflared
       Manual  : https://github.com/cloudflare/cloudflared/releases
  3. (Opsional, untuk popup QR) pip install qrcode pillow
  4. File server.py + cloudflare.py di direktori yang sama

Cara pakai:
  python server-cloudflare.py
"""

import os
import sys
import time
import socket
import atexit
import threading
from http.server import ThreadingHTTPServer


# ── Impor modul lokal ────────────────────────────────────────
import server     as bw_server
import cloudflare as cf_tunnel


# ════════════════════════════════════════════════════════════════
#  Utilitas
# ════════════════════════════════════════════════════════════════

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def run_whisper_server(host: str, port: int):
    """Jalankan HTTP server whisper (blocking, di thread daemon)."""
    srv = ThreadingHTTPServer((host, port), bw_server.Handler)
    print(f"  [Server] Listening on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def banner(title: str, char: str = "=") -> str:
    return char * 64


# ════════════════════════════════════════════════════════════════
#  Popup QR (opsional — butuh tkinter + Pillow + qrcode)
# ════════════════════════════════════════════════════════════════

def _try_show_qr_popup(port: int, url_state: dict):
    """Coba tampilkan popup Tkinter dengan QR code. Return True jika berhasil."""
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
        import qrcode
    except ImportError as e:
        print(f"  [QR popup dilewati: modul tidak tersedia - {e}]")
        print(f"  Install dengan: pip install qrcode pillow")
        return False

    class QRPopup:
        def __init__(self, port: int, url_state: dict):
            self.port = port
            self.url_state = url_state
            self._last_shown = ""
            self._tk_img = None

            self.root = tk.Tk()
            self.root.title("Balinese Whisper — QR Access")
            self.root.geometry("380x500")
            self.root.resizable(False, False)
            self.root.attributes("-topmost", True)
            self.root.configure(bg="white")

            tk.Label(
                self.root, text="BALINESE WHISPER",
                font=("Arial", 14, "bold"), bg="white", fg="#222",
            ).pack(pady=(14, 2))

            self.lbl_status = tk.Label(
                self.root, text="Menghubungkan ke Cloudflare...",
                font=("Arial", 9, "bold"), bg="white", fg="#888",
            )
            self.lbl_status.pack(pady=(2, 8))

            self.lbl_qr = tk.Label(self.root, bg="white", text="...", font=("Arial", 10))
            self.lbl_qr.pack()

            self.lbl_url = tk.Label(
                self.root, text="", font=("Courier", 9, "bold"),
                bg="white", fg="blue", wraplength=340, cursor="hand2",
            )
            self.lbl_url.pack(pady=(8, 2))
            self.lbl_url.bind("<Button-1>", self._copy_to_clipboard)

            self.lbl_local = tk.Label(
                self.root, text="", font=("Courier", 8),
                bg="white", fg="#888", wraplength=340,
            )
            self.lbl_local.pack(pady=(0, 4))

            tk.Label(
                self.root, text="(klik URL untuk menyalin)",
                font=("Arial", 8), bg="white", fg="#aaa",
            ).pack()

            self._render(f"http://{get_local_ip()}:{port}", "Jaringan Lokal (Cloudflare menghubungkan...)", "#666")
            self._poll()

        def _copy_to_clipboard(self, _evt=None):
            self.root.clipboard_clear()
            self.root.clipboard_append(self._last_shown)
            old = self.lbl_status.cget("text")
            old_color = self.lbl_status.cget("fg")
            self.lbl_status.config(text="URL tersalin ke clipboard", fg="#0066cc")
            self.root.after(2000, lambda: self.lbl_status.config(text=old, fg=old_color))

        def _render(self, url: str, status_text: str, status_color: str):
            if url == self._last_shown:
                return
            self._last_shown = url
            self.lbl_status.config(text=status_text, fg=status_color)
            self.lbl_url.config(text=url, fg=status_color)
            qr = qrcode.QRCode(
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8, border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img = img.resize((280, 280), __import__("PIL").Image.Resampling.LANCZOS)
            self._tk_img = ImageTk.PhotoImage(img)
            self.lbl_qr.config(image=self._tk_img, text="")
            self.lbl_local.config(text=f"http://{get_local_ip()}:{self.port}")

        def _poll(self):
            with self.url_state["lock"]:
                pub = self.url_state["value"]
            if pub:
                self._render(pub, "Public URL (Cloudflare) - ONLINE", "#0066cc")
            self.root.after(1500, self._poll)

        def run(self):
            self.root.mainloop()

    QRPopup(port, url_state).run()
    return True


# ════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════

def main():
    host = "127.0.0.1"
    port = int(os.environ.get("PORT", "8000"))

    # State bersama untuk URL publik
    url_state = {"value": None, "lock": threading.Lock()}

    def on_public_url(url: str):
        with url_state["lock"]:
            url_state["value"] = url
        bar = banner("")
        print()
        print(bar)
        print("  APLIKASI BALINESE WHISPER - ONLINE DI INTERNET")
        print(bar)
        print(f"  URL publik : {url}")
        print(f"  URL lokal  : http://{get_local_ip()}:{port}")
        print(bar)
        print("  Bagikan URL publik ke HP / teman untuk akses jarak jauh.")
        print("  Tekan Ctrl+C untuk mematikan server + tunnel.")
        print(bar)
        print(flush=True)

    bar = banner("")
    print(bar)
    print("  BALINESE WHISPER  +  CLOUDFLARE QUICK TUNNEL")
    print(bar)
    print(f"  Local IP  : {get_local_ip()}")
    print(f"  Port      : {port}")
    print(f"  Models    : {bw_server.list_models() or '(tidak ada - taruh .bin di folder models/)'}")
    print(f"  cloudflared: {'OK' if _cloudflared_available() else 'TIDAK ADA - install dulu'}")
    print(bar)
    print("  Syarat:  cloudflared ada di PATH (atau install via winget)")
    print("  Download manual: https://github.com/cloudflare/cloudflared/releases")
    print(bar)
    print(flush=True)

    if not _cloudflared_available():
        print()
        print("  cloudflared TIDAK ditemukan di PATH. Install dulu:")
        print("    winget install Cloudflare.cloudflared")
        print("  atau download binary dari link di atas, taruh di folder ini,")
        print("  dan jalankan ulang script ini.")
        print()
        # Tetap jalan local-only (tidak exit)
        try:
            run_whisper_server(host, port)
        except KeyboardInterrupt:
            pass
        return

    # 1. Jalankan HTTP server di thread terpisah
    server_thread = threading.Thread(
        target=run_whisper_server,
        args=(host, port),
        daemon=True,
        name="whisper-http",
    )
    server_thread.start()
    time.sleep(1.0)

    # 2. Jalankan Cloudflare tunnel
    tunnel = cf_tunnel.start_tunnel(port=port, on_url_update=on_public_url)

    # 3. Cleanup saat exit
    def cleanup():
        print("\n  Mematikan tunnel & server...")
        try:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=4)
            except Exception:
                tunnel.kill()
        except Exception:
            pass
    atexit.register(cleanup)

    # 4. Tampilkan popup QR (jika modul tersedia) ATAU blocking main loop
    if not _try_show_qr_popup(port, url_state):
        print("  [Popup QR tidak tersedia. Tekan Ctrl+C untuk keluar.]")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def _cloudflared_available() -> bool:
    import shutil
    return shutil.which("cloudflared") is not None


if __name__ == "__main__":
    main()
