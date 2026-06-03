import subprocess
import threading
import re

def start_tunnel(port, on_url_update=None):
    """
    Menjalankan Cloudflare Quick Tunnel.
    
    Args:
        port: Port lokal Flask yang akan di-expose.
        on_url_update: Callback(url: str) yang dipanggil setiap kali
                       URL publik Cloudflare terdeteksi atau berubah.
    Returns:
        process: Subprocess dari cloudflared.
    """
    print(f"[Cloudflare] Meminta lorong rahasia untuk port {port}...")

    command = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def monitor_output():
        for line in iter(process.stdout.readline, ''):
            match = url_pattern.search(line)
            if match:
                url = match.group(0)
                bar = "*" * 60
                print("\n" + bar)
                print(f"  WEB ONLINE DI: {url}")
                print(bar + "\n")

                # Panggil callback jika disediakan
                if on_url_update:
                    on_url_update(url)

    monitor_thread = threading.Thread(target=monitor_output, daemon=True)
    monitor_thread.start()

    return process