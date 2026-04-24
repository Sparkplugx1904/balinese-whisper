<div align="center">

# 🎙️ ASR Bahasa Bali
### Purwarupa *Automatic Speech Recognition* Bahasa Bali
#### Berbasis Fine-tuning Model Pra-latih OpenAI Whisper

<br>

[![Model on HuggingFace](https://img.shields.io/badge/🤗%20HuggingFace-Model-orange?style=for-the-badge)](https://huggingface.co/Sparkplugx1904/ggml-balinese-whisper-models)
[![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![OpenAI Whisper](https://img.shields.io/badge/OpenAI-Whisper%20Small-412991?style=for-the-badge&logo=openai&logoColor=white)](https://github.com/openai/whisper)
[![License](https://img.shields.io/badge/Lisensi-MIT-green?style=for-the-badge)](LICENSE)

<br>

> *"Bali mapitulung, aksara Bali maurip."*
> **Bahasa Bali dibantu, aksara Bali hidup kembali.**

</div>


---

## 📊 Hasil Evaluasi

### Model Fine-tuned vs. Model Original

| Metrik | 🏆 Whisper Balinese *(fine-tuned)* | Whisper Original |Peningkatan |
|:---|:---:|:---:|:---:|
| **WER Klasik** | **32,7%** | 64,5% | ⬇️ ~49% |
| **WER Soft (berbobot)** | **21,1%** | 43,5% | ⬇️ ~51% |
| **CharWER** | **8,2%** | 17,5% | ⬇️ ~53% |
| **TER** | **31,8%** | 65,7% | ⬇️ ~52% |

### Hasil Uji Lapangan (10 Responden)

| Kelompok | Jumlah | Rata-rata WER |
|:---|:---:|:---:|
| 🟢 Penutur Fasih | 5 orang | **26,0%** |
| 🟡 Penutur Kurang Fasih | 5 orang | **37,22%** |s
| 🌐 Global | 10 orang | **31,6%** |

> **Selisih antar kelompok: 11,2 poin persentase**

### Kurva Pelatihan

| Step | Training Loss | WER Validasi |
|:---:|:---:|:---:|
| 1.000 | 0,028 | 55,79% |
| 2.000 | ~0,009 | ~50,00% |
| **3.000** | **0,0019** | **45,75%** |

---

## 🗂️ Dataset

| Keterangan | Detail |
|:---|:---|
| **Jumlah berkas audio** | 1.187 file `.wav` |
| **Total durasi** | ± 33 menit |
| **Format** | 16kHz, mono, WAV |
| **Sumber primer** | Rekaman penutur asli Bahasa Bali |
| **Sumber sekunder** | *Web scraping* — Balinese Dictionary |

---

## 🏗️ Arsitektur & Infrastruktur

```
OpenAI Whisper Small (pre-trained, multilingual)
        │
        │  Fine-tuning (Transfer Learning)
        │  ├── Platform  : Kaggle
        │  ├── GPU       : NVIDIA Tesla P100 (16 GB)
        │  ├── Library   : PyTorch + Librosa
        │  ├── Steps     : 3.000
        │  └── Optimizer : AdamW
        ▼
  Whisper Balinese (fine-tuned)
        │
        └── Ekspor ke format GGML (llama.cpp compatible)
```

---

## 🚀 Penggunaan Model

### 1. Download Model

```bash
# Clone dari HuggingFace
git lfs install
git clone https://huggingface.co/Sparkplugx1904/ggml-balinese-whisper-models
```

### 2. Transkripsi dengan Whisper Python

```python
import whisper

# Load model fine-tuned
model = whisper.load_model("path/to/model")

# Transkripsi file audio
result = model.transcribe("audio_bali.wav", language="id")
print(result["text"])
```

### 3. Transkripsi dengan whisper.cpp (format GGML)

```bash
./main -m ggml-balinese.bin -f audio_bali.wav -l id
```

---

## 📏 Metrik Evaluasi

Penelitian ini menggunakan evaluasi **multi-strategi** yang dikembangkan khusus:

| Metrik | Deskripsi |
|:---|:---|
| **WER Klasik** | Proporsi kata yang salah (biner: benar/salah) |
| **WER Soft** | WER dengan bobot kemiripan karakter antar kata |
| **WER Soft + Fonetik** | WER Soft dengan normalisasi fonetik Bahasa Bali |
| **CharWER** | WER di tingkat karakter (lebih sensitif) |
| **TER** | Translation Edit Rate dengan deteksi pergeseran kata |

Rumus WER Klasik:

$$\text{WER} = \frac{S + D + I}{N} \times 100\%$$

> S = Substitusi · D = Delesi · I = Insersi · N = Total kata referensi

---

## 🔬 Analisis Kesalahan

Kesalahan transkripsi yang paling umum ditemukan:

- **Substitusi fonetik** — kata-kata yang bunyinya mirip, misalnya `nglanturang` → `lanturang`, `masekolah` → `sekolah`
- **Kosakata sakral** — istilah keagamaan Hindu-Bali seperti `swastyastu`, `titiang`, `sukma` sering mengalami distorsi
- **Penanda honorifik** — kata sapaan `ida`, `dane`, `tiang` cenderung tertukar satu sama lain
- **Variasi dialek** — perbedaan pelafalan antar wilayah di Bali yang tidak tercakup dataset


---

## 🛠️ Tools Pendukung

Repositori ini juga menyertakan skrip evaluasi WER canggih:

| File | Deskripsi |
|:---|:---|
| `wer.py` | Advanced WER Evaluator — Multi-Strategy Engine |
| `laporan_advanced_wer_balinese.csv` | Laporan WER model fine-tuned per kalimat |
| `laporan_advanced_wer_unfinetuned.csv` | Laporan WER model original per kalimat |



---

<div align="center">

**⭐ Jika proyek ini bermanfaat, jangan lupa beri bintang di HuggingFace!**

[![HuggingFace](https://img.shields.io/badge/🤗%20Lihat%20Model-HuggingFace-orange?style=for-the-badge)](https://huggingface.co/Sparkplugx1904/ggml-balinese-whisper-models)

*Dikembangkan dengan ❤️ untuk pelestarian Bahasa Bali*

</div>