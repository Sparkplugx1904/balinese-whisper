"""
╔══════════════════════════════════════════════════════════════════╗
║         ADVANCED WER EVALUATOR — MULTI-STRATEGY ENGINE          ║
║  Strategi: Soft WER + CharWER + Phonetic Norm + TER Shifts      ║
║  Bahasa  : Python 3.8+                                          ║
╚══════════════════════════════════════════════════════════════════╝

Jalankan:
  python wer_advanced.py

Pastikan file berikut ada di direktori yang sama:
  - metadata.csv          (ground truth)
  - whisper-balinese.csv  (model fine-tuned)
  - whisper-unfinetuned.csv (model original)

Output:
  - laporan_advanced_wer_balinese.csv
  - laporan_advanced_wer_unfinetuned.csv
"""

import re
import csv
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ══════════════════════════════════════════════════════════════════
# 1. PARSING CSV
# ══════════════════════════════════════════════════════════════════

def baca_csv(nama_file: str) -> List[Dict[str, str]]:
    """Membaca CSV dengan format path,sentence (sentence bisa berisi koma)."""
    rows = []
    try:
        with open(nama_file, encoding="utf-8", newline="") as f:
            content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Tidak bisa membuka file: {nama_file}")

    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Lewati header
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        # Pisahkan hanya pada koma pertama (path tidak mengandung koma)
        idx = line.index(",")
        path_val = line[:idx].strip()
        sent_val = line[idx + 1:].strip().strip('"').strip()
        rows.append({"path": path_val, "sentence": sent_val})
    return rows


# ══════════════════════════════════════════════════════════════════
# 2. TEXT NORMALIZATION
# ══════════════════════════════════════════════════════════════════

KAMUS_NORM = [
    (r"\b1\b", "satu"),  (r"\b2\b", "dua"),   (r"\b3\b", "tiga"),
    (r"\b4\b", "empat"), (r"\b5\b", "lima"),
    (r"\byg\b",   "yang"),    (r"\bdgn\b",  "dengan"),
    (r"\btdk\b",  "tidak"),   (r"\btsb\b",  "tersebut"),
    (r"\bsbb\b",  "sebagai berikut"),
    (r"\bdng\b",  "dengan"),  (r"\bnggih\b", "inggih"),
]

FONETIK_PAIRS = [
    ("ngg",                                       "ng"),
    (r"[éèê]",                                    "e"),
    (r"[âàá]",                                    "a"),
    (r"[îìí]",                                    "i"),
    (r"[ûùú]",                                    "u"),
    (r"[ôòó]",                                    "o"),
    ("ck",                                        "k"),
    ("ph",                                        "f"),
    ("oe",                                        "u"),
    ("tj",                                        "c"),
    ("dj",                                        "j"),
    ("ij",                                        "i"),
    ("ny",                                        "n"),
    (r"\bida\s+sang\b",                           "ida sanghyang"),
    (r"sanghyang|sang\s*hyang|sanggah\s*widi|sanghyang\s*widi", "dewata"),
    (r"trihita|tri\s*hita",                       "trihita"),
    (r"bareng[\s\-]+bareng",                      "barengbareng"),
    (r"gotong[\s\-]+royong",                      "gotongroyong"),
]


def tokenisasi(teks: str, fonetik: bool = False) -> List[str]:
    if not teks:
        return []
    s = teks.lower()

    # Terapkan kamus singkatan
    for pola, ganti in KAMUS_NORM:
        s = re.sub(pola, ganti, s, flags=re.IGNORECASE)

    # Tanda baca: dash → spasi
    s = re.sub(r"[-–—]", " ", s)

    # Hapus non-alfanumerik non-spasi (pertahankan UTF-8 multibyte)
    clean = ""
    for ch in s:
        if ch.isalnum() or ch == " " or ord(ch) > 127:
            clean += ch
        else:
            clean += " "
    s = clean

    # Normalisasi fonetik Bali
    if fonetik:
        for pola, ganti in FONETIK_PAIRS:
            s = re.sub(pola, ganti, s, flags=re.IGNORECASE)

    # Normalisasi spasi ganda
    s = re.sub(r"\s+", " ", s).strip()
    return s.split() if s else []


# ══════════════════════════════════════════════════════════════════
# 3. KARAKTER SIMILARITY (Normalized Levenshtein)
# ══════════════════════════════════════════════════════════════════

def edit_distance_str(a: str, b: str) -> int:
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j - 1], dp[j - 1], prev[j])
    return dp[m]


def kemiripan_karakter(kata1: str, kata2: str) -> float:
    if kata1 == kata2:
        return 1.0
    if not kata1 or not kata2:
        return 0.0
    dist = edit_distance_str(kata1, kata2)
    return 1.0 - dist / max(len(kata1), len(kata2))


def bobot_substitusi(r: str, h: str, ambang_sama: float = 0.85, ambang_mirip: float = 0.60) -> float:
    sim = kemiripan_karakter(r, h)
    if sim >= ambang_sama:
        return round((1.0 - sim) * 10000) / 10000
    if sim >= ambang_mirip:
        return 0.50
    return 1.00


# ══════════════════════════════════════════════════════════════════
# 4. WER KLASIK
# ══════════════════════════════════════════════════════════════════

@dataclass
class HasilWERKlasik:
    wer: float
    N: int
    S: int
    D: int
    I: int
    C: int


def hitung_wer_klasik(referensi: str, prediksi: str) -> HasilWERKlasik:
    ref = tokenisasi(referensi)
    hyp = tokenisasi(prediksi)
    n, m = len(ref), len(hyp)
    if n == 0:
        return HasilWERKlasik(0.0, 0, 0, 0, 0, 0)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i][j - 1], dp[i - 1][j])

    i, j = n, m
    S = D = I = C = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            C += 1; i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            S += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            D += 1; i -= 1
        else:
            I += 1; j -= 1

    wer = (S + D + I) / n
    return HasilWERKlasik(round(wer * 10000) / 10000, n, S, D, I, C)


# ══════════════════════════════════════════════════════════════════
# 5. SOFT WER
# ══════════════════════════════════════════════════════════════════

@dataclass
class HasilSoftWER:
    wer_soft: float
    N: int
    error_berbobot: float
    S_bobot: float
    D: int
    I: int
    benar: int


def hitung_soft_wer(referensi: str, prediksi: str, fonetik: bool = False) -> HasilSoftWER:
    ref = tokenisasi(referensi, fonetik)
    hyp = tokenisasi(prediksi, fonetik)
    n, m = len(ref), len(hyp)
    if n == 0:
        return HasilSoftWER(0.0, 0, 0.0, 0.0, 0, 0, 0)

    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = float(i)
    for j in range(m + 1): dp[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                w = bobot_substitusi(ref[i - 1], hyp[j - 1])
                dp[i][j] = min(
                    dp[i - 1][j - 1] + w,
                    dp[i][j - 1]     + 1.0,
                    dp[i - 1][j]     + 1.0,
                )

    total_err = dp[n][m]
    i, j = n, m
    S_bobot = 0.0
    D = I = C = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            C += 1; i -= 1; j -= 1
        elif (i > 0 and j > 0 and
              abs(dp[i][j] - (dp[i - 1][j - 1] + bobot_substitusi(ref[i - 1], hyp[j - 1]))) < 1e-9):
            S_bobot += bobot_substitusi(ref[i - 1], hyp[j - 1])
            i -= 1; j -= 1
        elif i > 0 and abs(dp[i][j] - (dp[i - 1][j] + 1.0)) < 1e-9:
            D += 1; i -= 1
        else:
            I += 1; j -= 1

    wer = total_err / n
    return HasilSoftWER(
        round(wer * 10000) / 10000,
        n,
        round(total_err * 1000) / 1000,
        round(S_bobot * 1000) / 1000,
        D, I, C,
    )


# ══════════════════════════════════════════════════════════════════
# 6. CHARACTER WER (CharWER)
# ══════════════════════════════════════════════════════════════════

@dataclass
class HasilCharWER:
    char_wer: float
    N_char: int
    S: int
    D: int
    I: int


def hitung_char_wer(referensi: str, prediksi: str) -> HasilCharWER:
    ref_tok = tokenisasi(referensi)
    hyp_tok = tokenisasi(prediksi)
    ref_str = " ".join(ref_tok)
    hyp_str = " ".join(hyp_tok)
    ref_cp = list(ref_str)
    hyp_cp = list(hyp_str)
    n, m = len(ref_cp), len(hyp_cp)
    if n == 0:
        return HasilCharWER(0.0, 0, 0, 0, 0)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_cp[i - 1] == hyp_cp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i][j - 1], dp[i - 1][j])

    i, j = n, m
    S = D = I = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_cp[i - 1] == hyp_cp[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            S += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            D += 1; i -= 1
        else:
            I += 1; j -= 1

    cw = (S + D + I) / n
    return HasilCharWER(round(cw * 10000) / 10000, n, S, D, I)


# ══════════════════════════════════════════════════════════════════
# 7. TER (Translation Edit Rate + Unigram Shifts)
# ══════════════════════════════════════════════════════════════════

@dataclass
class HasilTER:
    ter: float
    N_ter: int
    edit_final: int
    shifts: int


def hitung_ter(referensi: str, prediksi: str) -> HasilTER:
    ref = tokenisasi(referensi)
    hyp = tokenisasi(prediksi)
    n, m = len(ref), len(hyp)
    if n == 0:
        return HasilTER(0.0, 0, 0, 0)

    # Edit distance kata
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i][j - 1], dp[i - 1][j])
    edit_dist = dp[n][m]

    # Deteksi shifts via LCS
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                lcs[i][j] = lcs[i - 1][j - 1] + 1
            else:
                lcs[i][j] = max(lcs[i - 1][j], lcs[i][j - 1])

    matched_ref = [False] * n
    matched_hyp = [False] * m
    i, j = n, m
    while i > 0 and j > 0:
        if ref[i - 1] == hyp[j - 1]:
            matched_ref[i - 1] = True
            matched_hyp[j - 1] = True
            i -= 1; j -= 1
        elif lcs[i - 1][j] >= lcs[i][j - 1]:
            i -= 1
        else:
            j -= 1

    ur = Counter(ref[i] for i in range(n) if not matched_ref[i])
    uh = Counter(hyp[j] for j in range(m) if not matched_hyp[j])
    shifts = sum(min(ur[k], uh[k]) for k in ur if k in uh)

    ter = (edit_dist + 0.5 * shifts) / n
    return HasilTER(round(ter * 10000) / 10000, n, edit_dist, shifts)


# ══════════════════════════════════════════════════════════════════
# 8. KLASIFIKASI KUALITAS
# ══════════════════════════════════════════════════════════════════

def klasifikasi_kualitas(wer_klasik: float, wer_soft: float, char_wer: float) -> str:
    skor = wer_soft * 0.5 + char_wer * 0.3 + wer_klasik * 0.2
    if skor == 0.0:  return "Sempurna"
    if skor < 0.10:  return "Sangat Baik"
    if skor < 0.25:  return "Baik"
    if skor < 0.45:  return "Cukup"
    if skor < 0.70:  return "Buruk"
    return               "Sangat Buruk"


# ══════════════════════════════════════════════════════════════════
# 9. DETEKSI KATEGORI DARI PATH
# ══════════════════════════════════════════════════════════════════

def deteksi_kategori(path: str) -> str:
    p = path.lower()
    if "kurang fasih" in p or "kurang_fasih" in p or "kurangfasih" in p:
        return "Kurang Fasih"
    if "fasih" in p:
        return "Fasih"
    return "Tidak Diketahui"


# ══════════════════════════════════════════════════════════════════
# 10. ANALISIS PERBEDAAN KATA
# ══════════════════════════════════════════════════════════════════

@dataclass
class DiffItem:
    tipe: str       # "SUBSTITUSI", "DELESI", "INSERSI"
    ref_w: str
    hyp_w: str
    kemiripan: float
    penalti: float


def analisis_perbedaan(referensi: str, prediksi: str) -> List[DiffItem]:
    ref = tokenisasi(referensi)
    hyp = tokenisasi(prediksi)
    n, m = len(ref), len(hyp)
    if n == 0:
        return []

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i][j - 1], dp[i - 1][j])

    hasil = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            sim = kemiripan_karakter(ref[i - 1], hyp[j - 1])
            pen = bobot_substitusi(ref[i - 1], hyp[j - 1])
            hasil.append(DiffItem("SUBSTITUSI", ref[i - 1], hyp[j - 1], round(sim, 4), round(pen, 4)))
            i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            hasil.append(DiffItem("DELESI", ref[i - 1], "-", 0.0, 1.0))
            i -= 1
        else:
            hasil.append(DiffItem("INSERSI", "-", hyp[j - 1], 0.0, 1.0))
            j -= 1

    hasil.reverse()
    return hasil


# ══════════════════════════════════════════════════════════════════
# 11. STRUCT HASIL BARIS & AKUMULASI
# ══════════════════════════════════════════════════════════════════

@dataclass
class HasilBaris:
    path: str
    kategori: str
    N_kata: int
    N_char: int
    # Klasik
    wer_klasik: float
    S_kl: int; D_kl: int; I_kl: int; benar_kl: int
    # Soft
    wer_soft: float
    error_berbobot: float; S_bobot: float; D_soft: int; I_soft: int
    # Soft+Fonetik
    wer_soft_fonetik: float
    # CharWER
    char_wer: float
    S_ch: int; D_ch: int; I_ch: int
    # TER
    ter: float
    edit_final: int; shifts: int
    # Label
    kualitas: str


@dataclass
class AkumulasiGrup:
    nama: str
    jumlah_kalimat: int = 0
    total_N: int = 0
    total_Nc: int = 0
    total_S_kl: int = 0
    total_D_kl: int = 0
    total_I_kl: int = 0
    total_Sc: int = 0
    total_Dc: int = 0
    total_Ic: int = 0
    total_err_soft: float = 0.0
    total_wsf_p: float = 0.0
    total_ter: float = 0.0
    dist_kualitas: Dict[str, int] = field(default_factory=dict)

    def wer_klasik(self) -> float:
        return (self.total_S_kl + self.total_D_kl + self.total_I_kl) / self.total_N if self.total_N else 0.0

    def wer_soft(self) -> float:
        return self.total_err_soft / self.total_N if self.total_N else 0.0

    def wer_soft_fonetik(self) -> float:
        return self.total_wsf_p / self.jumlah_kalimat if self.jumlah_kalimat else 0.0

    def char_wer(self) -> float:
        return (self.total_Sc + self.total_Dc + self.total_Ic) / self.total_Nc if self.total_Nc else 0.0

    def ter_avg(self) -> float:
        return self.total_ter / self.jumlah_kalimat if self.jumlah_kalimat else 0.0

    def tambah(self, b: HasilBaris):
        self.jumlah_kalimat    += 1
        self.total_N           += b.N_kata
        self.total_Nc          += b.N_char
        self.total_S_kl        += b.S_kl
        self.total_D_kl        += b.D_kl
        self.total_I_kl        += b.I_kl
        self.total_Sc          += b.S_ch
        self.total_Dc          += b.D_ch
        self.total_Ic          += b.I_ch
        self.total_err_soft    += b.error_berbobot
        self.total_wsf_p       += b.wer_soft_fonetik
        self.total_ter         += b.ter
        self.dist_kualitas[b.kualitas] = self.dist_kualitas.get(b.kualitas, 0) + 1


# ══════════════════════════════════════════════════════════════════
# 12. SIMPAN CSV
# ══════════════════════════════════════════════════════════════════

def simpan_csv(data: List[HasilBaris], nama_file: str):
    header = [
        "path", "kategori", "N_kata", "N_char",
        "WER_Klasik", "S_klasik", "D_klasik", "I_klasik", "Benar_klasik",
        "WER_Soft", "Error_Berbobot", "S_bobot", "D_soft", "I_soft",
        "WER_Soft_Fonetik",
        "CharWER", "S_char", "D_char", "I_char",
        "TER", "Edit_Final", "Shifts",
        "Kualitas",
    ]
    with open(nama_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in data:
            writer.writerow([
                r.path, r.kategori, r.N_kata, r.N_char,
                f"{r.wer_klasik:.4f}", r.S_kl, r.D_kl, r.I_kl, r.benar_kl,
                f"{r.wer_soft:.4f}", f"{r.error_berbobot:.3f}", f"{r.S_bobot:.3f}", r.D_soft, r.I_soft,
                f"{r.wer_soft_fonetik:.4f}",
                f"{r.char_wer:.4f}", r.S_ch, r.D_ch, r.I_ch,
                f"{r.ter:.4f}", r.edit_final, r.shifts,
                r.kualitas,
            ])


# ══════════════════════════════════════════════════════════════════
# 13. HELPER CETAK
# ══════════════════════════════════════════════════════════════════

SEP  = "=" * 70
SEP2 = "-" * 70

def pct_str(v: float) -> str:
    return f"{v * 100:.1f}%"

def fmt(v: float, prec: int = 4) -> str:
    return f"{v:.{prec}f}"

def progress_bar(sim: float, panjang: int = 10) -> str:
    isi = max(0, min(panjang, round(sim * panjang)))
    return "#" * isi + "." * (panjang - isi)


def cetak_tabel_grup(g: AkumulasiGrup):
    print(f"  Kalimat   : {g.jumlah_kalimat}")
    print(f"  Kata (N)  : {g.total_N}")
    print(f"  Char (N)  : {g.total_Nc}\n")

    print("  +--------------------------------------------------+")
    print("  |  METRIK               |  NILAI    |  %           |")
    print("  +--------------------------------------------------+")

    def baris(nm, val):
        print(f"  |  {nm:<21} |  {fmt(val):>8}  |  {pct_str(val):>8}    |")

    baris("WER Klasik (biner)",  g.wer_klasik())
    baris("WER Soft (berbobot)", g.wer_soft())
    baris("WER Soft + Fonetik",  g.wer_soft_fonetik())
    baris("CharWER",             g.char_wer())
    baris("TER (avg)",           g.ter_avg())
    print("  +--------------------------------------------------+")

    total_err = g.total_S_kl + g.total_D_kl + g.total_I_kl
    print("\n  Breakdown Error Klasik:")
    print(f"     Substitusi (S)  : {g.total_S_kl:>5}  ({pct_str(g.total_S_kl/g.total_N if g.total_N else 0)} dari N)")
    print(f"     Delesi     (D)  : {g.total_D_kl:>5}  ({pct_str(g.total_D_kl/g.total_N if g.total_N else 0)} dari N)")
    print(f"     Insersi    (I)  : {g.total_I_kl:>5}  ({pct_str(g.total_I_kl/g.total_N if g.total_N else 0)} dari N)")
    print(f"     Total Error     : {total_err:>5}")

    if g.total_S_kl > 0:
        hem = g.total_S_kl - g.total_err_soft
        pct_hem = hem / g.total_S_kl * 100
        print("\n  Insight Soft WER:")
        print(f"     {g.total_S_kl} substitusi dihukum penuh oleh WER Klasik")
        print(f"     Pengurangan penalti efektif: {hem:.1f} poin ({pct_hem:.0f}%)")

    print("\n  Distribusi Kualitas:")
    for k, cnt in sorted(g.dist_kualitas.items()):
        print(f"     {k:<18} {cnt:>3}x  {'*' * cnt}")


def cetak_header_detail():
    print(f"  {'path':<32}{'N':>6}{'WER Kl.':>10}{'WER Soft':>10}{'+Fonetik':>10}{'CharWER':>9}{'TER':>8}  Kualitas")
    print("  " + "-" * 68)


def cetak_baris_detail(b: HasilBaris):
    sp = b.path
    for prefix in ("Fasih/", "Kurang Fasih/", "fasih/", "kurang fasih/"):
        pos = sp.find(prefix)
        if pos != -1:
            sp = sp[pos + len(prefix):]
            break
    if len(sp) > 29:
        sp = sp[:26] + "..."
    print(
        f"  {sp:<32}{b.N_kata:>6}"
        f"{pct_str(b.wer_klasik):>10}{pct_str(b.wer_soft):>10}"
        f"{pct_str(b.wer_soft_fonetik):>10}{pct_str(b.char_wer):>9}"
        f"{pct_str(b.ter):>8}  {b.kualitas}"
    )


# ══════════════════════════════════════════════════════════════════
# 14. PROSES SATU MODEL
# ══════════════════════════════════════════════════════════════════

def proses_model(
    df_ref: List[Dict],
    df_hyp: List[Dict],
    nama_model: str,
    nama_output_csv: str,
):
    hyp_map = {r["path"]: r["sentence"] for r in df_hyp}

    pasangan = []
    for row in df_ref:
        path = row["path"]
        asli = row["sentence"]
        if not asli:
            continue
        pred = hyp_map.get(path, "")
        if not pred:
            continue
        pasangan.append((path, asli, pred))

    if not pasangan:
        print(f"ERROR: Tidak ada data yang cocok untuk model '{nama_model}'.")
        return

    print(f"\n  {len(pasangan)} kalimat siap dievaluasi.\n")

    semua_hasil: List[HasilBaris] = []

    for path, asli, pred in pasangan:
        rk  = hitung_wer_klasik(asli, pred)
        rs  = hitung_soft_wer(asli, pred, fonetik=False)
        rsp = hitung_soft_wer(asli, pred, fonetik=True)
        rc  = hitung_char_wer(asli, pred)
        rt  = hitung_ter(asli, pred)

        b = HasilBaris(
            path=path,
            kategori=deteksi_kategori(path),
            N_kata=rk.N,
            N_char=rc.N_char,
            wer_klasik=rk.wer,
            S_kl=rk.S, D_kl=rk.D, I_kl=rk.I, benar_kl=rk.C,
            wer_soft=rs.wer_soft,
            error_berbobot=rs.error_berbobot,
            S_bobot=rs.S_bobot,
            D_soft=rs.D, I_soft=rs.I,
            wer_soft_fonetik=rsp.wer_soft,
            char_wer=rc.char_wer,
            S_ch=rc.S, D_ch=rc.D, I_ch=rc.I,
            ter=rt.ter,
            edit_final=rt.edit_final,
            shifts=rt.shifts,
            kualitas=klasifikasi_kualitas(rk.wer, rs.wer_soft, rc.char_wer),
        )
        semua_hasil.append(b)

    # Akumulasi
    grp_fasih  = AkumulasiGrup("Fasih")
    grp_kurang = AkumulasiGrup("Kurang Fasih")
    grp_global = AkumulasiGrup("GLOBAL")

    for b in semua_hasil:
        grp_global.tambah(b)
        if b.kategori == "Fasih":
            grp_fasih.tambah(b)
        elif b.kategori == "Kurang Fasih":
            grp_kurang.tambah(b)

    # ── Cetak laporan ──────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  [MODEL: {nama_model}]")
    print(SEP)

    for grp in (grp_fasih, grp_kurang):
        print(f"\n{SEP}")
        print(f"  [KELOMPOK: {grp.nama}]")
        print(SEP)
        if grp.jumlah_kalimat > 0:
            cetak_tabel_grup(grp)
        else:
            print(f"  (tidak ada data {grp.nama})")

    print(f"\n{SEP}")
    print("  [AKUMULASI GLOBAL]")
    print(SEP)
    cetak_tabel_grup(grp_global)

    # Perbandingan Fasih vs Kurang Fasih
    if grp_fasih.jumlah_kalimat > 0 and grp_kurang.jumlah_kalimat > 0:
        print(f"\n{SEP}")
        print("  PERBANDINGAN FASIH vs KURANG FASIH")
        print(SEP)
        print(f"  {'METRIK':<24}{'Fasih':>14}{'Kurang Fasih':>16}{'Delta':>14}")
        print("  " + "-" * 66)

        def banding(nama, vf, vk):
            delta = vk - vf
            tanda = "+" if delta >= 0 else ""
            print(f"  {nama:<24}{pct_str(vf):>12}{pct_str(vk):>16}{tanda + pct_str(delta):>12}")

        banding("WER Klasik",          grp_fasih.wer_klasik(),       grp_kurang.wer_klasik())
        banding("WER Soft (berbobot)", grp_fasih.wer_soft(),         grp_kurang.wer_soft())
        banding("WER Soft + Fonetik",  grp_fasih.wer_soft_fonetik(), grp_kurang.wer_soft_fonetik())
        banding("CharWER",             grp_fasih.char_wer(),         grp_kurang.char_wer())
        banding("TER",                 grp_fasih.ter_avg(),          grp_kurang.ter_avg())
        print("  " + "-" * 66)
        print("  Delta (+) = Kurang Fasih lebih tinggi error-nya")

    # Detail per kalimat
    print(f"\n{SEP}")
    print("  DETAIL PER KALIMAT")
    print(SEP)

    print("\n  >> FASIH")
    cetak_header_detail()
    for b in semua_hasil:
        if b.kategori == "Fasih":
            cetak_baris_detail(b)

    print("\n  >> KURANG FASIH")
    cetak_header_detail()
    for b in semua_hasil:
        if b.kategori == "Kurang Fasih":
            cetak_baris_detail(b)

    unknown = [b for b in semua_hasil if b.kategori == "Tidak Diketahui"]
    if unknown:
        print("\n  >> TIDAK DIKETAHUI")
        cetak_header_detail()
        for b in unknown:
            cetak_baris_detail(b)

    # Analisis error kata terburuk
    for kat in ("Fasih", "Kurang Fasih"):
        subset = sorted(
            [b for b in semua_hasil if b.kategori == kat],
            key=lambda x: x.wer_klasik,
            reverse=True,
        )
        print(f"\n{SEP}")
        print(f"  ANALISIS ERROR KATA -- {kat} (2 Terburuk)")
        print(SEP)
        hyp_map_local = {r["path"]: r["sentence"] for r in df_hyp}
        ref_map_local = {row["path"]: row["sentence"] for row in df_ref}

        for b in subset[:2]:
            asli_b = ref_map_local.get(b.path, "")
            pred_b = hyp_map_local.get(b.path, "")
            print(f"\n  [{b.path}]")
            print(f"     WER Klasik: {pct_str(b.wer_klasik)}  WER Soft: {pct_str(b.wer_soft)}  CharWER: {pct_str(b.char_wer)}")
            print("     Substitusi (sim karakter):")
            diffs = analisis_perbedaan(asli_b, pred_b)
            cnt = 0
            for d in diffs:
                if d.tipe != "SUBSTITUSI":
                    continue
                cnt += 1
                if cnt > 6:
                    break
                bar = progress_bar(d.kemiripan, 10)
                print(f"       [{bar}]  {d.ref_w:<18} -> {d.hyp_w:<18}  sim={d.kemiripan:.2f}  penalti={d.penalti:.2f}")

    # Simpan CSV
    simpan_csv(semua_hasil, nama_output_csv)
    print(f"\n  Laporan lengkap disimpan ke: {nama_output_csv}")
    print(f"\n{SEP}")
    print("  Selesai.")
    print(SEP)

    return grp_global  # kembalikan untuk perbandingan antar model


# ══════════════════════════════════════════════════════════════════
# 15. PERBANDINGAN ANTAR MODEL
# ══════════════════════════════════════════════════════════════════

def cetak_perbandingan_model(
    g_bal: AkumulasiGrup,
    g_unf: AkumulasiGrup,
    label_a: str = "Balinese",
    label_b: str = "Unfinetuned",
):
    print(f"\n{SEP}")
    print(f"  PERBANDINGAN MODEL: {label_a} vs {label_b} (GLOBAL)")
    print(SEP)
    print(f"  {'METRIK':<24}{label_a:>16}{label_b:>16}{'Delta':>14}{'Improv.':>10}")
    print("  " + "-" * 80)

    def baris(nama, va, vb):
        delta  = vb - va          # positif = B lebih buruk
        improv = (vb - va) / vb * 100 if vb != 0 else 0.0
        tanda  = "+" if delta >= 0 else ""
        imp_str = f"{improv:+.1f}%"
        print(f"  {nama:<24}{pct_str(va):>16}{pct_str(vb):>16}{tanda + pct_str(delta):>14}{imp_str:>10}")

    baris("WER Klasik",          g_bal.wer_klasik(),       g_unf.wer_klasik())
    baris("WER Soft (berbobot)", g_bal.wer_soft(),         g_unf.wer_soft())
    baris("WER Soft + Fonetik",  g_bal.wer_soft_fonetik(), g_unf.wer_soft_fonetik())
    baris("CharWER",             g_bal.char_wer(),         g_unf.char_wer())
    baris("TER",                 g_bal.ter_avg(),          g_unf.ter_avg())
    print("  " + "-" * 80)
    print(f"  Delta (+) = {label_b} lebih tinggi error-nya (fine-tuning berhasil menurunkan)")
    print(f"  Improv.   = persentase penurunan error dari {label_b} ke {label_a}")


# ══════════════════════════════════════════════════════════════════
# 16. MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print(f"\n{SEP}")
    print("  ADVANCED WER EVALUATOR -- MULTI-STRATEGY (Python)")
    print(SEP)

    # File konfigurasi
    FILE_META       = "metadata.csv"
    FILE_BALINESE   = "whisper-balinese.csv"
    FILE_UNFINE     = "whisper-unfinetuned.csv"
    OUT_BALINESE    = "laporan_advanced_wer_balinese.csv"
    OUT_UNFINE      = "laporan_advanced_wer_unfinetuned.csv"

    # Baca CSV
    try:
        df_meta   = baca_csv(FILE_META)
        df_bal    = baca_csv(FILE_BALINESE)
        df_unf    = baca_csv(FILE_UNFINE)
    except FileNotFoundError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    print(f"  metadata          : {len(df_meta)} baris")
    print(f"  whisper-balinese  : {len(df_bal)} baris")
    print(f"  whisper-unfinetuned: {len(df_unf)} baris")

    # Proses masing-masing model
    print(f"\n{'─'*70}")
    print("  ► Memproses Whisper Balinese (fine-tuned)...")
    g_bal = proses_model(df_meta, df_bal, "Whisper Balinese (Fine-tuned)", OUT_BALINESE)

    print(f"\n{'─'*70}")
    print("  ► Memproses Whisper Unfinetuned (original)...")
    g_unf = proses_model(df_meta, df_unf, "Whisper Unfinetuned (Original)", OUT_UNFINE)

    # Perbandingan akhir
    if g_bal and g_unf:
        cetak_perbandingan_model(g_bal, g_unf, "Balinese", "Unfinetuned")

    print(f"\n{SEP}")
    print("  Semua laporan berhasil disimpan.")
    print(SEP)


if __name__ == "__main__":
    main()