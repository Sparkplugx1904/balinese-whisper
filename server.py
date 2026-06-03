"""
╔══════════════════════════════════════════════════════════════════╗
║                 BALINESE WHISPER SERVER                         ║
║                                                                  ║
║  HTTP server (stdlib) untuk UI di index.html                    ║
║  Engine : bin/whisper-cli.exe (per-request subprocess)          ║
║  Audio  : ffmpeg  → normalisasi ke WAV + split 30 detik        ║
║  Stream : Server-Sent Events (SSE) untuk upload tab             ║
║  JSON   : simple POST/response untuk live tab                   ║
║                                                                  ║
║  Cara jalan:                                                     ║
║      pip tidak perlu install apa-apa. Cukup:                    ║
║          python server.py                                        ║
║      Lalu buka http://127.0.0.1:8000 di browser.                ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import uuid
import shutil
import threading
import subprocess
import tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


# ════════════════════════════════════════════════════════════════
#  KONFIGURASI PATH
# ════════════════════════════════════════════════════════════════

ROOT = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(ROOT, "models")
BIN_DIR    = os.path.join(ROOT, "bin")
WHISPER_CLI = os.path.join(BIN_DIR, "whisper-cli.exe")
WORK_DIR   = os.path.join(tempfile.gettempdir(), "balinese-whisper")
os.makedirs(WORK_DIR, exist_ok=True)

# Cek ketersediaan tool
HAS_WHISPER_CLI = os.path.isfile(WHISPER_CLI)
HAS_FFMPEG      = shutil.which("ffmpeg") is not None
HAS_FFPROBE     = shutil.which("ffprobe") is not None

# Batas transkripsi konkuren (hemat RAM karena tiap subprocess muat model)
MAX_CONCURRENT = 2
CHUNK_SECONDS  = 30


# ════════════════════════════════════════════════════════════════
#  STATE GLOBAL
# ════════════════════════════════════════════════════════════════

state_lock = threading.Lock()
state = {
    "selected_model": None,
    "status":         "idle",
    "message":        "Pilih model untuk memulai",
}

# Daftar proses whisper-cli yang sedang jalan (untuk fitur cancel)
active_procs   = set()
active_lock    = threading.Lock()

# Batas paralel
semaphore      = threading.Semaphore(MAX_CONCURRENT)


# ════════════════════════════════════════════════════════════════
#  UTILITAS
# ════════════════════════════════════════════════════════════════

def list_models() -> list:
    if not os.path.isdir(MODELS_DIR):
        return []
    return sorted(f for f in os.listdir(MODELS_DIR) if f.lower().endswith(".bin"))


def run_subprocess(cmd: list, timeout: int = 600) -> tuple:
    """Jalankan subprocess dan kembalikan (stdout, stderr, returncode)."""
    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    with active_lock:
        active_procs.add(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    finally:
        with active_lock:
            active_procs.discard(proc)
    return stdout, stderr, proc.returncode


def get_duration(audio_path: str) -> float:
    if not HAS_FFPROBE:
        return 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def to_wav(src: str, dst: str) -> bool:
    """Normalisasi audio ke WAV PCM 16-bit mono. Return True jika berhasil."""
    if not HAS_FFMPEG:
        # Tanpa ffmpeg, harap input sudah WAV
        if src.lower().endswith(".wav"):
            shutil.copy2(src, dst)
            return True
        return False
    try:
        cmd = [
            "ffmpeg", "-y", "-i", src,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", "16000",
            dst,
        ]
        r = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return True
    except Exception:
        return False


def split_chunks(wav_path: str, out_dir: str, chunk_sec: int = CHUNK_SECONDS) -> list:
    """Pecah WAV menjadi chunk ~chunk_sec detik. Return list path terurut."""
    if not HAS_FFMPEG:
        return [wav_path]
    pattern = os.path.join(out_dir, "chunk_%03d.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path,
             "-f", "segment",
             "-segment_time", str(chunk_sec),
             "-c", "copy",
             pattern],
            check=True, capture_output=True, text=True, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return sorted(
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if f.startswith("chunk_") and f.endswith(".wav")
        )
    except Exception:
        return [wav_path]


def transcribe_chunk(audio_path: str, model_path: str, language: str) -> str:
    """Transkrip satu file audio. Return plain text."""
    if not HAS_WHISPER_CLI:
        raise RuntimeError("whisper-cli.exe tidak ditemukan di folder bin/")

    # Mapping bahasa: whisper-cli tidak kenal 'ban' walau model-nya fine-tuned untuk Bali.
    # Pakai 'id' (Indonesia) sebagai gantinya — tokenizer dekat.
    LANG_MAP = {"ban": "id"}
    lang = LANG_MAP.get((language or "").lower(), language or "id")
    if lang == "auto":
        lang_arg = "auto"
    elif lang:
        lang_arg = lang
    else:
        lang_arg = None

    cmd = [
        WHISPER_CLI,
        "-m", model_path,
        "-f", audio_path,
        "-nt",
        "-np",
        "-t", "4",
    ]
    if lang_arg:
        cmd += ["-l", lang_arg]

    stdout, stderr, rc = run_subprocess(cmd, timeout=600)
    if rc != 0:
        err_tail = (stderr or "").strip().splitlines()[-1] if stderr else "unknown"
        raise RuntimeError(f"whisper-cli gagal (rc={rc}): {err_tail}")

    # Filter baris log/info, ambil hanya baris transkripsi.
    # whisper-cli menulis: whisper_* (init/load/timings), system_info:, main: processing...
    out = []
    for line in (stdout or "").splitlines():
        s = line.rstrip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("whisper_"):
            continue
        if low.startswith("system_info:") or low.startswith("main:"):
            continue
        out.append(s)
    return " ".join(out).strip()


def kill_all_procs() -> int:
    """Kill semua subprocess whisper-cli yang aktif. Return jumlah yang di-kill."""
    with active_lock:
        procs = list(active_procs)
    killed = 0
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
                killed += 1
        except Exception:
            pass
    return killed


# ════════════════════════════════════════════════════════════════
#  MULTIPART PARSER (tanpa dependency eksternal)
# ════════════════════════════════════════════════════════════════

def parse_multipart(body: bytes, content_type: str) -> tuple:
    """
    Parse multipart/form-data. Return (fields, files).
    fields = {name: str}
    files  = {name: (filename, data_bytes)}
    """
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part[9:].strip().strip('"')
            break
    if not boundary:
        raise ValueError("multipart: boundary tidak ditemukan")

    delim = ("--" + boundary).encode("latin-1")
    crlf  = b"\r\n"
    parts = body.split(delim)

    fields, files = {}, {}

    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if not part.strip():
            continue
        sep = crlf + crlf
        if sep not in part:
            continue
        header_blob, _, data = part.partition(sep)
        if data.endswith(crlf):
            data = data[:-2]

        name = None
        filename = None
        for line in header_blob.decode("latin-1").split("\r\n"):
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            if k.strip().lower() == "content-disposition":
                for attr in v.split(";"):
                    attr = attr.strip()
                    if attr.startswith("name="):
                        name = attr[5:].strip('"')
                    elif attr.startswith("filename="):
                        filename = attr[9:].strip('"')
        if filename is not None:
            files[name] = (filename, data)
        elif name:
            fields[name] = data.decode("utf-8", errors="replace")
    return fields, files


# ════════════════════════════════════════════════════════════════
#  HTTP HANDLER
# ════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "BalineseWhisper/1.0"

    # ── Quiet logging ────────────────────────────────────────
    def log_message(self, format, *args):
        sys.stderr.write(f"  [{self.log_date_time_string()}] {format % args}\n")

    # ── JSON helper ──────────────────────────────────────────
    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ── Routes ───────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.serve_index()
        if path == "/api/models":
            return self.api_models()
        if path == "/api/load-status":
            return self.api_load_status()
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/heartbeat":
            return self.api_heartbeat()
        if path == "/api/load":
            return self.api_load()
        if path == "/api/cancel":
            return self.api_cancel()
        if path == "/api/transcribe":
            return self.api_transcribe()
        return self._json(404, {"error": "not found"})

    # ── Static: index.html ───────────────────────────────────
    def serve_index(self):
        path = os.path.join(ROOT, "index.html")
        if not os.path.isfile(path):
            return self._json(404, {"error": "index.html not found"})
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── API: /api/models ─────────────────────────────────────
    def api_models(self):
        return self._json(200, {"models": list_models()})

    # ── API: /api/heartbeat ──────────────────────────────────
    def api_heartbeat(self):
        return self._json(200, {"ok": True, "ts": time.time()})

    # ── API: /api/load-status ────────────────────────────────
    def api_load_status(self):
        with state_lock:
            return self._json(200, {
                "status":  state["status"],
                "message": state["message"],
            })

    # ── API: /api/load ───────────────────────────────────────
    def api_load(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        model = data.get("model")
        if not model:
            return self._json(400, {"error": "model required"})
        if model not in list_models():
            return self._json(400, {"error": f"model '{model}' not found"})

        with state_lock:
            state["selected_model"] = model
            state["status"]         = "loading"
            state["message"]        = f"Memuat model {model}..."

        def _finish():
            model_path = os.path.join(MODELS_DIR, model)
            # Warm disk cache dengan membaca seluruh file model
            try:
                with open(model_path, "rb") as f:
                    while f.read(1024 * 1024):
                        pass
            except Exception:
                pass
            time.sleep(0.3)
            with state_lock:
                if state["selected_model"] == model:
                    state["status"]  = "ready"
                    state["message"] = f"Model '{model}' siap"
        threading.Thread(target=_finish, daemon=True).start()
        return self._json(200, {"ok": True})

    # ── API: /api/cancel ─────────────────────────────────────
    def api_cancel(self):
        killed = kill_all_procs()
        return self._json(200, {"ok": True, "killed": killed})

    # ── API: /api/transcribe ─────────────────────────────────
    def api_transcribe(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "multipart/form-data required"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._json(400, {"error": "empty body"})
        body = self.rfile.read(length)

        try:
            fields, files = parse_multipart(body, ctype)
        except Exception as e:
            return self._json(400, {"error": f"multipart parse error: {e}"})

        audio = files.get("audio")
        if not audio or not audio[1]:
            return self._json(400, {"error": "audio file missing"})
        _, audio_bytes = audio

        language = (fields.get("language") or "").strip()
        source   = (fields.get("source")   or "upload").strip()

        with state_lock:
            model = state["selected_model"]
        if not model:
            return self._json(400, {"error": "no model loaded"})
        model_path = os.path.join(MODELS_DIR, model)
        if not os.path.isfile(model_path):
            return self._json(400, {"error": f"model file missing: {model_path}"})

        # Simpan ke temp job dir
        job_id = uuid.uuid4().hex[:12]
        job_dir = os.path.join(WORK_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        raw_path  = os.path.join(job_dir, "raw_input")
        wav_path  = os.path.join(job_dir, "audio.wav")
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        # Normalisasi ke WAV
        if not to_wav(raw_path, wav_path):
            shutil.rmtree(job_dir, ignore_errors=True)
            return self._json(500, {
                "error": "gagal konversi audio ke WAV. Pastikan ffmpeg terinstall."
            })

        # ── Mode LIVE → JSON sederhana ──────────────────────
        if source == "live":
            try:
                with semaphore:
                    text = transcribe_chunk(wav_path, model_path, language)
            except Exception as e:
                shutil.rmtree(job_dir, ignore_errors=True)
                return self._json(500, {"text": "", "error": str(e)})
            shutil.rmtree(job_dir, ignore_errors=True)
            return self._json(200, {"text": text})

        # ── Mode UPLOAD/RECORD → SSE streaming ──────────────
        self.send_response(200)
        self.send_header("Content-Type",      "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control",     "no-cache")
        self.send_header("Connection",        "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        alive = True
        def send_event(event: str, data: dict | None = None) -> bool:
            nonlocal alive
            if not alive:
                return False
            try:
                if data is None:
                    msg = f"event: {event}\ndata: \n\n"
                else:
                    payload = json.dumps(data, ensure_ascii=False)
                    msg = f"event: {event}\ndata: {payload}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                alive = False
                return False

        try:
            duration = get_duration(wav_path)
            chunks   = split_chunks(wav_path, job_dir, CHUNK_SECONDS)
            total    = len(chunks)

            if not send_event("start", {"total": total, "duration": round(duration, 2)}):
                return

            running = ""
            for i, ck in enumerate(chunks):
                if not alive:
                    break
                if not send_event("processing", {"index": i, "total": total}):
                    break
                try:
                    with semaphore:
                        text = transcribe_chunk(ck, model_path, language)
                    if text:
                        running = (running + " " + text).strip() if running else text
                    if not send_event("chunk", {
                        "index":   i,
                        "total":   total,
                        "text":    text,
                        "running": running,
                    }):
                        break
                except Exception as e:
                    if not send_event("chunk_error", {
                        "index": i, "total": total, "error": str(e)
                    }):
                        break

            if alive:
                if not send_event("done", {"text": running}):
                    pass

        except Exception as e:
            send_event("error", {"error": str(e)})
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))

    bar = "=" * 60
    print(bar)
    print("  BALINESE WHISPER SERVER")
    print(bar)
    print(f"  Root        : {ROOT}")
    print(f"  Models dir  : {MODELS_DIR}")
    models = list_models()
    print(f"  Models      : {models if models else '(kosong — taruh file .bin di folder models/)'}")
    print(f"  Whisper CLI : {'OK ' + WHISPER_CLI if HAS_WHISPER_CLI else 'MISSING — ' + WHISPER_CLI}")
    print(f"  ffmpeg      : {'OK' if HAS_FFMPEG else 'MISSING (audio non-WAV akan gagal)'}")
    print(f"  ffprobe     : {'OK' if HAS_FFPROBE else 'MISSING (duration tidak akan dikirim)'}")
    print(f"  Workers     : {MAX_CONCURRENT} concurrent transcriptions")
    print(f"  Chunk size  : {CHUNK_SECONDS} detik")
    print(bar)
    print(f"  Buka di browser ->  http://{host}:{port}")
    print(f"  Tekan Ctrl+C untuk berhenti")
    print(bar)

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Mematikan server...")
        kill_all_procs()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
