/*
╔══════════════════════════════════════════════════════════════════╗
║         ADVANCED WER EVALUATOR — MULTI-STRATEGY ENGINE          ║
║  Strategi: Soft WER + CharWER + Phonetic Norm + TER Shifts      ║
║  Bahasa  : C++17                                                 ║
╚══════════════════════════════════════════════════════════════════╝

Kompilasi:
  g++ -std=c++17 -O2 -o wer_advanced wer_advanced.cpp

Jalankan:
  ./wer_advanced
  (pastikan metadata.csv dan whisper.csv ada di direktori yang sama)
*/

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <cstdint>
#include <cstring>
#include <vector>

// ══════════════════════════════════════════════════════════════════
// STRUCT HASIL
// ══════════════════════════════════════════════════════════════════

struct HasilWERKlasik {
    double wer;
    int    N, S, D, I, C;
};

struct HasilSoftWER {
    double wer_soft;
    int    N;
    double error_berbobot;
    double S_bobot;
    int    D, I, benar;
};

struct HasilCharWER {
    double char_wer;
    int    N_char, S, D, I;
};

struct HasilTER {
    double ter;
    int    N_ter, edit_final, shifts;
};

struct HasilBaris {
    std::string path;
    int    N_kata, N_char;
    // Klasik
    double wer_klasik;
    int    S_kl, D_kl, I_kl, benar_kl;
    // Soft
    double wer_soft, error_berbobot, S_bobot;
    int    D_soft, I_soft;
    // Soft+Fonetik
    double wer_soft_fonetik;
    // CharWER
    double char_wer;
    int    S_ch, D_ch, I_ch;
    // TER
    double ter;
    int    edit_final, shifts;
    // Label
    std::string kualitas;
    // Kategori
    std::string kategori;   // "Fasih" | "Kurang Fasih"
};

// ══════════════════════════════════════════════════════════════════
// 1. PARSING CSV
// ══════════════════════════════════════════════════════════════════

// Mengurai satu baris CSV (menangani field berkutip ganda)
std::vector<std::string> parseCsvLine(const std::string& line) {
    std::vector<std::string> fields;
    std::string field;
    bool dalam_kutip = false;

    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (c == '"') {
            if (dalam_kutip && i + 1 < line.size() && line[i+1] == '"') {
                field += '"'; ++i;  // escaped quote ""
            } else {
                dalam_kutip = !dalam_kutip;
            }
        } else if (c == ',' && !dalam_kutip) {
            fields.push_back(field);
            field.clear();
        } else {
            field += c;
        }
    }
    fields.push_back(field);
    return fields;
}

// Membaca seluruh CSV → vector of maps {kolom: nilai}
std::vector<std::unordered_map<std::string, std::string>>
bacaCsv(const std::string& nama_file) {
    std::ifstream f(nama_file);
    if (!f.is_open()) {
        throw std::runtime_error("Tidak bisa membuka file: " + nama_file);
    }

    std::vector<std::unordered_map<std::string, std::string>> rows;
    std::string line;

    // Header
    if (!std::getline(f, line)) return rows;
    // Hapus BOM UTF-8 jika ada
    if (line.size() >= 3 &&
        (unsigned char)line[0] == 0xEF &&
        (unsigned char)line[1] == 0xBB &&
        (unsigned char)line[2] == 0xBF) {
        line = line.substr(3);
    }
    // Hapus \r
    if (!line.empty() && line.back() == '\r') line.pop_back();

    std::vector<std::string> headers = parseCsvLine(line);
    // Trim spasi dari header
    for (auto& h : headers) {
        while (!h.empty() && h.front() == ' ') h.erase(h.begin());
        while (!h.empty() && h.back()  == ' ') h.pop_back();
    }

    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;

        auto vals = parseCsvLine(line);
        std::unordered_map<std::string, std::string> row;
        for (size_t i = 0; i < headers.size() && i < vals.size(); ++i) {
            // Trim spasi dari nilai
            std::string v = vals[i];
            while (!v.empty() && v.front() == ' ') v.erase(v.begin());
            while (!v.empty() && v.back()  == ' ') v.pop_back();
            row[headers[i]] = v;
        }
        rows.push_back(row);
    }
    return rows;
}

// ══════════════════════════════════════════════════════════════════
// 2. UTF-8 UTILITIES
// ══════════════════════════════════════════════════════════════════

// Konversi string UTF-8 → vector codepoint (uint32_t)
// Diperlukan untuk CharWER yang bekerja per karakter, bukan per byte.
std::vector<uint32_t> utf8ToCodepoints(const std::string& s) {
    std::vector<uint32_t> cps;
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = (unsigned char)s[i];
        uint32_t cp = 0;
        size_t extra = 0;
        if      (c < 0x80)  { cp = c;           extra = 0; }
        else if (c < 0xC0)  { ++i; continue; }  // byte lanjutan, skip
        else if (c < 0xE0)  { cp = c & 0x1F;    extra = 1; }
        else if (c < 0xF0)  { cp = c & 0x0F;    extra = 2; }
        else                 { cp = c & 0x07;    extra = 3; }
        ++i;
        for (size_t k = 0; k < extra && i < s.size(); ++k, ++i) {
            cp = (cp << 6) | ((unsigned char)s[i] & 0x3F);
        }
        cps.push_back(cp);
    }
    return cps;
}

// Lowercase sederhana (ASCII) + karakter UTF-8 tertentu
// Untuk multi-byte, kita lakukan penggantian string langsung via regex.
std::string toLowerAscii(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = c - 'A' + 'a';
    }
    return s;
}

// ══════════════════════════════════════════════════════════════════
// 3. TEXT NORMALIZATION
// ══════════════════════════════════════════════════════════════════

// Kamus normalisasi: pasangan (pola_regex, pengganti)
// Urutan penting — evaluasi dari atas ke bawah.
static const std::vector<std::pair<std::string, std::string>> KAMUS_NORM = {
    // Angka → kata
    { R"(\b1\b)", "satu"  },
    { R"(\b2\b)", "dua"   },
    { R"(\b3\b)", "tiga"  },
    { R"(\b4\b)", "empat" },
    { R"(\b5\b)", "lima"  },
    // Singkatan umum Bali/Indonesia
    { R"(\byg\b)",  "yang"            },
    { R"(\bdgn\b)", "dengan"          },
    { R"(\btdk\b)", "tidak"           },
    { R"(\btsb\b)", "tersebut"        },
    { R"(\bsbb\b)", "sebagai berikut" },
    { R"(\bdng\b)", "dengan"          },
    { R"(\bnggih\b)", "inggih"        },
};

// Normalisasi fonetik Bali
static const std::vector<std::pair<std::string, std::string>> FONETIK_PAIRS = {
    { "ngg",                         "ng"          },
    { R"([éèê])",                    "e"           },
    { R"([âàá])",                    "a"           },
    { R"([îìí])",                    "i"           },
    { R"([ûùú])",                    "u"           },
    { R"([ôòó])",                    "o"           },
    { "ck",                          "k"           },
    { "ph",                          "f"           },
    { "oe",                          "u"           },
    { "tj",                          "c"           },
    { "dj",                          "j"           },
    { "ij",                          "i"           },
    { "ny",                          "n"           },
    { R"(\bida\s+sang\b)",           "ida sanghyang"},
    { R"(sanghyang|sang\s*hyang|sanggah\s*widi|sanghyang\s*widi)", "dewata" },
    { R"(trihita|tri\s*hita)",       "trihita"     },
    { R"(bareng[\s\-]+bareng)",      "barengbareng"},
    { R"(gotong[\s\-]+royong)",      "gotongroyong"},
};

// Terapkan satu daftar pasangan regex-replace ke string
std::string terapkanRegexPairs(
        const std::string& teks,
        const std::vector<std::pair<std::string,std::string>>& pairs)
{
    std::string hasil = teks;
    for (auto& [pola, ganti] : pairs) {
        try {
            std::regex re(pola, std::regex::ECMAScript | std::regex::icase);
            hasil = std::regex_replace(hasil, re, ganti);
        } catch (...) {}
    }
    return hasil;
}

// Tokenisasi: normalisasi + split per kata
std::vector<std::string> tokenisasi(const std::string& teks, bool fonetik = false) {
    if (teks.empty()) return {};

    std::string s = toLowerAscii(teks);

    // Terapkan kamus singkatan
    s = terapkanRegexPairs(s, KAMUS_NORM);

    // Tanda baca: dash → spasi
    {
        std::regex reDash(R"([-–—])");
        s = std::regex_replace(s, reDash, " ");
    }
    // Hapus non-alfanumerik non-spasi (pertahankan UTF-8 multibyte)
    // Strategi: hapus karakter ASCII yang bukan huruf/angka/spasi
    {
        std::string clean;
        for (size_t i = 0; i < s.size(); ) {
            unsigned char c = (unsigned char)s[i];
            if (c >= 0x80) {
                // multi-byte UTF-8: pertahankan
                clean += s[i]; ++i;
                while (i < s.size() && ((unsigned char)s[i] & 0xC0) == 0x80) {
                    clean += s[i]; ++i;
                }
            } else if (std::isalnum(c) || c == ' ') {
                clean += (char)c; ++i;
            } else {
                clean += ' '; ++i;
            }
        }
        s = clean;
    }

    if (fonetik) {
        s = terapkanRegexPairs(s, FONETIK_PAIRS);
    }

    // Normalisasi spasi ganda
    {
        std::regex reSpasi(R"(\s+)");
        s = std::regex_replace(s, reSpasi, " ");
        while (!s.empty() && s.front() == ' ') s.erase(s.begin());
        while (!s.empty() && s.back()  == ' ') s.pop_back();
    }

    // Split
    std::vector<std::string> tokens;
    std::istringstream ss(s);
    std::string tok;
    while (ss >> tok) tokens.push_back(tok);
    return tokens;
}

// ══════════════════════════════════════════════════════════════════
// 4. KARAKTER SIMILARITY (Normalized Levenshtein)
// ══════════════════════════════════════════════════════════════════

int editDistanceStr(const std::string& a, const std::string& b) {
    int n = (int)a.size(), m = (int)b.size();
    std::vector<std::vector<int>> dp(n+1, std::vector<int>(m+1, 0));
    for (int i = 0; i <= n; ++i) dp[i][0] = i;
    for (int j = 0; j <= m; ++j) dp[0][j] = j;
    for (int i = 1; i <= n; ++i) {
        for (int j = 1; j <= m; ++j) {
            if (a[i-1] == b[j-1]) dp[i][j] = dp[i-1][j-1];
            else dp[i][j] = 1 + std::min({dp[i-1][j-1], dp[i][j-1], dp[i-1][j]});
        }
    }
    return dp[n][m];
}

double kemiripanKarakter(const std::string& kata1, const std::string& kata2) {
    if (kata1 == kata2) return 1.0;
    if (kata1.empty() || kata2.empty()) return 0.0;
    int len_max = (int)std::max(kata1.size(), kata2.size());
    int dist    = editDistanceStr(kata1, kata2);
    return 1.0 - (double)dist / len_max;
}

double bobotSubstitusi(const std::string& r, const std::string& h,
                       double ambang_sama = 0.85, double ambang_mirip = 0.60) {
    double sim = kemiripanKarakter(r, h);
    if (sim >= ambang_sama) return std::round((1.0 - sim) * 10000.0) / 10000.0;
    if (sim >= ambang_mirip) return 0.50;
    return 1.00;
}

// ══════════════════════════════════════════════════════════════════
// 5. SOFT WER
// ══════════════════════════════════════════════════════════════════

// DP generik dengan biaya substitusi berbobot (float)
std::vector<std::vector<double>> dpGenerik(
        const std::vector<std::string>& ref,
        const std::vector<std::string>& hyp,
        bool berbobot = true)
{
    int n = (int)ref.size(), m = (int)hyp.size();
    std::vector<std::vector<double>> dp(n+1, std::vector<double>(m+1, 0.0));
    for (int i = 0; i <= n; ++i) dp[i][0] = (double)i;
    for (int j = 0; j <= m; ++j) dp[0][j] = (double)j;

    for (int i = 1; i <= n; ++i) {
        for (int j = 1; j <= m; ++j) {
            if (ref[i-1] == hyp[j-1]) {
                dp[i][j] = dp[i-1][j-1];
            } else {
                double biaya_s = berbobot
                    ? bobotSubstitusi(ref[i-1], hyp[j-1])
                    : 1.0;
                dp[i][j] = std::min({ dp[i-1][j-1] + biaya_s,
                                      dp[i][j-1]   + 1.0,
                                      dp[i-1][j]   + 1.0 });
            }
        }
    }
    return dp;
}

HasilSoftWER hitungSoftWER(const std::string& referensi,
                            const std::string& prediksi,
                            bool fonetik = false)
{
    auto ref = tokenisasi(referensi, fonetik);
    auto hyp = tokenisasi(prediksi,  fonetik);

    if (ref.empty()) return {0.0, 0, 0.0, 0.0, 0, 0, 0};

    auto dp = dpGenerik(ref, hyp, /*berbobot=*/true);
    int  n  = (int)ref.size(), m = (int)hyp.size();

    double total_err = dp[n][m];

    // Backtracking
    int i = n, j = m;
    double S_bobot = 0.0;
    int D = 0, I = 0, C = 0;

    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && ref[i-1] == hyp[j-1]) {
            ++C; --i; --j;
        } else if (i > 0 && j > 0 &&
                   std::abs(dp[i][j] - (dp[i-1][j-1] + bobotSubstitusi(ref[i-1], hyp[j-1]))) < 1e-9) {
            S_bobot += bobotSubstitusi(ref[i-1], hyp[j-1]);
            --i; --j;
        } else if (i > 0 && std::abs(dp[i][j] - (dp[i-1][j] + 1.0)) < 1e-9) {
            ++D; --i;
        } else if (j > 0) {
            ++I; --j;
        } else break;
    }

    double wer = total_err / n;
    return {
        std::round(wer * 10000.0) / 10000.0,
        n,
        std::round(total_err * 1000.0) / 1000.0,
        std::round(S_bobot  * 1000.0) / 1000.0,
        D, I, C
    };
}

// ══════════════════════════════════════════════════════════════════
// 6. CHARACTER WER (CharWER)
// ══════════════════════════════════════════════════════════════════

HasilCharWER hitungCharWER(const std::string& referensi, const std::string& prediksi) {
    auto ref_tok = tokenisasi(referensi, false);
    auto hyp_tok = tokenisasi(prediksi,  false);

    // Gabung kembali jadi string lalu split per codepoint UTF-8
    std::string ref_str, hyp_str;
    for (auto& t : ref_tok) { if (!ref_str.empty()) ref_str += ' '; ref_str += t; }
    for (auto& t : hyp_tok) { if (!hyp_str.empty()) hyp_str += ' '; hyp_str += t; }

    auto ref_cp = utf8ToCodepoints(ref_str);
    auto hyp_cp = utf8ToCodepoints(hyp_str);

    int n = (int)ref_cp.size(), m = (int)hyp_cp.size();
    if (n == 0) return {0.0, 0, 0, 0, 0};

    std::vector<std::vector<int>> dp(n+1, std::vector<int>(m+1, 0));
    for (int i = 0; i <= n; ++i) dp[i][0] = i;
    for (int j = 0; j <= m; ++j) dp[0][j] = j;
    for (int i = 1; i <= n; ++i) {
        for (int j = 1; j <= m; ++j) {
            if (ref_cp[i-1] == hyp_cp[j-1]) dp[i][j] = dp[i-1][j-1];
            else dp[i][j] = 1 + std::min({dp[i-1][j-1], dp[i][j-1], dp[i-1][j]});
        }
    }

    // Backtracking
    int i = n, j = m, S = 0, D = 0, I = 0;
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && ref_cp[i-1] == hyp_cp[j-1]) {
            --i; --j;
        } else if (i > 0 && j > 0 && dp[i][j] == dp[i-1][j-1] + 1) {
            ++S; --i; --j;
        } else if (i > 0 && dp[i][j] == dp[i-1][j] + 1) {
            ++D; --i;
        } else {
            ++I; --j;
        }
    }

    double cw = (double)(S + D + I) / n;
    return { std::round(cw * 10000.0) / 10000.0, n, S, D, I };
}

// ══════════════════════════════════════════════════════════════════
// 7. WER KLASIK
// ══════════════════════════════════════════════════════════════════

HasilWERKlasik hitungWERKlasik(const std::string& referensi, const std::string& prediksi) {
    auto ref = tokenisasi(referensi, false);
    auto hyp = tokenisasi(prediksi,  false);

    int n = (int)ref.size(), m = (int)hyp.size();
    if (n == 0) return {0.0, 0, 0, 0, 0, 0};

    auto dp_int = [&]() {
        std::vector<std::vector<int>> dp(n+1, std::vector<int>(m+1, 0));
        for (int i = 0; i <= n; ++i) dp[i][0] = i;
        for (int j = 0; j <= m; ++j) dp[0][j] = j;
        for (int i = 1; i <= n; ++i)
            for (int j = 1; j <= m; ++j) {
                if (ref[i-1] == hyp[j-1]) dp[i][j] = dp[i-1][j-1];
                else dp[i][j] = 1 + std::min({dp[i-1][j-1], dp[i][j-1], dp[i-1][j]});
            }
        return dp;
    }();

    int i = n, j = m, S = 0, D = 0, I = 0, C = 0;
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && ref[i-1] == hyp[j-1]) {
            ++C; --i; --j;
        } else if (i > 0 && j > 0 && dp_int[i][j] == dp_int[i-1][j-1] + 1) {
            ++S; --i; --j;
        } else if (i > 0 && dp_int[i][j] == dp_int[i-1][j] + 1) {
            ++D; --i;
        } else {
            ++I; --j;
        }
    }

    double wer = (double)(S + D + I) / n;
    return { std::round(wer * 10000.0) / 10000.0, n, S, D, I, C };
}

// ══════════════════════════════════════════════════════════════════
// 8. TER (Translation Edit Rate + Unigram Shifts)
// ══════════════════════════════════════════════════════════════════

HasilTER hitungTER(const std::string& referensi, const std::string& prediksi) {
    auto ref = tokenisasi(referensi, false);
    auto hyp = tokenisasi(prediksi,  false);

    int n = (int)ref.size(), m = (int)hyp.size();
    if (n == 0) return {0.0, 0, 0, 0};

    // Edit distance kata
    auto editDistVec = [](const std::vector<std::string>& a,
                          const std::vector<std::string>& b) {
        int na = (int)a.size(), mb = (int)b.size();
        std::vector<std::vector<int>> dp(na+1, std::vector<int>(mb+1, 0));
        for (int i = 0; i <= na; ++i) dp[i][0] = i;
        for (int j = 0; j <= mb; ++j) dp[0][j] = j;
        for (int i = 1; i <= na; ++i)
            for (int j = 1; j <= mb; ++j) {
                if (a[i-1] == b[j-1]) dp[i][j] = dp[i-1][j-1];
                else dp[i][j] = 1 + std::min({dp[i-1][j-1], dp[i][j-1], dp[i-1][j]});
            }
        return dp[na][mb];
    };

    int edit_dist = editDistVec(ref, hyp);

    // Deteksi shifts: kata yang ada di kedua sisi tapi tidak cocok secara berurutan
    // Gunakan Longest Common Subsequence untuk mendapatkan posisi yang matched
    std::vector<std::vector<int>> lcs(n+1, std::vector<int>(m+1, 0));
    for (int i = 1; i <= n; ++i)
        for (int j = 1; j <= m; ++j) {
            if (ref[i-1] == hyp[j-1]) lcs[i][j] = lcs[i-1][j-1] + 1;
            else lcs[i][j] = std::max(lcs[i-1][j], lcs[i][j-1]);
        }

    // Backtrack LCS → posisi matched
    std::vector<bool> matched_ref(n, false), matched_hyp(m, false);
    {
        int i = n, j = m;
        while (i > 0 && j > 0) {
            if (ref[i-1] == hyp[j-1]) {
                matched_ref[i-1] = true;
                matched_hyp[j-1] = true;
                --i; --j;
            } else if (lcs[i-1][j] >= lcs[i][j-1]) {
                --i;
            } else {
                --j;
            }
        }
    }

    // Kata yang tidak matched di kedua sisi → kandidat shift
    std::map<std::string, int> unmatched_ref_cnt, unmatched_hyp_cnt;
    for (int i = 0; i < n; ++i)
        if (!matched_ref[i]) unmatched_ref_cnt[ref[i]]++;
    for (int j = 0; j < m; ++j)
        if (!matched_hyp[j]) unmatched_hyp_cnt[hyp[j]]++;

    int shifts = 0;
    for (auto& [kata, cnt_ref] : unmatched_ref_cnt) {
        auto it = unmatched_hyp_cnt.find(kata);
        if (it != unmatched_hyp_cnt.end()) {
            shifts += std::min(cnt_ref, it->second);
        }
    }

    double ter = ((double)edit_dist + 0.5 * shifts) / n;
    return { std::round(ter * 10000.0) / 10000.0, n, edit_dist, shifts };
}

// ══════════════════════════════════════════════════════════════════
// 9. KLASIFIKASI KUALITAS
// ══════════════════════════════════════════════════════════════════

std::string klasifikasiKesalahan(double wer_klasik, double wer_soft, double char_wer) {
    double skor = (wer_soft * 0.5) + (char_wer * 0.3) + (wer_klasik * 0.2);
    if (skor == 0.0)       return "Sempurna";
    if (skor < 0.10)       return "Sangat Baik";
    if (skor < 0.25)       return "Baik";
    if (skor < 0.45)       return "Cukup";
    if (skor < 0.70)       return "Buruk";
    return                        "Sangat Buruk";
}

// ══════════════════════════════════════════════════════════════════
// 10. ANALISIS PERBEDAAN KATA
// ══════════════════════════════════════════════════════════════════

struct DiffItem {
    std::string tipe;   // "SUBSTITUSI", "DELESI", "INSERSI"
    std::string ref_w;
    std::string hyp_w;
    double kemiripan;
    double penalti;
};

// Wagner-Fischer based diff (lebih akurat dari LCS backtrack)
std::vector<DiffItem> analisisPerbedaan(const std::string& referensi,
                                         const std::string& prediksi)
{
    auto ref = tokenisasi(referensi);
    auto hyp = tokenisasi(prediksi);
    int n = (int)ref.size(), m = (int)hyp.size();

    // Bangun DP edit distance biasa
    std::vector<std::vector<int>> dp(n+1, std::vector<int>(m+1, 0));
    for (int i = 0; i <= n; ++i) dp[i][0] = i;
    for (int j = 0; j <= m; ++j) dp[0][j] = j;
    for (int i = 1; i <= n; ++i)
        for (int j = 1; j <= m; ++j) {
            if (ref[i-1] == hyp[j-1]) dp[i][j] = dp[i-1][j-1];
            else dp[i][j] = 1 + std::min({dp[i-1][j-1], dp[i][j-1], dp[i-1][j]});
        }

    // Backtrack
    std::vector<DiffItem> hasil;
    int i = n, j = m;
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && ref[i-1] == hyp[j-1]) {
            --i; --j;
        } else if (i > 0 && j > 0 && dp[i][j] == dp[i-1][j-1] + 1) {
            double sim = kemiripanKarakter(ref[i-1], hyp[j-1]);
            double pen = bobotSubstitusi(ref[i-1], hyp[j-1]);
            hasil.push_back({"SUBSTITUSI", ref[i-1], hyp[j-1], sim, pen});
            --i; --j;
        } else if (i > 0 && dp[i][j] == dp[i-1][j] + 1) {
            hasil.push_back({"DELESI", ref[i-1], "-", 0.0, 1.0});
            --i;
        } else {
            hasil.push_back({"INSERSI", "-", hyp[j-1], 0.0, 1.0});
            --j;
        }
    }

    std::reverse(hasil.begin(), hasil.end());
    return hasil;
}

// ══════════════════════════════════════════════════════════════════
// 14. DETEKSI KATEGORI DARI PATH
// ══════════════════════════════════════════════════════════════════

std::string deteksiKategori(const std::string& path) {
    // Konversi ke lowercase untuk perbandingan case-insensitive
    std::string p_lower = path;
    for (char& c : p_lower) c = (char)std::tolower((unsigned char)c);

    if (p_lower.find("kurang fasih") != std::string::npos ||
        p_lower.find("kurang_fasih") != std::string::npos ||
        p_lower.find("kurangfasih")  != std::string::npos) {
        return "Kurang Fasih";
    }
    if (p_lower.find("fasih") != std::string::npos) {
        return "Fasih";
    }
    return "Tidak Diketahui";
}

// ══════════════════════════════════════════════════════════════════
// 15. STRUCT AKUMULASI PER GRUP
// ══════════════════════════════════════════════════════════════════

struct AkumulasiGrup {
    std::string nama;
    long long jumlah_kalimat = 0;
    long long total_N        = 0;
    long long total_Nc       = 0;
    long long total_S_kl     = 0;
    long long total_D_kl     = 0;
    long long total_I_kl     = 0;
    long long total_Sc       = 0;
    long long total_Dc       = 0;
    long long total_Ic       = 0;
    double    total_err_soft = 0.0;
    double    total_wsf_p    = 0.0;  // soft+fonetik (sum)
    double    total_ter      = 0.0;
    // distribusi kualitas
    std::map<std::string, int> dist_kualitas;

    // Hitung WER dari akumulasi
    double werKlasik()   const { return total_N  > 0 ? (double)(total_S_kl+total_D_kl+total_I_kl)/total_N  : 0; }
    double werSoft()     const { return total_N  > 0 ? total_err_soft / total_N : 0; }
    double werSoftFon()  const { return jumlah_kalimat > 0 ? total_wsf_p / jumlah_kalimat : 0; }
    double charWER()     const { return total_Nc > 0 ? (double)(total_Sc+total_Dc+total_Ic)/total_Nc : 0; }
    double terAvg()      const { return jumlah_kalimat > 0 ? total_ter / jumlah_kalimat : 0; }

    void tambah(const HasilBaris& b) {
        ++jumlah_kalimat;
        total_N       += b.N_kata;
        total_Nc      += b.N_char;
        total_S_kl    += b.S_kl;
        total_D_kl    += b.D_kl;
        total_I_kl    += b.I_kl;
        total_Sc      += b.S_ch;
        total_Dc      += b.D_ch;
        total_Ic      += b.I_ch;
        total_err_soft += b.error_berbobot;
        total_wsf_p   += b.wer_soft_fonetik;
        total_ter     += b.ter;
        dist_kualitas[b.kualitas]++;
    }
};
// ══════════════════════════════════════════════════════════════════
// 11. MENYIMPAN CSV
// ══════════════════════════════════════════════════════════════════

void simpanCsv(const std::vector<HasilBaris>& data, const std::string& nama_file) {
    std::ofstream f(nama_file);
    if (!f.is_open()) {
        std::cerr << "Gagal menyimpan: " << nama_file << "\n";
        return;
    }
    // Header — tambahkan kolom kategori
    f << "path,kategori,N_kata,N_char,"
      << "WER_Klasik,S_klasik,D_klasik,I_klasik,Benar_klasik,"
      << "WER_Soft,Error_Berbobot,S_bobot,D_soft,I_soft,"
      << "WER_Soft_Fonetik,"
      << "CharWER,S_char,D_char,I_char,"
      << "TER,Edit_Final,Shifts,"
      << "Kualitas\n";

    auto fmt = [](double v, int prec = 4) {
        std::ostringstream ss;
        ss << std::fixed << std::setprecision(prec) << v;
        return ss.str();
    };

    for (auto& r : data) {
        f << '"' << r.path     << '"' << ','
          << '"' << r.kategori << '"' << ','
          << r.N_kata << ',' << r.N_char << ','
          << fmt(r.wer_klasik) << ',' << r.S_kl << ',' << r.D_kl << ','
          << r.I_kl << ',' << r.benar_kl << ','
          << fmt(r.wer_soft) << ',' << fmt(r.error_berbobot,3) << ','
          << fmt(r.S_bobot,3) << ',' << r.D_soft << ',' << r.I_soft << ','
          << fmt(r.wer_soft_fonetik) << ','
          << fmt(r.char_wer) << ',' << r.S_ch << ',' << r.D_ch << ',' << r.I_ch << ','
          << fmt(r.ter) << ',' << r.edit_final << ',' << r.shifts << ','
          << '"' << r.kualitas << '"' << '\n';
    }
    f.close();
}

// ══════════════════════════════════════════════════════════════════
// 12. HELPER CETAK
// ══════════════════════════════════════════════════════════════════

std::string pctStr(double v) {
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(1) << (v * 100.0) << "%";
    return ss.str();
}

std::string fmtDouble(double v, int prec = 4) {
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(prec) << v;
    return ss.str();
}

// Progress bar sederhana dari karakter blok
std::string progressBar(double sim, int panjang = 10) {
    int isi = (int)std::round(sim * panjang);
    isi = std::max(0, std::min(panjang, isi));
    std::string bar;
    // Gunakan karakter ASCII agar aman di semua terminal
    bar += std::string(isi,       '#');
    bar += std::string(panjang - isi, '.');
    return bar;
}

// ══════════════════════════════════════════════════════════════════
// 13. MAIN
// ══════════════════════════════════════════════════════════════════

int main() {
    const std::string SEP(70, '=');
    const std::string SEP2(70, '-');

    std::cout << "\n" << SEP << "\n";
    std::cout << "  ADVANCED WER EVALUATOR -- MULTI-STRATEGY (C++17)\n";
    std::cout << SEP << "\n";

    // ── Baca CSV ───────────────────────────────────────────────────
    std::vector<std::unordered_map<std::string,std::string>> df_ref, df_hyp;
    try {
        df_ref = bacaCsv("metadata.csv");
        df_hyp = bacaCsv("whisper.csv");
    } catch (std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }

    std::unordered_map<std::string, std::string> hyp_map;
    for (auto& row : df_hyp)
        if (row.count("path") && row.count("sentence"))
            hyp_map[row.at("path")] = row.at("sentence");

    struct Pasangan { std::string path, asli, prediksi; };
    std::vector<Pasangan> pasangan;
    for (auto& row : df_ref) {
        if (!row.count("path") || !row.count("sentence")) continue;
        std::string path = row.at("path");
        std::string asli = row.at("sentence");
        if (asli.empty()) continue;
        auto it = hyp_map.find(path);
        if (it == hyp_map.end() || it->second.empty()) continue;
        pasangan.push_back({path, asli, it->second});
    }

    if (pasangan.empty()) {
        std::cerr << "ERROR: Tidak ada data yang cocok pada kolom 'path'.\n";
        return 1;
    }
    std::cout << "  " << pasangan.size() << " kalimat siap dievaluasi.\n\n";

    // ── Hitung semua metrik ────────────────────────────────────────
    std::vector<HasilBaris> semua_hasil;
    semua_hasil.reserve(pasangan.size());

    for (auto& p : pasangan) {
        auto rk  = hitungWERKlasik(p.asli, p.prediksi);
        auto rs  = hitungSoftWER(p.asli,   p.prediksi, false);
        auto rsp = hitungSoftWER(p.asli,   p.prediksi, true);
        auto rc  = hitungCharWER(p.asli,   p.prediksi);
        auto rt  = hitungTER(p.asli,       p.prediksi);

        HasilBaris b;
        b.path             = p.path;
        b.kategori         = deteksiKategori(p.path);
        b.N_kata           = rk.N;
        b.N_char           = rc.N_char;
        b.wer_klasik       = rk.wer;
        b.S_kl=rk.S; b.D_kl=rk.D; b.I_kl=rk.I; b.benar_kl=rk.C;
        b.wer_soft         = rs.wer_soft;
        b.error_berbobot   = rs.error_berbobot;
        b.S_bobot          = rs.S_bobot;
        b.D_soft=rs.D; b.I_soft=rs.I;
        b.wer_soft_fonetik = rsp.wer_soft;
        b.char_wer         = rc.char_wer;
        b.S_ch=rc.S; b.D_ch=rc.D; b.I_ch=rc.I;
        b.ter              = rt.ter;
        b.edit_final       = rt.edit_final;
        b.shifts           = rt.shifts;
        b.kualitas         = klasifikasiKesalahan(rk.wer, rs.wer_soft, rc.char_wer);

        semua_hasil.push_back(b);
    }

    // ── Akumulasi per kategori + global ───────────────────────────
    AkumulasiGrup grp_fasih       {"Fasih"};
    AkumulasiGrup grp_kurang      {"Kurang Fasih"};
    AkumulasiGrup grp_global      {"GLOBAL"};

    for (auto& b : semua_hasil) {
        grp_global.tambah(b);
        if      (b.kategori == "Fasih")        grp_fasih.tambah(b);
        else if (b.kategori == "Kurang Fasih") grp_kurang.tambah(b);
    }

    // ── Helper cetak tabel metrik satu grup ───────────────────────
    auto cetakTabelGrup = [&](const AkumulasiGrup& g) {
        std::cout << "  Kalimat   : " << g.jumlah_kalimat << "\n";
        std::cout << "  Kata (N)  : " << g.total_N  << "\n";
        std::cout << "  Char (N)  : " << g.total_Nc << "\n\n";

        std::cout << "  +--------------------------------------------------+\n";
        std::cout << "  |  METRIK               |  NILAI    |  %           |\n";
        std::cout << "  +--------------------------------------------------+\n";

        auto baris = [&](const std::string& nm, double val) {
            std::cout << "  |  " << std::left  << std::setw(21) << nm
                      << " |  " << std::right << std::setw(8)  << fmtDouble(val)
                      << "  |  " << std::setw(8) << pctStr(val) << "    |\n";
        };
        baris("WER Klasik (biner)",  g.werKlasik());
        baris("WER Soft (berbobot)", g.werSoft());
        baris("WER Soft + Fonetik",  g.werSoftFon());
        baris("CharWER",             g.charWER());
        baris("TER (avg)",           g.terAvg());
        std::cout << "  +--------------------------------------------------+\n";

        // Breakdown error klasik
        long long total_err = g.total_S_kl + g.total_D_kl + g.total_I_kl;
        std::cout << "\n  Breakdown Error Klasik:\n";
        std::cout << "     Substitusi (S)  : " << std::setw(5) << g.total_S_kl
                  << "  (" << pctStr(g.total_N > 0 ? (double)g.total_S_kl/g.total_N : 0) << " dari N)\n";
        std::cout << "     Delesi     (D)  : " << std::setw(5) << g.total_D_kl
                  << "  (" << pctStr(g.total_N > 0 ? (double)g.total_D_kl/g.total_N : 0) << " dari N)\n";
        std::cout << "     Insersi    (I)  : " << std::setw(5) << g.total_I_kl
                  << "  (" << pctStr(g.total_N > 0 ? (double)g.total_I_kl/g.total_N : 0) << " dari N)\n";
        std::cout << "     Total Error     : " << std::setw(5) << total_err << "\n";

        // Insight soft WER
        if (g.total_S_kl > 0) {
            double hem = (double)g.total_S_kl - g.total_err_soft;
            double pct = hem / (double)g.total_S_kl * 100.0;
            std::cout << "\n  Insight Soft WER:\n";
            std::cout << "     " << g.total_S_kl << " substitusi dihukum penuh oleh WER Klasik\n";
            std::cout << "     Pengurangan penalti efektif: "
                      << fmtDouble(hem, 1) << " poin (" << fmtDouble(pct, 0) << "%)\n";
        }

        // Distribusi kualitas
        std::cout << "\n  Distribusi Kualitas:\n";
        for (auto& [k, cnt] : g.dist_kualitas) {
            std::string bar(cnt, '*');
            std::cout << "     " << std::left << std::setw(18) << k
                      << std::right << std::setw(3) << cnt << "x  " << bar << "\n";
        }
    };

    // ── Cetak laporan per grup ─────────────────────────────────────

    // --- Fasih ---
    std::cout << "\n" << SEP << "\n";
    std::cout << "  [KELOMPOK: FASIH]\n";
    std::cout << SEP << "\n";
    if (grp_fasih.jumlah_kalimat > 0)
        cetakTabelGrup(grp_fasih);
    else
        std::cout << "  (tidak ada data Fasih)\n";

    // --- Kurang Fasih ---
    std::cout << "\n" << SEP << "\n";
    std::cout << "  [KELOMPOK: KURANG FASIH]\n";
    std::cout << SEP << "\n";
    if (grp_kurang.jumlah_kalimat > 0)
        cetakTabelGrup(grp_kurang);
    else
        std::cout << "  (tidak ada data Kurang Fasih)\n";

    // --- Global ---
    std::cout << "\n" << SEP << "\n";
    std::cout << "  [AKUMULASI GLOBAL (Fasih + Kurang Fasih)]\n";
    std::cout << SEP << "\n";
    cetakTabelGrup(grp_global);

    // ── Tabel perbandingan ringkas Fasih vs Kurang Fasih ──────────
    if (grp_fasih.jumlah_kalimat > 0 && grp_kurang.jumlah_kalimat > 0) {
        std::cout << "\n" << SEP << "\n";
        std::cout << "  PERBANDINGAN FASIH vs KURANG FASIH\n";
        std::cout << SEP << "\n";
        std::cout << "  " << std::left  << std::setw(24) << "METRIK"
                  << std::right << std::setw(14) << "Fasih"
                  << std::setw(16) << "Kurang Fasih"
                  << std::setw(14) << "Delta\n";
        std::cout << "  " << std::string(66, '-') << "\n";

        auto banding = [&](const std::string& nama, double vf, double vk) {
            double delta = vk - vf;
            std::string tanda = (delta >= 0 ? "+" : "");
            std::cout << "  " << std::left  << std::setw(24) << nama
                      << std::right << std::setw(12) << pctStr(vf)
                      << std::setw(16) << pctStr(vk)
                      << std::setw(12) << (tanda + pctStr(delta)) << "\n";
        };

        banding("WER Klasik",         grp_fasih.werKlasik(),  grp_kurang.werKlasik());
        banding("WER Soft (berbobot)",grp_fasih.werSoft(),    grp_kurang.werSoft());
        banding("WER Soft + Fonetik", grp_fasih.werSoftFon(), grp_kurang.werSoftFon());
        banding("CharWER",            grp_fasih.charWER(),    grp_kurang.charWER());
        banding("TER",                grp_fasih.terAvg(),     grp_kurang.terAvg());
        std::cout << "  " << std::string(66, '-') << "\n";
        std::cout << "  Delta (+) = Kurang Fasih lebih tinggi error-nya\n";
    }

    // ── Detail per kalimat (dikelompokkan) ────────────────────────
    std::cout << "\n" << SEP << "\n";
    std::cout << "  DETAIL PER KALIMAT\n";
    std::cout << SEP << "\n";

    auto cetakHeaderDetail = [&]() {
        std::cout << "  " << std::left
                  << std::setw(32) << "path"
                  << std::right
                  << std::setw(6)  << "N"
                  << std::setw(10) << "WER Kl."
                  << std::setw(10) << "WER Soft"
                  << std::setw(10) << "+Fonetik"
                  << std::setw(9)  << "CharWER"
                  << std::setw(8)  << "TER"
                  << "  Kualitas\n";
        std::cout << "  " << std::string(68, '-') << "\n";
    };

    auto cetakBarisDetail = [&](const HasilBaris& b) {
        std::string sp = b.path;
        // Hapus prefix kategori dari nama tampil
        for (auto& prefix : {"Fasih/", "Kurang Fasih/", "fasih/", "kurang fasih/"}) {
            size_t pos = sp.find(prefix);
            if (pos != std::string::npos) { sp = sp.substr(pos + strlen(prefix)); break; }
        }
        if (sp.size() > 29) sp = sp.substr(0, 26) + "...";
        std::cout << "  " << std::left  << std::setw(32) << sp
                  << std::right << std::setw(6)  << b.N_kata
                  << std::setw(10) << pctStr(b.wer_klasik)
                  << std::setw(10) << pctStr(b.wer_soft)
                  << std::setw(10) << pctStr(b.wer_soft_fonetik)
                  << std::setw(9)  << pctStr(b.char_wer)
                  << std::setw(8)  << pctStr(b.ter)
                  << "  " << b.kualitas << "\n";
    };

    // Fasih
    std::cout << "\n  >> FASIH\n";
    cetakHeaderDetail();
    for (auto& b : semua_hasil)
        if (b.kategori == "Fasih") cetakBarisDetail(b);

    // Kurang Fasih
    std::cout << "\n  >> KURANG FASIH\n";
    cetakHeaderDetail();
    for (auto& b : semua_hasil)
        if (b.kategori == "Kurang Fasih") cetakBarisDetail(b);

    // Tidak diketahui (jika ada)
    bool ada_unknown = false;
    for (auto& b : semua_hasil)
        if (b.kategori == "Tidak Diketahui") { ada_unknown = true; break; }
    if (ada_unknown) {
        std::cout << "\n  >> TIDAK DIKETAHUI\n";
        cetakHeaderDetail();
        for (auto& b : semua_hasil)
            if (b.kategori == "Tidak Diketahui") cetakBarisDetail(b);
    }

    // ── Simpan CSV ─────────────────────────────────────────────────
    simpanCsv(semua_hasil, "laporan_advanced_wer.csv");
    std::cout << "\n  Laporan lengkap disimpan ke: laporan_advanced_wer.csv\n";

    // ── Analisis error kata: terburuk per kategori ────────────────
    auto cetakAnalisisError = [&](const std::string& kategori_filter) {
        std::vector<size_t> idx;
        for (size_t i = 0; i < semua_hasil.size(); ++i)
            if (semua_hasil[i].kategori == kategori_filter)
                idx.push_back(i);

        std::sort(idx.begin(), idx.end(), [&](size_t a, size_t b2) {
            return semua_hasil[a].wer_klasik > semua_hasil[b2].wer_klasik;
        });

        std::cout << "\n" << SEP << "\n";
        std::cout << "  ANALISIS ERROR KATA -- " << kategori_filter
                  << " (2 Terburuk)\n";
        std::cout << SEP << "\n";

        int tampil = std::min(2, (int)idx.size());
        for (int k = 0; k < tampil; ++k) {
            auto& b = semua_hasil[idx[k]];
            std::string asli_b, pred_b;
            for (auto& p : pasangan)
                if (p.path == b.path) { asli_b = p.asli; pred_b = p.prediksi; break; }

            std::cout << "\n  [" << b.path << "]\n";
            std::cout << "     WER Klasik: " << pctStr(b.wer_klasik)
                      << "  WER Soft: "      << pctStr(b.wer_soft)
                      << "  CharWER: "       << pctStr(b.char_wer) << "\n";
            std::cout << "     Substitusi (sim karakter):\n";

            auto diffs = analisisPerbedaan(asli_b, pred_b);
            int cnt = 0;
            for (auto& d : diffs) {
                if (d.tipe != "SUBSTITUSI") continue;
                if (++cnt > 6) break;
                std::string bar = progressBar(d.kemiripan, 10);
                std::cout << "       [" << bar << "]  "
                          << std::left  << std::setw(18) << d.ref_w
                          << " -> "
                          << std::setw(18) << d.hyp_w
                          << "  sim=" << fmtDouble(d.kemiripan, 2)
                          << "  penalti=" << fmtDouble(d.penalti, 2) << "\n";
            }
        }
    };

    cetakAnalisisError("Fasih");
    cetakAnalisisError("Kurang Fasih");

    std::cout << "\n" << SEP << "\n";
    std::cout << "  Selesai.\n";
    std::cout << SEP << "\n\n";

    return 0;
}