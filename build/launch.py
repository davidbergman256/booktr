"""Vstupní bod pro PyInstaller — absolutní import, aby fungovaly relativní importy balíčku.

Poslední záchrana: pokud selže i samotný start, ukážeme klidnou českou hlášku
místo technického dialogu PyInstalleru.
"""
import sys


def _panic_dialog(exc: Exception) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "BookTr",
            "Něco se nepovedlo, ale kniha není ztracena.\n"
            "Zkuste program vypnout a znovu zapnout.",
        )
        root.destroy()
    except Exception:  # noqa: BLE001 — ani dialog nesmí shodit proces
        pass


if __name__ == "__main__":
    try:
        from booktr.__main__ import main

        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            import logging

            logging.getLogger("booktr").exception("startup crashed")
        except Exception:  # noqa: BLE001
            pass
        _panic_dialog(exc)
        sys.exit(1)
