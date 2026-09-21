"""
app_gui.py
==========
Aplikasi Windows Portable untuk Balinese Whisper ASR.
Fitur:
- Menjalankan HTTP server secara otomatis di background.
- Mengaktifkan Cloudflare Quick Tunnel secara otomatis (memakai cloudflared.exe lokal).
- Menampilkan jendela GUI native dengan QR Code agar mudah di-scan dari HP.
- Tombol salin URL publik dan tombol buka web di browser komputer.
- Pembersihan proses saat aplikasi ditutup.
"""

import os
import sys
import time
import socket
import tempfile
import threading
import webbrowser
import subprocess
from http.server import ThreadingHTTPServer

# Impor GUI & QR
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import qrcode

# Setup path lokal & PyInstaller frozen
if getattr(sys, 'frozen', False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, ROOT)
import server as bw_server
import cloudflare as cf_tunnel


def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
    """Cari port kosong mulai dari start_port."""
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class BalineseWhisperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Balinese Whisper — Portable Server & Tunnel")
        self.root.geometry("450x640")
        self.root.resizable(False, False)
        self.root.configure(bg="#080b0f")

        # Set Icon
        icon_path = os.path.join(ROOT, "app.ico")
        if os.path.isfile(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        self.port = find_free_port(8000)
        self.local_url = f"http://127.0.0.1:{self.port}"
        self.lan_url = f"http://{get_local_ip()}:{self.port}"
        self.public_url = None
        self.tunnel_proc = None
        self.httpd = None
        self.qr_img_tk = None

        self._build_ui()
        self._start_services()

        # Tangani saat tombol X (tutup jendela) diklik
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _build_ui(self):
        # ── Header ──
        header_frame = tk.Frame(self.root, bg="#080b0f")
        header_frame.pack(fill="x", padx=20, pady=(18, 10))

        title_lbl = tk.Label(
            header_frame,
            text="🎙️ BALINESE WHISPER",
            font=("Segoe UI", 16, "bold"),
            fg="#e8935a",
            bg="#080b0f"
        )
        title_lbl.pack()

        subtitle_lbl = tk.Label(
            header_frame,
            text="Automatic Speech Recognition Bahasa Bali",
            font=("Segoe UI", 9),
            fg="#8892b0",
            bg="#080b0f"
        )
        subtitle_lbl.pack(pady=(2, 0))

        # ── Status Card ──
        status_card = tk.Frame(self.root, bg="#0f1318", highlightbackground="#1e2530", highlightthickness=1)
        status_card.pack(fill="x", padx=20, pady=5)

        self.lbl_server_status = tk.Label(
            status_card,
            text=f"● Server Lokal: {self.local_url}",
            font=("Consolas", 8),
            fg="#5ae8b0",
            bg="#0f1318",
            anchor="w"
        )
        self.lbl_server_status.pack(fill="x", padx=12, pady=(8, 2))

        self.lbl_tunnel_status = tk.Label(
            status_card,
            text="● Cloudflare Tunnel: Menghubungkan lorong...",
            font=("Consolas", 8, "bold"),
            fg="#e8935a",
            bg="#0f1318",
            anchor="w"
        )
        self.lbl_tunnel_status.pack(fill="x", padx=12, pady=(2, 8))

        # ── QR Code Frame ──
        qr_card = tk.Frame(self.root, bg="#0f1318", highlightbackground="#1e2530", highlightthickness=1)
        qr_card.pack(fill="both", expand=True, padx=20, pady=10)

        self.lbl_qr_title = tk.Label(
            qr_card,
            text="PINDAI QR UNTUK AKSES DARI HP",
            font=("Segoe UI", 9, "bold"),
            fg="#c8d0dc",
            bg="#0f1318"
        )
        self.lbl_qr_title.pack(pady=(10, 6))

        # QR Image Placeholder
        self.lbl_qr = tk.Label(
            qr_card,
            text="Menghubungkan ke Cloudflare...\n\nMohon tunggu beberapa detik.",
            font=("Segoe UI", 10),
            fg="#8892b0",
            bg="#080b0f",
            width=28,
            height=12
        )
        self.lbl_qr.pack(pady=4)

        # URL Label
        self.lbl_url = tk.Label(
            qr_card,
            text="Menunggu URL publik...",
            font=("Consolas", 9, "bold"),
            fg="#8892b0",
            bg="#0f1318",
            wraplength=380,
            cursor="hand2"
        )
        self.lbl_url.pack(pady=(8, 4))
        self.lbl_url.bind("<Button-1>", lambda e: self.copy_url())

        # ── Buttons ──
        btn_frame = tk.Frame(self.root, bg="#080b0f")
        btn_frame.pack(fill="x", padx=20, pady=8)

        self.btn_copy = tk.Button(
            btn_frame,
            text="📋 Salin Link",
            font=("Segoe UI", 9, "bold"),
            bg="#1e2530",
            fg="#c8d0dc",
            activebackground="#2a3442",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=6,
            command=self.copy_url
        )
        self.btn_copy.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.btn_open_browser = tk.Button(
            btn_frame,
            text="🌐 Buka di Komputer",
            font=("Segoe UI", 9, "bold"),
            bg="#e8935a",
            fg="#080b0f",
            activebackground="#f0a570",
            activeforeground="#000000",
            relief="flat",
            padx=10,
            pady=6,
            command=self.open_in_browser
        )
        self.btn_open_browser.pack(side="right", expand=True, fill="x", padx=(4, 0))

        # ── Footer ──
        models = bw_server.list_models()
        model_info = f"Model Aktif: {len(models)} file .bin terdeteksi" if models else "PERINGATAN: Folder models/ kosong"
        footer_lbl = tk.Label(
            self.root,
            text=f"{model_info}  •  Tutup jendela ini untuk mematikan",
            font=("Segoe UI", 8),
            fg="#4a5568",
            bg="#080b0f"
        )
        footer_lbl.pack(side="bottom", pady=(0, 10))

    def _start_services(self):
        # 1. Jalankan HTTP Server lokal di Thread Daemon
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), bw_server.Handler)
            server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            server_thread.start()
        except Exception as e:
            messagebox.showerror("Error Server", f"Gagal menjalankan server lokal:\n{e}")
            return

        # 2. Jalankan Cloudflare Tunnel di Background
        def start_cf():
            try:
                self.tunnel_proc = cf_tunnel.start_tunnel(
                    port=self.port,
                    on_url_update=self._on_public_url
                )
            except Exception as e:
                self.root.after(0, lambda: self._on_tunnel_error(str(e)))

        threading.Thread(target=start_cf, daemon=True).start()

    def _on_public_url(self, url: str):
        self.public_url = url
        # Update GUI harus dari thread utama Tkinter
        self.root.after(0, lambda: self._update_gui_with_url(url))

    def _on_tunnel_error(self, err_msg: str):
        self.lbl_tunnel_status.config(
            text=f"● Tunnel Offline (Pakai Jaringan Lokal)",
            fg="#e85a5a"
        )
        self.lbl_url.config(
            text=f"{self.lan_url}",
            fg="#5ae8b0"
        )
        # Render QR kode lokal
        self._render_qr(self.lan_url)

    def _update_gui_with_url(self, url: str):
        self.lbl_tunnel_status.config(
            text="● Cloudflare Tunnel: ONLINE (Bisa diakses dari mana saja)",
            fg="#5ae8b0"
        )
        self.lbl_url.config(
            text=url,
            fg="#5ae8b0"
        )
        self._render_qr(url)

    def _render_qr(self, data: str):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#080b0f", back_color="#ffffff")
        img = img.resize((210, 210), Image.Resampling.LANCZOS)
        self.qr_img_tk = ImageTk.PhotoImage(img)
        self.lbl_qr.config(image=self.qr_img_tk, text="", width=210, height=210, bg="#ffffff")

    def copy_url(self):
        target = self.public_url or self.local_url
        self.root.clipboard_clear()
        self.root.clipboard_append(target)
        old_text = self.btn_copy.cget("text")
        self.btn_copy.config(text="✓ Tersalin ke Clipboard!", bg="#5ae8b0", fg="#080b0f")
        self.root.after(2000, lambda: self.btn_copy.config(text=old_text, bg="#1e2530", fg="#c8d0dc"))

    def open_in_browser(self):
        target = self.local_url
        webbrowser.open(target)

    def on_closing(self):
        # Hentikan proses Cloudflare tunnel
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
                try:
                    self.tunnel_proc.wait(timeout=2)
                except Exception:
                    self.tunnel_proc.kill()
            except Exception:
                pass

        # Hentikan semua proses whisper-cli
        try:
            bw_server.kill_all_procs()
        except Exception:
            pass

        # Matikan HTTP server
        if self.httpd:
            try:
                threading.Thread(target=self.httpd.shutdown, daemon=True).start()
            except Exception:
                pass

        self.root.destroy()
        sys.exit(0)


def main():
    root = tk.Tk()
    app = BalineseWhisperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
