"""Reader settings: only book typography and the two narration choices."""
from __future__ import annotations

from dataclasses import replace
from tkinter import StringVar

import customtkinter as ctk

from .config import save_preferences

BG = '#f5f3ec'
PAPER = '#ffffff'
INK = '#202b25'
MUTED = '#626c65'
GREEN = '#27664d'


def show_settings(app):
    if app.settings_window is not None and app.settings_window.winfo_exists():
        app.settings_window.focus()
        return
    window = ctk.CTkToplevel(app, fg_color=BG)
    app.settings_window = window
    window.title('Nastavení')
    window.geometry('640x660')
    window.minsize(580, 610)
    window.transient(app)
    window.grid_columnconfigure(0, weight=1)
    window.grid_rowconfigure(0, weight=1)
    panel = ctk.CTkScrollableFrame(window, fg_color=BG, corner_radius=0)
    panel.grid(row=0, column=0, sticky='nsew', padx=24, pady=(20, 8))
    panel.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(panel, text='Nastavení', font=app.font_head, text_color=INK,
                 anchor='w').grid(row=0, column=0, sticky='ew', pady=(0, 24))
    ctk.CTkLabel(panel, text='Velikost písma v PDF', font=app.font_button,
                 text_color=INK, anchor='w').grid(row=1, column=0, sticky='ew')
    size = StringVar(value=str(app.config_obj.font_size))
    preview = ctk.CTkLabel(panel, text='Příběh začíná na další stránce…',
                          font=(app.font_family, app.config_obj.font_size),
                          text_color=INK, fg_color=PAPER, corner_radius=12,
                          height=82, wraplength=500)
    def update_preview(value):
        preview.configure(font=(app.font_family, int(value)))
    ctk.CTkSegmentedButton(panel, values=['12', '14', '16', '18', '20', '22', '24'], variable=size,
                          command=update_preview, font=app.font_body,
                          selected_color=GREEN, selected_hover_color='#1d503b',
                          height=46).grid(row=2, column=0, sticky='ew', pady=(12, 14))
    preview.grid(row=3, column=0, sticky='ew')
    ctk.CTkLabel(panel, text='Hlas audioknihy', font=app.font_button, text_color=INK,
                 anchor='w').grid(row=4, column=0, sticky='ew', pady=(28, 16))
    voice = StringVar(value=app.config_obj.voice_mode)
    for row, value, title, description in (
        (5, 'normal', 'Normální — ~250 Kč / kniha', 'Přirozený český hlas.'),
        (7, 'advanced', 'Pokročilý — ~700 Kč / kniha', 'Výraznější a jemnější přednes.'),
    ):
        ctk.CTkRadioButton(panel, text=title, variable=voice, value=value, font=app.font_body,
                           text_color=INK, fg_color=GREEN, hover_color=GREEN,
                           radiobutton_width=26, radiobutton_height=26,
                           height=44).grid(row=row, column=0, sticky='w')
        ctk.CTkLabel(panel, text=description, font=(app.font_family, 16), text_color=MUTED,
                     anchor='w').grid(row=row + 1, column=0, sticky='ew', padx=(36, 0), pady=(0, 12))
    ctk.CTkLabel(panel, text='Cena je orientační. Záleží na délce knihy a tarifu služby.',
                 font=(app.font_family, 15), text_color=MUTED, wraplength=520,
                 anchor='w', justify='left').grid(row=9, column=0, sticky='ew', pady=(4, 8))
    error = ctk.CTkLabel(panel, text='', text_color='#a23832', font=(app.font_family, 15), wraplength=520)
    error.grid(row=10, column=0, sticky='ew')
    def save():
        try:
            updated = replace(app.config_obj, font_size=int(size.get()), voice_mode=voice.get())
            save_preferences(updated)
        except (OSError, ValueError):
            error.configure(text='Nastavení se nepodařilo uložit. Zkuste to prosím znovu.')
            return
        app.config_obj = updated
        window.destroy()
        if not app.busy:
            app.show_start()
    footer = ctk.CTkFrame(window, fg_color=BG)
    footer.grid(row=1, column=0, sticky='ew', padx=34, pady=(8, 26))
    app.button(footer, 'Uložit nastavení', save).pack(side='right')
    app.button(footer, 'Zpět', window.destroy, secondary=True).pack(side='left')
    window.after(50, window.grab_set)
