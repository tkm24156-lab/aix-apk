"""AIX untuk Android - membungkus aix.py (program terminal) menjadi aplikasi GUI.

APK Android tidak punya terminal, jadi layar ini menyediakan:
  * kotak keluaran (semua print() dari aix.py)
  * kotak masukan + tombol Kirim (semua input() dari aix.py)
  * tombol A+/A- (ukuran huruf), Bersihkan, dan Hapus data

aix.py TIDAK diubah sama sekali.
"""

import os
import queue

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput

from aix_bridge import ConsoleBridge


def folder_aplikasi():
    """Folder tempat main.py dan aix.py berada (aman di p4a/Buildozer)."""
    return os.path.dirname(os.path.abspath(__file__))


def jalur_data():
    """Ikuti aturan aix.py: env AIX_DATA, kalau tidak ada -> folder skrip."""
    return os.environ.get("AIX_DATA") or os.path.join(folder_aplikasi(),
                                                     "aix_data.json")


class LayarAIX(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(6),
                         spacing=dp(6), **kwargs)
        self.bridge = ConsoleBridge(os.path.join(folder_aplikasi(), "aix.py"))
        self.ukuran = 15

        self.keluaran = TextInput(
            readonly=True, multiline=True, font_size=sp(self.ukuran),
            background_color=(0.06, 0.07, 0.09, 1),
            foreground_color=(0.93, 0.95, 0.98, 1),
            cursor_color=(0, 0, 0, 0),
        )
        self.add_widget(self.keluaran)

        baris = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
        for teks, aksi in (("A+", self.besar), ("A-", self.kecil),
                           ("Bersihkan", self.bersihkan),
                           ("Hapus data", self.hapus_data)):
            tombol = Button(text=teks, font_size=sp(12))
            tombol.bind(on_release=aksi)
            baris.add_widget(tombol)
        self.add_widget(baris)

        baris_input = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        self.masukan = TextInput(
            multiline=False, font_size=sp(self.ukuran),
            hint_text="Ketik di sini, lalu tekan Enter atau Kirim")
        # Enter di keyboard memicu on_text_validate karena multiline=False
        self.masukan.bind(on_text_validate=self.kirim)
        kirim = Button(text="Kirim", size_hint_x=None, width=dp(76),
                       font_size=sp(13))
        kirim.bind(on_release=self.kirim)
        baris_input.add_widget(self.masukan)
        baris_input.add_widget(kirim)
        self.add_widget(baris_input)

        Clock.schedule_interval(self._tarik_output, 0.1)
        Clock.schedule_once(self._mulai_program, 0.3)

    # ------------------------------------------------ tampilan
    def tulis(self, teks):
        self.keluaran.text += teks
        try:
            self.keluaran.scroll_y = 0      # 0 = paling bawah
        except Exception:
            pass

    def besar(self, *_):
        self.ukuran = min(30, self.ukuran + 2)
        self._ukuran_ulang()

    def kecil(self, *_):
        self.ukuran = max(9, self.ukuran - 2)
        self._ukuran_ulang()

    def _ukuran_ulang(self):
        self.keluaran.font_size = sp(self.ukuran)
        self.masukan.font_size = sp(self.ukuran)

    def bersihkan(self, *_):
        self.keluaran.text = ""

    def hapus_data(self, *_):
        """Hapus aix_data.json supaya riwayat/memori mulai dari nol lagi."""
        dihapus = 0
        for jalur in (jalur_data(), os.path.join(folder_aplikasi(),
                                                 "aix_data.json.rusak")):
            if jalur and os.path.exists(jalur):
                try:
                    os.remove(jalur)
                    dihapus += 1
                except OSError:
                    pass
        self.tulis("\n[AIX-Android] %d berkas data dihapus "
                   "(riwayat, kosakata, skor kuis, memori chat). "
                   "Ketik apa saja di bawah lalu Enter untuk mulai ulang.\n"
                   % dihapus)

    # ------------------------------------------------ jembatan
    def _mulai_program(self, *_):
        if not os.path.exists(self.bridge.script):
            self.tulis("[AIX-Android] Berkas aix.py tidak ditemukan di %s\n"
                       % folder_aplikasi())
            return
        self.tulis("[AIX-Android] Menjalankan aix.py...\n")
        self.bridge.mulai()

    def _tarik_output(self, _dt):
        kumpulan = []
        selesai = False
        while True:
            try:
                jenis, isi = self.bridge.output.get_nowait()
            except queue.Empty:
                break
            if jenis == "text":
                kumpulan.append(isi)
            elif jenis == "done":
                selesai = True
        if kumpulan:
            self.tulis("".join(kumpulan))
        if selesai:
            self.tulis("\n[AIX-Android] Program selesai. Ketik apa saja di "
                       "bawah lalu Enter untuk menjalankannya lagi.\n")
        return True

    def kirim(self, *_):
        teks = self.masukan.text.strip()
        self.masukan.text = ""
        if not teks:
            return
        if not self.bridge.running:
            self.tulis("\n[AIX-Android] Menjalankan ulang aix.py...\n")
            self.bridge.mulai()
        self.bridge.kirim(teks)   # diantre sampai aix.py memanggil input()


class AIXApp(App):
    def build(self):
        self.title = "AIX - Chatbot & Kalkulator"   # cara resmi Kivy
        return LayarAIX()

    def on_pause(self):
        return True     # program tetap hidup saat aplikasi dipindah ke belakang


if __name__ == "__main__":
    AIXApp().run()
