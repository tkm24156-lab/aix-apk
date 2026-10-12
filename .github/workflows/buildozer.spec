[app]
title = AIX - Chatbot & Kalkulator
package.name = aix
package.domain = org.aix

# Semua berkas di root repo (main.py wajib ada)
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt
source.exclude_dirs = tests,bin,.buildozer,__pycache__,.github
source.exclude_patterns = *.md

version = 3.0
# Preset LENGKAP (offline + terjemahan online + romaji). Kalau build gagal
# karena salah satu paket, ganti satu baris di bawah dengan versi minimal:
#   requirements = python3,kivy==2.3.1
requirements = python3,kivy==2.3.1,openssl,certifi,idna,urllib3,chardet,requests,beautifulsoup4,pykakasi,deep-translator

orientation = portrait
fullscreen = 0

# Terjemahan online butuh internet; fitur lain tetap offline
android.permissions = INTERNET
# Build lebih cepat 1 arsitektur. Untuk HP lama tambahkan: ,armeabi-v7a
android.archs = arm64-v8a
android.accept_sdk_license = True
android.debug_artifact = apk
android.release_artifact = apk

# android.api dan android.ndk sengaja TIDAK diisi: pakai default buildozer
# (paling aman dan paling sering berhasil). Isi hanya kalau build menolak.

[buildozer]
log_level = 2
warn_on_root = 1
