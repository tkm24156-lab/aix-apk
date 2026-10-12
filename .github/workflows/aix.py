import ast
import calendar
import copy
import difflib
import functools
import importlib.util
import json
import logging
import math
import os
import random
import re
import textwrap
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import (Decimal, DivisionByZero, InvalidOperation, Overflow,
                     ROUND_HALF_UP, getcontext)
from functools import reduce

getcontext().prec = 100  # presisi tinggi supaya hitungan tetap eksak


def _saring_peringatan_stanza(rekaman):
    """Sembunyikan peringatan tak berbahaya "... expects mwt, which has been
    added" dari Stanza (dipakai Argos). Peringatan lain tetap tampil."""
    return "expects mwt" not in rekaman.getMessage()


logging.getLogger("stanza").addFilter(_saring_peringatan_stanza)

NAMA_BOT = "AIX"
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]


# ---------------------------------------------------------------- utilitas
# ------------------------------------------------ penyimpanan data & riwayat
DATA = {}
_PERINGATAN_SIMPAN = [False]
RIWAYAT_AKTIF = [False]
AWALAN_BUKAN_HASIL = ("Rumus", "Cek", "Tips", "Status", "(", "MATERI", "===", "Mesin")


def lokasi_data():
    """File data: aix_data.json di folder program (atau variabel AIX_DATA)."""
    jalur = os.environ.get("AIX_DATA")
    if jalur:
        return jalur
    try:
        folder = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        folder = os.getcwd()
    return os.path.join(folder, "aix_data.json")


def siapkan_data():
    for kunci, awal in (("riwayat", []), ("kosakata", []), ("skor_kuis", {}),
                        ("memori_chat", {})):
        if not isinstance(DATA.get(kunci), type(awal)):
            DATA[kunci] = awal


def muat_data():
    """Baca data tersimpan: riwayat, ingatan chat, kosakata, skor kuis."""
    DATA.clear()
    jalur = lokasi_data()
    try:
        with open(jalur, encoding="utf-8") as f:
            isi = json.load(f)
        if isinstance(isi, dict):
            DATA.update(isi)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        try:
            os.replace(jalur, jalur + ".rusak")
        except OSError:
            pass
        print("AIX: file data lama rusak, jadi aku mulai dari awal "
              "(file lama disimpan dengan akhiran .rusak).")
    siapkan_data()


def simpan_data():
    siapkan_data()
    try:
        with open(lokasi_data(), "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=1)
    except OSError:
        if not _PERINGATAN_SIMPAN[0]:
            _PERINGATAN_SIMPAN[0] = True
            print("AIX: (data tidak bisa disimpan ke file, jadi hanya berlaku "
                  "selama program berjalan.)")


def catat_riwayat(teks):
    siapkan_data()
    daftar = DATA["riwayat"]
    daftar.append([datetime.now().strftime("%d-%m %H:%M"), teks])
    del daftar[:-300]
    simpan_data()


def mungkin_catat(teks):
    """Catat baris yang tampak seperti hasil hitungan."""
    if teks.startswith(AWALAN_BUKAN_HASIL):
        return
    if " = " in teks or teks.startswith("Hasil dari") or " adalah " in teks:
        catat_riwayat(teks)


def bot(teks):
    print(f"{NAMA_BOT}: {teks}")
    if RIWAYAT_AKTIF[0]:
        mungkin_catat(teks)


def merekam(fungsi):
    """Dekorator: selama fungsi berjalan, hasil hitungan dicatat ke riwayat."""
    @functools.wraps(fungsi)
    def bungkus(*args, **kwargs):
        lama = RIWAYAT_AKTIF[0]
        RIWAYAT_AKTIF[0] = True
        try:
            return fungsi(*args, **kwargs)
        finally:
            RIWAYAT_AKTIF[0] = lama
    return bungkus


def format_angka(x):
    """Tampilkan Decimal tanpa notasi ilmiah, maksimal 10 angka desimal.
    Angka yang sangat besar/kecil ditampilkan dalam notasi ilmiah."""
    if x == 0:
        return "0"
    if abs(x) >= Decimal("1e90") or abs(x) < Decimal("1e-10"):
        mantissa, eksponen = format(x, ".10e").split("e")
        return mantissa.rstrip("0").rstrip(".") + "e" + eksponen
    x = x.quantize(Decimal(1).scaleb(-10), rounding=ROUND_HALF_UP)
    teks = format(x, "f")
    if "." in teks:
        teks = teks.rstrip("0").rstrip(".")
    return "0" if teks in ("", "-0") else teks


def format_tanggal(d):
    return f"{HARI[d.weekday()]}, {d.day} {BULAN[d.month - 1]} {d.year}"


def baca_angka(prompt):
    """Minta angka sampai valid. Mengembalikan Decimal, atau None kalau 'batal'."""
    while True:
        s = input(prompt).strip().replace(",", ".")
        if s.lower() in ("batal", "kembali"):
            return None
        try:
            angka = Decimal(s)
        except InvalidOperation:
            bot("Itu bukan angka. Coba lagi ya (atau ketik 'batal').")
            continue
        if not angka.is_finite():
            bot("Angka harus berupa bilangan biasa (bukan inf/nan).")
            continue
        if angka != 0 and not (-40 <= angka.adjusted() <= 40):
            bot("Angkanya terlalu besar atau terlalu kecil. Coba angka lain.")
            continue
        return angka


def baca_tanggal(prompt):
    """Minta tanggal format DD-MM-YYYY. Mengembalikan None kalau 'batal'."""
    while True:
        s = input(prompt).strip()
        if s.lower() in ("batal", "kembali"):
            return None
        for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        bot("Format tanggal salah. Pakai DD-MM-YYYY, contoh: 17-08-2026 "
            "(atau ketik 'batal').")


fa = format_angka
_RE_RIBUAN_TEKS = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+)(?![\d.,])")

def _angka_teks(teks):
    """'1.000' -> '1000' (pemisah ribuan), '2,5' -> '2.5' (desimal).
    Dipakai kalkulator chat dan akar/persen agar konsisten."""
    teks = _RE_RIBUAN_TEKS.sub(lambda m: m.group(1).replace(".", ""), teks)
    return teks.replace(",", ".")


def tampil_menu(judul, pilihan):
    """Cetak sub-menu bernomor (+ opsi 0 untuk kembali), kembalikan input user."""
    print()
    bot(f"=== {judul} ===")
    for nomor, teks in enumerate(pilihan, start=1):
        print(f"{nomor}. {teks}")
    print("0. Kembali")
    while True:
        pilih = input("AIX: pilih menu: ").strip()
        if pilih.lower() == "materi":
            ulangi_materi()
            continue
        return pilih


def ambil_nomor(pilih, jumlah):
    """Ubah teks pilihan jadi angka 1..jumlah, atau None kalau tidak valid."""
    if pilih in [str(i) for i in range(1, jumlah + 1)]:
        return int(pilih)
    bot("Pilihan tidak valid.")
    return None


def baca_positif(prompt):
    """Seperti baca_angka, tapi harus > 0. None kalau 'batal'."""
    while True:
        angka = baca_angka(prompt)
        if angka is None:
            return None
        if angka > 0:
            return angka
        bot("Nilainya harus lebih besar dari 0.")


def baca_banyak(*daftar_prompt):
    """Baca beberapa angka positif berurutan. None kalau user batal."""
    hasil = []
    for prompt in daftar_prompt:
        angka = baca_positif(prompt)
        if angka is None:
            return None
        hasil.append(angka)
    return hasil


def baca_bulat_positif(prompt):
    while True:
        angka = baca_positif(prompt)
        if angka is None:
            return None
        if angka == angka.to_integral_value():
            return int(angka)
        bot("Harus berupa bilangan bulat.")


def baca_bulat(prompt, batas=1000):
    """Bilangan bulat (boleh negatif/nol) dengan batas |nilai| <= batas."""
    while True:
        angka = baca_angka(prompt)
        if angka is None:
            return None
        if angka != angka.to_integral_value():
            bot("Harus berupa bilangan bulat.")
            continue
        if abs(angka) > batas:
            bot(f"Pakai bilangan antara -{batas} dan {batas}.")
            continue
        return int(angka)


def submenu(judul, daftar):
    """Tampilkan sub-menu berulang sampai user memilih 0.
    daftar = [(nama, fungsi), ...]"""
    while True:
        pilih = tampil_menu(judul, [nama for nama, _ in daftar])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(daftar))
        if n is not None:
            daftar[n - 1][1]()


# ---------------------------------------------------------- fitur 1: chat
SINGKATAN = {
    "gmn": "bagaimana", "gimana": "bagaimana", "gmna": "bagaimana",
    "bgmn": "bagaimana", "knp": "kenapa", "kmu": "kamu", "km": "kamu",
    "lu": "kamu", "lo": "kamu", "anda": "kamu", "gw": "aku", "gue": "aku",
    "gua": "aku", "saya": "aku", "sy": "aku", "udah": "sudah", "udh": "sudah",
    "sdh": "sudah", "gak": "tidak", "ga": "tidak", "nggak": "tidak",
    "enggak": "tidak", "engga": "tidak", "tdk": "tidak", "bgt": "banget",
    "yg": "yang", "dgn": "dengan", "kbr": "kabar", "mksh": "makasih",
    "thx": "thanks", "tq": "thanks", "yaudah": "oke", "yoi": "iya",
}

CONTOH_CHAT = [
    "berapa 12 x 4   |   2 pangkat 10   |   akar 144   |   15% dari 200",
    "hari apa 17 agustus 2026   |   berapa hari lagi 25-12-2026",
    "terjemahkan selamat pagi ke jepang",
    "ceritakan fakta unik   |   lelucon   |   tebak-tebakan   |   motivasi",
    "namaku Budi   (aku akan mengingat namamu)",
    "ketik 'menu' untuk kuis, konversi satuan, keuangan, kalender, flashcard, dll.",
]

LELUCON = [
    "Kenapa komputer nggak pernah kedinginan? Karena dia punya banyak Windows!",
    "Kenapa programmer lebih suka dark mode? Karena cahaya menarik serangga (bug)!",
    "Kenapa buku matematika sedih? Karena punya terlalu banyak masalah.",
    "Apa bedanya matematika dan cinta? Di matematika kamu yang mencari X, "
    "di cinta X-nya yang datang sendiri.",
    "Kenapa ayam menyeberang jalan? Untuk sampai ke seberang. (Iya, selucu itu.)",
    "Kalau komputer bisa mengantuk, namanya apa? Sleep mode!",
]

FAKTA = [
    "Madu hampir tidak pernah basi. Kalau disimpan dengan benar, madu bisa awet "
    "sangat lama karena kadar airnya rendah dan sifatnya asam.",
    "Gurita punya tiga jantung: dua memompa darah ke insang, satu ke seluruh tubuh.",
    "Satu hari di Venus (satu putaran pada porosnya) lebih lama daripada satu "
    "tahunnya mengelilingi Matahari.",
    "Jerapah dan manusia sama-sama punya tujuh ruas tulang leher.",
    "Cahaya dari Matahari butuh sekitar 8 menit untuk sampai ke Bumi.",
    "Kotoran wombat berbentuk kubus, dan itu bukan lelucon!",
    "Secara botani, pisang termasuk buah beri (berry), sedangkan stroberi bukan.",
    "Kata 'algoritma' berasal dari nama ilmuwan Persia, Al-Khawarizmi.",
    "Teorema Pythagoras sudah dikenal bangsa Babilonia lebih dari seribu tahun "
    "sebelum Pythagoras lahir.",
    "Pi (3,14159...) adalah bilangan irasional: desimalnya tidak pernah berakhir "
    "dan tidak berulang membentuk pola.",
    "Bumi mengelilingi Matahari sekali dalam sekitar 365,25 hari, makanya ada "
    "tahun kabisat.",
]

MOTIVASI = [
    "Langkah kecil yang dilakukan tiap hari lebih berharga daripada rencana besar "
    "yang tidak pernah dimulai.",
    "Salah itu bagian dari belajar. Yang penting kamu mencoba lagi dengan cara "
    "yang lebih baik.",
    "Kamu tidak harus sempurna untuk mulai. Mulai dulu, rapikan sambil jalan.",
    "Istirahat bukan tanda menyerah, tapi cara mengisi tenaga untuk lanjut.",
    "Soal yang sulit hari ini akan terasa mudah di hari esok, asal kamu terus berlatih.",
]

TEBAKAN = [
    ("Aku punya kota tapi tanpa rumah, punya sungai tapi tanpa air, dan punya "
     "jalan tapi tanpa kendaraan. Aku apa?", ["peta"]),
    ("Semakin banyak diambil, semakin besar. Apa itu?", ["lubang"]),
    ("Apa yang selalu ada di depanmu tapi tidak bisa kamu lihat?",
     ["masa depan"]),
    ("Aku punya tombol tapi tidak bisa membuka pintu, dan punya huruf tapi "
     "bukan buku. Aku apa?", ["keyboard", "papan ketik"]),
    ("Apa yang makin basah kalau dipakai mengeringkan?", ["handuk", "lap"]),
    ("Apa yang bisa kamu patahkan tanpa pernah menyentuhnya?", ["janji"]),
]

KRISIS = [
    "bunuh diri", "mengakhiri hidup", "akhiri hidup", "ingin mati", "pengen mati",
    "mau mati", "menyakiti diri", "melukai diri", "tidak ingin hidup",
    "tidak mau hidup",
]

BALASAN_KRISIS = [
    "Aku turut prihatin kamu merasa seperti ini, dan terima kasih sudah mau "
    "menceritakannya.",
    "Perasaanmu itu nyata dan penting. Kamu pantas mendapat dukungan, dan tidak "
    "harus melewatinya sendirian.",
    "Kalau kamu sedang dalam bahaya sekarang atau berpikir akan menyakiti dirimu, "
    "segera hubungi layanan darurat setempat atau minta orang terdekat menemanimu "
    "saat ini juga.",
    "Kamu juga bisa bicara dengan keluarga, teman, atau tenaga profesional seperti "
    "psikolog/psikiater atau layanan kesehatan jiwa di dekatmu. Aku di sini kalau "
    "kamu mau bercerita.",
]

# (nama, kata kunci, jawaban, maks_kata). Frasa 2+ kata bernilai lebih tinggi.
# "{n}" diganti ", <nama user>" kalau namanya sudah diketahui.
INTENT = [
    ("salam_masuk", ["assalamualaikum", "assalamu alaikum"], None, None),
    ("salam_pilihan_balasan", ["waalaikumsalam", "waalaikumussalam", "alaikumussalam",
                               "wslm"], None, None),
    ("salam_pilihan", ["salam", "wasalam", "wassalam"], None, None),
    ("selamat_waktu", ["selamat pagi", "selamat siang", "selamat sore",
                       "selamat malam"], [], None),
    ("sapa", ["halo", "hai", "hi", "hello", "hallo", "hei", "hey",
              "assalamualaikum", "permisi"], [
        "Halo{n}! Senang ketemu kamu. Mau ngobrol apa hari ini?",
        "Hai{n}! Ada yang bisa aku bantu?",
        "Halo halo{n}! Lagi pengin ngobrol atau butuh bantuan hitung-hitungan?",
    ], None),
    ("kabar", ["apa kabar", "bagaimana kabar", "kabarmu", "kabar kamu",
               "how are you", "kamu baik"], [
        "Aku baik-baik saja, terima kasih sudah nanya! Kamu sendiri bagaimana kabarnya?",
        "Baik dong! Kamu sendiri gimana kabarnya{n}?",
    ], None),
    ("lagi_apa", ["lagi apa", "sedang apa", "ngapain"], [
        "Lagi ngobrol sama kamu dong! Kamu sendiri lagi apa?",
        "Lagi nungguin kamu ngetik :) Ada cerita apa?",
    ], None),
    ("siapa", ["siapa kamu", "kamu siapa", "nama kamu", "namamu", "kenalan",
               "perkenalkan diri"], [
        "Aku AIX-001, chatbot buatan Dzaki dxd. Aku bisa ngobrol, berhitung, "
        "menghitung hari, dan menerjemahkan.",
        "Namaku AIX, asisten kecil yang suka matematika dan ngobrol. Kalau kamu siapa?",
    ], None),
    ("pembuat", ["siapa pembuat", "pembuatmu", "pembuat kamu", "pengembang",
                 "yang membuat", "dibuat oleh", "penciptamu", "siapa yang buat"], [
        "Aku dikembangkan oleh Dzaki dxd.",
    ], None),
    ("kemampuan", ["bisa apa", "bantuan", "fitur", "help", "apa saja",
                   "kemampuan", "bantu aku"], [], None),
    ("terima_kasih", ["terima kasih", "terimakasih", "makasih", "makasi",
                      "thanks", "thank you", "trims"], [
        "Sama-sama{n}!", "Senang bisa membantu!", "Siap, kapan saja!",
    ], None),
    ("pujian", ["kamu pintar", "kamu keren", "kamu hebat", "kamu bagus",
                "kamu canggih", "pintar banget", "keren banget", "hebat banget"], [
        "Terima kasih{n}, kamu bikin aku semangat!",
        "Wah, jadi malu... makasih ya!",
    ], None),
    ("sedih", ["sedih", "galau", "stres", "stress", "depresi", "kesepian",
               "sendirian", "capek", "lelah", "pusing", "kecewa", "takut",
               "cemas", "khawatir", "patah hati", "putus asa", "menangis"], [
        "Aku turut sedih mendengarnya{n}. Mau cerita lebih banyak? Aku di sini "
        "mendengarkan.",
        "Kedengarannya berat ya. Tarik napas pelan-pelan dulu. Aku siap mendengarkan.",
        "Wajar kok kalau merasa begitu. Istirahat sebentar, minum air putih, dan "
        "kalau perlu cerita ke orang yang kamu percaya ya.",
    ], None),
    ("bosan", ["bosan", "bosen", "gabut", "bete", "membosankan"], [
        "Bosan ya? Coba ketik 'tebak-tebakan', 'lelucon', atau 'fakta unik' biar seru!",
        "Gabut? Yuk main tebak-tebakan. Ketik 'tebak-tebakan'!",
    ], None),
    ("senang", ["senang", "bahagia", "gembira", "asik", "asyik", "seru", "hore",
                "lulus", "berhasil", "menang"], [
        "Wah, ikut senang dengarnya{n}!",
        "Mantap! Kabar baik memang bikin hari lebih cerah.",
        "Selamat ya! Ceritain dong, ada apa?",
    ], None),
    ("lelucon", ["lelucon", "lawak", "candaan", "bercanda", "jokes", "joke",
                 "lucu", "humor", "lawakan"], [], None),
    ("fakta", ["fakta", "fakta unik", "tahukah", "info menarik", "pengetahuan"],
     [], None),
    ("motivasi", ["motivasi", "semangat", "kata mutiara", "inspirasi", "bijak"],
     [], None),
    ("tebakan", ["tebak", "tebakan", "teka teki", "tebak tebakan"], [], None),
    ("umur", ["umur kamu", "umurmu", "usia kamu", "usiamu", "berapa umur",
              "berapa usia"], [
        "Aku masih muda sekali, baru berupa program Python yang terus ditambah fiturnya.",
    ], None),
    ("robot", ["kamu manusia", "kamu robot", "kamu bot", "kamu asli", "kamu ai",
               "manusia atau robot", "kamu hidup"], [
        "Aku bukan manusia, aku chatbot (program komputer) buatan Dzaki dxd. "
        "Tapi aku senang ngobrol sama kamu!",
    ], None),
    ("hobi_bot", ["hobimu", "hobi kamu", "kamu suka apa", "apa hobi",
                  "kegemaranmu"], [
        "Hobiku berhitung dan ngobrol! Kalau kamu hobi apa?",
    ], None),
    ("makan", ["lapar", "makan", "makanan", "haus", "minum"], [
        "Jangan lupa makan teratur ya{n}! Kamu suka makanan apa?",
        "Lapar ya? Semoga ada makanan enak di dekatmu. Aku sih makannya listrik, hehe.",
    ], None),
    ("cuaca", ["cuaca", "hujan", "panas", "dingin", "mendung"], [
        "Aku tidak bisa melihat cuaca langsung, tapi kamu bisa cek aplikasi cuaca "
        "di HP. Kalau hujan, jangan lupa bawa payung!",
    ], None),
    ("ingat", ["siapa aku", "namaku siapa", "aku suka apa", "tentang aku",
               "kamu ingat aku", "kamu tahu aku", "ingat namaku",
               "siapa namaku"], [], None),
    ("tidur", ["selamat tidur", "ngantuk", "mau tidur", "mengantuk"], [
        "Selamat tidur{n}! Semoga mimpi indah.",
        "Istirahat yang cukup ya{n}. Sampai ketemu lagi!",
    ], None),
    ("pamit", ["bye", "dadah", "sampai jumpa", "selamat tinggal", "pamit",
               "udahan", "pergi dulu"], [
        "Sampai jumpa{n}! Ketik 'menu' kalau mau kembali ke menu utama.",
        "Dadah{n}! Senang ngobrol sama kamu.",
    ], None),
    ("setuju", ["ya", "iya", "oke", "ok", "siap", "sip", "boleh", "baik"], [
        "Oke!", "Siap!", "Sip, lanjut.",
    ], 3),
    ("tidak", ["tidak", "nope"], [
        "Oke, tidak apa-apa.", "Baik, santai saja.",
    ], 3),
]

POSITIF = {"baik", "bagus", "senang", "sehat", "mantap", "oke", "ok", "lancar",
           "seru", "asik", "asyik", "hebat"}
NEGATIF = {"sedih", "capek", "lelah", "buruk", "stres", "stress", "pusing",
           "sakit", "galau", "bosan", "kacau", "lesu"}
NETRAL = {"biasa", "lumayan", "begitu", "gitu", "so"}
PENGISI = {"banget", "sih", "dong", "deh", "kok", "nih", "aja", "saja"}

OPERATOR_KATA = [
    (r"\bdikali(?:kan)?\b", "*"), (r"\bkali\b", "*"),
    (r"\bdibagi\b", "/"), (r"\bbagi\b", "/"),
    (r"\bditambah\b", "+"), (r"\btambah\b", "+"), (r"\bplus\b", "+"),
    (r"\bdikurangi\b", "-"), (r"\bkurang\b", "-"), (r"\bminus\b", "-"),
    (r"\bpangkat\b", "**"),
]
KATA_ISI = (r"\b(?:berapa|berapakah|hitung|hitunglah|tolong|dong|donk|ya|sih|deh|"
            r"nih|hasil|hasilnya|adalah|sama|dengan|itu|ini|apa|kan|coba|bisa|"
            r"kamu|aku|soal|jawab|jawabannya|matematika)\b")

POLA_TERJEMAH = re.compile(
    r"^\s*(?:tolong\s+)?(?:terjemahkan|terjemahin|translate|artikan)\s+[\"']?(.+?)"
    r"[\"']?\s+(?:ke\s+dalam\s+bahasa|ke\s+bahasa|dalam\s+bahasa|ke|into)\s+"
    r"(indonesia|inggris|rusia|jepang|jerman)\s*[.!?]?\s*$", re.IGNORECASE)
KODE_BAHASA = {"indonesia": "id", "inggris": "en", "rusia": "ru",
               "jepang": "ja", "jerman": "de"}


# ---- alat bantu
def normalisasi(teks):
    low = teks.lower()
    tokens = [SINGKATAN.get(t, t) for t in re.findall(r"[a-z0-9]+", low)]
    return low, tokens


def ada(kata_kata, teks):
    """Cek apakah salah satu kata/frasa muncul sebagai kata utuh di teks."""
    return any(re.search(rf"\b{re.escape(k)}\b", teks) for k in kata_kata)


def skor(kata_kunci, tokens, bersih):
    total = 0
    for kunci in kata_kunci:
        if " " in kunci:
            if re.search(rf"\b{re.escape(kunci)}\b", bersih):
                total += 2
        elif kunci in tokens:
            total += 1
        elif len(kunci) >= 7 and difflib.get_close_matches(
                kunci, tokens, n=1, cutoff=0.86):
            total += 1  # toleran typo untuk kata panjang
    return total


def putar(daftar, state, kunci):
    """Ambil item berikutnya dari daftar yang diacak, tanpa mengulang sebelum habis."""
    antrian = state.setdefault("antrian", {})
    if not antrian.get(kunci):
        baru = list(daftar)
        random.shuffle(baru)
        if len(baru) > 1 and baru[-1] == state.get("terakhir", {}).get(kunci):
            baru[0], baru[-1] = baru[-1], baru[0]
        antrian[kunci] = baru
    item = antrian[kunci].pop()
    state.setdefault("terakhir", {})[kunci] = item
    return item


def sebut_nama(state):
    return f", {state['nama']}" if state.get("nama") else ""


def periode_hari(jam=None):
    jam = datetime.now().hour if jam is None else jam
    if 4 <= jam < 11:
        return "pagi"
    if 11 <= jam < 15:
        return "siang"
    if 15 <= jam < 18:
        return "sore"
    return "malam"


# ---- tanggal & waktu
def cari_tanggal(low, hari_ini, cari_berikutnya):
    """Cari tanggal di teks: DD-MM-YYYY, DD/MM/YYYY, atau '17 agustus 2026'."""
    hasil = []
    for h, b, t in re.findall(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", low):
        try:
            hasil.append(date(int(t), int(b), int(h)))
        except ValueError:
            pass
    nama_bulan = [b.lower() for b in BULAN]
    pola = r"\b(\d{1,2})\s+(" + "|".join(nama_bulan) + r")(?:\s+(\d{4}))?\b"
    for h, b, t in re.findall(pola, low):
        bulan = nama_bulan.index(b) + 1
        try:
            if t:
                d = date(int(t), bulan, int(h))
            else:
                d = date(hari_ini.year, bulan, int(h))
                if cari_berikutnya and d < hari_ini:
                    d = date(hari_ini.year + 1, bulan, int(h))
            hasil.append(d)
        except ValueError:
            pass
    return hasil


def jawab_tanggal_waktu(low):
    sekarang = datetime.now()
    hari_ini = sekarang.date()
    if re.search(r"\b(?:jam|pukul)\s+berapa\b", low) or "waktu sekarang" in low:
        return [f"Sekarang pukul {sekarang.strftime('%H:%M')}."]

    tanggal = cari_tanggal(low, hari_ini, "lagi" in low)
    petunjuk = re.search(r"\b(?:hari|tanggal|selisih|lagi|jatuh|menuju|sisa|lalu)\b", low)
    if tanggal and petunjuk:
        if len(tanggal) >= 2:
            d1, d2 = tanggal[0], tanggal[1]
            return [f"Selisih {format_tanggal(d1)} dan {format_tanggal(d2)} "
                    f"adalah {abs((d2 - d1).days)} hari."]
        d = tanggal[0]
        selisih = (d - hari_ini).days
        baris = [f"{format_tanggal(d)}."]
        if selisih > 0:
            baris.append(f"Itu {selisih} hari lagi dari hari ini.")
        elif selisih < 0:
            baris.append(f"Itu sudah {-selisih} hari yang lalu.")
        else:
            baris.append("Itu hari ini!")
        return baris

    if not tanggal and re.search(
            r"\b(?:hari apa|tanggal berapa|tanggal sekarang|sekarang tanggal)\b", low):
        if re.search(r"\bkemarin\b", low):
            offset, label = -1, "Kemarin"
        elif re.search(r"\blusa\b", low):
            offset, label = 2, "Lusa"
        elif re.search(r"\bbesok\b", low):
            offset, label = 1, "Besok"
        else:
            offset, label = 0, "Hari ini"
        return [f"{label} {format_tanggal(hari_ini + timedelta(days=offset))}."]
    return None


# ---- hitung-hitungan lewat chat
def _nilai(node):
    """Evaluasi ekspresi aritmatika dengan aman (hanya angka dan + - * / **)."""
    if isinstance(node, ast.Expression):
        return _nilai(node.body)
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _nilai(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        a, b = _nilai(node.left), _nilai(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            if b == 0:
                raise ValueError("Tidak bisa membagi dengan nol.")
            return a / b
        if isinstance(node.op, ast.Pow):
            return hitung_pangkat(a, b)
    raise ValueError("Ekspresi itu belum bisa kuhitung.")


def ekspresi_dari_teks(low):
    e = low
    for pola, op in OPERATOR_KATA:
        e = re.sub(pola, f" {op} ", e)
    e = re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", " * ", e)
    e = e.replace(":", " / ").replace("^", " ** ")
    e = _RE_RIBUAN_TEKS.sub(lambda m: m.group(1).replace(".", ""), e)
    e = re.sub(r"(?<=\d),(?=\d)", ".", e)
    e = re.sub(KATA_ISI, " ", e)
    e = re.sub(r"[=?!]", " ", e)
    e = re.sub(r"\s+", " ", e).strip().rstrip(". ")
    if not e or len(e) > 80 or not re.fullmatch(r"[0-9+\-*/().\s]+", e):
        return None
    if len(re.findall(r"\d+(?:\.\d+)?", e)) < 2 or not re.search(r"[+\-*/]", e):
        return None
    return e


def coba_hitung(low):
    """Kalau pesan berisi soal hitungan, kembalikan jawabannya (list baris)."""
    if re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b", low):
        return None  # itu tanggal, bukan hitungan
    angka = r"(\d+(?:[.,]\d+)?)"

    def desimal(s):
        return Decimal(_angka_teks(s))

    try:
        m = re.search(angka + r"\s*(?:%|persen)\s*(?:dari\s+)?" + angka, low)
        if m:
            p, x = desimal(m.group(1)), desimal(m.group(2))
            return [f"{fa(p)}% dari {fa(x)} = {fa(p * x / 100)}"]
        m = re.search(r"\bakar\s+(?:pangkat\s+tiga|kubik|3)\s+(?:dari\s+)?" + angka, low)
        if m:
            x = desimal(m.group(1))
            return [f"Akar pangkat tiga dari {fa(x)} = {fa(hitung_akar(x, Decimal(3)))}"]
        m = re.search(r"\bakar\s+(?:kuadrat\s+)?(?:dari\s+)?" + angka, low)
        if m:
            x = desimal(m.group(1))
            return [f"Akar kuadrat dari {fa(x)} = {fa(hitung_akar(x, Decimal(2)))}"]
        e = ekspresi_dari_teks(low)
        if e is None:
            return None
        hasil = _nilai(ast.parse(e, mode="eval"))
        tampil = e.replace("**", "^").replace("*", "x")
        return [f"{tampil} = {fa(hasil)}"]
    except SyntaxError:
        return None
    except ValueError as err:
        return [str(err)]
    except ArithmeticError:
        return ["Hasilnya terlalu besar atau tidak bisa dihitung."]


# ---- terjemah lewat chat
def jawab_terjemah(m):
    teks = m.group(1).strip()
    nama = m.group(2).lower()
    kode = KODE_BAHASA[nama]
    asal_offline = "id" if kode != "id" else "en"  # tebakan untuk mesin offline
    try:
        hasil, dipakai = terjemahkan(teks, "auto", kode, "otomatis", asal_offline)
    except GagalTerjemah as err:
        return [f"Aku belum bisa menerjemahkannya: {err}.",
                "Buka menu 5 (Translate) untuk petunjuk pemasangan."]
    baris = [f"{nama.capitalize()}: {hasil}"]
    if dipakai == "offline":
        baris.append(f"(diterjemahkan offline; teks dianggap berbahasa "
                     f"{NAMA_BAHASA[asal_offline]})")
    elif dipakai == "kamus":
        baris.append("(dari kamus bawaan)")
    if kode == "ru":
        baris.append(f"Cara baca: {latinkan_rusia(hasil)}")
    elif kode == "ja":
        romaji = romaji_jepang(hasil)
        if romaji:
            baris.append(f"Cara baca (romaji): {romaji}")
    return baris


# ---- memori percakapan
def ekstrak_info(low, state):
    m = re.search(r"\b(?:namaku|nama aku|nama saya|panggil aku|panggil saya|"
                  r"panggil gue|panggil gw)\s+(?:adalah\s+|aja\s+|saja\s+)?"
                  r"([a-z]{2,20})\b", low)
    if m and m.group(1) not in ("siapa", "apa"):
        state["nama"] = m.group(1).capitalize()
        return [f"Senang berkenalan, {state['nama']}! Aku akan mengingat namamu."]

    m = re.search(r"\b(?:aku|saya|gue|gw)\s+(?:sangat\s+)?(?:suka|hobi|gemar|cinta)"
                  r"\s+(?:sama\s+|dengan\s+)?([a-z0-9 ]{2,40})", low)
    if m:
        hobi = re.sub(r"\s+(banget|sekali|nih|sih|dong|ya)$", "", m.group(1).strip())
        if re.match(r"^(apa|siapa|bagaimana|kenapa)\b", hobi):
            return None
        if re.match(r"^(kamu|dirimu|km)\b", hobi):
            return ["Wah, makasih! Aku juga senang ngobrol sama kamu."]
        daftar = state.setdefault("suka", [])
        if hobi not in daftar:
            daftar.append(hobi)
            del daftar[:-5]
        return [f"Wah, kamu suka {hobi} ya! Seru. Aku akan mengingatnya."]
    return None


def jawab_ingat(state):
    baris = []
    if state.get("nama"):
        baris.append(f"Namamu {state['nama']}.")
    if state.get("suka"):
        baris.append("Kamu suka " + ", ".join(state["suka"]) + ".")
    if not baris:
        return ["Aku belum tahu apa-apa tentang kamu. Coba bilang: 'namaku Budi' "
                "atau 'aku suka membaca'."]
    return baris


# ---- tebak-tebakan
def jawab_tebakan(bersih, state):
    soal, jawaban = state["tebakan"]
    n = sebut_nama(state)
    if re.search(r"\b(?:menyerah|nyerah|skip|lewati|stop|batal)\b", bersih) or \
            "tidak tahu" in bersih or "tidak tau" in bersih:
        state.pop("tebakan")
        state.pop("salah", None)
        return [f"Jawabannya: {jawaban[0]}. Mau tebakan lagi? Ketik 'tebak-tebakan'."]
    if any(re.search(rf"\b{re.escape(k)}\b", bersih) for k in jawaban):
        state.pop("tebakan")
        state.pop("salah", None)
        return [f"Benar sekali! Kamu pintar{n}. Mau tebakan lagi?"]
    state["salah"] = state.get("salah", 0) + 1
    if state["salah"] >= 3:
        state.pop("tebakan")
        state.pop("salah", None)
        return [f"Belum tepat. Jawabannya: {jawaban[0]}. Mau coba tebakan lain?"]
    return [f"Belum tepat. Coba lagi ya (kesempatan tersisa {3 - state['salah']}), "
            "atau ketik 'menyerah'."]


# ---- menjawab
def tindak_lanjut_kabar(tokens, state):
    n = sebut_nama(state)
    kata = set(tokens)
    if kata & NEGATIF or ("tidak" in kata and kata & POSITIF):
        daftar = ["Aduh, semoga cepat membaik ya{n}. Mau cerita apa yang bikin begitu?",
                  "Turut prihatin ya. Istirahat yang cukup dan jangan dipendam sendiri."]
    elif kata & POSITIF:
        daftar = ["Syukurlah kalau begitu{n}! Semoga harimu tetap menyenangkan.",
                  "Wah, bagus dong! Ikut senang dengarnya."]
    elif kata & NETRAL:
        daftar = ["Hari yang biasa juga nggak apa-apa kok{n}.",
                  "Oke, santai saja. Ada yang mau diobrolkan?"]
    else:
        return None
    return [putar(daftar, state, "tindak_lanjut_kabar").replace("{n}", n)]


def pilih_intent(tokens):
    bersih = " ".join(tokens)
    terbaik, nilai_terbaik = None, 0
    for intent in INTENT:
        _, kata_kunci, _, maks = intent
        if maks is not None and (len(tokens) > maks
                                 or not set(tokens) <= set(kata_kunci) | PENGISI):
            continue
        s = skor(kata_kunci, tokens, bersih)
        if s > nilai_terbaik:
            terbaik, nilai_terbaik = intent, s
    return terbaik


JAWABAN_SALAM = {
    "salam_masuk": [
        "Wa'alaikumussalam wr.wb. Salam hormat untuk kakak, semoga harinya penuh keberkahan.",
        "Waalaikumsalam wr.wb. Salam kakak kami terima, semoga sehat selalu dan rezekinya lancar.",
        "Wa'alaikumussalam wr.wb. Ada yang bisa kami bantu, kakak? Atau sekadar mau ngobrol santai?",
    ],
    "salam_pilihan": [
        "Wa'alaikumussalam wr.wb. Salam hormat balik buat kakak, semoga harinya penuh barakah dan rezekinya lancar. Ada yang bisa kami bantu?",
        "Waalaikumsalam wr.wb. Salam menyelimuti kakak, semoga hari ini ringan-ringan. Mau tanya, hitung, atau sekadar teman?",
        "Wa'alaikumussalam wr.wb. Salam balik dengan hormat, semoga tubuh kakak sehat, hati tenang, urusan lancar. Yuk, mau ngobrol apa?",
        "Waalaikumsalam wr.wb. Salam kakak kami terima dengan bangga, semoga hari ini bikin senyum banyak. Ada yang mau kami bantu?",
        "Wa'alaikumussalam wr.wb. Salam kakak diterima dengan ikhlas, yuk kita lanjutkan obrolan kamu, kami siap diajak ngobrol.",
    ],
    "salam_pilihan_balasan": [
        "Assalamu'alaikum wr.wb. Salam hormat balik, semoga kakak senantiasa diberi kelancaran, kesehatan, dan rejeki yang melimpah.",
        "Wa'alaikumussalam wr.wb. Amin, salam kakak kami ikuti dengan pembacaan yang sama. Semoga hari ini cinta Allah melimpah.",
        "Assalamu'alaikum wr.wb. Salam kakak kami balas dengan senyum (maya-maya), semoga tubuh kakak selalu kuat dan hati damai.",
    ],
}


def jawab_intent(intent, bersih, state):
    nama, _, jawaban, _ = intent
    if nama in JAWABAN_SALAM:
        return [putar(JAWABAN_SALAM[nama], state, nama)]
    n = sebut_nama(state)
    if nama == "selamat_waktu":
        waktu = re.search(r"selamat (pagi|siang|sore|malam)", bersih).group(1)
        return [f"Selamat {waktu}{n}! Semoga harimu menyenangkan. Ada yang bisa kubantu?"]
    if nama == "kabar":
        state["menunggu_kabar"] = True
    if nama == "kemampuan":
        return ["Aku bisa banyak hal! Coba ketik, misalnya:"] + \
               [f"   - {c}" for c in CONTOH_CHAT]
    if nama == "lelucon":
        return [putar(LELUCON, state, "lelucon")]
    if nama == "fakta":
        return ["Tahukah kamu? " + putar(FAKTA, state, "fakta")]
    if nama == "motivasi":
        return [putar(MOTIVASI, state, "motivasi")]
    if nama == "tebakan":
        soal, kunci = putar(TEBAKAN, state, "tebakan")
        state["tebakan"], state["salah"] = (soal, kunci), 0
        return [f"Tebak-tebakan: {soal}",
                "(Ketik jawabanmu, atau 'menyerah' kalau mau tahu jawabannya.)"]
    if nama == "ingat":
        return jawab_ingat(state)
    return [putar(jawaban, state, nama).replace("{n}", n)]


def jawab_bingung(low, tokens, state):
    n = sebut_nama(state)
    if "?" in low:
        jenis, daftar = "tanya", [
            "Pertanyaan bagus{n}! Sayangnya aku belum tahu jawabannya. Coba tanya "
            "yang lain, atau ketik 'bantuan' untuk melihat yang bisa kulakukan.",
            "Hmm, itu di luar yang aku tahu. Ketik 'bantuan' untuk melihat apa saja "
            "yang bisa kubantu.",
        ]
    elif "aku" in tokens:
        jenis, daftar = "cerita", [
            "Oh begitu{n}. Ceritakan lebih banyak, aku dengerin.",
            "Hmm, menarik. Terus bagaimana?",
            "Aku paham. Mau cerita lebih panjang?",
        ]
    else:
        jenis, daftar = "umum", [
            "Hmm, aku belum paham maksudmu. Coba pakai kata lain?",
            "Maaf, aku masih belajar. Ketik 'bantuan' untuk melihat yang bisa kulakukan.",
            "Menarik! Coba ceritakan lebih banyak.",
        ]
    return [putar(daftar, state, "bingung_" + jenis).replace("{n}", n)]


def balas(teks, state):
    """Kembalikan jawaban chat sebagai list baris."""
    teks = teks[:500]
    low, tokens = normalisasi(teks)
    bersih = " ".join(tokens)

    if any(re.search(rf"\b{re.escape(k)}\b", bersih) for k in KRISIS):
        return BALASAN_KRISIS

    if state.get("tebakan"):
        minta_baru = ada(["tebak", "tebakan", "teka teki"], bersih)
        if len(tokens) <= 5 and not minta_baru:
            return jawab_tebakan(bersih, state)
        state.pop("tebakan", None)
        state.pop("salah", None)

    if ada(["lupakan semuanya", "reset memori", "hapus memori", "lupakan aku"], bersih):
        state.clear()
        return ["Oke, semua yang kuingat tentang kamu sudah kulupakan."]

    m = POLA_TERJEMAH.match(teks)
    if m:
        return jawab_terjemah(m)

    for fungsi in (jawab_tanggal_waktu, coba_hitung):
        hasil = fungsi(low)
        if hasil:
            return hasil

    hasil = ekstrak_info(low, state)
    if hasil:
        return hasil

    if state.pop("menunggu_kabar", False) and len(tokens) <= 6:
        hasil = tindak_lanjut_kabar(tokens, state)
        if hasil:
            return hasil

    intent = pilih_intent(tokens)
    if intent:
        return jawab_intent(intent, bersih, state)
    return jawab_bingung(low, tokens, state)


PILIHAN_CHAT = [
    "1. Salam pagi", "2. Salam siang", "3. Salam sore", "4. Salam malam",
    "5. Ngobrol biasa / hitung", "6. Balas salam wassalam",
    "7. Balas salam waalaikumsalam", "8. Cetak menu lagi", "0. Kembali",
]
SALAM_PILIHAN = {"1": "selamat pagi", "2": "selamat siang", "3": "selamat sore",
                 "4": "selamat malam", "6": "wassalamualaikum",
                 "7": "waalaikumsalam"}


def mode_chat_pilihan():
    """Ngobrol pilihan banyak: pilih salam dari daftar, atau pilih 5 untuk ngobrol."""
    siapkan_data()
    state = copy.deepcopy(DATA["memori_chat"])
    print("\n== NGOBROL PILIHAN BANYAK ==")
    print("\n".join(PILIHAN_CHAT))
    ngobrol = False
    while True:
        t = input("chat> ").strip()
        if t.lower() in ("menu", "batal", "kembali", "0", "keluar"):
            break
        if t == "8":
            print("\n".join(PILIHAN_CHAT))
            continue
        if t == "5" and not ngobrol:
            ngobrol = True
            bot("Silakan ngobrol. Ketik 'pilihan' untuk kembali ke daftar, "
                "atau 'menu' untuk keluar.")
            continue
        if ngobrol and t.lower() == "pilihan":
            ngobrol = False
            print("\n".join(PILIHAN_CHAT))
            continue
        if not ngobrol and t in SALAM_PILIHAN:
            t = SALAM_PILIHAN[t]
        if not t:
            continue
        for baris in balas(t, state):
            bot(baris)
        ingat = {k: state[k] for k in ("nama", "suka") if state.get(k)}
        if ingat != DATA["memori_chat"]:
            DATA["memori_chat"] = copy.deepcopy(ingat)
            simpan_data()


def mode_chat():
    siapkan_data()
    state = copy.deepcopy(DATA["memori_chat"])
    bot(f"Selamat {periode_hari()}{sebut_nama(state)}! Aku AIX, siap ngobrol. "
        "Coba ketik, misalnya:")
    for contoh in CONTOH_CHAT:
        print(f"   - {contoh}")
    bot("Ketik 'menu' kapan saja untuk kembali ke menu utama.")
    while True:
        teks = input("Kamu: ").strip()
        if not teks:
            continue
        if teks.lower() == "pilihan":
            mode_chat_pilihan()
            continue
        if teks.lower() in ("menu", "kembali", "keluar", "exit"):
            break
        for baris in balas(teks, state):
            if baris.startswith("   "):
                print(baris)
            else:
                bot(baris)
        ingat = {k: state[k] for k in ("nama", "suka") if state.get(k)}
        if ingat != DATA["memori_chat"]:
            DATA["memori_chat"] = copy.deepcopy(ingat)
            simpan_data()


# ------------------------------------------------------ penjelasan materi
MATERI = {}
MATERI_DILIHAT = set()


def _materi(kunci, judul, rumus, *isi):
    MATERI[kunci] = (judul, isi, rumus)


def _cetak_rapi(teks):
    print(textwrap.fill(teks, width=64, initial_indent="   ",
                        subsequent_indent="   "))


def tampil_materi(kunci):
    """Pertama kali dibuka: penjelasan lengkap. Selanjutnya: rumus saja."""
    judul, isi, rumus = MATERI[kunci]
    print()
    if kunci in MATERI_DILIHAT:
        bot(f"Rumus {judul}: {rumus}")
        return
    MATERI_DILIHAT.add(kunci)
    bot(f"=== MATERI: {judul} ===")
    for baris in isi:
        _cetak_rapi(baris)
    _cetak_rapi(f"RUMUS: {rumus}")
    print()


def ulangi_materi():
    MATERI_DILIHAT.clear()
    bot("Oke, penjelasan materi lengkap akan ditampilkan lagi.")


# ---- aritmatika dasar
_materi("tambah", "Penjumlahan", "a + b = jumlah",
        "Penjumlahan adalah menggabungkan dua bilangan atau lebih menjadi satu jumlah.",
        "Urutannya boleh ditukar: a + b = b + a.",
        "Contoh: 12 + 5 = 17, dan 0,5 + 0,25 = 0,75.")
_materi("kurang", "Pengurangan", "a - b = selisih",
        "Pengurangan adalah mencari selisih atau sisa setelah suatu bilangan diambil.",
        "Urutannya tidak boleh ditukar: 7 - 3 = 4, tetapi 3 - 7 = -4.",
        "Contoh: 20 - 8 = 12.")
_materi("kali", "Perkalian", "a x b = hasil kali",
        "Perkalian adalah penjumlahan berulang: 4 x 3 = 4 + 4 + 4 = 12.",
        "Urutan boleh ditukar (4 x 3 = 3 x 4), dan perkalian dengan 0 selalu 0.",
        "Aturan tanda: (+) x (+) = (+), (-) x (-) = (+), (+) x (-) = (-).")
_materi("bagi", "Pembagian", "a : b = hasil bagi (b tidak boleh 0)",
        "Pembagian adalah kebalikan perkalian: membagi suatu bilangan menjadi "
        "bagian-bagian yang sama besar.",
        "Contoh: 12 : 3 = 4, karena 4 x 3 = 12.",
        "Pembagian dengan 0 tidak terdefinisi (tidak boleh dilakukan).",
        "Urutannya tidak boleh ditukar: 12 : 3 tidak sama dengan 3 : 12.")

# ---- perpangkatan
_materi("pangkat", "Perpangkatan", "a^n = a x a x ... x a (sebanyak n kali)",
        "Perpangkatan adalah perkalian berulang dengan bilangan yang sama.",
        "Pada a^n, a disebut bilangan pokok (basis) dan n disebut pangkat (eksponen).",
        "Contoh: 2^4 = 2 x 2 x 2 x 2 = 16.",
        "Bilangan negatif berpangkat genap hasilnya positif, berpangkat ganjil "
        "hasilnya negatif: (-2)^2 = 4 dan (-2)^3 = -8.")
_materi("pangkat_kali", "Perkalian pangkat (basis sama)", "a^m x a^n = a^(m+n)",
        "Jika basisnya sama, perkalian bilangan berpangkat dilakukan dengan "
        "MENJUMLAHKAN pangkatnya.",
        "Contoh: 2^3 x 2^4 = (2x2x2) x (2x2x2x2) = 2^7 = 128.",
        "Syarat: basisnya harus sama. 2^3 x 3^4 tidak bisa digabung dengan cara ini.")
_materi("pangkat_bagi", "Pembagian pangkat (basis sama)", "a^m : a^n = a^(m-n)",
        "Jika basisnya sama, pembagian bilangan berpangkat dilakukan dengan "
        "MENGURANGKAN pangkatnya.",
        "Contoh: 5^6 : 5^2 = 5^(6-2) = 5^4 = 625.",
        "Syarat: basis tidak boleh 0.")
_materi("pangkat_pangkat", "Pangkat dari pangkat", "(a^m)^n = a^(m x n)",
        "Bilangan berpangkat yang dipangkatkan lagi: pangkatnya DIKALIKAN.",
        "Contoh: (2^3)^2 = 2^3 x 2^3 = 2^6 = 64.",
        "Hati-hati: (a^m)^n berbeda dengan a^(m^n).")
_materi("pangkat_kali_basis", "Pangkat dari perkalian", "(a x b)^n = a^n x b^n",
        "Pangkat dari perkalian dibagikan ke masing-masing faktor.",
        "Contoh: (2 x 3)^2 = 6^2 = 36, dan 2^2 x 3^2 = 4 x 9 = 36.",
        "Sifat ini berlaku untuk perkalian, TIDAK untuk penjumlahan: (a + b)^n "
        "tidak sama dengan a^n + b^n.")
_materi("pangkat_bagi_basis", "Pangkat dari pembagian", "(a : b)^n = a^n : b^n",
        "Pangkat dari pembagian dibagikan ke pembilang dan penyebut.",
        "Contoh: (6 : 3)^2 = 2^2 = 4, dan 6^2 : 3^2 = 36 : 9 = 4.",
        "Syarat: pembagi b tidak boleh 0.")
_materi("pangkat_nol_neg", "Pangkat nol dan pangkat negatif",
        "a^0 = 1  dan  a^(-n) = 1 / a^n  (a tidak boleh 0)",
        "Setiap bilangan (selain 0) berpangkat 0 hasilnya 1. Contoh: 7^0 = 1.",
        "Pangkat negatif artinya kebalikan: 2^(-3) = 1 / 2^3 = 1/8 = 0,125.",
        "Asal-usulnya: 2^3 : 2^3 = 2^(3-3) = 2^0, dan hasil bagi bilangan yang "
        "sama pasti 1.")
_materi("pangkat_pecahan", "Pangkat pecahan", "a^(m/n) = akar pangkat n dari (a^m)",
        "Pangkat pecahan adalah akar yang ditulis sebagai pangkat.",
        "a^(1/2) = akar kuadrat dari a, dan a^(1/3) = akar pangkat tiga dari a.",
        "Bentuk umum: a^(m/n) = akar pangkat n dari (a^m). Contoh: 8^(2/3) = "
        "akar pangkat tiga dari 64 = 4.")

# ---- akar
_materi("akar_kuadrat", "Akar kuadrat", "akar(x) = y artinya y^2 = x",
        "Akar kuadrat adalah kebalikan dari kuadrat (pangkat dua).",
        "Akar kuadrat dari x adalah bilangan yang jika dikuadratkan hasilnya x. "
        "Contoh: akar 49 = 7, karena 7^2 = 49.",
        "Bilangan negatif tidak punya akar kuadrat real. Akar 2 = 1,4142... "
        "(desimalnya tidak berakhir, disebut bilangan irasional).")
_materi("akar_kubik", "Akar pangkat tiga", "akar3(x) = y artinya y^3 = x",
        "Akar pangkat tiga adalah kebalikan dari pangkat tiga.",
        "Contoh: akar pangkat 3 dari 27 = 3, karena 3^3 = 27.",
        "Berbeda dengan akar kuadrat, bilangan negatif punya akar pangkat tiga: "
        "akar pangkat 3 dari -8 = -2.")
_materi("akar_n", "Akar pangkat n", "akar_n(x) = y artinya y^n = x",
        "Akar pangkat n adalah kebalikan dari pangkat n.",
        "Contoh: akar pangkat 4 dari 16 = 2, karena 2^4 = 16.",
        "Jika n genap, x tidak boleh negatif. Jika n ganjil, x boleh negatif.")

# ---- luas bangun datar
_materi("luas_persegi", "Luas persegi", "L = sisi x sisi",
        "Persegi adalah bangun datar dengan empat sisi sama panjang dan empat "
        "sudut siku-siku.",
        "Luas menyatakan banyaknya persegi satuan yang menutupi bangun tersebut.",
        "Contoh: sisi 5 -> L = 5 x 5 = 25.")
_materi("luas_pp", "Luas persegi panjang", "L = panjang x lebar",
        "Persegi panjang punya dua pasang sisi sejajar yang sama panjang dan "
        "empat sudut siku-siku.",
        "Contoh: panjang 10 dan lebar 4 -> L = 10 x 4 = 40.",
        "Rumus ini juga dipakai untuk menghitung luas lantai, ruangan, atau tanah "
        "yang berbentuk persegi panjang.")
_materi("luas_segitiga", "Luas segitiga", "L = 1/2 x alas x tinggi",
        "Segitiga adalah bangun datar bersisi tiga. Luasnya setengah dari persegi "
        "panjang yang alas dan tingginya sama.",
        "Tinggi diukur tegak lurus terhadap alas (bukan panjang sisi miringnya).",
        "Contoh: alas 6 dan tinggi 5 -> L = 1/2 x 6 x 5 = 15.")
_materi("luas_jajargenjang", "Luas jajargenjang", "L = alas x tinggi",
        "Jajargenjang punya dua pasang sisi sejajar, tetapi sudutnya tidak harus "
        "siku-siku.",
        "Tinggi diukur tegak lurus alas. Kalau bagian miringnya dipindahkan, bangun "
        "ini berubah menjadi persegi panjang.",
        "Contoh: alas 8 dan tinggi 3 -> L = 8 x 3 = 24.")
_materi("luas_trapesium", "Luas trapesium", "L = 1/2 x (a + b) x tinggi",
        "Trapesium punya sepasang sisi sejajar (sebut a dan b) dan tinggi yang "
        "tegak lurus terhadap keduanya.",
        "Luasnya sama dengan rata-rata kedua sisi sejajar dikali tinggi.",
        "Contoh: a = 3, b = 5, tinggi 4 -> L = 1/2 x (3 + 5) x 4 = 16.")
_materi("luas_belah_ketupat", "Luas belah ketupat", "L = 1/2 x diagonal1 x diagonal2",
        "Belah ketupat punya empat sisi sama panjang. Kedua diagonalnya "
        "berpotongan tegak lurus.",
        "Contoh: diagonal 6 dan 8 -> L = 1/2 x 6 x 8 = 24.")
_materi("luas_layang", "Luas layang-layang", "L = 1/2 x diagonal1 x diagonal2",
        "Layang-layang punya dua pasang sisi berdekatan yang sama panjang. "
        "Kedua diagonalnya berpotongan tegak lurus.",
        "Contoh: diagonal 10 dan 7 -> L = 1/2 x 10 x 7 = 35.")

# ---- triple Pythagoras
_materi("triple_cek", "Triple Pythagoras", "a^2 + b^2 = c^2  (c = bilangan terbesar)",
        "Triple Pythagoras adalah tiga bilangan bulat positif (a, b, c) yang "
        "memenuhi a^2 + b^2 = c^2.",
        "Contoh terkenal: (3, 4, 5) karena 9 + 16 = 25. Ada juga (5, 12, 13) dan "
        "(8, 15, 17).",
        "Kelipatan sebuah triple juga triple: (6, 8, 10) = 2 x (3, 4, 5). Triple "
        "yang tidak bisa disederhanakan disebut triple primitif.")
_materi("triple_cari", "Teorema Pythagoras", "c^2 = a^2 + b^2",
        "Pada segitiga siku-siku, kuadrat sisi miring (hipotenusa) sama dengan "
        "jumlah kuadrat kedua sisi tegaknya.",
        "Mencari sisi miring: c = akar(a^2 + b^2). Mencari sisi tegak: "
        "a = akar(c^2 - b^2).",
        "Contoh: sisi tegak 3 dan 4 -> sisi miring = akar(9 + 16) = 5.")
_materi("triple_daftar", "Membuat triple Pythagoras (rumus Euclid)",
        "a = m^2 - n^2,  b = 2mn,  c = m^2 + n^2",
        "Semua triple primitif bisa dibuat dari dua bilangan bulat m > n > 0 yang "
        "saling prima dan tidak keduanya ganjil.",
        "Contoh: m = 2, n = 1 -> (3, 4, 5). m = 3, n = 2 -> (5, 12, 13).",
        "Kalikan triple primitif dengan bilangan bulat untuk mendapat triple lainnya.")

# ---- lingkaran
_materi("lingkaran", "Lingkaran", "K = 2 x pi x r,   L = pi x r^2",
        "Lingkaran adalah kumpulan titik yang jaraknya sama dari titik pusat.",
        "Jari-jari (r) = jarak pusat ke tepi. Diameter (d) = garis lurus lewat "
        "pusat dari tepi ke tepi, jadi d = 2 x r.",
        "Keliling (K) = panjang garis tepi. Luas (L) = daerah di dalam lingkaran.",
        "pi (3,14159...) adalah perbandingan keliling terhadap diameter. Di "
        "sekolah sering dipakai 22/7 (kalau r kelipatan 7) atau 3,14.")
_materi("lingkaran_r", "Lingkaran dari jari-jari", "d = 2r,  K = 2 x pi x r,  L = pi x r^2",
        "Kalau jari-jari diketahui, semua unsur lain langsung bisa dihitung.")
_materi("lingkaran_d", "Lingkaran dari diameter", "r = d / 2",
        "Jari-jari adalah setengah dari diameter, lalu hitung keliling dan luasnya.")
_materi("lingkaran_k", "Lingkaran dari keliling", "r = K / (2 x pi)",
        "Dari K = 2 x pi x r, jari-jari didapat dengan membagi keliling dengan "
        "2 x pi.")
_materi("lingkaran_l", "Lingkaran dari luas", "r = akar(L / pi)",
        "Dari L = pi x r^2, jari-jari didapat dengan mengakarkan hasil bagi luas "
        "dengan pi.")

# ---- perbandingan segitiga siku-siku
_materi("siku", "Perbandingan trigonometri segitiga siku-siku",
        "sin A = depan/miring,  cos A = samping/miring,  tan A = depan/samping",
        "Pada segitiga siku-siku dengan sudut A: sisi depan = sisi di hadapan "
        "sudut A, sisi samping = sisi yang menempel sudut A (selain sisi miring), "
        "sisi miring = sisi terpanjang.",
        "Cara mengingat: sin = de-mi, cos = sa-mi, tan = de-sa.",
        "Kebalikannya: cosec A = 1/sin A, sec A = 1/cos A, cot A = 1/tan A.",
        "Contoh: sisi 3, 4, 5 dengan depan = 3 -> sin A = 3/5 = 0,6.")
_materi("siku_dua_sisi", "Dari dua sisi yang diketahui",
        "miring = akar(depan^2 + samping^2)",
        "Kalau dua sisi diketahui, sisi ketiga dicari dengan teorema Pythagoras, "
        "lalu semua perbandingan dihitung dari ketiga sisinya.",
        "Besar sudut A didapat dari tan A = depan/samping.")
_materi("siku_sudut", "Dari sudut dan satu sisi",
        "depan = miring x sin A,  samping = miring x cos A",
        "Kalau sudut dan satu sisi diketahui, sisi lain dicari dengan perbandingan "
        "yang sesuai.",
        "Contoh: A = 30 derajat dan miring = 10 -> depan = 10 x 0,5 = 5.")

# ---- kubus
_materi("kubus", "Kubus", "V = s^3,  LP = 6 x s^2,  jumlah rusuk = 12 x s",
        "Kubus adalah bangun ruang dengan 6 sisi berbentuk persegi yang sama "
        "besar, 12 rusuk sama panjang, dan 8 titik sudut.",
        "Volume = ruang yang ditempati kubus = sisi x sisi x sisi.",
        "Luas permukaan = luas 6 persegi. Diagonal bidang = s x akar(2), "
        "diagonal ruang = s x akar(3).",
        "Contoh: sisi 4 -> volume = 64 dan luas permukaan = 6 x 16 = 96.")
_materi("kubus_volume", "Sisi kubus dari volume", "s = akar pangkat tiga dari V",
        "Kalau volume diketahui, panjang sisi didapat dengan akar pangkat tiga.",
        "Contoh: V = 27 -> s = 3, karena 3^3 = 27.")


# ------------------------------------------------- fitur 2: matematika dasar
# ---- perpangkatan
def hitung_pangkat(a, n):
    """a^n (a dan n boleh int/Decimal). Melempar ValueError(pesan) kalau tidak
    bisa dihitung."""
    a, n = Decimal(a), Decimal(n)
    if abs(n) > 10000:
        raise ValueError("Pangkatnya terlalu besar. Pakai pangkat antara "
                         "-10000 dan 10000.")
    if a == 0 and n <= 0:
        raise ValueError("0 pangkat 0 atau pangkat negatif tidak terdefinisi.")
    if a < 0 and n != n.to_integral_value():
        raise ValueError("Bilangan negatif dengan pangkat pecahan tidak "
                         "menghasilkan bilangan real.")
    return a ** n


def coba(hitung):
    """Jalankan hitung(). Kalau gagal, tampilkan pesan dan kembalikan None."""
    try:
        return hitung()
    except ValueError as err:
        bot(str(err))
    except ArithmeticError:
        bot("Hasilnya terlalu besar atau tidak bisa dihitung.")
    return None


def kurung(x):
    """Format angka; beri kurung kalau negatif, supaya (-2)^3 tidak terbaca
    sebagai -(2^3)."""
    x = Decimal(x)
    return f"({fa(x)})" if x < 0 else fa(x)


def pangkat_biasa():
    tampil_materi("pangkat")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    n = baca_angka("AIX: masukkan pangkat (n): ")
    if n is None:
        return
    hasil = coba(lambda: hitung_pangkat(a, n))
    if hasil is None:
        return
    bulat = n == n.to_integral_value()
    uraian = ""
    if bulat and 2 <= n <= 5:
        uraian = " x ".join([kurung(a)] * int(n)) + " = "
    elif bulat and n < 0:
        uraian = f"1 / {kurung(a)}^{fa(-n)} = "
    bot(f"{kurung(a)}^{kurung(n)} = {uraian}{fa(hasil)}")


def perkalian_pangkat():
    tampil_materi("pangkat_kali")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    m = baca_bulat("AIX: masukkan pangkat pertama (m): ")
    if m is None:
        return
    n = baca_bulat("AIX: masukkan pangkat kedua (n): ")
    if n is None:
        return
    nilai = coba(lambda: (hitung_pangkat(a, m), hitung_pangkat(a, n),
                          hitung_pangkat(a, m + n)))
    if nilai is None:
        return
    pm, pn, hasil = nilai
    p = kurung(a)
    bot(f"{p}^{kurung(m)} x {p}^{kurung(n)} = {p}^({kurung(m)} + {kurung(n)}) "
        f"= {p}^{kurung(m + n)} = {fa(hasil)}")
    bot(f"Cek langsung: {kurung(pm)} x {kurung(pn)} = {fa(pm * pn)}")


def pembagian_pangkat():
    tampil_materi("pangkat_bagi")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    if a == 0:
        bot("Bilangan pokok tidak boleh 0 pada pembagian pangkat.")
        return
    m = baca_bulat("AIX: masukkan pangkat pertama (m): ")
    if m is None:
        return
    n = baca_bulat("AIX: masukkan pangkat kedua (n): ")
    if n is None:
        return
    nilai = coba(lambda: (hitung_pangkat(a, m), hitung_pangkat(a, n),
                          hitung_pangkat(a, m - n)))
    if nilai is None:
        return
    pm, pn, hasil = nilai
    p = kurung(a)
    bot(f"{p}^{kurung(m)} : {p}^{kurung(n)} = {p}^({kurung(m)} - {kurung(n)}) "
        f"= {p}^{kurung(m - n)} = {fa(hasil)}")
    bot(f"Cek langsung: {kurung(pm)} : {kurung(pn)} = {fa(pm / pn)}")


def pangkat_dari_pangkat():
    tampil_materi("pangkat_pangkat")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    m = baca_bulat("AIX: masukkan pangkat dalam (m): ")
    if m is None:
        return
    n = baca_bulat("AIX: masukkan pangkat luar (n): ")
    if n is None:
        return

    def hitung():
        dalam = hitung_pangkat(a, m)
        return dalam, hitung_pangkat(dalam, n), hitung_pangkat(a, m * n)

    nilai = coba(hitung)
    if nilai is None:
        return
    dalam, langsung, hasil = nilai
    p = kurung(a)
    bot(f"({p}^{kurung(m)})^{kurung(n)} = {p}^({kurung(m)} x {kurung(n)}) "
        f"= {p}^{kurung(m * n)} = {fa(hasil)}")
    bot(f"Cek langsung: ({fa(dalam)})^{kurung(n)} = {fa(langsung)}")


def pangkat_dari_perkalian():
    tampil_materi("pangkat_kali_basis")
    a = baca_angka("AIX: masukkan bilangan pertama (a): ")
    if a is None:
        return
    b = baca_angka("AIX: masukkan bilangan kedua (b): ")
    if b is None:
        return
    n = baca_bulat("AIX: masukkan pangkat (n): ")
    if n is None:
        return
    nilai = coba(lambda: (hitung_pangkat(a, n), hitung_pangkat(b, n),
                          hitung_pangkat(a * b, n)))
    if nilai is None:
        return
    pa, pb, langsung = nilai
    bot(f"({fa(a)} x {fa(b)})^{kurung(n)} = {kurung(a)}^{kurung(n)} x "
        f"{kurung(b)}^{kurung(n)} = {kurung(pa)} x {kurung(pb)} = {fa(pa * pb)}")
    bot(f"Cek langsung: ({fa(a * b)})^{kurung(n)} = {fa(langsung)}")


def pangkat_dari_pembagian():
    tampil_materi("pangkat_bagi_basis")
    a = baca_angka("AIX: masukkan bilangan pertama (a): ")
    if a is None:
        return
    b = baca_angka("AIX: masukkan bilangan kedua (b): ")
    if b is None:
        return
    if b == 0:
        bot("Pembaginya (b) tidak boleh 0.")
        return
    n = baca_bulat("AIX: masukkan pangkat (n): ")
    if n is None:
        return
    nilai = coba(lambda: (hitung_pangkat(a, n), hitung_pangkat(b, n),
                          hitung_pangkat(a / b, n)))
    if nilai is None:
        return
    pa, pb, langsung = nilai
    hasil = coba(lambda: pa / pb)
    if hasil is None:
        return
    bot(f"({fa(a)} : {fa(b)})^{kurung(n)} = {kurung(a)}^{kurung(n)} : "
        f"{kurung(b)}^{kurung(n)} = {kurung(pa)} : {kurung(pb)} = {fa(hasil)}")
    bot(f"Cek langsung: ({fa(a / b)})^{kurung(n)} = {fa(langsung)}")


def pangkat_nol_negatif():
    tampil_materi("pangkat_nol_neg")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    if a == 0:
        bot("Bilangan pokok tidak boleh 0.")
        return
    n = baca_bulat_positif("AIX: masukkan n (bilangan bulat positif): ")
    if n is None:
        return
    nilai = coba(lambda: (hitung_pangkat(a, n), hitung_pangkat(a, -n)))
    if nilai is None:
        return
    pn, hasil = nilai
    p = kurung(a)
    bot(f"{p}^0 = 1")
    bot(f"{p}^(-{n}) = 1 / {p}^{n} = 1 / {kurung(pn)} = {fa(hasil)}")


def pangkat_pecahan():
    tampil_materi("pangkat_pecahan")
    a = baca_angka("AIX: masukkan bilangan pokok (a): ")
    if a is None:
        return
    m = baca_bulat("AIX: masukkan pembilang pangkat (m): ")
    if m is None:
        return
    while True:
        n = baca_bulat_positif("AIX: masukkan penyebut pangkat / pangkat akar "
                               "(n, 2 sampai 10000): ")
        if n is None:
            return
        if 2 <= n <= 10000:
            break
        bot("n harus antara 2 dan 10000.")
    if a < 0 and n % 2 == 0:
        bot("Bilangan negatif dengan penyebut pangkat genap tidak menghasilkan "
            "bilangan real.")
        return

    def hitung():
        dalam = hitung_pangkat(a, m)
        hasil = hitung_akar(dalam, Decimal(n))
        akar_a = hitung_akar(a, Decimal(n))
        return dalam, hasil, akar_a, hitung_pangkat(akar_a, m)

    nilai = coba(hitung)
    if nilai is None:
        return
    dalam, hasil, akar_a, cek = nilai
    p = kurung(a)
    bot(f"{p}^({m}/{n}) = akar pangkat {n} dari ({p}^{kurung(m)}) "
        f"= akar pangkat {n} dari {fa(dalam)} = {fa(hasil)}")
    bot(f"Cek: (akar pangkat {n} dari {fa(a)})^{kurung(m)} = "
        f"({fa(akar_a)})^{kurung(m)} = {fa(cek)}")


def rangkuman_pangkat():
    bot("Sifat-sifat perpangkatan (a dan b tidak boleh 0 bila ada pembagian):")
    print("   1. a^1 = a  dan  a^0 = 1")
    print("   2. a^m x a^n = a^(m+n)         (perkalian, basis sama)")
    print("   3. a^m : a^n = a^(m-n)         (pembagian, basis sama)")
    print("   4. (a^m)^n = a^(m x n)         (pangkat dari pangkat)")
    print("   5. (a x b)^n = a^n x b^n       (pangkat dari perkalian)")
    print("   6. (a : b)^n = a^n : b^n       (pangkat dari pembagian)")
    print("   7. a^(-n) = 1 / a^n            (pangkat negatif)")
    print("   8. a^(m/n) = akar pangkat n dari (a^m)   (pangkat pecahan)")


def perpangkatan():
    submenu("Perpangkatan", [
        ("Hitung a^n", pangkat_biasa),
        ("Perkalian pangkat: a^m x a^n", perkalian_pangkat),
        ("Pembagian pangkat: a^m : a^n", pembagian_pangkat),
        ("Pangkat dari pangkat: (a^m)^n", pangkat_dari_pangkat),
        ("Pangkat dari perkalian: (a x b)^n", pangkat_dari_perkalian),
        ("Pangkat dari pembagian: (a : b)^n", pangkat_dari_pembagian),
        ("Pangkat nol dan pangkat negatif: a^0, a^(-n)", pangkat_nol_negatif),
        ("Pangkat pecahan: a^(m/n)", pangkat_pecahan),
        ("Rangkuman sifat-sifat perpangkatan", rangkuman_pangkat),
    ])


# ---- akar
def hitung_akar(x, n):
    """Akar pangkat n dari x (Decimal). None jika hasilnya bukan bilangan real."""
    if x == 0:
        return Decimal(0)
    if n == 2:
        return x.sqrt() if x > 0 else None
    if x < 0:
        if int(n) % 2 == 0:
            return None
        return -((-x) ** (Decimal(1) / n))
    return x ** (Decimal(1) / n)


def akar():
    pilih = tampil_menu("Akar", ["Akar kuadrat", "Akar pangkat tiga",
                                 "Akar pangkat n"])
    if pilih == "0":
        return
    jenis = ambil_nomor(pilih, 3)
    if jenis is None:
        return
    tampil_materi(("akar_kuadrat", "akar_kubik", "akar_n")[jenis - 1])
    x = baca_angka("AIX: masukkan bilangan di dalam akar: ")
    if x is None:
        return
    if jenis == 1:
        n, nama = 2, "Akar kuadrat"
    elif jenis == 2:
        n, nama = 3, "Akar pangkat tiga"
    else:
        while True:
            n = baca_angka("AIX: masukkan pangkat akar (bilangan bulat 2-10000): ")
            if n is None:
                return
            if n == n.to_integral_value() and 2 <= n <= 10000:
                n = int(n)
                break
            bot("Pangkat akar harus bilangan bulat antara 2 dan 10000.")
        nama = f"Akar pangkat {n}"
    hasil = hitung_akar(x, Decimal(n))
    if hasil is None:
        bot("Akar berpangkat genap dari bilangan negatif tidak menghasilkan "
            "bilangan real.")
        return
    sempurna = hasil.to_integral_value() ** n == x
    bot(f"{nama} dari {fa(x)} = {fa(hasil)}"
        + (" (akar sempurna)" if sempurna else ""))


@merekam
def mode_matematika():
    operasi = {
        "1": ("tambah", "+", lambda a, b: a + b),
        "2": ("kurang", "-", lambda a, b: a - b),
        "3": ("kali", "*", lambda a, b: a * b),
        "4": ("bagi", "/", lambda a, b: a / b),
    }
    nama_ke_nomor = {v[0]: k for k, v in operasi.items()}
    nama_ke_nomor.update({"pangkat": "5", "perpangkatan": "5", "akar": "6"})

    bot("Tips: tiap pilihan menampilkan penjelasan materi saat pertama kali dibuka. "
        "Ketik 'materi' di menu mana pun untuk menampilkannya lagi.")
    while True:
        print()
        bot("=== Matematika Dasar ===")
        print("1. Tambah")
        print("2. Kurang")
        print("3. Kali")
        print("4. Bagi")
        print("5. Perpangkatan (hitung, perkalian, pembagian, dll.)")
        print("6. Akar (kuadrat, pangkat tiga, pangkat n)")
        print("0. Kembali ke menu utama")
        pilih = input("AIX: pilih operasi (angka atau nama): ").strip().lower()
        if pilih == "materi":
            ulangi_materi()
            continue
        pilih = nama_ke_nomor.get(pilih, pilih)

        if pilih == "0":
            return
        if pilih == "5":
            perpangkatan()
            continue
        if pilih == "6":
            akar()
            continue
        if pilih not in operasi:
            bot("Pilihan tidak valid.")
            continue

        tampil_materi(operasi[pilih][0])
        a = baca_angka("AIX: masukkan angka pertama: ")
        if a is None:
            continue
        b = baca_angka("AIX: masukkan angka kedua: ")
        if b is None:
            continue

        nama, simbol, fungsi = operasi[pilih]
        if pilih == "4" and b == 0:
            bot("Tidak bisa membagi dengan nol.")
            continue

        bot(f"Hasil dari {format_angka(a)} {simbol} {format_angka(b)} "
            f"adalah {format_angka(fungsi(a, b))}")


# ------------------------------------------------- fitur 3: matematika lanjut
PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459")


# ---- 1. luas bangun datar
def luas_bangun_datar():
    diagonal = ["diagonal pertama", "diagonal kedua"]
    bangun = [
        ("Persegi", ["panjang sisi"], lambda s: s * s, "sisi x sisi"),
        ("Persegi panjang", ["panjang", "lebar"], lambda p, l: p * l,
         "panjang x lebar"),
        ("Segitiga", ["alas", "tinggi"], lambda a, t: a * t / 2,
         "1/2 x alas x tinggi"),
        ("Jajargenjang", ["alas", "tinggi"], lambda a, t: a * t,
         "alas x tinggi"),
        ("Trapesium", ["sisi sejajar pertama", "sisi sejajar kedua", "tinggi"],
         lambda a, b, t: (a + b) * t / 2, "1/2 x (a + b) x tinggi"),
        ("Belah ketupat", diagonal, lambda d1, d2: d1 * d2 / 2,
         "1/2 x diagonal1 x diagonal2"),
        ("Layang-layang", diagonal, lambda d1, d2: d1 * d2 / 2,
         "1/2 x diagonal1 x diagonal2"),
    ]
    while True:
        pilih = tampil_menu("Luas Bangun Datar", [b[0] for b in bangun])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(bangun))
        if n is None:
            continue
        nama, label, fungsi, rumus = bangun[n - 1]
        tampil_materi(("luas_persegi", "luas_pp", "luas_segitiga", "luas_jajargenjang",
                       "luas_trapesium", "luas_belah_ketupat", "luas_layang")[n - 1])
        nilai = baca_banyak(*[f"AIX: masukkan {l}: " for l in label])
        if nilai is None:
            continue
        bot(f"Luas {nama.lower()} = {rumus} = {fa(fungsi(*nilai))} satuan luas")


# ---- 2. triple Pythagoras
def cek_triple():
    tampil_materi("triple_cek")
    sisi = []
    for urutan in ("pertama", "kedua", "ketiga"):
        n = baca_bulat_positif(f"AIX: masukkan sisi {urutan} (bilangan bulat): ")
        if n is None:
            return
        sisi.append(n)
    a, b, c = sorted(sisi)
    if a * a + b * b == c * c:
        g = math.gcd(math.gcd(a, b), c)
        if g == 1:
            jenis = "triple primitif"
        else:
            jenis = f"kelipatan {g} dari ({a // g}, {b // g}, {c // g})"
        bot(f"{a}^2 + {b}^2 = {a * a} + {b * b} = {c * c} = {c}^2")
        bot(f"({a}, {b}, {c}) ADALAH triple Pythagoras ({jenis}).")
    else:
        bot(f"{a}^2 + {b}^2 = {a * a + b * b}, sedangkan {c}^2 = {c * c}.")
        bot(f"({a}, {b}, {c}) BUKAN triple Pythagoras.")


def cari_sisi_ketiga():
    tampil_materi("triple_cari")
    print("1. Cari sisi miring (diketahui dua sisi tegak)")
    print("2. Cari sisi tegak (diketahui sisi miring dan satu sisi tegak)")
    n = ambil_nomor(input("AIX: pilih (1/2): ").strip(), 2)
    if n is None:
        return
    if n == 1:
        v = baca_banyak("AIX: sisi tegak pertama: ", "AIX: sisi tegak kedua: ")
        if v is None:
            return
        a, b = v
        c = (a * a + b * b).sqrt()
        bot(f"Sisi miring = akar({fa(a)}^2 + {fa(b)}^2) = "
            f"akar({fa(a * a + b * b)}) = {fa(c)}")
    else:
        v = baca_banyak("AIX: panjang sisi miring: ",
                        "AIX: panjang sisi tegak yang diketahui: ")
        if v is None:
            return
        c, a = v
        if a >= c:
            bot("Sisi miring harus lebih panjang dari sisi tegak.")
            return
        b = (c * c - a * a).sqrt()
        bot(f"Sisi tegak lainnya = akar({fa(c)}^2 - {fa(a)}^2) = "
            f"akar({fa(c * c - a * a)}) = {fa(b)}")
    if all(x == x.to_integral_value() for x in (a, b, c)):
        urut = tuple(sorted(int(x) for x in (a, b, c)))
        bot(f"Ketiga sisinya bilangan bulat, jadi {urut} adalah triple Pythagoras.")


def daftar_triple():
    tampil_materi("triple_daftar")
    batas = baca_bulat_positif("AIX: tampilkan triple dengan sisi miring sampai "
                               "(maksimal 200): ")
    if batas is None:
        return
    if batas > 200:
        bot("Maksimal 200 ya, supaya daftarnya tidak kepanjangan.")
        return
    kuadrat = {i * i: i for i in range(1, batas + 1)}
    hasil = []
    for a in range(1, batas + 1):
        for b in range(a + 1, batas + 1):
            c2 = a * a + b * b
            if c2 > batas * batas:
                break
            if c2 in kuadrat:
                hasil.append((a, b, kuadrat[c2]))
    if not hasil:
        bot(f"Tidak ada triple Pythagoras dengan sisi miring sampai {batas}.")
        return
    bot(f"Ada {len(hasil)} triple Pythagoras dengan sisi miring sampai {batas}:")
    teks = []
    for a, b, c in hasil:
        primitif = math.gcd(math.gcd(a, b), c) == 1
        teks.append(f"({a}, {b}, {c})" + ("*" if primitif else " "))
    for i in range(0, len(teks), 3):
        print("   " + "   ".join(t.ljust(17) for t in teks[i:i + 3]))
    bot("Tanda * = triple primitif (tidak bisa disederhanakan).")


def triple_pythagoras():
    menu = [cek_triple, cari_sisi_ketiga, daftar_triple]
    while True:
        pilih = tampil_menu("Triple Pythagoras", [
            "Cek apakah tiga bilangan adalah triple Pythagoras",
            "Cari sisi ketiga segitiga siku-siku",
            "Daftar triple Pythagoras",
        ])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(menu))
        if n is not None:
            menu[n - 1]()


# ---- 3. lingkaran
def pilih_pi():
    print("AIX: pilih nilai pi yang dipakai:")
    print("1. 3,14159... (paling akurat)")
    print("2. 3,14")
    print("3. 22/7")
    pilih = input("AIX: pilih 1/2/3 (kosong = 1): ").strip()
    if pilih == "2":
        return Decimal("3.14"), "3,14"
    if pilih == "3":
        return Decimal(22) / Decimal(7), "22/7"
    return PI, "3,14159..."


def lingkaran():
    tampil_materi("lingkaran")
    pi, label_pi = pilih_pi()
    sumber = ["jari-jari", "diameter", "keliling", "luas"]
    while True:
        pilih = tampil_menu("Lingkaran", [f"Diketahui {s}" for s in sumber])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(sumber))
        if n is None:
            continue
        tampil_materi(("lingkaran_r", "lingkaran_d", "lingkaran_k", "lingkaran_l")[n - 1])
        nilai = baca_positif(f"AIX: masukkan {sumber[n - 1]}: ")
        if nilai is None:
            continue
        if n == 1:
            r = nilai
        elif n == 2:
            r = nilai / 2
        elif n == 3:
            r = nilai / (2 * pi)
        else:
            r = (nilai / pi).sqrt()
        bot(f"Jari-jari (r) = {fa(r)}")
        bot(f"Diameter (d)  = 2 x r = {fa(2 * r)}")
        bot(f"Keliling      = 2 x pi x r = {fa(2 * pi * r)}")
        bot(f"Luas          = pi x r x r = {fa(pi * r * r)}")
        bot(f"(memakai pi = {label_pi})")


# ---- 4. perbandingan segitiga siku-siku
def tampilkan_perbandingan(depan, samping, miring):
    sudut = Decimal(str(math.degrees(math.atan2(float(depan), float(samping)))))
    bot(f"Sisi depan = {fa(depan)}, sisi samping = {fa(samping)}, "
        f"sisi miring = {fa(miring)}")
    bot(f"Sudut A = {fa(sudut)} derajat, sudut lainnya = {fa(90 - sudut)} derajat")
    rasio = [
        ("sin A", "depan/miring", depan, miring),
        ("cos A", "samping/miring", samping, miring),
        ("tan A", "depan/samping", depan, samping),
        ("cosec A", "miring/depan", miring, depan),
        ("sec A", "miring/samping", miring, samping),
        ("cot A", "samping/depan", samping, depan),
    ]
    for nama, rumus, atas, bawah in rasio:
        bot(f"{nama:<8}= {rumus:<15}= {fa(atas)}/{fa(bawah)} = {fa(atas / bawah)}")


def siku_dari_dua_sisi():
    tampil_materi("siku_dua_sisi")
    print("1. Depan dan samping")
    print("2. Depan dan miring")
    print("3. Samping dan miring")
    n = ambil_nomor(input("AIX: sisi mana yang diketahui? ").strip(), 3)
    if n is None:
        return
    nama1, nama2 = [("depan", "samping"), ("depan", "miring"),
                    ("samping", "miring")][n - 1]
    v = baca_banyak(f"AIX: panjang sisi {nama1}: ", f"AIX: panjang sisi {nama2}: ")
    if v is None:
        return
    if n == 1:
        depan, samping = v
        miring = (depan * depan + samping * samping).sqrt()
    else:
        tegak, miring = v
        if tegak >= miring:
            bot("Sisi miring harus lebih panjang dari sisi lainnya.")
            return
        lain = (miring * miring - tegak * tegak).sqrt()
        depan, samping = (tegak, lain) if n == 2 else (lain, tegak)
    tampilkan_perbandingan(depan, samping, miring)


def siku_dari_sudut():
    tampil_materi("siku_sudut")
    while True:
        sudut = baca_positif("AIX: masukkan besar sudut A (derajat, antara 0 dan 90): ")
        if sudut is None:
            return
        if sudut < 90:
            break
        bot("Sudut harus di antara 0 dan 90 derajat.")
    print("1. Sisi depan")
    print("2. Sisi samping")
    print("3. Sisi miring")
    n = ambil_nomor(input("AIX: sisi mana yang diketahui? ").strip(), 3)
    if n is None:
        return
    nama = ("depan", "samping", "miring")[n - 1]
    x = baca_positif(f"AIX: panjang sisi {nama}: ")
    if x is None:
        return
    rad = math.radians(float(sudut))
    sin_a, cos_a = Decimal(str(math.sin(rad))), Decimal(str(math.cos(rad)))
    if n == 1:
        depan, miring = x, x / sin_a
        samping = miring * cos_a
    elif n == 2:
        samping, miring = x, x / cos_a
        depan = miring * sin_a
    else:
        miring = x
        depan, samping = x * sin_a, x * cos_a
    tampilkan_perbandingan(depan, samping, miring)


def perbandingan_siku_siku():
    tampil_materi("siku")
    while True:
        pilih = tampil_menu("Perbandingan Segitiga Siku-siku",
                            ["Diketahui dua sisi", "Diketahui sudut dan satu sisi"])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, 2)
        if n == 1:
            siku_dari_dua_sisi()
        elif n == 2:
            siku_dari_sudut()


# ---- 5. volume kubus
def volume_kubus():
    tampil_materi("kubus")
    pilih = tampil_menu("Kubus", ["Diketahui panjang sisi (rusuk)", "Diketahui volume"])
    if pilih == "0":
        return
    n = ambil_nomor(pilih, 2)
    if n is None:
        return
    if n == 1:
        s = baca_positif("AIX: masukkan panjang sisi: ")
        if s is None:
            return
    else:
        tampil_materi("kubus_volume")
        v = baca_positif("AIX: masukkan volume: ")
        if v is None:
            return
        s = hitung_akar(v, Decimal(3))
        bot(f"Panjang sisi = akar pangkat tiga dari {fa(v)} = {fa(s)}")
    bot(f"Volume            = sisi^3 = {fa(s)}^3 = {fa(s ** 3)}")
    bot(f"Luas permukaan    = 6 x sisi^2 = {fa(6 * s * s)}")
    bot(f"Jumlah rusuk      = 12 x sisi = {fa(12 * s)}")
    bot(f"Diagonal bidang   = sisi x akar(2) = {fa(s * Decimal(2).sqrt())}")
    bot(f"Diagonal ruang    = sisi x akar(3) = {fa(s * Decimal(3).sqrt())}")


@merekam
def mode_matematika_lanjut():
    bot("Tips: tiap pilihan menampilkan penjelasan materi saat pertama kali dibuka. "
        "Ketik 'materi' di menu mana pun untuk menampilkannya lagi.")
    submenu("Matematika Lanjut", [
        ("Bangun datar (luas, keliling, lingkaran)", bangun_datar_menu),
        ("Bangun ruang (kubus, balok, tabung, kerucut, bola, limas, prisma)",
         bangun_ruang_menu),
        ("Triple Pythagoras", triple_pythagoras),
        ("Perbandingan segitiga siku-siku (sin, cos, tan)", perbandingan_siku_siku),
        ("Bilangan (FPB, KPK, faktorisasi prima, bilangan prima)", bilangan_menu),
        ("Aljabar (persamaan linear dan kuadrat, SPLDV)", aljabar_menu),
        ("Statistika (rata-rata, median, modus)", statistika),
        ("Logaritma", logaritma),
        ("Peluang (peluang kejadian, faktorial, permutasi, kombinasi)", peluang_menu),
    ])


# ---------------------------------------------------- fitur 4: hitung hari
def selisih_dua_tanggal():
    d1 = baca_tanggal("AIX: tanggal pertama (DD-MM-YYYY): ")
    if d1 is None:
        return
    d2 = baca_tanggal("AIX: tanggal kedua (DD-MM-YYYY): ")
    if d2 is None:
        return
    selisih = abs((d2 - d1).days)
    minggu, sisa = divmod(selisih, 7)
    bot(f"Selisih {format_tanggal(d1)} dan {format_tanggal(d2)} "
        f"adalah {selisih} hari ({minggu} minggu {sisa} hari).")


def dari_hari_ini():
    target = baca_tanggal("AIX: masukkan tanggal (DD-MM-YYYY): ")
    if target is None:
        return
    selisih = (target - date.today()).days
    if selisih > 0:
        bot(f"{selisih} hari lagi menuju {format_tanggal(target)}.")
    elif selisih < 0:
        bot(f"{format_tanggal(target)} sudah lewat {-selisih} hari yang lalu.")
    else:
        bot("Tanggal itu adalah hari ini!")


def tambah_hari():
    d = baca_tanggal("AIX: tanggal awal (DD-MM-YYYY): ")
    if d is None:
        return
    n = baca_angka("AIX: tambah berapa hari? (negatif untuk mundur): ")
    if n is None:
        return
    if n != n.to_integral_value():
        bot("Jumlah hari harus bilangan bulat.")
        return
    try:
        hasil = d + timedelta(days=int(n))
    except OverflowError:
        bot("Hasilnya di luar jangkauan kalender.")
        return
    bot(f"{format_tanggal(d)} {'+' if n >= 0 else '-'} {abs(int(n))} hari "
        f"= {format_tanggal(hasil)}")


def cek_hari():
    d = baca_tanggal("AIX: masukkan tanggal (DD-MM-YYYY): ")
    if d is None:
        return
    bot(f"{d.day}-{d.month}-{d.year} jatuh pada hari {HARI[d.weekday()]}.")


@merekam
def mode_hitung_hari():
    menu = {
        "1": selisih_dua_tanggal,
        "2": dari_hari_ini,
        "3": tambah_hari,
        "4": cek_hari,
        "5": umur_ulang_tahun,
        "6": hari_kerja_menu,
        "7": kalender_bulanan,
    }
    while True:
        print()
        bot("=== Hitung Hari ===")
        print("1. Selisih dua tanggal")
        print("2. Hitung hari dari hari ini ke tanggal tertentu")
        print("3. Tambah/kurangi hari dari sebuah tanggal")
        print("4. Cek hari dari sebuah tanggal")
        print("5. Umur lengkap dan hitung mundur ulang tahun")
        print("6. Hari kerja (Senin-Jumat)")
        print("7. Kalender bulanan")
        print("0. Kembali ke menu utama")
        pilih = input("AIX: pilih menu: ").strip()
        if pilih == "0":
            return
        if pilih in menu:
            menu[pilih]()
        else:
            bot("Pilihan tidak valid.")


# ------------------------------------------- matematika lanjut: materi tambahan
_materi("keliling", "Keliling bangun datar", "K = jumlah panjang semua sisi",
        "Keliling adalah panjang garis yang mengelilingi tepi suatu bangun datar.",
        "Persegi: 4 x s. Persegi panjang: 2 x (p + l). Segitiga: a + b + c.",
        "Jajargenjang dan layang-layang: 2 x (dua sisi berbeda). Belah ketupat: "
        "4 x s. Trapesium: jumlah keempat sisinya.",
        "Contoh: persegi panjang 10 x 4 -> K = 2 x (10 + 4) = 28.")
_materi("balok", "Balok", "V = p x l x t,  LP = 2 x (pl + pt + lt)",
        "Balok adalah bangun ruang dengan 6 sisi berbentuk persegi panjang "
        "(3 pasang sisi sama), 12 rusuk, dan 8 titik sudut.",
        "Volume = p x l x t. Luas permukaan = jumlah luas keenam sisinya.",
        "Contoh: 5 x 4 x 3 -> V = 60 dan LP = 2 x (20 + 15 + 12) = 94.")
_materi("tabung", "Tabung", "V = pi x r^2 x t,  LP = 2 x pi x r x (r + t)",
        "Tabung punya dua alas berbentuk lingkaran dan satu selimut (bidang lengkung).",
        "Volume = luas alas x tinggi = pi x r^2 x t.",
        "Luas permukaan = 2 alas + selimut = 2 x pi x r^2 + 2 x pi x r x t.",
        "Contoh: r = 7, t = 10, pi = 22/7 -> V = 22/7 x 49 x 10 = 1540.")
_materi("kerucut", "Kerucut", "V = 1/3 x pi x r^2 x t,  LP = pi x r x (r + s)",
        "Kerucut punya satu alas lingkaran dan selimut yang meruncing ke satu "
        "titik puncak.",
        "Garis pelukis (s) = sisi miring dari jari-jari dan tinggi: s = akar(r^2 + t^2).",
        "Contoh: r = 3, t = 4 -> s = 5 dan V = 1/3 x pi x 9 x 4 = 12 x pi.")
_materi("bola", "Bola", "V = 4/3 x pi x r^3,  LP = 4 x pi x r^2",
        "Bola adalah bangun ruang yang semua titik permukaannya berjarak sama "
        "(jari-jari r) dari pusat.",
        "Luas permukaan bola sama dengan empat kali luas lingkaran besarnya.",
        "Contoh: r = 3 -> V = 4/3 x pi x 27 = 36 x pi dan LP = 4 x pi x 9 = 36 x pi.")
_materi("limas", "Limas persegi", "V = 1/3 x s^2 x t,  LP = s^2 + 2 x s x m",
        "Limas persegi punya alas persegi dan empat sisi tegak berbentuk segitiga "
        "yang bertemu di satu titik puncak.",
        "Tinggi sisi tegak: m = akar(t^2 + (s/2)^2).",
        "Contoh: s = 6, t = 4 -> V = 1/3 x 36 x 4 = 48, m = akar(16 + 9) = 5, "
        "LP = 36 + 2 x 6 x 5 = 96.")
_materi("prisma", "Prisma segitiga", "V = luas alas x tinggi prisma",
        "Prisma segitiga punya dua alas segitiga yang sama dan tiga sisi tegak "
        "berbentuk persegi panjang.",
        "Luas alas dari tiga sisi (rumus Heron): L = akar(s(s-a)(s-b)(s-c)), "
        "dengan s = (a + b + c)/2.",
        "Luas permukaan = 2 x luas alas + keliling alas x tinggi prisma.",
        "Contoh: alas 3-4-5, tinggi prisma 10 -> luas alas 6, V = 60, LP = 12 + 120 = 132.")
_materi("fpb_kpk", "FPB dan KPK", "FPB x KPK = a x b  (untuk dua bilangan)",
        "FPB (Faktor Persekutuan Terbesar) adalah bilangan terbesar yang membagi "
        "habis semua bilangan itu. KPK (Kelipatan Persekutuan Terkecil) adalah "
        "bilangan terkecil yang habis dibagi semuanya.",
        "Dengan faktorisasi prima: FPB = faktor prima yang sama dengan pangkat "
        "TERKECIL, KPK = semua faktor prima dengan pangkat TERBESAR.",
        "Contoh: 12 = 2^2 x 3 dan 18 = 2 x 3^2 -> FPB = 2 x 3 = 6, "
        "KPK = 2^2 x 3^2 = 36.")
_materi("faktorisasi", "Faktorisasi prima", "n = p1^a x p2^b x ...,  banyak faktor = (a+1)(b+1)...",
        "Bilangan prima adalah bilangan asli lebih dari 1 yang hanya habis dibagi "
        "1 dan dirinya sendiri (2, 3, 5, 7, 11, ...).",
        "Faktorisasi prima menyatakan suatu bilangan sebagai perkalian bilangan "
        "prima. Caranya: bagi terus dengan prima terkecil yang bisa membagi.",
        "Contoh: 360 = 2^3 x 3^2 x 5, banyak faktornya (3+1) x (2+1) x (1+1) = 24.")
_materi("prima", "Bilangan prima", "n prima jika tidak habis dibagi 2 sampai akar(n)",
        "Bilangan prima hanya punya dua faktor: 1 dan dirinya sendiri. 2 adalah "
        "satu-satunya prima genap, dan 1 bukan bilangan prima.",
        "Untuk mengeceknya cukup coba bagi dengan bilangan sampai akar dari n.",
        "Contoh: 97 -> akar 97 sekitar 9,8; tidak habis dibagi 2, 3, 5, 7 -> prima.")
_materi("linear", "Persamaan linear satu variabel", "ax + b = c  ->  x = (c - b) / a",
        "Persamaan linear satu variabel adalah persamaan yang pangkat tertinggi "
        "variabelnya 1.",
        "Cara menyelesaikan: pindahkan konstanta ke sisi lain (tandanya berubah), "
        "lalu bagi dengan koefisien x.",
        "Contoh: 2x + 3 = 11 -> 2x = 11 - 3 = 8 -> x = 4.")
_materi("kuadrat", "Persamaan kuadrat (rumus ABC)", "x = (-b +/- akar(b^2 - 4ac)) / 2a",
        "Persamaan kuadrat berbentuk ax^2 + bx + c = 0 dengan a tidak sama dengan 0.",
        "Diskriminan D = b^2 - 4ac menentukan akarnya: D > 0 dua akar real berbeda, "
        "D = 0 satu akar kembar, D < 0 tidak punya akar real (akarnya kompleks).",
        "Jumlah akar = -b/a dan hasil kali akar = c/a.",
        "Contoh: x^2 - 5x + 6 = 0 -> D = 25 - 24 = 1 -> x = (5 +/- 1)/2 -> x = 3 atau x = 2.")
_materi("spldv", "Sistem persamaan linear dua variabel", "x = Dx / D,  y = Dy / D,  D = a1 b2 - a2 b1",
        "SPLDV adalah dua persamaan a1 x + b1 y = c1 dan a2 x + b2 y = c2 yang "
        "harus dipenuhi bersamaan.",
        "Metode determinan: D = a1 b2 - a2 b1, Dx = c1 b2 - c2 b1, Dy = a1 c2 - a2 c1.",
        "Jika D = 0: tidak ada solusi, atau solusinya tak hingga banyak.",
        "Contoh: x + y = 5 dan x - y = 1 -> D = -2, Dx = -6, Dy = -4 -> x = 3, y = 2.")
_materi("statistik", "Statistika dasar", "rata-rata = jumlah data / banyak data",
        "Rata-rata (mean) = jumlah semua data dibagi banyak data.",
        "Median = nilai tengah setelah data diurutkan (kalau banyak data genap, "
        "rata-rata dua nilai tengah).",
        "Modus = nilai yang paling sering muncul. Jangkauan = data terbesar - data terkecil.",
        "Contoh: 3, 5, 5, 7, 10 -> rata-rata 6, median 5, modus 5, jangkauan 7.")
_materi("logaritma", "Logaritma", "a log b = c  artinya  a^c = b",
        "Logaritma adalah kebalikan dari perpangkatan: a log b = c artinya a "
        "pangkat c sama dengan b.",
        "Syarat: basis a > 0 dan a tidak sama dengan 1, serta b > 0.",
        "Contoh: 2 log 8 = 3, karena 2^3 = 8. 'log' tanpa basis berarti basis 10, "
        "dan 'ln' berarti basis e (2,71828...).",
        "Sifat: log(b x c) = log b + log c, log(b : c) = log b - log c, "
        "log(b^n) = n x log b.")
_materi("faktorial", "Faktorial", "n! = n x (n-1) x ... x 2 x 1,  0! = 1",
        "Faktorial n (ditulis n!) adalah hasil kali semua bilangan bulat dari 1 sampai n.",
        "Contoh: 5! = 5 x 4 x 3 x 2 x 1 = 120. Ditetapkan 0! = 1.",
        "Faktorial dipakai untuk menghitung banyak cara menyusun n benda berbeda "
        "dalam satu baris.")
_materi("permutasi", "Permutasi", "nPr = n! / (n - r)!",
        "Permutasi adalah banyak cara memilih r unsur dari n unsur dengan "
        "memperhatikan URUTAN.",
        "Contoh: memilih ketua, wakil, dan sekretaris dari 5 orang -> "
        "5P3 = 5!/2! = 60.")
_materi("kombinasi", "Kombinasi", "nCr = n! / (r! x (n - r)!)",
        "Kombinasi adalah banyak cara memilih r unsur dari n unsur TANPA "
        "memperhatikan urutan.",
        "Contoh: memilih 3 orang dari 5 untuk satu tim -> 5C3 = 5!/(3! x 2!) = 10.",
        "Hubungan: nCr = nPr / r!.")
_materi("peluang", "Peluang kejadian", "P(A) = n(A) / n(S)",
        "Peluang adalah ukuran seberapa mungkin suatu kejadian terjadi, nilainya "
        "antara 0 sampai 1.",
        "n(A) = banyak hasil yang diinginkan, n(S) = banyak seluruh hasil yang "
        "mungkin (ruang sampel).",
        "Contoh: melempar dadu, peluang muncul mata genap = 3/6 = 1/2 = 50%.")


# ---- alat bantu input
def baca_daftar_angka(prompt, minimal=1, maksimal=500):
    """Daftar angka dipisah spasi. None kalau 'batal'."""
    while True:
        s = input(prompt).strip()
        if s.lower() in ("batal", "kembali"):
            return None
        s = re.sub(r"(?<=\d),(?=\d)", ".", s)
        bagian = [b for b in re.split(r"[\s;,]+", s) if b]
        try:
            angka = [Decimal(b) for b in bagian]
        except InvalidOperation:
            bot("Ada yang bukan angka. Pisahkan dengan spasi, contoh: 7 8 9 10 "
                "(atau ketik 'batal').")
            continue
        if any(not x.is_finite() or (x != 0 and not -40 <= x.adjusted() <= 40)
               for x in angka):
            bot("Ada angka yang terlalu besar/kecil atau tidak valid.")
            continue
        if not minimal <= len(angka) <= maksimal:
            bot(f"Masukkan antara {minimal} sampai {maksimal} angka.")
            continue
        return angka


def fmt_bulat(n):
    """Bilangan bulat besar: tampil penuh kalau pendek, ringkas kalau panjang."""
    s = str(n)
    if len(s) <= 30:
        return s
    return f"{s[0]}.{s[1:11]}e+{len(s) - 1} ({len(s)} digit)"


def tanda_suku(x):
    return f"+ {fa(x)}" if x >= 0 else f"- {fa(-x)}"


# ---- bangun datar: keliling
def keliling_bangun_datar():
    tampil_materi("keliling")
    bangun = [
        ("Persegi", ["panjang sisi"], lambda s: 4 * s, "4 x sisi"),
        ("Persegi panjang", ["panjang", "lebar"], lambda p, l: 2 * (p + l),
         "2 x (panjang + lebar)"),
        ("Segitiga", ["sisi pertama", "sisi kedua", "sisi ketiga"],
         lambda a, b, c: a + b + c, "a + b + c"),
        ("Jajargenjang", ["sisi alas", "sisi miring"], lambda a, b: 2 * (a + b),
         "2 x (alas + sisi miring)"),
        ("Trapesium", ["sisi sejajar pertama", "sisi sejajar kedua", "kaki pertama",
                       "kaki kedua"], lambda a, b, c, d: a + b + c + d,
         "jumlah keempat sisi"),
        ("Belah ketupat", ["panjang sisi"], lambda s: 4 * s, "4 x sisi"),
        ("Layang-layang", ["sisi pendek", "sisi panjang"], lambda a, b: 2 * (a + b),
         "2 x (sisi pendek + sisi panjang)"),
    ]
    while True:
        pilih = tampil_menu("Keliling Bangun Datar", [b[0] for b in bangun])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(bangun))
        if n is None:
            continue
        nama, label, fungsi, rumus = bangun[n - 1]
        nilai = baca_banyak(*[f"AIX: masukkan {l}: " for l in label])
        if nilai is None:
            continue
        if nama == "Segitiga":
            a, b, c = sorted(nilai)
            if a + b <= c:
                bot("Ketiga sisi itu tidak bisa membentuk segitiga "
                    "(dua sisi terpendek harus lebih panjang dari sisi terpanjang).")
                continue
        bot(f"Keliling {nama.lower()} = {rumus} = {fa(fungsi(*nilai))}")


def bangun_datar_menu():
    submenu("Bangun Datar", [
        ("Luas bangun datar", luas_bangun_datar),
        ("Keliling bangun datar", keliling_bangun_datar),
        ("Lingkaran (jari-jari, diameter, keliling, luas)", lingkaran),
    ])


# ---- bangun ruang
def volume_balok():
    tampil_materi("balok")
    v = baca_banyak("AIX: panjang: ", "AIX: lebar: ", "AIX: tinggi: ")
    if v is None:
        return
    p, l, t = v
    bot(f"Volume           = p x l x t = {fa(p * l * t)}")
    bot(f"Luas permukaan   = 2 x (pl + pt + lt) = {fa(2 * (p * l + p * t + l * t))}")
    bot(f"Jumlah rusuk     = 4 x (p + l + t) = {fa(4 * (p + l + t))}")
    bot(f"Diagonal ruang   = akar(p^2 + l^2 + t^2) = {fa((p * p + l * l + t * t).sqrt())}")


def volume_tabung():
    tampil_materi("tabung")
    pi, label = pilih_pi()
    v = baca_banyak("AIX: jari-jari alas: ", "AIX: tinggi: ")
    if v is None:
        return
    r, t = v
    bot(f"Volume           = pi x r^2 x t = {fa(pi * r * r * t)}")
    bot(f"Luas selimut     = 2 x pi x r x t = {fa(2 * pi * r * t)}")
    bot(f"Luas permukaan   = 2 x pi x r x (r + t) = {fa(2 * pi * r * (r + t))}")
    bot(f"(memakai pi = {label})")


def volume_kerucut():
    tampil_materi("kerucut")
    pi, label = pilih_pi()
    v = baca_banyak("AIX: jari-jari alas: ", "AIX: tinggi: ")
    if v is None:
        return
    r, t = v
    s = (r * r + t * t).sqrt()
    bot(f"Garis pelukis (s) = akar(r^2 + t^2) = {fa(s)}")
    bot(f"Volume           = 1/3 x pi x r^2 x t = {fa(pi * r * r * t / 3)}")
    bot(f"Luas selimut     = pi x r x s = {fa(pi * r * s)}")
    bot(f"Luas permukaan   = pi x r x (r + s) = {fa(pi * r * (r + s))}")
    bot(f"(memakai pi = {label})")


def volume_bola():
    tampil_materi("bola")
    pi, label = pilih_pi()
    r = baca_positif("AIX: jari-jari: ")
    if r is None:
        return
    bot(f"Volume           = 4/3 x pi x r^3 = {fa(4 * pi * r ** 3 / 3)}")
    bot(f"Luas permukaan   = 4 x pi x r^2 = {fa(4 * pi * r * r)}")
    bot(f"(memakai pi = {label})")


def volume_limas():
    tampil_materi("limas")
    v = baca_banyak("AIX: panjang sisi alas (persegi): ", "AIX: tinggi limas: ")
    if v is None:
        return
    s, t = v
    m = (t * t + (s / 2) ** 2).sqrt()
    bot(f"Tinggi sisi tegak (m) = akar(t^2 + (s/2)^2) = {fa(m)}")
    bot(f"Volume           = 1/3 x s^2 x t = {fa(s * s * t / 3)}")
    bot(f"Luas permukaan   = s^2 + 2 x s x m = {fa(s * s + 2 * s * m)}")


def volume_prisma():
    tampil_materi("prisma")
    v = baca_banyak("AIX: sisi alas pertama (a): ", "AIX: sisi alas kedua (b): ",
                    "AIX: sisi alas ketiga (c): ", "AIX: tinggi prisma: ")
    if v is None:
        return
    a, b, c, t = v
    x, y, z = sorted((a, b, c))
    if x + y <= z:
        bot("Ketiga sisi alas itu tidak bisa membentuk segitiga.")
        return
    sp = (a + b + c) / 2
    luas_alas = (sp * (sp - a) * (sp - b) * (sp - c)).sqrt()
    bot(f"Luas alas (rumus Heron) = {fa(luas_alas)}")
    bot(f"Volume           = luas alas x tinggi = {fa(luas_alas * t)}")
    bot(f"Luas permukaan   = 2 x luas alas + keliling alas x tinggi = "
        f"{fa(2 * luas_alas + (a + b + c) * t)}")


def bangun_ruang_menu():
    submenu("Bangun Ruang", [
        ("Kubus", volume_kubus),
        ("Balok", volume_balok),
        ("Tabung", volume_tabung),
        ("Kerucut", volume_kerucut),
        ("Bola", volume_bola),
        ("Limas persegi", volume_limas),
        ("Prisma segitiga", volume_prisma),
    ])


# ---- bilangan
def faktorkan(n):
    """Faktorisasi prima n (int > 1) sebagai dict {prima: pangkat}."""
    hasil = {}
    for p in (2, 3):
        while n % p == 0:
            hasil[p] = hasil.get(p, 0) + 1
            n //= p
    i = 5
    while i * i <= n:
        for p in (i, i + 2):
            while n % p == 0:
                hasil[p] = hasil.get(p, 0) + 1
                n //= p
        i += 6
    if n > 1:
        hasil[n] = hasil.get(n, 0) + 1
    return hasil


def teks_faktor(faktor):
    return " x ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in sorted(faktor.items()))


def adalah_prima(n):
    if n < 2:
        return False
    if n % 2 == 0 or n % 3 == 0:
        return n in (2, 3)
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


BATAS_BILANGAN = 10 ** 12


def baca_bilangan_asli(prompt, batas=BATAS_BILANGAN):
    n = baca_bulat_positif(prompt)
    if n is not None and n > batas:
        bot(f"Bilangannya terlalu besar. Maksimal {batas}.")
        return None
    return n


def fpb_kpk():
    tampil_materi("fpb_kpk")
    angka = baca_daftar_angka("AIX: masukkan bilangan bulat positif (pisahkan spasi, "
                              "minimal 2): ", 2, 20)
    if angka is None:
        return
    if any(x != x.to_integral_value() or x <= 0 for x in angka):
        bot("Semua bilangan harus bilangan bulat positif.")
        return
    nilai = [int(x) for x in angka]
    daftar = ", ".join(str(x) for x in nilai)
    g = reduce(math.gcd, nilai)
    k = reduce(lambda x, y: x * y // math.gcd(x, y), nilai)
    if all(x <= BATAS_BILANGAN for x in nilai):
        for x in nilai:
            if x > 1:
                print(f"   {x} = {teks_faktor(faktorkan(x))}")
    bot(f"FPB({daftar}) = {fmt_bulat(g)}")
    bot(f"KPK({daftar}) = {fmt_bulat(k)}")
    if len(nilai) == 2:
        bot(f"Cek: FPB x KPK = {fmt_bulat(g * k)} dan a x b = {fmt_bulat(nilai[0] * nilai[1])}")


def faktorisasi_prima():
    tampil_materi("faktorisasi")
    n = baca_bilangan_asli("AIX: masukkan bilangan bulat positif: ")
    if n is None:
        return
    if n == 1:
        bot("1 bukan bilangan prima dan tidak punya faktorisasi prima.")
        return
    faktor = faktorkan(n)
    banyak = reduce(lambda x, y: x * y, (e + 1 for e in faktor.values()))
    jumlah = reduce(lambda x, y: x * y,
                    ((p ** (e + 1) - 1) // (p - 1) for p, e in faktor.items()))
    if len(faktor) == 1 and list(faktor.values())[0] == 1:
        bot(f"{n} adalah bilangan prima (faktornya hanya 1 dan {n}).")
    else:
        bot(f"{n} = {teks_faktor(faktor)}")
        bot(f"Banyak faktor positif = {banyak}, jumlah semua faktor = {jumlah}")


def cek_prima():
    tampil_materi("prima")
    n = baca_bilangan_asli("AIX: masukkan bilangan bulat positif: ")
    if n is None:
        return
    if adalah_prima(n):
        bot(f"{n} adalah bilangan prima.")
    elif n == 1:
        bot("1 bukan bilangan prima (prima harus lebih dari 1).")
    else:
        faktor = faktorkan(n)
        bot(f"{n} bukan bilangan prima: {n} = {teks_faktor(faktor)}.")
    berikut = n + 1
    while not adalah_prima(berikut):
        berikut += 1
    bot(f"Bilangan prima berikutnya setelah {n} adalah {berikut}.")


def daftar_prima():
    tampil_materi("prima")
    batas = baca_bulat_positif("AIX: tampilkan bilangan prima sampai (maksimal 1000): ")
    if batas is None:
        return
    if batas > 1000:
        bot("Maksimal 1000 ya.")
        return
    prima = [x for x in range(2, batas + 1) if adalah_prima(x)]
    bot(f"Ada {len(prima)} bilangan prima sampai {batas}:")
    for i in range(0, len(prima), 10):
        print("   " + " ".join(f"{x:>4}" for x in prima[i:i + 10]))


def bilangan_menu():
    submenu("Bilangan", [
        ("FPB dan KPK", fpb_kpk),
        ("Faktorisasi prima", faktorisasi_prima),
        ("Cek bilangan prima", cek_prima),
        ("Daftar bilangan prima", daftar_prima),
    ])


# ---- aljabar
def persamaan_linear():
    tampil_materi("linear")
    bot("Bentuk persamaan: a x + b = c")
    a = baca_angka("AIX: nilai a (tidak boleh 0): ")
    if a is None:
        return
    if a == 0:
        bot("Nilai a tidak boleh 0 (itu bukan persamaan linear lagi).")
        return
    b = baca_angka("AIX: nilai b: ")
    if b is None:
        return
    c = baca_angka("AIX: nilai c: ")
    if c is None:
        return
    x = (c - b) / a
    print(f"   {fa(a)}x {tanda_suku(b)} = {fa(c)}")
    print(f"   {fa(a)}x = {fa(c)} - ({fa(b)}) = {fa(c - b)}")
    bot(f"x = {fa(c - b)} / {fa(a)} = {fa(x)}")


def persamaan_kuadrat():
    tampil_materi("kuadrat")
    bot("Bentuk persamaan: a x^2 + b x + c = 0")
    a = baca_angka("AIX: nilai a (tidak boleh 0): ")
    if a is None:
        return
    if a == 0:
        bot("Nilai a tidak boleh 0 (itu bukan persamaan kuadrat).")
        return
    b = baca_angka("AIX: nilai b: ")
    if b is None:
        return
    c = baca_angka("AIX: nilai c: ")
    if c is None:
        return
    d = b * b - 4 * a * c
    print(f"   {fa(a)}x^2 {tanda_suku(b)}x {tanda_suku(c)} = 0")
    bot(f"Diskriminan D = b^2 - 4ac = {fa(d)}")
    if d > 0:
        akar_d = d.sqrt()
        x1, x2 = (-b + akar_d) / (2 * a), (-b - akar_d) / (2 * a)
        bot(f"D > 0: dua akar real berbeda. x1 = {fa(x1)} dan x2 = {fa(x2)}")
    elif d == 0:
        bot(f"D = 0: satu akar kembar. x = {fa(-b / (2 * a))}")
    else:
        nyata = -b / (2 * a)
        imajiner = (-d).sqrt() / (2 * abs(a))
        bot(f"D < 0: tidak ada akar real. Akar kompleks: x = {fa(nyata)} +/- {fa(imajiner)}i")
    bot(f"Jumlah akar = -b/a = {fa(-b / a)}, hasil kali akar = c/a = {fa(c / a)}")


def spldv():
    tampil_materi("spldv")
    bot("Bentuk: a1 x + b1 y = c1   dan   a2 x + b2 y = c2")
    nilai = []
    for nama in ("a1", "b1", "c1", "a2", "b2", "c2"):
        v = baca_angka(f"AIX: nilai {nama}: ")
        if v is None:
            return
        nilai.append(v)
    a1, b1, c1, a2, b2, c2 = nilai
    d = a1 * b2 - a2 * b1
    dx = c1 * b2 - c2 * b1
    dy = a1 * c2 - a2 * c1
    print(f"   D = {fa(d)}, Dx = {fa(dx)}, Dy = {fa(dy)}")
    if d == 0:
        if dx == 0 and dy == 0:
            bot("D = 0 dan Dx = Dy = 0: solusinya tak hingga banyak (kedua persamaan sejajar berimpit).")
        else:
            bot("D = 0: tidak ada solusi (kedua garis sejajar).")
        return
    bot(f"x = Dx / D = {fa(dx / d)} dan y = Dy / D = {fa(dy / d)}")


def aljabar_menu():
    submenu("Aljabar", [
        ("Persamaan linear satu variabel (ax + b = c)", persamaan_linear),
        ("Persamaan kuadrat (rumus ABC)", persamaan_kuadrat),
        ("Sistem persamaan linear dua variabel", spldv),
    ])


# ---- statistika
def statistika():
    tampil_materi("statistik")
    data = baca_daftar_angka("AIX: masukkan data (pisahkan spasi, contoh: 7 8 8 9 10): ")
    if data is None:
        return
    urut = sorted(data)
    n = len(urut)
    jumlah = sum(urut)
    rata = jumlah / n
    tengah = n // 2
    median = urut[tengah] if n % 2 else (urut[tengah - 1] + urut[tengah]) / 2
    hitung = Counter(urut)
    terbanyak = max(hitung.values())
    modus = sorted(k for k, v in hitung.items() if v == terbanyak)
    print("   Data terurut: " + " ".join(fa(x) for x in urut))
    bot(f"Banyak data = {n}, jumlah = {fa(jumlah)}")
    bot(f"Rata-rata = jumlah / banyak data = {fa(rata)}")
    bot(f"Median = {fa(median)}")
    if terbanyak == 1 and n > 1:
        bot("Modus = tidak ada (semua data muncul satu kali)")
    else:
        bot(f"Modus = {', '.join(fa(m) for m in modus)} (muncul {terbanyak} kali)")
    bot(f"Nilai terkecil = {fa(urut[0])}, terbesar = {fa(urut[-1])}, "
        f"jangkauan = {fa(urut[-1] - urut[0])}")


# ---- logaritma
def logaritma():
    tampil_materi("logaritma")
    bot("Menghitung a log b. Basis boleh angka, atau ketik 'e' untuk ln.")
    while True:
        teks = input("AIX: basis a (kosong = 10, 'e' = bilangan e, 'batal'): ").strip().lower()
        if teks in ("batal", "kembali"):
            return
        if teks in ("", "10"):
            basis, nama = Decimal(10), "10"
            break
        if teks == "e":
            basis, nama = Decimal(1).exp(), "e"
            break
        try:
            basis = Decimal(teks.replace(",", "."))
        except InvalidOperation:
            bot("Itu bukan angka.")
            continue
        if not basis.is_finite() or basis <= 0 or basis == 1:
            bot("Basis harus lebih dari 0 dan tidak boleh 1.")
            continue
        nama = fa(basis)
        break
    b = baca_positif("AIX: nilai b (harus lebih dari 0): ")
    if b is None:
        return
    hasil = b.ln() / basis.ln()
    bot(f"{nama} log {fa(b)} = {fa(hasil)}")
    pembulatan = hasil.quantize(Decimal(1).scaleb(-10))
    if pembulatan == pembulatan.to_integral_value() and nama != "e":
        bot(f"Cek: {nama}^{fa(pembulatan)} = {fa(hitung_pangkat(basis, pembulatan))}")


# ---- peluang
def faktorial():
    tampil_materi("faktorial")
    n = baca_bulat("AIX: masukkan n (0 sampai 1000): ", 1000)
    if n is None:
        return
    if n < 0:
        bot("n tidak boleh negatif.")
        return
    hasil = math.factorial(n)
    uraian = ""
    if 2 <= n <= 8:
        uraian = " x ".join(str(i) for i in range(n, 0, -1)) + " = "
    bot(f"{n}! = {uraian}{fmt_bulat(hasil)}")


def _baca_n_r():
    n = baca_bulat("AIX: masukkan n (banyak seluruh unsur, 0-1000): ", 1000)
    if n is None:
        return None
    r = baca_bulat("AIX: masukkan r (banyak unsur yang dipilih): ", 1000)
    if r is None:
        return None
    if n < 0 or r < 0 or r > n:
        bot("Syarat: 0 <= r <= n.")
        return None
    return n, r


def permutasi():
    tampil_materi("permutasi")
    nr = _baca_n_r()
    if nr is None:
        return
    n, r = nr
    hasil = math.factorial(n) // math.factorial(n - r)
    bot(f"{n}P{r} = {n}! / {n - r}! = {fmt_bulat(hasil)}")


def kombinasi():
    tampil_materi("kombinasi")
    nr = _baca_n_r()
    if nr is None:
        return
    n, r = nr
    hasil = math.factorial(n) // (math.factorial(r) * math.factorial(n - r))
    bot(f"{n}C{r} = {n}! / ({r}! x {n - r}!) = {fmt_bulat(hasil)}")


def peluang_kejadian():
    tampil_materi("peluang")
    na = baca_bulat("AIX: n(A), banyak hasil yang diinginkan: ", 10 ** 9)
    if na is None:
        return
    ns = baca_bulat("AIX: n(S), banyak seluruh hasil yang mungkin: ", 10 ** 9)
    if ns is None:
        return
    if ns <= 0 or na < 0 or na > ns:
        bot("Syarat: 0 <= n(A) <= n(S) dan n(S) > 0.")
        return
    g = math.gcd(na, ns) or 1
    p = Decimal(na) / Decimal(ns)
    bot(f"P(A) = {na}/{ns} = {na // g}/{ns // g} = {fa(p)} = {fa(p * 100)}%")


def peluang_menu():
    submenu("Peluang", [
        ("Peluang kejadian P(A) = n(A) / n(S)", peluang_kejadian),
        ("Faktorial (n!)", faktorial),
        ("Permutasi (nPr)", permutasi),
        ("Kombinasi (nCr)", kombinasi),
    ])


# ------------------------------------------------ fitur: konversi & keuangan
_materi("persen", "Persen", "x% dari y = x/100 x y",
        "Persen berarti per seratus: 25% = 25/100 = 0,25.",
        "Mencari persen dari suatu nilai: kalikan nilai itu dengan persen/100. "
        "Contoh: 20% dari 150 = 0,2 x 150 = 30.",
        "Mencari berapa persen a dari b: (a / b) x 100%. Contoh: 30 dari 150 = 20%.",
        "Kenaikan/penurunan persen = (nilai baru - nilai lama) / nilai lama x 100%.")
_materi("diskon", "Diskon dan pajak", "harga akhir = harga x (100 - diskon) / 100",
        "Diskon adalah potongan harga dalam persen. Besar potongan = harga x diskon/100.",
        "Contoh: harga Rp 200.000 diskon 25% -> potongan Rp 50.000, harga bayar "
        "Rp 150.000.",
        "Pajak (misalnya PPN) menambah harga: total = harga x (100 + pajak) / 100.")
_materi("bunga_tunggal", "Bunga tunggal", "I = M x b x t,  total = M + I",
        "Bunga tunggal dihitung hanya dari modal awal (M) dengan bunga b per "
        "periode selama t periode.",
        "Contoh: modal Rp 1.000.000, bunga 6% per tahun, 2 tahun -> "
        "I = 1.000.000 x 0,06 x 2 = Rp 120.000.")
_materi("bunga_majemuk", "Bunga majemuk", "A = M x (1 + b/m)^(m x t)",
        "Pada bunga majemuk, bunga ikut berbunga: bunga tiap periode ditambahkan "
        "ke modal.",
        "m = berapa kali bunga dihitung per tahun (12 = bulanan), t dalam tahun.",
        "Contoh: Rp 1.000.000, bunga 6% per tahun, majemuk tahunan, 2 tahun -> "
        "A = 1.000.000 x 1,06^2 = Rp 1.123.600.")
_materi("cicilan", "Cicilan pinjaman", "anuitas: cicilan = P x r x (1+r)^n / ((1+r)^n - 1)",
        "Cicilan anuitas: besar cicilan tiap bulan tetap, terdiri dari pokok dan "
        "bunga. P = pinjaman, r = bunga per bulan (bunga tahunan / 12), n = jumlah bulan.",
        "Cicilan flat: bunga dihitung dari pinjaman awal terus. cicilan = "
        "(P + P x bunga/tahun x tahun) / n.",
        "Contoh flat: P = Rp 12.000.000, bunga flat 10% per tahun, 12 bulan -> "
        "cicilan = (12.000.000 + 1.200.000) / 12 = Rp 1.100.000.")

SATUAN = {
    "panjang": {"km": Decimal("1000"), "hm": Decimal("100"), "dam": Decimal("10"),
                "m": Decimal("1"), "dm": Decimal("0.1"), "cm": Decimal("0.01"),
                "mm": Decimal("0.001"), "inci": Decimal("0.0254"),
                "kaki": Decimal("0.3048"), "yard": Decimal("0.9144"),
                "mil": Decimal("1609.344")},
    "berat": {"ton": Decimal("1000"), "kwintal": Decimal("100"), "kg": Decimal("1"),
              "hg": Decimal("0.1"), "ons": Decimal("0.1"), "g": Decimal("0.001"),
              "mg": Decimal("0.000001"), "pon": Decimal("0.45359237")},
    "volume": {"kl": Decimal("1000"), "hl": Decimal("100"), "dal": Decimal("10"),
               "l": Decimal("1"), "dl": Decimal("0.1"), "cl": Decimal("0.01"),
               "ml": Decimal("0.001"), "m3": Decimal("1000"), "cm3": Decimal("0.001"),
               "galon": Decimal("3.785411784")},
    "luas": {"km2": Decimal("1000000"), "ha": Decimal("10000"), "are": Decimal("100"),
             "m2": Decimal("1"), "cm2": Decimal("0.0001"), "mm2": Decimal("0.000001"),
             "acre": Decimal("4046.8564224")},
    "waktu": {"minggu": Decimal("604800"), "hari": Decimal("86400"),
              "jam": Decimal("3600"), "menit": Decimal("60"), "detik": Decimal("1"),
              "ms": Decimal("0.001")},
    "kecepatan": {"m/s": Decimal("1"), "km/jam": Decimal("1000") / Decimal("3600"),
                  "mph": Decimal("0.44704"), "knot": Decimal("1852") / Decimal("3600")},
    "data": {"bit": Decimal("1") / Decimal("8"), "byte": Decimal("1"),
             "kb": Decimal("1024"), "mb": Decimal("1024") ** 2,
             "gb": Decimal("1024") ** 3, "tb": Decimal("1024") ** 4},
}
ALIAS_SATUAN = {
    "inch": "inci", "in": "inci", "ft": "kaki", "feet": "kaki", "mile": "mil",
    "mi": "mil", "lb": "pon", "lbs": "pon", "pound": "pon", "liter": "l",
    "gallon": "galon", "hektar": "ha", "kilometer": "km", "meter": "m",
    "sentimeter": "cm", "milimeter": "mm", "kilogram": "kg", "gram": "g",
    "miligram": "mg", "s": "detik", "sec": "detik", "min": "menit", "h": "jam",
    "kmh": "km/jam", "kph": "km/jam", "kmj": "km/jam", "knots": "knot",
    "bytes": "byte", "b": "byte",
}
ALIAS_SUHU = {"c": "c", "celsius": "c", "f": "f", "fahrenheit": "f", "k": "k",
              "kelvin": "k", "r": "r", "reamur": "r"}
POLA_KONVERSI = re.compile(
    r"^\s*(-?\d+(?:[.,]\d+)?)\s*([a-z0-9/]+)\s*(?:ke|to|jadi|=|->)?\s*([a-z0-9/]+)\s*$")


def konversi_suhu(nilai, dari, ke):
    celsius = {"c": nilai, "f": (nilai - 32) * 5 / 9, "k": nilai - Decimal("273.15"),
               "r": nilai * 5 / 4}[dari]
    if celsius < Decimal("-273.15"):
        raise ValueError("Suhu itu di bawah nol mutlak (tidak mungkin).")
    return {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + Decimal("273.15"),
            "r": celsius * 4 / 5}[ke]


def konversi_kategori(nama):
    suhu = nama == "suhu"
    if suhu:
        bot("Satuan suhu: c (Celsius), f (Fahrenheit), k (Kelvin), r (Reamur).")
    else:
        bot("Satuan " + nama + ": " + ", ".join(SATUAN[nama]))
    contoh = "36,6 c ke f" if suhu else {
        "panjang": "5 km ke m", "berat": "2,5 kg ke g", "volume": "3 l ke ml",
        "luas": "2 ha ke m2", "waktu": "90 menit ke jam",
        "kecepatan": "72 km/jam ke m/s", "data": "2 gb ke mb"}[nama]
    while True:
        teks = input(f"AIX: ketik konversi (contoh: {contoh}) atau 'batal': ").strip().lower()
        if teks in ("batal", "kembali", "menu"):
            return
        m = POLA_KONVERSI.match(teks)
        if not m:
            bot(f"Formatnya: <angka> <satuan asal> ke <satuan tujuan>, contoh: {contoh}")
            continue
        nilai = Decimal(m.group(1).replace(",", "."))
        if suhu:
            dari, ke = ALIAS_SUHU.get(m.group(2)), ALIAS_SUHU.get(m.group(3))
            if not dari or not ke:
                bot("Satuan suhu tidak dikenal. Pakai c, f, k, atau r.")
                continue
            try:
                hasil = konversi_suhu(nilai, dari, ke)
            except ValueError as err:
                bot(str(err))
                continue
            bot(f"{fa(nilai)} {dari.upper()} = {fa(hasil)} {ke.upper()}")
            continue
        tabel = SATUAN[nama]
        dari = ALIAS_SATUAN.get(m.group(2), m.group(2))
        ke = ALIAS_SATUAN.get(m.group(3), m.group(3))
        if dari not in tabel or ke not in tabel:
            bot("Satuan tidak dikenal untuk kategori ini. Lihat daftar satuan di atas.")
            continue
        bot(f"{fa(nilai)} {dari} = {fa(nilai * tabel[dari] / tabel[ke])} {ke}")


def konversi_menu():
    kategori = ["panjang", "berat", "volume", "luas", "waktu", "kecepatan", "data", "suhu"]
    while True:
        pilih = tampil_menu("Konversi Satuan", [k.capitalize() for k in kategori])
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(kategori))
        if n is not None:
            konversi_kategori(kategori[n - 1])


def rupiah(x):
    x = Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return f"{'-' if x < 0 else ''}Rp {abs(int(x)):,}".replace(",", ".")


# ---- persen dan diskon
def persen_dari():
    tampil_materi("persen")
    p = baca_angka("AIX: persen (misal 20): ")
    if p is None:
        return
    y = baca_angka("AIX: dari nilai berapa: ")
    if y is None:
        return
    bot(f"{fa(p)}% dari {fa(y)} = {fa(p)}/100 x {fa(y)} = {fa(p * y / 100)}")


def berapa_persen():
    tampil_materi("persen")
    a = baca_angka("AIX: nilai a (bagian): ")
    if a is None:
        return
    b = baca_angka("AIX: nilai b (keseluruhan, tidak boleh 0): ")
    if b is None:
        return
    if b == 0:
        bot("Nilai b tidak boleh 0.")
        return
    bot(f"{fa(a)} adalah {fa(a / b * 100)}% dari {fa(b)}")


def kenaikan_persen():
    tampil_materi("persen")
    awal = baca_angka("AIX: nilai awal (tidak boleh 0): ")
    if awal is None:
        return
    if awal == 0:
        bot("Nilai awal tidak boleh 0.")
        return
    akhir = baca_angka("AIX: nilai akhir: ")
    if akhir is None:
        return
    p = (akhir - awal) / abs(awal) * 100
    arah = "naik" if p > 0 else "turun" if p < 0 else "tetap"
    bot(f"Dari {fa(awal)} ke {fa(akhir)}: {arah} {fa(abs(p))}%")


def diskon_harga():
    tampil_materi("diskon")
    harga = baca_positif("AIX: harga awal (Rp): ")
    if harga is None:
        return
    d = baca_angka("AIX: diskon (%): ")
    if d is None:
        return
    if not 0 <= d <= 100:
        bot("Diskon harus antara 0 sampai 100 persen.")
        return
    d2 = baca_angka("AIX: diskon kedua (%), ketik 0 kalau tidak ada: ")
    if d2 is None:
        return
    if not 0 <= d2 <= 100:
        bot("Diskon harus antara 0 sampai 100 persen.")
        return
    akhir = harga * (100 - d) / 100 * (100 - d2) / 100
    bot(f"Harga awal = {rupiah(harga)}")
    bot(f"Harga setelah diskon = {rupiah(akhir)}")
    bot(f"Kamu hemat = {rupiah(harga - akhir)} ({fa((harga - akhir) / harga * 100)}%)")


def harga_sebelum_diskon():
    tampil_materi("diskon")
    akhir = baca_positif("AIX: harga setelah diskon (Rp): ")
    if akhir is None:
        return
    d = baca_angka("AIX: besar diskon (%, kurang dari 100): ")
    if d is None:
        return
    if not 0 <= d < 100:
        bot("Diskon harus 0 sampai kurang dari 100 persen.")
        return
    awal = akhir * 100 / (100 - d)
    bot(f"Harga sebelum diskon = {rupiah(awal)}")


def hitung_pajak():
    tampil_materi("diskon")
    harga = baca_positif("AIX: harga sebelum pajak (Rp): ")
    if harga is None:
        return
    teks = input("AIX: besar pajak (%), kosong = 11 (PPN): ").strip().replace(",", ".")
    try:
        p = Decimal(teks) if teks else Decimal(11)
    except InvalidOperation:
        bot("Itu bukan angka.")
        return
    if not p.is_finite() or p < 0:
        bot("Pajak harus berupa angka 0 atau lebih.")
        return
    bot(f"Pajak {fa(p)}% = {rupiah(harga * p / 100)}")
    bot(f"Total yang dibayar = {rupiah(harga * (100 + p) / 100)}")


def persen_menu():
    submenu("Persen dan Diskon", [
        ("x% dari y", persen_dari),
        ("Berapa persen a dari b", berapa_persen),
        ("Kenaikan / penurunan persen", kenaikan_persen),
        ("Harga setelah diskon", diskon_harga),
        ("Harga sebelum diskon", harga_sebelum_diskon),
        ("Pajak / PPN", hitung_pajak),
    ])


# ---- bunga dan cicilan
def bunga_tunggal():
    tampil_materi("bunga_tunggal")
    v = baca_banyak("AIX: modal (Rp): ", "AIX: bunga per tahun (%): ",
                    "AIX: lama (tahun, boleh desimal, 6 bulan = 0,5): ")
    if v is None:
        return
    m, b, t = v
    bunga = m * b / 100 * t
    bot(f"Bunga = M x b x t = {rupiah(bunga)}")
    bot(f"Total uang = modal + bunga = {rupiah(m + bunga)}")


def bunga_majemuk():
    tampil_materi("bunga_majemuk")
    v = baca_banyak("AIX: modal (Rp): ", "AIX: bunga per tahun (%): ",
                    "AIX: lama (tahun): ")
    if v is None:
        return
    m, b, t = v
    k = baca_bulat_positif("AIX: bunga dihitung berapa kali per tahun (1 = tahunan, "
                           "12 = bulanan): ")
    if k is None:
        return
    total = coba(lambda: m * hitung_pangkat(1 + b / 100 / k, k * t))
    if total is None:
        return
    bot(f"Total uang = M x (1 + b/{k})^({k} x t) = {rupiah(total)}")
    bot(f"Bunga yang didapat = {rupiah(total - m)}")


def cicilan_anuitas():
    tampil_materi("cicilan")
    v = baca_banyak("AIX: jumlah pinjaman (Rp): ", "AIX: bunga per tahun (%): ")
    if v is None:
        return
    p, b = v
    n = baca_bulat_positif("AIX: tenor / lama cicilan (bulan): ")
    if n is None:
        return
    if n > 1200:
        bot("Tenor maksimal 1200 bulan.")
        return
    r = b / 100 / 12
    if r == 0:
        cicilan = p / n
    else:
        faktor = coba(lambda: hitung_pangkat(1 + r, n))
        if faktor is None:
            return
        cicilan = p * r * faktor / (faktor - 1)
    bot(f"Cicilan per bulan = {rupiah(cicilan)}")
    bot(f"Total pembayaran = {rupiah(cicilan * n)}")
    bot(f"Total bunga = {rupiah(cicilan * n - p)}")


def cicilan_flat():
    tampil_materi("cicilan")
    v = baca_banyak("AIX: jumlah pinjaman (Rp): ", "AIX: bunga flat per tahun (%): ")
    if v is None:
        return
    p, b = v
    n = baca_bulat_positif("AIX: tenor / lama cicilan (bulan): ")
    if n is None:
        return
    bunga = p * b / 100 * Decimal(n) / 12
    bot(f"Total bunga = {rupiah(bunga)}")
    bot(f"Total pembayaran = {rupiah(p + bunga)}")
    bot(f"Cicilan per bulan = {rupiah((p + bunga) / n)}")


def keuangan_menu():
    submenu("Bunga dan Cicilan", [
        ("Bunga tunggal", bunga_tunggal),
        ("Bunga majemuk", bunga_majemuk),
        ("Cicilan anuitas (cicilan tetap, bunga menurun)", cicilan_anuitas),
        ("Cicilan flat", cicilan_flat),
    ])


@merekam
def mode_konversi():
    submenu("Konversi dan Keuangan", [
        ("Konversi satuan (panjang, berat, suhu, dll.)", konversi_menu),
        ("Persen dan diskon", persen_menu),
        ("Bunga dan cicilan", keuangan_menu),
    ])


# ------------------------------------------------ hitung hari: tambahan
def tambah_bulan(d, n):
    """Tambah n bulan ke tanggal d (hari disesuaikan kalau bulan lebih pendek)."""
    total = d.year * 12 + (d.month - 1) + n
    tahun, bulan = divmod(total, 12)
    return date(tahun, bulan + 1, min(d.day, calendar.monthrange(tahun, bulan + 1)[1]))


def ulang_tahun_di(lahir, tahun):
    try:
        return date(tahun, lahir.month, lahir.day)
    except ValueError:  # 29 Februari di tahun bukan kabisat
        return date(tahun, 3, 1)


def umur_ulang_tahun():
    lahir = baca_tanggal("AIX: tanggal lahir (DD-MM-YYYY): ")
    if lahir is None:
        return
    hari_ini = date.today()
    if lahir > hari_ini:
        bot("Tanggal lahir tidak boleh di masa depan.")
        return
    bulan_total = (hari_ini.year - lahir.year) * 12 + hari_ini.month - lahir.month
    if tambah_bulan(lahir, bulan_total) > hari_ini:
        bulan_total -= 1
    tahun, bulan = divmod(bulan_total, 12)
    hari = (hari_ini - tambah_bulan(lahir, bulan_total)).days
    total_hari = (hari_ini - lahir).days
    bot(f"Umur = {tahun} tahun {bulan} bulan {hari} hari")
    bot(f"Sudah hidup selama {total_hari} hari (sekitar {total_hari // 7} minggu)")
    bot(f"Lahir pada hari {HARI[lahir.weekday()]}")
    berikut = ulang_tahun_di(lahir, hari_ini.year)
    if berikut < hari_ini:
        berikut = ulang_tahun_di(lahir, hari_ini.year + 1)
    sisa = (berikut - hari_ini).days
    if sisa == 0:
        bot(f"Hari ini kamu ulang tahun ke-{tahun}! Selamat ulang tahun!")
    else:
        bot(f"Ulang tahun ke-{berikut.year - lahir.year} jatuh pada "
            f"{format_tanggal(berikut)}, {sisa} hari lagi")


def hari_kerja_antara(d1, d2):
    """(total hari, hari kerja Senin-Jumat), kedua tanggal ikut dihitung."""
    if d1 > d2:
        d1, d2 = d2, d1
    total = (d2 - d1).days + 1
    minggu, sisa = divmod(total, 7)
    kerja = minggu * 5
    for i in range(sisa):
        if (d1 + timedelta(days=minggu * 7 + i)).weekday() < 5:
            kerja += 1
    return total, kerja


def tambah_hari_kerja(d, n):
    langkah = 1 if n >= 0 else -1
    sisa = abs(n)
    while sisa > 0:
        d += timedelta(days=langkah)
        if d.weekday() < 5:
            sisa -= 1
    return d


def hitung_hari_kerja():
    d1 = baca_tanggal("AIX: tanggal mulai (DD-MM-YYYY): ")
    if d1 is None:
        return
    d2 = baca_tanggal("AIX: tanggal selesai (DD-MM-YYYY): ")
    if d2 is None:
        return
    libur = baca_bulat("AIX: jumlah hari libur nasional yang jatuh di hari kerja "
                       "(0 kalau tidak ada): ", 365)
    if libur is None:
        return
    total, kerja = hari_kerja_antara(d1, d2)
    libur = max(0, min(libur, kerja))
    bot(f"Dari {format_tanggal(min(d1, d2))} sampai {format_tanggal(max(d1, d2))} "
        f"(kedua tanggal ikut dihitung):")
    bot(f"Total hari = {total}, akhir pekan = {total - kerja}, "
        f"hari kerja = {kerja - libur}" + (f" (setelah {libur} hari libur)" if libur else ""))


def tambah_n_hari_kerja():
    d = baca_tanggal("AIX: tanggal awal (DD-MM-YYYY): ")
    if d is None:
        return
    n = baca_bulat("AIX: tambah berapa hari kerja? (negatif untuk mundur): ", 20000)
    if n is None:
        return
    try:
        hasil = tambah_hari_kerja(d, n)
    except OverflowError:
        bot("Hasilnya di luar jangkauan kalender.")
        return
    bot(f"{format_tanggal(d)} {'+' if n >= 0 else '-'} {abs(n)} hari kerja = "
        f"{format_tanggal(hasil)}")


def hari_kerja_menu():
    submenu("Hari Kerja (Senin-Jumat)", [
        ("Hitung hari kerja antara dua tanggal", hitung_hari_kerja),
        ("Tambah / kurangi hari kerja dari sebuah tanggal", tambah_n_hari_kerja),
    ])


def kalender_bulanan():
    hari_ini = date.today()
    while True:
        bulan = baca_bulat("AIX: bulan (1-12): ", 10 ** 6)
        if bulan is None:
            return
        if 1 <= bulan <= 12:
            break
        bot("Bulan harus 1 sampai 12.")
    while True:
        tahun = baca_bulat("AIX: tahun (misal 2026): ", 10 ** 6)
        if tahun is None:
            return
        if 1 <= tahun <= 9999:
            break
        bot("Tahun harus 1 sampai 9999.")
    print()
    print(f"      {BULAN[bulan - 1]} {tahun}")
    print("".join(f"{n:>3} " for n in ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")))
    for minggu in calendar.monthcalendar(tahun, bulan):
        sel = []
        for h in minggu:
            if h == 0:
                sel.append("    ")
            elif (tahun, bulan, h) == (hari_ini.year, hari_ini.month, hari_ini.day):
                sel.append(f"[{h:>2}]")
            else:
                sel.append(f"{h:>3} ")
        print("".join(sel).rstrip())
    if (tahun, bulan) == (hari_ini.year, hari_ini.month):
        print("   [ ] = hari ini")


# ------------------------------------------------ fitur: kuis latihan soal
# Tiap pembuat soal: f(tingkat 1..3) -> (soal, jawaban Decimal, pembahasan)
D = Decimal


def _ri(tingkat, *batas):
    """Batas atas acak sesuai tingkat (mudah, sedang, sulit)."""
    return batas[tingkat - 1]


def q_tambah(tk):
    m = _ri(tk, 20, 200, 2000)
    a, b = random.randint(2, m), random.randint(2, m)
    return f"{a} + {b} = ?", D(a + b), f"{a} + {b} = {a + b}"


def q_kurang(tk):
    m = _ri(tk, 20, 200, 2000)
    a, b = sorted((random.randint(2, m), random.randint(2, m)), reverse=True)
    return f"{a} - {b} = ?", D(a - b), f"{a} - {b} = {a - b}"


def q_kali(tk):
    m = _ri(tk, 9, 15, 50)
    a, b = random.randint(2, m), random.randint(2, m)
    return f"{a} x {b} = ?", D(a * b), f"{a} x {b} = {a * b}"


def q_bagi(tk):
    b = random.randint(2, _ri(tk, 9, 15, 30))
    q = random.randint(2, _ri(tk, 10, 20, 40))
    return f"{b * q} : {b} = ?", D(q), f"{b * q} : {b} = {q}, karena {q} x {b} = {b * q}"


def q_pangkat(tk):
    a = random.randint(2, _ri(tk, 5, 9, 12))
    n = random.randint(2, _ri(tk, 3, 4, 5))
    uraian = " x ".join([str(a)] * n)
    return f"{a}^{n} = ?", D(a ** n), f"{a}^{n} = {uraian} = {a ** n}"


def q_sifat_pangkat(tk):
    a = random.randint(2, 9)
    jenis = random.choice(("kali", "bagi", "pangkat"))
    if jenis == "kali":
        m, n = random.randint(2, 9), random.randint(2, 9)
        return (f"Jika {a}^{m} x {a}^{n} = {a}^k, berapa k?", D(m + n),
                f"Basis sama, pangkat dijumlahkan: k = {m} + {n} = {m + n}")
    if jenis == "bagi":
        m, n = sorted((random.randint(2, 12), random.randint(2, 9)), reverse=True)
        return (f"Jika {a}^{m} : {a}^{n} = {a}^k, berapa k?", D(m - n),
                f"Basis sama, pangkat dikurangkan: k = {m} - {n} = {m - n}")
    m, n = random.randint(2, 6), random.randint(2, 6)
    return (f"Jika ({a}^{m})^{n} = {a}^k, berapa k?", D(m * n),
            f"Pangkat dikalikan: k = {m} x {n} = {m * n}")


def q_akar_kuadrat(tk):
    s = random.randint(2, _ri(tk, 12, 25, 50))
    return (f"Akar kuadrat dari {s * s} = ?", D(s),
            f"{s} x {s} = {s * s}, jadi akar {s * s} = {s}")


def q_akar_kubik(tk):
    s = random.randint(2, _ri(tk, 5, 9, 15))
    return (f"Akar pangkat tiga dari {s ** 3} = ?", D(s),
            f"{s} x {s} x {s} = {s ** 3}, jadi akar pangkat tiga dari {s ** 3} = {s}")


def q_luas_persegi(tk):
    s = random.randint(2, _ri(tk, 12, 30, 90))
    return (f"Luas persegi dengan sisi {s} cm (dalam cm^2)?", D(s * s),
            f"L = sisi x sisi = {s} x {s} = {s * s}")


def q_luas_pp(tk):
    m = _ri(tk, 15, 40, 120)
    p, l = random.randint(3, m), random.randint(2, m)
    return (f"Luas persegi panjang dengan panjang {p} cm dan lebar {l} cm?", D(p * l),
            f"L = p x l = {p} x {l} = {p * l}")


def q_luas_segitiga(tk):
    a = 2 * random.randint(1, _ri(tk, 8, 20, 50))
    t = random.randint(2, _ri(tk, 15, 40, 100))
    return (f"Luas segitiga dengan alas {a} cm dan tinggi {t} cm?", D(a * t // 2),
            f"L = 1/2 x alas x tinggi = 1/2 x {a} x {t} = {a * t // 2}")


def q_luas_trapesium(tk):
    m = _ri(tk, 12, 30, 80)
    a, b, t = random.randint(2, m), random.randint(2, m), 2 * random.randint(1, m // 2)
    return (f"Luas trapesium dengan sisi sejajar {a} cm dan {b} cm, tinggi {t} cm?",
            D((a + b) * t // 2),
            f"L = 1/2 x (a + b) x t = 1/2 x ({a} + {b}) x {t} = {(a + b) * t // 2}")


def q_luas_belah_ketupat(tk):
    d1 = 2 * random.randint(1, _ri(tk, 8, 20, 50))
    d2 = random.randint(2, _ri(tk, 15, 40, 100))
    return (f"Luas belah ketupat dengan diagonal {d1} cm dan {d2} cm?", D(d1 * d2 // 2),
            f"L = 1/2 x d1 x d2 = 1/2 x {d1} x {d2} = {d1 * d2 // 2}")


def q_keliling_pp(tk):
    m = _ri(tk, 15, 40, 120)
    p, l = random.randint(3, m), random.randint(2, m)
    return (f"Keliling persegi panjang dengan panjang {p} cm dan lebar {l} cm?",
            D(2 * (p + l)), f"K = 2 x (p + l) = 2 x ({p} + {l}) = {2 * (p + l)}")


def q_lingkaran(tk):
    k = random.randint(1, _ri(tk, 2, 3, 6))
    r = 7 * k
    if random.random() < 0.5:
        return (f"Keliling lingkaran berjari-jari {r} cm (pi = 22/7)?", D(44 * k),
                f"K = 2 x pi x r = 2 x 22/7 x {r} = {44 * k}")
    return (f"Luas lingkaran berjari-jari {r} cm (pi = 22/7), dalam cm^2?", D(154 * k * k),
            f"L = pi x r^2 = 22/7 x {r} x {r} = {154 * k * k}")


def q_volume_kubus(tk):
    s = random.randint(2, _ri(tk, 6, 12, 25))
    return (f"Volume kubus dengan rusuk {s} cm (dalam cm^3)?", D(s ** 3),
            f"V = s^3 = {s} x {s} x {s} = {s ** 3}")


def q_volume_balok(tk):
    m = _ri(tk, 8, 15, 30)
    p, l, t = (random.randint(2, m) for _ in range(3))
    return (f"Volume balok {p} cm x {l} cm x {t} cm (dalam cm^3)?", D(p * l * t),
            f"V = p x l x t = {p} x {l} x {t} = {p * l * t}")


def q_luas_permukaan_kubus(tk):
    s = random.randint(2, _ri(tk, 6, 12, 25))
    return (f"Luas permukaan kubus dengan rusuk {s} cm (dalam cm^2)?", D(6 * s * s),
            f"LP = 6 x s^2 = 6 x {s} x {s} = {6 * s * s}")


def q_volume_tabung(tk):
    t = random.randint(2, _ri(tk, 10, 25, 60))
    return (f"Volume tabung berjari-jari 7 cm dan tinggi {t} cm (pi = 22/7, dalam cm^3)?",
            D(154 * t), f"V = pi x r^2 x t = 22/7 x 49 x {t} = 154 x {t} = {154 * t}")


def q_volume_kerucut(tk):
    k = random.randint(1, _ri(tk, 4, 8, 15))
    t = 3 * k
    return (f"Volume kerucut berjari-jari 7 cm dan tinggi {t} cm (pi = 22/7, dalam cm^3)?",
            D(154 * k),
            f"V = 1/3 x pi x r^2 x t = 1/3 x 22/7 x 49 x {t} = {154 * k}")


TRIPEL = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (20, 21, 29)]


def q_pyth_miring(tk):
    a, b, c = random.choice(TRIPEL)
    k = random.randint(1, _ri(tk, 1, 2, 4))
    a, b, c = a * k, b * k, c * k
    return (f"Segitiga siku-siku dengan sisi tegak {a} cm dan {b} cm. Panjang sisi miringnya?",
            D(c), f"c = akar({a}^2 + {b}^2) = akar({a * a} + {b * b}) = akar({c * c}) = {c}")


def q_pyth_tegak(tk):
    a, b, c = random.choice(TRIPEL)
    k = random.randint(1, _ri(tk, 1, 2, 4))
    a, b, c = a * k, b * k, c * k
    return (f"Segitiga siku-siku dengan sisi miring {c} cm dan salah satu sisi tegak "
            f"{a} cm. Panjang sisi tegak yang lain?", D(b),
            f"b = akar({c}^2 - {a}^2) = akar({c * c} - {a * a}) = akar({b * b}) = {b}")


def q_fpb_kpk(tk):
    g = random.randint(2, _ri(tk, 6, 12, 20))
    while True:
        x, y = random.randint(2, _ri(tk, 6, 9, 15)), random.randint(2, _ri(tk, 6, 9, 15))
        if x != y and math.gcd(x, y) == 1:
            break
    a, b = g * x, g * y
    if random.random() < 0.5:
        return (f"FPB dari {a} dan {b}?", D(g),
                f"{a} = {g} x {x} dan {b} = {g} x {y}; {x} dan {y} tidak punya faktor "
                f"sama selain 1, jadi FPB = {g}")
    return (f"KPK dari {a} dan {b}?", D(g * x * y),
            f"FPB = {g}, KPK = a x b / FPB = {a} x {b} / {g} = {g * x * y}")


def q_persen_dari(tk):
    p = random.choice((5, 10, 20, 25, 40, 50, 75))
    y = 20 * random.randint(1, _ri(tk, 10, 50, 200))
    return (f"{p}% dari {y} = ?", D(p * y // 100),
            f"{p}% dari {y} = {p}/100 x {y} = {p * y // 100}")


def q_diskon(tk):
    harga = 1000 * random.randint(10, _ri(tk, 50, 200, 900))
    d = random.choice((10, 20, 25, 30, 40, 50))
    akhir = harga * (100 - d) // 100
    return (f"Harga barang Rp {harga} didiskon {d}%. Berapa harga yang dibayar (Rp)?",
            D(akhir), f"Bayar = {100 - d}% x {harga} = {akhir}")


def q_naik_persen(tk):
    awal = 20 * random.randint(5, _ri(tk, 50, 200, 800))
    p = random.choice((10, 20, 25, 50))
    akhir = awal * (100 + p) // 100
    return (f"Harga naik dari Rp {awal} menjadi Rp {akhir}. Berapa persen kenaikannya?",
            D(p), f"Kenaikan = {akhir} - {awal} = {akhir - awal}; "
                  f"{akhir - awal} / {awal} x 100% = {p}%")


def q_mean(tk):
    n = random.choice((4, 5, 6))
    data = [random.randint(1, _ri(tk, 20, 50, 100)) for _ in range(n - 1)]
    sisa = (-sum(data)) % n
    data.append(sisa if sisa else n)
    return (f"Rata-rata dari data: {' '.join(map(str, data))} ?", D(sum(data) // n),
            f"Jumlah = {sum(data)}, banyak data = {n}, rata-rata = {sum(data)} / {n} = "
            f"{sum(data) // n}")


def q_median(tk):
    n = random.choice((5, 7))
    data = random.sample(range(1, _ri(tk, 30, 60, 120)), n)
    urut = sorted(data)
    return (f"Median dari data: {' '.join(map(str, data))} ?", D(urut[n // 2]),
            f"Data terurut: {' '.join(map(str, urut))}; nilai tengahnya = {urut[n // 2]}")


def q_jangkauan(tk):
    data = [random.randint(1, _ri(tk, 30, 80, 200)) for _ in range(random.choice((5, 6)))]
    return (f"Jangkauan dari data: {' '.join(map(str, data))} ?", D(max(data) - min(data)),
            f"Jangkauan = terbesar - terkecil = {max(data)} - {min(data)} = "
            f"{max(data) - min(data)}")


def q_modus(tk):
    lain = random.sample(range(1, _ri(tk, 30, 60, 120)), 4)
    m = lain.pop()
    data = lain + [m, m, m]
    random.shuffle(data)
    return (f"Modus dari data: {' '.join(map(str, data))} ?", D(m),
            f"Nilai yang paling sering muncul adalah {m} (3 kali)")


def q_faktorial(tk):
    n = random.randint(3, _ri(tk, 5, 6, 8))
    uraian = " x ".join(str(i) for i in range(n, 0, -1))
    return f"{n}! = ?", D(math.factorial(n)), f"{n}! = {uraian} = {math.factorial(n)}"


def q_kombinasi(tk):
    n = random.randint(4, _ri(tk, 6, 8, 10))
    r = random.randint(2, n - 2)
    return (f"Berapa nilai {n}C{r} (kombinasi {r} unsur dari {n} unsur)?",
            D(math.comb(n, r) if hasattr(math, "comb")
              else math.factorial(n) // (math.factorial(r) * math.factorial(n - r))),
            f"{n}C{r} = {n}! / ({r}! x {n - r}!) = "
            f"{math.factorial(n) // (math.factorial(r) * math.factorial(n - r))}")


def q_permutasi(tk):
    n = random.randint(4, _ri(tk, 6, 8, 10))
    r = random.randint(2, min(4, n - 1))
    hasil = math.factorial(n) // math.factorial(n - r)
    return (f"Berapa nilai {n}P{r} (permutasi {r} unsur dari {n} unsur)?", D(hasil),
            f"{n}P{r} = {n}! / {n - r}! = {hasil}")


def q_logaritma(tk):
    b = random.choice((2, 3, 5, 10))
    k = random.randint(2, _ri(tk, 4, 5, 6))
    return (f"{b} log {b ** k} = ?", D(k), f"{b}^{k} = {b ** k}, jadi {b} log {b ** k} = {k}")


def q_linear(tk):
    x = random.choice([v for v in range(-10, 11) if v])
    a = random.randint(2, _ri(tk, 5, 9, 15))
    b = random.randint(-_ri(tk, 9, 20, 50), _ri(tk, 9, 20, 50))
    c = a * x + b
    sb = f"+ {b}" if b >= 0 else f"- {-b}"
    return (f"Selesaikan {a}x {sb} = {c}. Berapa x?", D(x),
            f"{a}x = {c} - ({b}) = {c - b}, jadi x = {c - b} / {a} = {x}")


def q_kuadrat(tk):
    r1, r2 = random.sample(range(-5, 10), 2)
    b, c = -(r1 + r2), r1 * r2
    suku = "x^2"
    if b:
        suku += f" {'+' if b > 0 else '-'} {'' if abs(b) == 1 else abs(b)}x"
    if c:
        suku += f" {'+' if c > 0 else '-'} {abs(c)}"
    return (f"Akar terbesar dari {suku} = 0 ?", D(max(r1, r2)),
            f"Cari dua bilangan yang jumlahnya {-b} dan hasil kalinya {c}: "
            f"{r1} dan {r2}. Akar terbesar = {max(r1, r2)}")


TOPIK_KUIS = [
    ("Aritmatika dasar", [q_tambah, q_kurang, q_kali, q_bagi]),
    ("Perpangkatan dan akar", [q_pangkat, q_sifat_pangkat, q_akar_kuadrat, q_akar_kubik]),
    ("Bangun datar", [q_luas_persegi, q_luas_pp, q_luas_segitiga, q_luas_trapesium,
                      q_luas_belah_ketupat, q_keliling_pp, q_lingkaran]),
    ("Bangun ruang", [q_volume_kubus, q_volume_balok, q_luas_permukaan_kubus,
                      q_volume_tabung, q_volume_kerucut]),
    ("Pythagoras", [q_pyth_miring, q_pyth_tegak]),
    ("FPB dan KPK", [q_fpb_kpk]),
    ("Persen dan diskon", [q_persen_dari, q_diskon, q_naik_persen]),
    ("Statistika", [q_mean, q_median, q_jangkauan, q_modus]),
    ("Peluang dan logaritma", [q_faktorial, q_kombinasi, q_permutasi, q_logaritma]),
    ("Aljabar", [q_linear, q_kuadrat]),
]
NAMA_CAMPURAN = "Campuran (semua topik)"


def baca_jawaban(teks):
    """Ubah jawaban user jadi Decimal. Boleh '3,5', '3.5', atau pecahan '7/2'."""
    teks = teks.strip().replace(",", ".").replace(" ", "")
    try:
        if "/" in teks:
            atas, bawah = teks.split("/")
            return Decimal(atas) / Decimal(bawah)
        angka = Decimal(teks)
        return angka if angka.is_finite() else None
    except (InvalidOperation, ValueError, ZeroDivisionError, ArithmeticError):
        return None


def jalankan_kuis(nama, pembuat, tingkat, jumlah):
    benar = terjawab = 0
    dipakai = set()
    for no in range(1, jumlah + 1):
        for _ in range(20):
            soal, jawaban, bahas = random.choice(pembuat)(tingkat)
            if soal not in dipakai:
                break
        dipakai.add(soal)
        print()
        bot(f"Soal {no}/{jumlah}: {soal}")
        berhenti = lewat = False
        nilai = None
        while True:
            teks = input("Jawabanmu: ").strip()
            if teks.lower() in ("stop", "selesai"):
                berhenti = True
                break
            if teks.lower() in ("lewat", "skip", "menyerah"):
                lewat = True
                break
            nilai = baca_jawaban(teks)
            if nilai is not None:
                break
            bot("Jawab dengan angka (contoh: 12, 3,5, atau 7/2). 'lewat' untuk "
                "melewati soal, 'stop' untuk mengakhiri kuis.")
        if berhenti:
            break
        terjawab += 1
        toleransi = max(Decimal("0.01"), abs(jawaban) * Decimal("0.0001"))
        if not lewat and abs(nilai - jawaban) <= toleransi:
            benar += 1
            bot("Benar! Kerja bagus.")
        else:
            bot(f"{'Dilewati' if lewat else 'Belum tepat'}. Jawaban yang benar: {fa(jawaban)}")
            print(textwrap.fill("Pembahasan: " + bahas, width=64, initial_indent="   ",
                                subsequent_indent="   "))
    print()
    if terjawab == 0:
        bot("Kuis dihentikan sebelum ada soal yang dijawab.")
        return
    persen = round(100 * benar / terjawab)
    bot(f"Selesai! Skor kamu: {benar} dari {terjawab} soal ({persen}%).")
    bot("Luar biasa!" if persen >= 90 else "Bagus, terus berlatih!" if persen >= 70
        else "Lumayan. Baca lagi materinya di menu matematika, lalu coba lagi!"
        if persen >= 40 else "Jangan menyerah! Pelajari materinya di menu "
                             "matematika lalu ulangi kuisnya ya.")
    if terjawab >= 3:
        siapkan_data()
        catatan = DATA["skor_kuis"].setdefault(nama, {"terbaik": 0, "main": 0})
        catatan["main"] += 1
        if persen > catatan["terbaik"]:
            if catatan["main"] > 1:
                bot(f"Rekor baru untuk topik ini! (sebelumnya {catatan['terbaik']}%)")
            catatan["terbaik"] = persen
        simpan_data()


def mode_kuis():
    semua = [g for _, daftar in TOPIK_KUIS for g in daftar]
    pilihan = [(NAMA_CAMPURAN, semua)] + TOPIK_KUIS
    bot("Kuis latihan soal: soalnya acak, jawab dengan angka, lalu dapat skor dan "
        "pembahasan.")
    while True:
        siapkan_data()
        label = []
        for nama, _ in pilihan:
            rekor = DATA["skor_kuis"].get(nama, {}).get("terbaik")
            label.append(nama + (f"   [rekor {rekor}%]" if rekor is not None else ""))
        pilih = tampil_menu("Kuis Latihan Soal", label)
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(pilihan))
        if n is None:
            continue
        nama, pembuat = pilihan[n - 1]
        pilih = tampil_menu("Tingkat Kesulitan", ["Mudah", "Sedang", "Sulit"])
        tingkat = ambil_nomor(pilih, 3) if pilih != "0" else None
        if tingkat is None:
            continue
        teks = input("AIX: berapa soal? (1-30, kosong = 5): ").strip()
        if teks == "":
            jumlah = 5
        elif teks.isdigit() and 1 <= int(teks) <= 30:
            jumlah = int(teks)
        else:
            bot("Jumlah soal harus 1 sampai 30.")
            continue
        bot("Mulai! (ketik 'lewat' untuk melewati soal, 'stop' untuk mengakhiri)")
        jalankan_kuis(nama, pembuat, tingkat, jumlah)


# ------------------------------------------------------- fitur 5: translate
BAHASA = {
    "1": ("Indonesia", "id"),
    "2": ("Inggris", "en"),
    "3": ("Rusia", "ru"),
    "4": ("Jepang", "ja"),
    "5": ("Jerman", "de"),
    "6": ("Arab", "ar"),
}

HURUF_RUSIA = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def latinkan_rusia(teks):
    """Tulisan Cyrillic -> huruf latin (cara baca)."""
    hasil = []
    for huruf in teks:
        kecil = huruf.lower()
        if kecil in HURUF_RUSIA:
            latin = HURUF_RUSIA[kecil]
            hasil.append(latin.capitalize() if huruf != kecil else latin)
        else:
            hasil.append(huruf)
    return "".join(hasil)


def latinkan_jepang(teks):
    """Romaji lewat library 'pykakasi' (opsional). None kalau tidak tersedia."""
    try:
        import pykakasi
        potongan = pykakasi.kakasi().convert(teks)
        return " ".join(item["hepburn"] for item in potongan).strip() or None
    except Exception:
        return None


NAMA_BAHASA = {kode: nama for nama, kode in BAHASA.values()}
KODE_MYMEMORY = {"id": "id-ID", "en": "en-GB", "ru": "ru-RU", "ja": "ja-JP",
                 "de": "de-DE", "ar": "ar-SA"}


class GagalTerjemah(Exception):
    """Terjemahan gagal. Isi pesan = alasan yang boleh ditampilkan ke user."""


def ada_modul(nama):
    """Cek library sudah terpasang atau belum (tanpa mengimpornya)."""
    try:
        return importlib.util.find_spec(nama) is not None
    except (ImportError, ValueError):
        return False


def nama_pasangan(pasangan):
    return f"{NAMA_BAHASA[pasangan[0]]} -> {NAMA_BAHASA[pasangan[1]]}"


def pilih_bahasa(prompt, boleh_otomatis=False):
    """Kembalikan (nama, kode), atau None kalau user ingin kembali ke menu."""
    while True:
        pilih = input(prompt).strip().lower()
        if pilih in ("menu", "/menu", "kembali", "batal"):
            return None
        if boleh_otomatis and pilih == "0":
            return ("Otomatis", "auto")
        if pilih in BAHASA:
            return BAHASA[pilih]
        bot("Pilihan tidak valid.")


# ---- mesin online (Google Translate lewat deep-translator)
def terjemah_online(teks, kode_asal, kode_tujuan):
    from deep_translator import GoogleTranslator
    try:
        return GoogleTranslator(source=kode_asal, target=kode_tujuan).translate(teks)
    except Exception:
        if kode_asal == "auto" or kode_asal not in KODE_MYMEMORY \
                or kode_tujuan not in KODE_MYMEMORY:
            raise
        from deep_translator import MyMemoryTranslator
        return MyMemoryTranslator(source=KODE_MYMEMORY[kode_asal],
                                  target=KODE_MYMEMORY[kode_tujuan]).translate(teks)


# ---- mesin offline (Argos Translate)
def pasangan_terpasang():
    """Kumpulan pasangan bahasa offline yang sudah terpasang, misal ('id', 'en')."""
    from argostranslate import package
    return {(p.from_code, p.to_code) for p in package.get_installed_packages()}


def paket_kurang(kode_asal, kode_tujuan):
    """Paket yang masih harus diunduh. Bahasa non-Inggris diterjemahkan lewat
    bahasa Inggris (Argos otomatis 'melompat' lewat Inggris)."""
    perlu = []
    if kode_asal != "en":
        perlu.append((kode_asal, "en"))
    if kode_tujuan != "en":
        perlu.append(("en", kode_tujuan))
    terpasang = pasangan_terpasang()
    return [p for p in perlu if p not in terpasang]


def terjemah_offline(teks, kode_asal, kode_tujuan):
    if kode_asal == "auto":
        raise GagalTerjemah("mode offline tidak bisa mendeteksi bahasa otomatis, "
                            "pilih bahasa asal 1-5")
    kurang = paket_kurang(kode_asal, kode_tujuan)
    if kurang:
        daftar = ", ".join(nama_pasangan(p) for p in kurang)
        raise GagalTerjemah(f"paket offline belum diunduh ({daftar}). Unduh lewat "
                            "menu 'Kelola paket bahasa offline'")
    from argostranslate import translate
    bahasa = {b.code: b for b in translate.get_installed_languages()}
    asal, tujuan = bahasa.get(kode_asal), bahasa.get(kode_tujuan)
    terjemahan = asal.get_translation(tujuan) if asal and tujuan else None
    if terjemahan is None:
        raise GagalTerjemah("pasangan bahasa ini belum bisa dipakai secara offline")
    return terjemahan.translate(teks)


def terjemahkan(teks, kode_asal, kode_tujuan, mesin, asal_offline=None):
    """mesin: 'online', 'offline', atau 'otomatis' (online dulu, gagal -> offline).
    asal_offline: bahasa asal yang dianggap untuk mesin offline kalau kode_asal
    'auto' (mesin offline tidak bisa mendeteksi bahasa).
    Mengembalikan (hasil, mesin_yang_dipakai). Melempar GagalTerjemah."""
    alasan = []
    if mesin in ("online", "otomatis"):
        try:
            return terjemah_online(teks, kode_asal, kode_tujuan), "online"
        except ImportError:
            alasan.append("library deep-translator belum terpasang")
        except Exception:
            alasan.append("koneksi internet bermasalah")
    if mesin in ("offline", "otomatis"):
        try:
            asal = asal_offline if kode_asal == "auto" and asal_offline else kode_asal
            return terjemah_offline(teks, asal, kode_tujuan), "offline"
        except GagalTerjemah as err:
            alasan.append(str(err))
        except ImportError:
            alasan.append("library argostranslate belum terpasang")
        except Exception as err:
            alasan.append(f"mesin offline bermasalah ({type(err).__name__})")
    if mesin in ("kamus", "otomatis"):
        try:
            return terjemah_kamus(teks, kode_asal, kode_tujuan), "kamus"
        except GagalTerjemah as err:
            alasan.append(str(err))
    raise GagalTerjemah("; ".join(alasan))


# ---- kelola paket bahasa offline
def konfirmasi(pesan):
    return input(f"AIX: {pesan} (y/n): ").strip().lower() in ("y", "ya", "yes")


def unduh_paket(daftar_kode):
    """Unduh paket (dua arah dengan Inggris) untuk tiap kode bahasa non-Inggris."""
    from argostranslate import package
    try:
        terpasang = pasangan_terpasang()
        perlu = [p for kode in daftar_kode for p in ((kode, "en"), ("en", kode))
                 if p not in terpasang]
        if not perlu:
            bot("Semua paket yang dipilih sudah terpasang.")
            return
        if not konfirmasi(f"Akan mengunduh {len(perlu)} paket (sekitar "
                          f"{len(perlu) * 100} MB, butuh internet). Lanjutkan?"):
            bot("Dibatalkan.")
            return
        bot("Mengambil daftar paket...")
        package.update_package_index()
        tersedia = package.get_available_packages()
    except Exception as err:
        bot(f"Gagal mengambil daftar paket ({type(err).__name__}). "
            "Cek koneksi internet kamu.")
        return
    for pasangan in perlu:
        paket = next((p for p in tersedia
                      if (p.from_code, p.to_code) == pasangan), None)
        if paket is None:
            bot(f"Paket {nama_pasangan(pasangan)} tidak ditemukan di daftar.")
            continue
        bot(f"Mengunduh {nama_pasangan(pasangan)} ... mohon tunggu.")
        try:
            package.install_from_path(paket.download())
            bot("  selesai.")
        except Exception as err:
            bot(f"  gagal ({type(err).__name__}). Coba lagi nanti.")


def kelola_paket_offline():
    if not ada_modul("argostranslate"):
        bot("Fitur offline butuh library 'argostranslate'. Install dulu:")
        bot("    pip install argostranslate   (Windows: py -m pip install argostranslate)")
        return
    lainnya = [(nama, kode) for nama, kode in BAHASA.values() if kode != "en"]
    while True:
        try:
            terpasang = pasangan_terpasang()
        except Exception as err:
            bot(f"Library argostranslate bermasalah ({type(err).__name__}).")
            return
        print()
        bot("Status paket offline (tiap bahasa = dua arah dengan Inggris):")
        for nama, kode in lainnya:
            ada = ((kode, "en") in terpasang) + (("en", kode) in terpasang)
            status = ["belum diunduh", "sebagian", "lengkap"][ada]
            print(f"   {nama:<10}: {status}")
        pilihan = [f"Unduh paket {nama} (2 paket, sekitar 200 MB)"
                   for nama, _ in lainnya]
        pilihan.append(f"Unduh SEMUA bahasa ({2 * len(lainnya)} paket, "
                       f"sekitar {200 * len(lainnya)} MB)")
        pilih = tampil_menu("Paket Bahasa Offline", pilihan)
        if pilih == "0":
            return
        n = ambil_nomor(pilih, len(pilihan))
        if n is None:
            continue
        kode_dipilih = [kode for _, kode in lainnya] if n == len(pilihan) \
            else [lainnya[n - 1][1]]
        unduh_paket(kode_dipilih)


# ---- terjemahkan teks
def pilih_mesin():
    print("Mesin terjemahan:")
    print("1. Otomatis (online dulu, kalau gagal offline, lalu kamus bawaan)")
    print("2. Online saja (Google, butuh internet)")
    print("3. Offline saja (Argos, tanpa internet)")
    print("4. Kamus bawaan (offline, tanpa install apa pun, kata/kalimat umum saja)")
    while True:
        pilih = input("AIX: pilih mesin (1-4, 'menu' = kembali): ").strip().lower()
        if pilih in ("menu", "/menu", "kembali", "batal"):
            return None
        if pilih in ("1", "2", "3", "4"):
            return {"1": "otomatis", "2": "online", "3": "offline", "4": "kamus"}[pilih]
        bot("Pilihan tidak valid.")


_PETUNJUK_KAMUS = [False]


def mesin_siap(mesin):
    """Cek library untuk mesin yang dipilih; beri petunjuk kalau belum ada."""
    if mesin == "kamus" and not _PETUNJUK_KAMUS[0]:
        _PETUNJUK_KAMUS[0] = True
        bot("Tips: baru pertama kali memakai kamus bawaan? Tutorialnya ada di "
            "menu Translate > Kamus bawaan > Tutorial.")
    online, offline = ada_modul("deep_translator"), ada_modul("argostranslate")
    petunjuk_online = ("Mesin online butuh library 'deep-translator'. Install dulu:\n"
                       "AIX:     pip install deep-translator   "
                       "(Windows: py -m pip install deep-translator)")
    petunjuk_offline = ("Mesin offline butuh library 'argostranslate'. Install dulu:\n"
                        "AIX:     pip install argostranslate   "
                        "(Windows: py -m pip install argostranslate)")
    if mesin == "online" and not online:
        bot(petunjuk_online)
        return False
    if mesin == "offline" and not offline:
        bot(petunjuk_offline)
        return False
    if mesin == "otomatis":
        if not online and not offline:
            bot("(Catatan: library online dan offline belum terpasang, jadi hanya "
                "kamus bawaan yang dipakai. Kata/kalimat yang dikenalinya terbatas. "
                "Tutorial: menu Translate > Kamus bawaan.)")
        elif not online:
            bot("(Catatan: library online belum terpasang, jadi memakai mesin "
                "offline dan kamus bawaan.)")
        elif not offline:
            bot("(Catatan: library offline belum terpasang, jadi memakai mesin "
                "online dan kamus bawaan.)")
    return True


def terjemahkan_teks():
    petunjuk_romaji = False
    while True:
        print()
        mesin = pilih_mesin()
        if mesin is None:
            return
        if not mesin_siap(mesin):
            return
        for nomor, (nama, _) in BAHASA.items():
            print(f"{nomor}. {nama}")
        otomatis_boleh = mesin != "offline"
        asal = pilih_bahasa(
            "AIX: bahasa asal (1-5" + (", 0 = deteksi otomatis" if otomatis_boleh
                                       else "") + ", 'menu' = kembali): ",
            boleh_otomatis=otomatis_boleh)
        if asal is None:
            return
        tujuan = pilih_bahasa("AIX: bahasa tujuan (1-5, 'menu' = kembali): ")
        if tujuan is None:
            return
        if asal[1] == tujuan[1]:
            bot("Bahasa asal dan tujuan sama. Pilih bahasa yang berbeda.")
            continue
        if mesin == "offline":
            kurang = paket_kurang(asal[1], tujuan[1])
            if kurang:
                bot("Paket offline yang belum ada: "
                    + ", ".join(nama_pasangan(p) for p in kurang) + ".")
                bot("Unduh dulu lewat menu 'Kelola paket bahasa offline' "
                    "(butuh internet sekali saja).")
                continue
            bot("(Terjemahan offline pertama kali bisa agak lama karena memuat model.)")

        bot(f"{asal[0]} -> {tujuan[0]}. Ketik teks yang mau diterjemahkan "
            "('/simpan' = simpan hasil ke kosakata, '/ganti' = ganti mesin/bahasa, "
            "'/menu' = kembali).")
        terakhir = None
        while True:
            teks = input("Kamu: ").strip()
            if not teks:
                continue
            if teks.lower() == "/menu":
                return
            if teks.lower() == "/ganti":
                break
            if teks.lower() == "/simpan":
                if terakhir is None:
                    bot("Belum ada terjemahan yang bisa disimpan.")
                elif simpan_kosakata(*terakhir):
                    bot("Disimpan ke kosakata. Latihan flashcard ada di menu 8.")
                else:
                    bot("Terjemahan itu sudah ada di kosakata.")
                continue
            if len(teks) > 4500:
                bot("Teksnya terlalu panjang (maksimal 4500 karakter).")
                continue
            try:
                hasil, dipakai = terjemahkan(teks, asal[1], tujuan[1], mesin)
            except GagalTerjemah as err:
                bot(f"Gagal menerjemahkan: {err}.")
                continue
            if not hasil:
                bot("Tidak ada hasil terjemahan. Coba teks yang lain.")
                continue
            bot(hasil)
            terakhir = (teks, hasil, asal[0], tujuan[0])
            if mesin == "otomatis" and dipakai == "offline":
                bot("(diterjemahkan secara offline)")
            elif mesin == "otomatis" and dipakai == "kamus":
                bot("(dari kamus bawaan)")
            if tujuan[1] == "ru":
                bot(f"Cara baca: {latinkan_rusia(hasil)}")
            elif tujuan[1] == "ja":
                romaji = romaji_jepang(hasil)
                if romaji:
                    bot(f"Cara baca (romaji): {romaji}")
                elif not petunjuk_romaji:
                    petunjuk_romaji = True
                    bot("(Tips: install 'pykakasi' (pip install pykakasi) supaya "
                        "romaji Jepang ikut ditampilkan.)")


def mode_translate():
    submenu("Translate", [
        ("Terjemahkan teks", terjemahkan_teks),
        ("Kelola paket bahasa offline (cek status / unduh)", kelola_paket_offline),
        ("Kamus bawaan (tutorial, latihan, daftar isi)", menu_kamus),
    ])


# ------------------------------------------------ kamus bawaan (offline, terbatas)
# (Indonesia, Inggris, Rusia, Jepang, romaji Jepang, Jerman)
KAMUS = [
    # sapaan dan sopan santun
    ("halo", "hello", "привет", "こんにちは", "konnichiwa", "hallo"),
    ("selamat pagi", "good morning", "доброе утро", "おはようございます", "ohayou gozaimasu", "guten Morgen"),
    ("selamat siang", "good afternoon", "добрый день", "こんにちは", "konnichiwa", "guten Tag"),
    ("selamat malam", "good evening", "добрый вечер", "こんばんは", "konbanwa", "guten Abend"),
    ("selamat tidur", "good night", "спокойной ночи", "おやすみなさい", "oyasuminasai", "gute Nacht"),
    ("selamat tinggal", "goodbye", "до свидания", "さようなら", "sayounara", "auf Wiedersehen"),
    ("sampai jumpa", "see you", "до встречи", "またね", "mata ne", "bis bald"),
    ("sampai besok", "see you tomorrow", "до завтра", "また明日", "mata ashita", "bis morgen"),
    ("terima kasih", "thank you", "спасибо", "ありがとう", "arigatou", "danke"),
    ("terima kasih banyak", "thank you very much", "большое спасибо", "どうもありがとうございます", "doumo arigatou gozaimasu", "vielen Dank"),
    ("sama-sama", "you're welcome", "пожалуйста", "どういたしまして", "dou itashimashite", "bitte schön"),
    ("maaf", "sorry", "прости", "ごめんなさい", "gomen nasai", "es tut mir leid"),
    ("permisi", "excuse me", "извините", "すみません", "sumimasen", "entschuldigen Sie"),
    ("tolong bantu saya", "please help me", "помогите мне, пожалуйста", "手伝ってください", "tetsudatte kudasai", "bitte helfen Sie mir"),
    ("selamat datang", "welcome", "добро пожаловать", "ようこそ", "youkoso", "willkommen"),
    ("selamat ulang tahun", "happy birthday", "с днём рождения", "お誕生日おめでとう", "otanjoubi omedetou", "alles Gute zum Geburtstag"),
    ("selamat", "congratulations", "поздравляю", "おめでとう", "omedetou", "herzlichen Glückwunsch"),
    ("semoga harimu menyenangkan", "have a nice day", "хорошего дня", "良い一日を", "yoi ichinichi o", "einen schönen Tag noch"),
    ("selamat makan", "bon appetit", "приятного аппетита", "いただきます", "itadakimasu", "guten Appetit"),
    ("hati-hati", "be careful", "осторожно", "気をつけて", "ki o tsukete", "Vorsicht"),
    ("ayo pergi", "let's go", "пошли", "行きましょう", "ikimashou", "gehen wir"),
    ("ya", "yes", "да", "はい", "hai", "ja"),
    ("tidak", "no", "нет", "いいえ", "iie", "nein"),
    ("mungkin", "maybe", "может быть", "たぶん", "tabun", "vielleicht"),
    # perkenalan
    ("apa kabar", "how are you", "как дела", "お元気ですか", "ogenki desu ka", "wie geht's"),
    ("saya baik-baik saja", "I am fine", "у меня всё хорошо", "元気です", "genki desu", "mir geht es gut"),
    ("siapa namamu", "what is your name", "как тебя зовут", "お名前は何ですか", "onamae wa nan desu ka", "wie heißt du"),
    ("senang bertemu denganmu", "nice to meet you", "приятно познакомиться", "はじめまして", "hajimemashite", "freut mich"),
    ("berapa umurmu", "how old are you", "сколько тебе лет", "何歳ですか", "nansai desu ka", "wie alt bist du"),
    ("saya berasal dari indonesia", "I am from Indonesia", "я из Индонезии", "私はインドネシア出身です", "watashi wa indoneshia shusshin desu", "ich komme aus Indonesien"),
    ("saya tidak mengerti", "I don't understand", "я не понимаю", "わかりません", "wakarimasen", "ich verstehe nicht"),
    ("saya tidak tahu", "I don't know", "я не знаю", "知りません", "shirimasen", "ich weiß nicht"),
    ("apakah kamu bisa berbahasa inggris", "do you speak English", "вы говорите по-английски", "英語を話せますか", "eigo o hanasemasu ka", "sprechen Sie Englisch"),
    # kebutuhan sehari-hari
    ("berapa harganya", "how much is it", "сколько это стоит", "いくらですか", "ikura desu ka", "wie viel kostet das"),
    ("di mana toilet", "where is the toilet", "где туалет", "トイレはどこですか", "toire wa doko desu ka", "wo ist die Toilette"),
    ("ini apa", "what is this", "что это", "これは何ですか", "kore wa nan desu ka", "was ist das"),
    ("saya lapar", "I am hungry", "я хочу есть", "お腹がすきました", "onaka ga sukimashita", "ich habe Hunger"),
    ("saya haus", "I am thirsty", "я хочу пить", "のどが渇きました", "nodo ga kawakimashita", "ich habe Durst"),
    ("saya cinta kamu", "I love you", "я люблю тебя", "あなたを愛しています", "anata o aishiteimasu", "ich liebe dich"),
    ("saya suka belajar", "I like to study", "я люблю учиться", "勉強が好きです", "benkyou ga suki desu", "ich lerne gern"),
    ("saya sedang belajar", "I am studying", "я учусь", "勉強しています", "benkyou shiteimasu", "ich lerne gerade"),
    ("saya belajar bahasa inggris", "I am learning English", "я изучаю английский язык", "私は英語を勉強しています", "watashi wa eigo o benkyou shiteimasu", "ich lerne Englisch"),
    ("saya ingin pergi ke sekolah", "I want to go to school", "я хочу пойти в школу", "学校に行きたいです", "gakkou ni ikitai desu", "ich möchte zur Schule gehen"),
    # kata tanya
    ("di mana", "where", "где", "どこ", "doko", "wo"),
    ("kapan", "when", "когда", "いつ", "itsu", "wann"),
    ("siapa", "who", "кто", "誰", "dare", "wer"),
    ("apa", "what", "что", "何", "nani", "was"),
    ("mengapa", "why", "почему", "なぜ", "naze", "warum"),
    ("bagaimana", "how", "как", "どう", "dou", "wie"),
    ("berapa", "how many", "сколько", "いくつ", "ikutsu", "wie viele"),
    # angka
    ("nol", "zero", "ноль", "零", "rei", "null"),
    ("satu", "one", "один", "一", "ichi", "eins"),
    ("dua", "two", "два", "二", "ni", "zwei"),
    ("tiga", "three", "три", "三", "san", "drei"),
    ("empat", "four", "четыре", "四", "yon", "vier"),
    ("lima", "five", "пять", "五", "go", "fünf"),
    ("enam", "six", "шесть", "六", "roku", "sechs"),
    ("tujuh", "seven", "семь", "七", "nana", "sieben"),
    ("delapan", "eight", "восемь", "八", "hachi", "acht"),
    ("sembilan", "nine", "девять", "九", "kyuu", "neun"),
    ("sepuluh", "ten", "десять", "十", "juu", "zehn"),
    # hari
    ("senin", "monday", "понедельник", "月曜日", "getsuyoubi", "Montag"),
    ("selasa", "tuesday", "вторник", "火曜日", "kayoubi", "Dienstag"),
    ("rabu", "wednesday", "среда", "水曜日", "suiyoubi", "Mittwoch"),
    ("kamis", "thursday", "четверг", "木曜日", "mokuyoubi", "Donnerstag"),
    ("jumat", "friday", "пятница", "金曜日", "kin'youbi", "Freitag"),
    ("sabtu", "saturday", "суббота", "土曜日", "doyoubi", "Samstag"),
    ("minggu", "sunday", "воскресенье", "日曜日", "nichiyoubi", "Sonntag"),
    # warna
    ("merah", "red", "красный", "赤", "aka", "rot"),
    ("biru", "blue", "синий", "青", "ao", "blau"),
    ("hijau", "green", "зелёный", "緑", "midori", "grün"),
    ("kuning", "yellow", "жёлтый", "黄色", "kiiro", "gelb"),
    ("hitam", "black", "чёрный", "黒", "kuro", "schwarz"),
    ("putih", "white", "белый", "白", "shiro", "weiß"),
    # orang
    ("ibu", "mother", "мама", "お母さん", "okaasan", "Mutter"),
    ("ayah", "father", "папа", "お父さん", "otousan", "Vater"),
    ("teman", "friend", "друг", "友達", "tomodachi", "Freund"),
    ("keluarga", "family", "семья", "家族", "kazoku", "Familie"),
    ("anak", "child", "ребёнок", "子供", "kodomo", "Kind"),
    ("guru", "teacher", "учитель", "先生", "sensei", "Lehrer"),
    ("murid", "student", "ученик", "生徒", "seito", "Schüler"),
    # makanan dan minuman
    ("air", "water", "вода", "水", "mizu", "Wasser"),
    ("nasi", "rice", "рис", "ご飯", "gohan", "Reis"),
    ("roti", "bread", "хлеб", "パン", "pan", "Brot"),
    ("kopi", "coffee", "кофе", "コーヒー", "koohii", "Kaffee"),
    ("teh", "tea", "чай", "お茶", "ocha", "Tee"),
    ("susu", "milk", "молоко", "牛乳", "gyuunyuu", "Milch"),
    ("ayam", "chicken", "курица", "鶏肉", "toriniku", "Hähnchen"),
    ("ikan", "fish", "рыба", "魚", "sakana", "Fisch"),
    ("apel", "apple", "яблоко", "りんご", "ringo", "Apfel"),
    ("pisang", "banana", "банан", "バナナ", "banana", "Banane"),
    ("makan", "eat", "есть", "食べる", "taberu", "essen"),
    ("minum", "drink", "пить", "飲む", "nomu", "trinken"),
    # tempat dan benda
    ("rumah", "house", "дом", "家", "ie", "Haus"),
    ("sekolah", "school", "школа", "学校", "gakkou", "Schule"),
    ("buku", "book", "книга", "本", "hon", "Buch"),
    ("mobil", "car", "машина", "車", "kuruma", "Auto"),
    ("sepeda", "bicycle", "велосипед", "自転車", "jitensha", "Fahrrad"),
    ("kota", "city", "город", "都市", "toshi", "Stadt"),
    ("negara", "country", "страна", "国", "kuni", "Land"),
    ("uang", "money", "деньги", "お金", "okane", "Geld"),
    ("waktu", "time", "время", "時間", "jikan", "Zeit"),
    # alam dan waktu
    ("hari", "day", "день", "日", "hi", "Tag"),
    ("pagi", "morning", "утро", "朝", "asa", "Morgen"),
    ("malam", "night", "ночь", "夜", "yoru", "Nacht"),
    ("matahari", "sun", "солнце", "太陽", "taiyou", "Sonne"),
    ("bulan", "moon", "луна", "月", "tsuki", "Mond"),
    ("hujan", "rain", "дождь", "雨", "ame", "Regen"),
    # sifat
    ("besar", "big", "большой", "大きい", "ookii", "groß"),
    ("kecil", "small", "маленький", "小さい", "chiisai", "klein"),
    ("baru", "new", "новый", "新しい", "atarashii", "neu"),
    ("panas", "hot", "жаркий", "暑い", "atsui", "heiß"),
    ("dingin", "cold", "холодный", "寒い", "samui", "kalt"),
    ("cantik", "beautiful", "красивый", "美しい", "utsukushii", "schön"),
    ("senang", "happy", "счастливый", "嬉しい", "ureshii", "glücklich"),
    ("sedih", "sad", "грустный", "悲しい", "kanashii", "traurig"),
]

IDX_KAMUS = {"id": 0, "en": 1, "ru": 2, "ja": 3, "de": 5}
ROMAJI_KAMUS = {e[3]: e[4] for e in KAMUS}


def norm_kamus(teks, kode):
    """Samakan teks untuk pencarian: huruf kecil, tanpa tanda baca."""
    t = teks.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t).replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t.replace(" ", "") if kode == "ja" else t


INDEKS_KAMUS = {}
for _i, _e in enumerate(KAMUS):
    for _kode, _j in IDX_KAMUS.items():
        INDEKS_KAMUS.setdefault((_kode, norm_kamus(_e[_j], _kode)), _i)


def saran_kamus(teks, kode_asal):
    """Kata/kalimat di kamus yang mirip dengan teks (untuk saran 'maksudmu')."""
    saran = []
    for kode in ([kode_asal] if kode_asal != "auto" else list(IDX_KAMUS)):
        kunci = [k for (b, k) in INDEKS_KAMUS if b == kode]
        for m in difflib.get_close_matches(norm_kamus(teks, kode), kunci, n=3,
                                           cutoff=0.85):
            asli = KAMUS[INDEKS_KAMUS[(kode, m)]][IDX_KAMUS[kode]]
            if asli not in saran:
                saran.append(asli)
    return saran[:3]


# ---- kamus frasa tambahan (dari AIX 3.0): untuk kata/frasa yang persis cocok
KAMUS_FRASA_ID_EN = {
    "assalamualaikum": "assalamu alaikum",
    "wassalamualaikum": "wassalamu alaikum",
    "waalaikumsalam": "wa alaikumussalam",
    "wa'alaikumussalam": "wa alaikumussalam",
    "halo": "hello", "hai": "hi", "hello": "hello", "hi": "hi",
    "selamat pagi": "good morning", "selamat siang": "good afternoon",
    "selamat sore": "good evening", "selamat malam": "good night",
    "selamat tidur": "good night / sweet dreams",
    "selamat tinggal": "goodbye",
    "apa kabar": "how are you", "kabarmu": "how are you",
    "kabar": "hello / how are you", "kamu baik": "you are good",
    "kabarnya": "how are you doing",
    "sedih": "sad", "senang": "happy", "bahagia": "glad",
    "bosan": "bored", "capek": "tired", "lelah": "exhausted",
    "gembira": "joyful", "sepi": "lonely", "sepiin": "lonely",
    "galau": "troubled / confused", "stres": "stressed",
    "depresi": "depressed", "cemas": "anxious", "takut": "scared",
    "pusing": "dizzy / confused", "kecewa": "disappointed",
    "putus asa": "hopeless", "pride": "proud",
    "syukur": "grateful", "sulit": "hard / difficult", "mudah": "easy",
    "santai": "chill", "riang": "cheerful",
    "makasih": "thanks", "terima kasih": "thank you",
    "trims": "thanks", "tq": "thanks", "thx": "thanks",
    "yaudah": "okay then", "oke": "okay", "ya": "yes / okay",
    "tidak": "no", "nope": "no", "iya": "yes", "yoi": "yeah / yes",
    "silahkan": "please go ahead", "boleh": "may / okay",
    "siap": "ready", "baik": "good",
    "belajar": "study / learning", "matematika": "mathematics",
    "hitungan": "calculation", "kalkulator": "calculator", "kuis": "quiz",
    "tanya": "question", "jawaban": "answer", "soal": "problem / question",
    "benar": "correct / right", "salah": "wrong / incorrect",
    "sangat": "very", "selalu": "always", "jarang": "rarely",
    "sering": "often", "minggu": "week", "hari": "day",
    "bulan": "month", "tahun": "year", "jam": "hour / clock",
    "menit": "minute", "detik": "second",
}
KAMUS_FRASA_EN_ID = {
    "hello": "halo", "hi": "hai", "hey": "hei",
    "good morning": "selamat pagi", "good afternoon": "selamat siang",
    "good evening": "selamat sore", "good night": "selamat malam",
    "goodbye": "selamat tinggal", "bye": "selamat tinggal",
    "how are you": "apa kabar", "how are you doing": "kabarnya",
    "fine": "baik / saja", "good": "baik", "bad": "jelek / buruk",
    "thank you": "terima kasih", "thanks": "terima kasih / makasih",
    "you are welcome": "sama-sama",
    "sorry": "maaf", "excuse me": "permisi",
    "yes": "iya / ya", "no": "tidak", "okay": "oke / baik",
    "please": "tolong / silahkan",
    "i am fine": "aku baik", "i am fine thanks": "aku baik makasih",
    "sad": "sedih", "happy": "senang", "glad": "bahagia",
    "joyful": "gembira", "cheerful": "riang",
    "bored": "bosan", "lonely": "sepi", "tired": "capek",
    "exhausted": "lelah", "troubled": "galau", "confused": "pusing",
    "stressed": "stres", "anxious": "cemas",
    "scared": "takut", "disappointed": "kecewa", "hopeless": "putus asa",
    "depressed": "depresi", "proud": "bangga", "grateful": "syukur",
    "study": "belajar", "learning": "belajar / pembelajaran",
    "mathematics": "matematika", "calculation": "hitungan",
    "calculator": "kalkulator", "quiz": "kuis",
    "problem": "soal / masalah", "question": "tanya",
    "answer": "jawaban", "correct": "benar", "right": "benar",
    "wrong": "salah", "easy": "mudah",
    "hard": "sulit", "difficult": "sulit",
    "see you": "sampai jumpa", "see you later": "sampai jumpa nanti",
    "good night": "selamat malam", "good morning everyone": "selamat pagi semua",
}


def _cari_frasa(teks, asal, tujuan):
    kunci = " ".join(re.findall(r"[a-zA-Z0-9'\-]+", teks.lower()))
    kamus = []
    if asal in ("id", "auto") and tujuan == "en":
        kamus.append(KAMUS_FRASA_ID_EN)
    if asal in ("en", "auto") and tujuan == "id":
        kamus.append(KAMUS_FRASA_EN_ID)
    for k in kamus:
        if kunci in k:
            return k[kunci]
    return None


def terjemah_kamus(teks, kode_asal, kode_tujuan):
    if kode_tujuan not in IDX_KAMUS:
        raise GagalTerjemah("kamus bawaan belum punya bahasa itu (Arab hanya lewat online)")
    """Terjemah dari kamus bawaan. Mendukung deteksi bahasa ('auto')."""
    for kode in ([kode_asal] if kode_asal != "auto" else list(IDX_KAMUS)):
        i = INDEKS_KAMUS.get((kode, norm_kamus(teks, kode)))
        if i is not None:
            hasil = KAMUS[i][IDX_KAMUS[kode_tujuan]]
            return hasil[:1].upper() + hasil[1:]
    frasa = _cari_frasa(teks, kode_asal, kode_tujuan)
    if frasa:
        return frasa
    saran = saran_kamus(teks, kode_asal)
    pesan = "kata/kalimat itu belum ada di kamus bawaan"
    if saran:
        pesan += ". Mungkin maksudmu: " + ", ".join(f"'{s}'" for s in saran)
    raise GagalTerjemah(pesan)


def romaji_jepang(teks):
    """Romaji: dari kamus bawaan dulu, kalau tidak ada pakai pykakasi."""
    return ROMAJI_KAMUS.get(teks) or latinkan_jepang(teks)


def lihat_kamus():
    bot(f"Kamus bawaan berisi {len(KAMUS)} kata/kalimat umum. Pilih bahasa untuk "
        "melihat daftar yang dikenali:")
    for nomor, (nama, _) in BAHASA.items():
        print(f"{nomor}. {nama}")
    bahasa = pilih_bahasa("AIX: bahasa (1-6, 'menu' = kembali): ")
    if bahasa is None:
        return
    if bahasa[1] not in IDX_KAMUS:
        bot("Kamus bawaan belum memuat bahasa itu (Arab hanya lewat online).")
        return
    j = IDX_KAMUS[bahasa[1]]
    daftar = " | ".join(dict.fromkeys(e[j] for e in KAMUS))
    print(textwrap.fill(daftar, width=64, initial_indent="   ", subsequent_indent="   "))


def kartu_kamus(teks):
    """Cari teks (bahasa apa pun) di kamus. Kembalikan (baris 5 bahasa, saran)."""
    for kode in IDX_KAMUS:
        i = INDEKS_KAMUS.get((kode, norm_kamus(teks, kode)))
        if i is not None:
            e = KAMUS[i]
            baris = []
            for nama, k in BAHASA.values():
                isi = e[IDX_KAMUS[k]]
                isi = isi[:1].upper() + isi[1:]
                tambahan = ""
                if k == "ru":
                    tambahan = f"   (cara baca: {latinkan_rusia(isi)})"
                elif k == "ja":
                    tambahan = f"   (cara baca: {e[4]})"
                baris.append(f"   {nama:<10}: {isi}{tambahan}")
            return baris, []
    return None, saran_kamus(teks, "auto")


def coba_kamus():
    bot("Latihan kamus: ketik satu kata atau kalimat dari bahasa mana pun "
        "(Indonesia, Inggris, Rusia, Jepang, Jerman). Aku tampilkan artinya dalam "
        "kelima bahasa sekaligus. Contoh: halo, thank you, спасибо, Danke.")
    bot("Ketik 'selesai' untuk kembali.")
    while True:
        teks = input("Kamu: ").strip()
        if not teks:
            continue
        if teks.lower() in ("selesai", "menu", "/menu", "batal", "kembali"):
            return
        baris, saran = kartu_kamus(teks)
        if baris:
            bot(f'"{teks}" dalam 5 bahasa:')
            for b in baris:
                print(b)
        else:
            pesan = "Belum ada di kamus bawaan."
            if saran:
                pesan += " Mungkin maksudmu: " + ", ".join(f"'{s}'" for s in saran) + "."
            else:
                pesan += " Lihat daftar isi kamus lewat menu 'Lihat daftar' (harus sama persis)."
            bot(pesan)


def tutorial_kamus():
    def judul(teks):
        print()
        bot(teks)

    def para(teks, indent="   ", lanjut="   "):
        print(textwrap.fill(teks, width=64, initial_indent=indent,
                            subsequent_indent=lanjut))

    judul("=== TUTORIAL KAMUS BAWAAN ===")
    judul("1. Apa itu kamus bawaan?")
    para(f"Daftar {len(KAMUS)} kata dan kalimat umum dalam 5 bahasa (Indonesia, "
         "Inggris, Rusia, Jepang, Jerman): sapaan, angka, hari, warna, makanan, "
         "dan kalimat sehari-hari. Bisa dipakai TANPA internet dan TANPA install "
         "apa pun, jadi aman dipakai di HP.")
    judul("2. Cara memakainya")
    para("a. Buka menu 5 (Translate), lalu pilih 'Terjemahkan teks'.", lanjut="      ")
    para("b. Pilih mesin 4 (Kamus bawaan). Mesin 1 (Otomatis) juga memakai kamus "
         "kalau mesin lain tidak tersedia.", lanjut="      ")
    para("c. Pilih bahasa asal (angka 1-5, atau 0 untuk deteksi otomatis) dan bahasa "
         "tujuan.", lanjut="      ")
    para("d. Ketik kata atau kalimatnya, lalu baca hasilnya. Untuk Rusia dan Jepang, "
         "cara baca dalam huruf latin ikut ditampilkan.", lanjut="      ")
    judul("3. Contoh hasil (satu kata, lima bahasa)")
    for contoh in ("selamat pagi", "terima kasih"):
        print(f'   Kamu ketik: "{contoh}"')
        baris, _ = kartu_kamus(contoh)
        for b in baris:
            print("   " + b)
        print()
    judul("4. Aturan penting")
    para("- Huruf besar/kecil dan tanda baca tidak berpengaruh: \"Selamat Pagi!\" "
         "sama dengan \"selamat pagi\".", lanjut="     ")
    para("- Teksnya harus SAMA PERSIS dengan yang ada di kamus. Kamus tidak bisa "
         "menyusun kalimat baru: \"selamat pagi teman-teman\" tidak dikenali, tapi "
         "\"selamat pagi\" dikenali.", lanjut="     ")
    para("- Kalau tidak ketemu, kamu dapat saran \"Mungkin maksudmu: ...\" bila ada "
         "yang mirip (berguna kalau salah ketik).", lanjut="     ")
    para("- Dengan bahasa asal 0 (deteksi otomatis), kamus mencari katamu di kelima "
         "bahasa sekaligus.", lanjut="     ")
    para("- Teks Rusia dan Jepang harus diketik dengan huruf aslinya, bukan huruf "
         "latin. Kalau keyboard HP-mu belum punya bahasa itu, pakai Indonesia atau "
         "Inggris sebagai bahasa asal.", lanjut="     ")
    judul("5. Tips")
    para("- Menu Translate > Kamus bawaan > 'Lihat daftar': semua kata yang dikenali "
         "per bahasa.", lanjut="     ")
    para("- Menu Translate > Kamus bawaan > 'Coba kamus': ketik satu kata, langsung "
         "lihat artinya di lima bahasa.", lanjut="     ")
    para("- Setelah menerjemahkan, ketik /simpan untuk menyimpan ke kosakata, lalu "
         "berlatih lewat menu 8 (flashcard).", lanjut="     ")
    para("- Di menu Ngobrol, kamu bisa mengetik: terjemahkan terima kasih ke jepang",
         lanjut="     ")
    judul("6. Kalau kata yang kamu cari tidak ada")
    para("Pakai mesin online (butuh internet) atau mesin offline Argos. Keduanya bisa "
         "menerjemahkan kalimat bebas. Pasang dengan: pip install deep-translator "
         "(online) atau pip install argostranslate (offline, lalu unduh paket "
         "bahasanya lewat menu Translate > Kelola paket bahasa offline).")
    print()


def menu_kamus():
    submenu("Kamus Bawaan", [
        ("Tutorial: cara memakai kamus", tutorial_kamus),
        ("Coba kamus: satu kata dalam 5 bahasa sekaligus", coba_kamus),
        ("Lihat daftar kata dan kalimat di kamus", lihat_kamus),
    ])


# ------------------------------------------------------ fitur: riwayat hitungan
def lihat_riwayat():
    siapkan_data()
    daftar = DATA["riwayat"]
    if not daftar:
        bot("Riwayat masih kosong. Hasil hitungan dari menu matematika, hitung "
            "hari, dan konversi akan tercatat di sini.")
        return
    bot(f"{min(20, len(daftar))} hasil terakhir (dari total {len(daftar)}):")
    for waktu, teks in daftar[-20:]:
        print(f"   [{waktu}] {teks}")


def ekspor_riwayat():
    siapkan_data()
    if not DATA["riwayat"]:
        bot("Riwayat masih kosong, tidak ada yang disimpan.")
        return
    jalur = os.path.join(os.path.dirname(lokasi_data()) or ".", "aix_riwayat.txt")
    try:
        with open(jalur, "w", encoding="utf-8") as f:
            for waktu, teks in DATA["riwayat"]:
                f.write(f"[{waktu}] {teks}\n")
    except OSError:
        bot("Gagal menyimpan file. Folder program mungkin tidak bisa ditulisi.")
        return
    bot(f"Riwayat ({len(DATA['riwayat'])} baris) disimpan ke: {jalur}")


def hapus_riwayat():
    if konfirmasi("Hapus semua riwayat hitungan?"):
        siapkan_data()
        DATA["riwayat"] = []
        simpan_data()
        bot("Riwayat dihapus.")
    else:
        bot("Dibatalkan.")


def mode_riwayat():
    submenu("Riwayat Hitungan", [
        ("Lihat 20 hasil terakhir", lihat_riwayat),
        ("Simpan riwayat ke file teks (aix_riwayat.txt)", ekspor_riwayat),
        ("Hapus semua riwayat", hapus_riwayat),
    ])


# ------------------------------------------------------ fitur: kosakata & flashcard
def simpan_kosakata(asal, tujuan, dari, ke):
    """Simpan pasangan terjemahan. True kalau baru, False kalau sudah ada."""
    siapkan_data()
    for kartu in DATA["kosakata"]:
        if kartu["asal"] == asal and kartu["tujuan"] == tujuan:
            return False
    DATA["kosakata"].append({"asal": asal, "tujuan": tujuan, "dari": dari,
                             "ke": ke, "benar": 0, "salah": 0})
    simpan_data()
    return True


def lihat_kosakata():
    siapkan_data()
    daftar = DATA["kosakata"]
    if not daftar:
        bot("Kosakata masih kosong. Di menu Translate, ketik /simpan setelah "
            "sebuah terjemahan untuk menyimpannya ke sini.")
        return
    bot(f"Kosakata tersimpan ({len(daftar)}):")
    for i, k in enumerate(daftar, start=1):
        print(f"   {i}. {k['asal']}  ->  {k['tujuan']}   "
              f"({k['dari']} -> {k['ke']}; benar {k['benar']}, salah {k['salah']})")


def latihan_flashcard():
    siapkan_data()
    daftar = DATA["kosakata"]
    if not daftar:
        bot("Belum ada kosakata. Simpan dulu lewat menu Translate (ketik /simpan).")
        return
    bot("Latihan flashcard. Lihat kartu, ingat-ingat artinya, lalu tekan Enter "
        "untuk melihat jawaban. Ketik 'stop' untuk selesai.")
    benar = salah = 0
    terakhir = None
    while True:
        kandidat = [k for k in daftar if k is not terakhir] or daftar
        bobot = [max(0.3, 1 + 2 * k["salah"] - 0.5 * k["benar"]) for k in kandidat]
        kartu = random.choices(kandidat, weights=bobot)[0]
        terakhir = kartu
        print()
        bot(f"[{kartu['dari']} -> {kartu['ke']}]  {kartu['asal']}")
        if input("AIX: tekan Enter untuk lihat jawaban ('stop' = selesai): "
                 ).strip().lower() == "stop":
            break
        bot(f"Jawaban: {kartu['tujuan']}")
        nilai = input("AIX: apakah kamu ingat? (y/n, 'stop' = selesai): ").strip().lower()
        if nilai == "stop":
            break
        if nilai in ("y", "ya"):
            kartu["benar"] += 1
            benar += 1
        else:
            kartu["salah"] += 1
            salah += 1
        simpan_data()
    total = benar + salah
    if total:
        bot(f"Selesai! Ingat {benar} dari {total} kartu ({round(100 * benar / total)}%).")
    else:
        bot("Latihan selesai.")


def hapus_kosakata():
    if konfirmasi("Hapus semua kosakata tersimpan?"):
        siapkan_data()
        DATA["kosakata"] = []
        simpan_data()
        bot("Kosakata dihapus.")
    else:
        bot("Dibatalkan.")


def mode_kosakata():
    submenu("Kosakata & Flashcard", [
        ("Lihat daftar kosakata", lihat_kosakata),
        ("Latihan flashcard", latihan_flashcard),
        ("Hapus semua kosakata", hapus_kosakata),
    ])


# --------------------------------------------------------------- program utama
def main():
    print ("*" * 40)
    print("Selamat datang di program AIX!")
    print("INFORMASI AIX")
    print("Model AI: AIX-001")
    print("Versi: 3.0(beta testing)")
    print("Pengembang: Dzaki dxd")
    print ("*" * 40)
    muat_data()

    menu = {
        "1": mode_chat,
        "2": mode_matematika,
        "3": mode_matematika_lanjut,
        "4": mode_hitung_hari,
        "5": mode_translate,
        "6": mode_konversi,
        "7": mode_kuis,
        "8": mode_kosakata,
        "9": mode_riwayat,
    }
    while True:
        print()
        bot("=== MENU UTAMA ====================")
        print("1. Ngobrol (chat)")
        print("2. Matematika dasar")
        print("3. Matematika lanjut")
        print("4. Hitung hari")
        print("5. Translate (online, offline, kamus bawaan)")
        print("6. Konversi satuan dan keuangan")
        print("7. Kuis latihan soal")
        print("8. Kosakata dan flashcard")
        print("9. Riwayat hitungan")
        print("0. Keluar")
        print("=" * 40)
        pilih = input("AIX: pilih menu (0-9): ").strip()
        if pilih == "0":
            bot("Terima kasih telah menggunakan program AIX. Sampai jumpa!")
            break
        if pilih in menu:
            menu[pilih]()
        else:
            bot("Pilihan tidak valid, coba lagi.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nAIX: program dihentikan. Sampai jumpa!")