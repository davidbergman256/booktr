# PyInstaller spec — jeden soubor BookTr.exe s přibalenou typst binárkou.
# Build: pyinstaller build/booktr.spec   (na Windows; viz .github/workflows)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

spec_dir = SPECPATH  # noqa: F821 — PyInstaller jej injektuje
repo = os.path.dirname(spec_dir)

datas = [
    (os.path.join(repo, "src", "booktr", "assets"), "booktr/assets"),
]
datas += collect_data_files("customtkinter")
datas += collect_data_files("imageio_ffmpeg")
binaries = collect_dynamic_libs("typst")
typst_exe = os.path.join(repo, "typst", "typst.exe")
if os.path.isfile(typst_exe):
    binaries.append((typst_exe, "typst"))

a = Analysis(  # noqa: F821
    # launcher, ne __main__.py — ten jako top-level skript nemá balíček
    # a jeho relativní importy okamžitě spadnou
    [os.path.join(spec_dir, "launch.py")],
    pathex=[os.path.join(repo, "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=["booktr.gui", "booktr.gui_settings", "google.auth", "google.auth.transport.requests", "typst", "imageio_ffmpeg", "imageio_ffmpeg.binaries"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="BookTr",
    console=False,
    upx=False,
)
