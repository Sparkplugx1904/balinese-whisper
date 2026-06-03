from flask import Flask, render_template, request, send_from_directory, jsonify
import os
from ultralytics import YOLO
from werkzeug.utils import secure_filename
from PIL import Image, ImageTk
import pillow_heif
import base64
import io
import uuid
import socket
import qrcode
import tkinter as tk
import threading
import cloudflare
import atexit

app = Flask(__name__)

# ===============================
# Konfigurasi Upload
# ===============================
UPLOAD_FOLDER = "images_upload"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'heic'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ===============================
# API Authentication Key & Load Model
# ===============================
API_AUTH_KEY = os.environ.get("API_AUTH_KEY", "")

models = {
    "anedet": YOLO("Anedet AI/best2.pt"),
    "diadet": YOLO("Diadet AI/best.pt")
}

medical1 = {
    "anedet": [
        "Pale or white color of the conjunctiva, instead of the usual pink or reddish hue",
        "The conjungtiva appears lighter or colorless"
    ],
    "diadet": [
        "Tongue changes color to brown, black, or yellowish",
        "White patches or coating on the tongue that can sometimes be scraped off",
        "The tongue surface appears dry and cracked"
    ]
}

# ===============================
# Fungsi Bantu
# ===============================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_heic_to_jpeg(image_data):
    try:
        heif_file = pillow_heif.read_heif(image_data)
        img = Image.frombytes(
            heif_file.mode, heif_file.size, heif_file.data, "raw"
        ).convert("RGB")
        output = io.BytesIO()
        img.save(output, format='JPEG')
        output.seek(0)
        return output
    except Exception as e:
        raise ValueError(f"Failed to convert HEIC: {e}")

HEIC_MAGIC_BYTES_1 = b'\x00\x00\x00\x18'
HEIC_MAGIC_BYTES_2 = b'ftypheic'
HEIC_MAGIC_BYTES_3 = b'ftypheix'

def decode_base64_image(base64_string):
    try:
        if ',' in base64_string:
            base64_string = base64_string.split(',', 1)[1]
        image_data = base64.b64decode(base64_string)
        is_heic = False
        if len(image_data) >= 16:
            if image_data[:4] == HEIC_MAGIC_BYTES_1 or \
               image_data[4:12] == HEIC_MAGIC_BYTES_2 or \
               image_data[4:12] == HEIC_MAGIC_BYTES_3:
                is_heic = True
        if is_heic:
            image_data = convert_heic_to_jpeg(io.BytesIO(image_data)).read()
        image = Image.open(io.BytesIO(image_data))
        return image
    except Exception as e:
        raise ValueError(f"Failed to decode image: {e}")

def authenticate_request(request_data):
    authkey = request_data.get('authkey', '')
    if not API_AUTH_KEY:
        return False, "API authentication not configured"
    if authkey != API_AUTH_KEY:
        return False, "Invalid authentication key"
    return True, None

def process_prediction_results(results):
    if results and results[0].boxes is not None and len(results[0].boxes) > 0:
        pred_class = results[0].names[int(results[0].boxes.cls[0])]
        confidence = float(results[0].boxes.conf[0])
        return True, pred_class, confidence
    return False, None, None

# ===============================
# Rute Flask
# ===============================
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/images_upload/<filename>")
def display_image(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/", methods=["POST"])
def predict():
    if 'imagefile' not in request.files:
        return render_template("index.html", error="❌ No file uploaded!")
    imagefile = request.files["imagefile"]
    if imagefile.filename == "":
        return render_template("index.html", error="❌ Empty filename!")
    if not allowed_file(imagefile.filename):
        return render_template("index.html", error="❌ Unsupported file format!")

    model_choice = request.form.get("model", 0)
    if model_choice not in models:
        return render_template("index.html", error="❌ Invalid model choice!")

    filename = secure_filename(imagefile.filename)
    image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    imagefile.save(image_path)

    if filename.lower().endswith(".heic"):
        try:
            heif_file = pillow_heif.read_heif(image_path)
            img = Image.frombytes(
                heif_file.mode, heif_file.size, heif_file.data, "raw"
            ).convert("RGB")
            new_filename = filename.rsplit(".", 1)[0] + ".jpg"
            new_image_path = os.path.join(app.config["UPLOAD_FOLDER"], new_filename)
            img.save(new_image_path, "JPEG")
            os.remove(image_path)
            filename = new_filename
            image_path = new_image_path
        except Exception as e:
            return render_template("index.html", error=f"❌ Failed to convert HEIC: {e}")

    try:
        results = models[model_choice](image_path)
        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            pred_class = results[0].names[int(results[0].boxes.cls[0])]
            percent = f"{float(results[0].boxes.conf[0]) * 100:.2f}"
            return render_template(
                "index.html",
                prediction=pred_class,
                percent=percent,
                image_file=filename,
                model_used=model_choice,
                medical=medical1[model_choice]
            )
        else:
            return render_template("index.html", error="No object detected.", image_file=filename)
    except Exception as e:
        return render_template("index.html", error=f"❌ Error during prediction: {e}")


# ===============================
# Utilitas IP Lokal
# ===============================
def get_local_ip():
    """Mendapatkan IP lokal asli (bukan localhost)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


# ===============================
# QR Code Popup — Auto-Update
# ===============================
class QRPopupApp:
    """
    Jendela popup Tkinter yang menampilkan QR Code.

    Prioritas tampilan:
      1. URL Cloudflare  →  ditampilkan segera saat tunnel terhubung,
                            dan diperbarui otomatis bila URL berubah.
      2. IP Lokal        →  ditampilkan selama tunnel belum tersambung,
                            diperbarui setiap 3 detik bila IP berubah.
    """

    # Warna status
    COLOR_WAITING   = "#999999"   # abu  — menunggu Cloudflare
    COLOR_CLOUDFLARE = "#0066cc"  # biru — URL publik aktif
    COLOR_LOCAL      = "#2e7d32"  # hijau — IP lokal

    def __init__(self, port: int, qr_size: int = 300):
        self.port     = port
        self.qr_size  = qr_size

        self._lock          = threading.Lock()
        self._cloudflare_url: str | None = None   # diisi callback dari cloudflare.py
        self._last_local_ip: str         = ""
        self._last_shown_url: str        = ""

        # ── Bangun jendela ──────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("Andidet.AI — QR Access")
        self.root.geometry(f"{qr_size + 60}x{qr_size + 165}")
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)   # selalu di atas layar
        self.root.configure(bg="white")

        # Label baris atas: sumber URL (Cloudflare / Lokal)
        self.lbl_source = tk.Label(
            self.root,
            text="⏳ Menghubungkan ke Cloudflare...",
            font=("Arial", 10, "bold"),
            bg="white",
            fg=self.COLOR_WAITING
        )
        self.lbl_source.pack(pady=(12, 0))

        # Gambar QR
        self.lbl_qr = tk.Label(self.root, bg="white")
        self.lbl_qr.pack(pady=(4, 0))

        # Teks URL di bawah QR (dapat diklik untuk copy)
        self.lbl_url = tk.Label(
            self.root,
            text="",
            font=("Courier", 9),
            bg="white",
            fg="blue",
            cursor="hand2",
            wraplength=qr_size + 40
        )
        self.lbl_url.pack(pady=(4, 8))
        self.lbl_url.bind("<Button-1>", self._copy_url_to_clipboard)

        # Tampilkan QR IP lokal awal sambil menunggu Cloudflare
        self._refresh_local_qr()

        # Mulai loop polling IP lokal
        self._poll_local_ip()

    # ── Callback dari cloudflare.py ─────────────────────────────────────
    def set_cloudflare_url(self, url: str):
        """Dipanggil dari thread monitor Cloudflare saat URL baru terdeteksi."""
        with self._lock:
            self._cloudflare_url = url
        # Jadwalkan update UI di main thread Tkinter (thread-safe)
        self.root.after(0, lambda: self._render_qr(
            url=url,
            source_text="🌐  Public URL (Cloudflare)",
            source_color=self.COLOR_CLOUDFLARE
        ))
        print(f"[QR Popup] URL Cloudflare diperbarui → {url}")

    # ── Polling IP lokal (hanya aktif bila belum ada URL Cloudflare) ────
    def _poll_local_ip(self):
        with self._lock:
            has_cf = self._cloudflare_url is not None

        if not has_cf:
            new_ip = get_local_ip()
            if new_ip != self._last_local_ip:
                self._last_local_ip = new_ip
                self._refresh_local_qr()

        # Ulangi setiap 3 detik
        self.root.after(3000, self._poll_local_ip)

    def _refresh_local_qr(self):
        ip  = get_local_ip()
        url = f"http://{ip}:{self.port}"
        self._render_qr(
            url=url,
            source_text="📡  Local Network",
            source_color=self.COLOR_LOCAL
        )

    # ── Render QR ke jendela ────────────────────────────────────────────
    def _render_qr(self, url: str, source_text: str, source_color: str):
        """Generate dan tampilkan QR code untuk `url`."""
        if url == self._last_shown_url:
            return  # tidak ada perubahan, lewati

        self._last_shown_url = url

        # Update label sumber
        self.lbl_source.config(text=source_text, fg=source_color)

        # Update label URL
        self.lbl_url.config(text=url, fg=source_color)

        # Generate QR
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((self.qr_size, self.qr_size), Image.Resampling.LANCZOS)

        # Simpan referensi agar tidak di-GC oleh Python
        self._tk_img = ImageTk.PhotoImage(img)
        self.lbl_qr.config(image=self._tk_img)

    # ── Salin URL ke clipboard ──────────────────────────────────────────
    def _copy_url_to_clipboard(self, _event=None):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._last_shown_url)
        # Beri umpan balik visual sebentar
        original = self.lbl_tip.cget("text")
        self.lbl_tip.config(text="✅  URL tersalin!", fg="#0066cc")
        self.root.after(2000, lambda: self.lbl_tip.config(text=original, fg="#aaaaaa"))

    # ── Entry point ─────────────────────────────────────────────────────
    def run(self):
        self.root.mainloop()


# ===============================
# Run App & Parallel Tunnel
# ===============================
if __name__ == "__main__":
    PORT     = 5005
    QR_SIZE  = 300

    # 1. Buat instance popup terlebih dahulu
    qr_app = QRPopupApp(port=PORT, qr_size=QR_SIZE)

    # 2. Jalankan Cloudflare Tunnel; kirim callback ke popup
    tunnel_process = cloudflare.start_tunnel(
        port=PORT,
        on_url_update=qr_app.set_cloudflare_url  # ← dipanggil saat URL berubah
    )

    # 3. Matikan tunnel otomatis saat program ditutup
    def cleanup():
        print("\n🧹 Mematikan Cloudflare Tunnel...")
        tunnel_process.terminate()

    atexit.register(cleanup)

    # 4. Jalankan Flask di thread latar belakang
    #    (Tkinter harus jalan di main thread)
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=PORT,
            debug=False,
            use_reloader=False
        ),
        daemon=True
    )
    flask_thread.start()
    print(f"⚙️ [Flask] Server lokal berjalan di port {PORT}...")

    # 5. Jalankan popup Tkinter di main thread (blocking sampai jendela ditutup)
    qr_app.run()