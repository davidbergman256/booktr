"""Všechny texty viditelné pro uživatele. Aplikace je záměrně pouze česká."""

APP_TITLE = "BookTr — Překlad knih"

# Obrazovka 1 — start
HEADLINE = "Přeložit knihu do češtiny"
PICK_BOOK = "Vybrat knihu (PDF)"
RESUME_PROMPT = "Minule jsme nedokončili překlad knihy\n„{title}“.\n\nChcete pokračovat?"
RESUME_YES = "Pokračovat"
RESUME_NO = "Začít jinou knihu"

# Obrazovka 2 — průběh
PAUSE = "Pozastavit"
UNPAUSE = "Pokračovat"
CLOSE_SAFE = "Překlad je uložen.\nAž program znovu zapnete, budeme pokračovat."
OFFLINE_BANNER = "Internet je odpojen. Až bude připojení znovu fungovat, budu sám pokračovat."
TIME_LEFT = "Zbývá asi {minutes} min"

STAGE_LABELS = {
    "ingest": "Čtu knihu… (strana {cur} z {tot})",
    "segment": "Připravuji text…",
    "stylesheet": "Připravuji se na překlad… (kapitola {cur} z {tot})",
    "translate": "Překládám… (část {cur} z {tot})",
    "review": "Kontroluji překlad… (část {cur} z {tot})",
    "merge": "Skládám knihu dohromady…",
    "typeset": "Sázím knihu do PDF…",
}

# Obrazovka 3 — hotovo
DONE_HEAD = "Hotovo!"
DONE_BODY = "Přeložená kniha je na ploše."
OPEN_BOOK = "Otevřít knihu"
ANOTHER = "Přeložit další knihu"

# Chyby — vždy klidné, vždy česky, vždy s jednou jasnou akcí
RETRY = "Zkusit znovu"
PICK_OTHER = "Vybrat jinou knihu"
QUIT = "Zavřít"

ERRORS = {
    "auth": "Program se nemůže přihlásit ke službě překladu.\nZavolejte prosím: {helper_name} {helper_phone}",
    "quota": "Služba překladu je momentálně vyčerpaná.\nZavolejte prosím: {helper_name} {helper_phone}",
    "config": "Program ještě není nastavený.\nZavolejte prosím: {helper_name} {helper_phone}",
    "bad_pdf": "Tento soubor se nepodařilo přečíst.\nZkuste prosím jiný soubor s knihou.",
    "disk_full": "Na počítači není dost místa.\nSmažte prosím nepotřebné soubory, nebo zavolejte: {helper_name}",
    "output_locked": "Kniha je otevřená v jiném programu.\nZavřete ji prosím a zkuste to znovu.",
    "typeset": "Nepodařilo se vytvořit PDF soubor.\nZavolejte prosím: {helper_name} {helper_phone}",
    "unknown": "Něco se nepovedlo, ale kniha není ztracena.\nZkuste program vypnout a znovu zapnout.",
}

# Akce nabízená u jednotlivých chyb
ERROR_ACTIONS = {
    "auth": RETRY,
    "quota": RETRY,
    "config": RETRY,
    "bad_pdf": PICK_OTHER,
    "disk_full": RETRY,
    "output_locked": RETRY,
    "typeset": RETRY,
    "unknown": QUIT,
}
