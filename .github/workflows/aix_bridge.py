"""Jembatan terminal -> GUI untuk program gaya input()/print().

Dipakai oleh main.py (Kivy) di Android. Tidak ada satu baris pun di aix.py
yang perlu diubah: input() dan print() dialihkan ke kotak teks di layar.

Berkas ini tidak mengimpor Kivy, jadi bisa diuji tanpa HP:
    python3 tests/test_bridge.py
"""

import builtins
import queue
import runpy
import sys
import threading
import traceback

STOP = object()  # penanda: pengguna menutup aplikasi


class ConsoleBridge:
    """Menjalankan skrip terminal di thread terpisah dan meneruskan
    keluarannya ke UI lewat queue."""

    def __init__(self, script):
        self.script = script
        # pesan untuk UI: ("text", str) | ("waiting", None) | ("done", None)
        self.output = queue.Queue()
        self.answers = queue.Queue()
        self.running = False
        self.thread = None

    # ---------- sisi program (worker thread) ----------
    def write(self, teks):
        if teks:
            self.output.put(("text", teks))
        return len(teks)

    def flush(self):
        pass

    def isatty(self):
        return False

    def input(self, prompt=""):
        """Pengganti input(): tampilkan prompt, tunggu jawaban dari UI."""
        if prompt:
            self.output.put(("text", str(prompt)))
        self.output.put(("waiting", None))
        jawaban = self.answers.get()          # memblokir thread ini saja
        if jawaban is STOP:
            raise EOFError("aplikasi ditutup")
        self.output.put(("text", jawaban + "\n"))   # gaungkan seperti terminal
        return jawaban

    def readline(self, *args):
        return self.input() + "\n"

    def kirim(self, baris):
        """Dipanggil UI saat pengguna menekan Enter/Kirim."""
        self.answers.put(baris)

    def berhenti(self):
        self.answers.put(STOP)

    # ---------- kontrol ----------
    def mulai(self, script=None, argv=None):
        if self.running:
            return
        # buang jawaban lama yang belum sempat dipakai (mis. dari run sebelumnya)
        while True:
            try:
                self.answers.get_nowait()
            except queue.Empty:
                break
        path = script or self.script
        self.running = True
        self.thread = threading.Thread(target=self._jalankan, args=(path, argv),
                                       daemon=True)
        self.thread.start()

    def _jalankan(self, path, argv):
        lama = (builtins.input, sys.stdout, sys.argv)
        builtins.input = self.input
        sys.stdout = self
        sys.argv = [path] if argv is None else argv
        try:
            # runpy = persis seperti `python aix.py`; __file__ dan
            # if __name__ == "__main__" tetap benar.
            runpy.run_path(path, run_name="__main__")
        except SystemExit:
            pass
        except BaseException:
            self.output.put(("text", "\n" + traceback.format_exc()))
        finally:
            builtins.input, sys.stdout, sys.argv = lama
            self.running = False
            self.output.put(("done", None))
