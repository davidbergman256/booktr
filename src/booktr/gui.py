"""Modern Czech desktop shell; background work communicates through one queue."""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import strings_cs as S
from .api import ApiClient
from .config import Config, load_config
from .engine import Engine
from .errors import BookTrError, action_label, categorize, friendly_message
from .gui_settings import BG, GREEN, INK, MUTED, PAPER, show_settings
from .state import Job, list_unfinished

log = logging.getLogger('booktr.gui')
STAGE_WEIGHTS = {'ingest': 10, 'segment': 2, 'stylesheet': 10, 'translate': 70,
                 'merge': 2, 'typeset': 6}


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode('light')
        ctk.set_default_color_theme('green')
        super().__init__(fg_color=BG)
        self.title(S.APP_TITLE)
        self.geometry('900x660')
        self.minsize(780, 580)
        self.font_family = 'Segoe UI' if sys.platform == 'win32' else 'Helvetica'
        self.font_head = ctk.CTkFont(self.font_family, 32, weight='bold')
        self.font_body = ctk.CTkFont(self.font_family, 18)
        self.font_button = ctk.CTkFont(self.font_family, 20, weight='bold')
        self.queue = queue.Queue()
        self.worker = None
        self.busy = False
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.current_job = None
        self.output_path = None
        self.pdf_path = None
        self.mode = 'pdf'
        self.settings_window = None
        self.stage_fractions = {}
        self.started_at = None
        self.config_obj = Config()
        self.frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.frame.pack(fill='both', expand=True, padx=40, pady=30)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        try:
            self.config_obj = load_config(validate=False)
        except BookTrError as err:
            self.show_error(err)
        else:
            unfinished = list_unfinished()
            self.show_resume(unfinished[0]) if unfinished else self.show_start()
        self.after(100, self.poll_queue)

    def clear(self):
        for widget in self.frame.winfo_children():
            widget.destroy()

    def label(self, parent, text, *, large=False, muted=False, **kwargs):
        selected_font = kwargs.pop('font', self.font_head if large else self.font_body)
        return ctk.CTkLabel(parent, text=text, font=selected_font,
                           text_color=MUTED if muted else INK, **kwargs)

    def button(self, parent, text, command, *, secondary=False, **kwargs):
        height = kwargs.pop('height', 54)
        return ctk.CTkButton(parent, text=text, command=command, font=self.font_button,
                            fg_color='#e5e9e1' if secondary else GREEN,
                            hover_color='#d7ddd2' if secondary else '#1d503b',
                            text_color=INK if secondary else 'white', corner_radius=12,
                            height=height, **kwargs)

    def header(self, settings=True):
        bar = ctk.CTkFrame(self.frame, fg_color=BG)
        bar.pack(fill='x', pady=(0, 34))
        ctk.CTkLabel(bar, text='BookTr', text_color=GREEN,
                     font=(self.font_family, 27, 'bold')).pack(side='left')
        if settings:
            self.button(bar, 'Nastavení', lambda: show_settings(self), secondary=True,
                        width=140).pack(side='right')

    def show_start(self):
        self.clear()
        self.header()
        self.label(self.frame, 'Knihy v češtině.', large=True, anchor='w').pack(fill='x', pady=(24, 8))
        self.label(self.frame, 'Vyberte, co chcete s knihou udělat.', muted=True,
                   anchor='w').pack(fill='x', pady=(0, 38))
        choices = ctk.CTkFrame(self.frame, fg_color=BG)
        choices.pack(fill='x')
        choices.grid_columnconfigure((0, 1), weight=1, uniform='choice')
        for column, mode, title, detail in (
            (0, 'pdf', 'Přeložit knihu', 'PDF s obrázky a poznámkami.'),
            (1, 'audio', 'Audiokniha', 'Kniha k poslechu v češtině.'),
        ):
            pane = ctk.CTkFrame(choices, fg_color=PAPER, corner_radius=16)
            pane.grid(row=0, column=column, sticky='nsew', padx=(0, 10) if column == 0 else (10, 0))
            self.button(pane, title, lambda m=mode: self.pick_book(m),
                        secondary=column == 1, height=100).pack(fill='x', padx=18, pady=(18, 14))
            self.label(pane, detail, muted=True, wraplength=300).pack(padx=12, pady=(0, 22))
        self.label(self.frame, 'Na začátku vyberete soubor PDF. O zbytek se postará program.',
                   muted=True, wraplength=740, anchor='w', justify='left').pack(fill='x', pady=(30, 0))
        footer = ctk.CTkFrame(self.frame, fg_color=BG)
        footer.pack(side='bottom', fill='x', pady=(24, 0))
        voice = 'Normální' if self.config_obj.voice_mode == 'normal' else 'Pokročilý'
        self.label(footer, f'Písmo PDF: {self.config_obj.font_size} bodů   ·   Hlas: {voice}',
                   muted=True, anchor='w').pack(fill='x')

    def show_resume(self, job):
        self.clear()
        self.header()
        self.label(self.frame, 'Navážeme tam, kde jsme skončili.', large=True,
                   wraplength=740, justify='left', anchor='w').pack(fill='x', pady=(36, 18))
        self.label(self.frame, f'Rozpracovaná kniha: {job.title}', wraplength=740,
                   anchor='w').pack(fill='x', pady=(0, 34))
        self.button(self.frame, 'Pokračovat', lambda: self.start_job(job)).pack(anchor='w')
        self.button(self.frame, 'Vybrat jinou knihu', self.show_start, secondary=True).pack(anchor='w', pady=16)

    def pick_book(self, mode='pdf'):
        path = filedialog.askopenfilename(title='Vybrat knihu', filetypes=[('Kniha v PDF', '*.pdf')])
        if not path:
            return
        try:
            job = Job.from_pdf(Path(path))
        except Exception as exc:
            self.show_error(categorize(exc))
            return
        self.start_job(job, mode)

    def show_progress(self, title):
        self.clear()
        self.header(settings=False)
        self.label(self.frame, 'Připravuji audioknihu.' if self.mode == 'audio' else 'Připravuji vaši knihu.',
                   large=True, anchor='w').pack(fill='x', pady=(24, 10))
        self.label(self.frame, title, muted=True, wraplength=740, anchor='w').pack(fill='x', pady=(0, 30))
        self.offline_banner = self.label(self.frame, S.OFFLINE_BANNER, wraplength=720,
                                        fg_color='#f3dfb8', corner_radius=10, height=70)
        self.stage_label = self.label(self.frame, 'Čtu knihu…', anchor='w')
        self.stage_label.pack(fill='x', pady=(0, 18))
        self.progress_bar = ctk.CTkProgressBar(self.frame, progress_color=GREEN,
                                            fg_color='#dde3d8', height=16, corner_radius=8)
        self.progress_bar.pack(fill='x')
        self.progress_bar.set(0)
        self.time_label = self.label(self.frame, '', muted=True, anchor='w')
        self.time_label.pack(fill='x', pady=(16, 30))
        self.pause_btn = self.button(self.frame, S.PAUSE, self.toggle_pause, secondary=True)
        self.pause_btn.pack(anchor='w')
        self.label(self.frame, 'Hotové části průběžně ukládám. Po zavření lze pokračovat.',
                   muted=True, wraplength=730, anchor='w').pack(side='bottom', fill='x', pady=(20, 0))

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text=S.UNPAUSE)
        else:
            self.pause_event.set()
            self.pause_btn.configure(text=S.PAUSE)

    def show_done(self, output):
        self.clear()
        self.output_path = Path(output)
        self.header()
        self.label(self.frame, 'Vaše audiokniha je hotová.' if self.mode == 'audio' else 'Vaše kniha je hotová.',
                   large=True, wraplength=740, anchor='w').pack(fill='x', pady=(36, 14))
        self.label(self.frame, f'Uloženo do: {self.output_path.parent}', muted=True,
                   wraplength=730, anchor='w', justify='left').pack(fill='x', pady=(0, 28))
        self.button(self.frame, 'Přehrát audioknihu' if self.mode == 'audio' else 'Otevřít knihu',
                    lambda: self.open_path(self.output_path)).pack(anchor='w')
        if self.mode == 'audio' and self.pdf_path:
            self.button(self.frame, 'Otevřít také PDF', lambda: self.open_path(self.pdf_path),
                        secondary=True).pack(anchor='w', pady=(14, 0))
        self.button(self.frame, 'Další kniha', self.show_start, secondary=True).pack(anchor='w', pady=14)
        if self.current_job and self.current_job.exists('warnings.json'):
            warnings = self.current_job.read_json('warnings.json')
            if warnings:
                self.label(self.frame, 'Některá místa vyžadují kontrolu. Podrobnosti jsou u uložené knihy.',
                           muted=True, wraplength=730, anchor='w').pack(fill='x', pady=12)

    @staticmethod
    def open_path(path):
        if not path:
            return
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        except OSError:
            log.exception('cannot open output')

    def show_error(self, err):
        log.error('error category=%s', err.code)
        self.clear()
        self.header()
        self.label(self.frame, friendly_message(err, self.config_obj), wraplength=730,
                   justify='left', anchor='w').pack(fill='x', pady=(42, 28))
        action = action_label(err)
        command = self.destroy if action == S.QUIT else self.show_start if action == S.PICK_OTHER else self.retry
        self.button(self.frame, action, command).pack(anchor='w')
        if action != S.PICK_OTHER:
            self.button(self.frame, 'Zpět na začátek', self.show_start, secondary=True).pack(anchor='w', pady=16)

    def retry(self):
        if self.current_job:
            self.start_job(self.current_job, self.mode)
        else:
            self.show_start()

    def start_job(self, job, mode=None):
        if self.busy:
            return
        self.current_job = job
        self.mode = mode or job.progress().get('output_mode', 'pdf')
        try:
            cfg = load_config()
        except BookTrError as err:
            self.show_error(err)
            return
        self.config_obj = cfg
        cfg = replace(cfg)  # A running book uses an immutable snapshot of reader settings.
        self.pause_event.set()
        self.cancel_event.clear()
        self.stage_fractions = {}
        self.started_at = time.monotonic()
        self.busy = True
        job.set_progress(output_mode=self.mode, status='running')
        self.show_progress(job.title)
        api = ApiClient(cfg, on_offline=lambda: self.queue.put(('offline', True)),
                        on_online=lambda: self.queue.put(('offline', False)), cancel_event=self.cancel_event)
        engine = Engine(job, api, cfg,
                        progress_cb=lambda st, c, t: self.queue.put(('progress', st, c, t)),
                        pause_event=self.pause_event, cancel_event=self.cancel_event)
        def work():
            try:
                if self.mode == 'audio':
                    from .audio.providers import make_provider
                    provider = make_provider(cfg, cancel_event=self.cancel_event)
                    try:
                        provider.validate()
                    finally:
                        provider.close()
                self.pdf_path = engine.run()
                output = self.pdf_path
                if self.mode == 'audio':
                    from .audio import run as make_audio
                    job.set_progress(status='running')
                    output = make_audio(engine)
                self.queue.put(('done', output))
            except Exception as exc:
                log.exception('book processing interrupted')
                self.queue.put(('error', categorize(exc)))
        self.worker = threading.Thread(target=work, name='booktr-job', daemon=True)
        self.worker.start()

    def poll_queue(self):
        try:
            while True:
                self.handle_event(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def handle_event(self, event):
        kind = event[0]
        if kind == 'progress':
            self.update_progress(*event[1:])
        elif kind == 'offline' and hasattr(self, 'offline_banner') and self.offline_banner.winfo_exists():
            if event[1]:
                self.offline_banner.pack(fill='x', pady=(0, 14), before=self.stage_label)
            else:
                self.offline_banner.pack_forget()
        elif kind in ('done', 'error'):
            self.busy = False
            self.show_done(event[1]) if kind == 'done' else self.show_error(event[1])

    def update_progress(self, stage, cur, total):
        if not hasattr(self, 'stage_label') or not self.stage_label.winfo_exists():
            return
        self.stage_fractions[stage] = max(self.stage_fractions.get(stage, 0), cur / max(total, 1))
        weights = STAGE_WEIGHTS if self.mode == 'pdf' else {**STAGE_WEIGHTS, 'audio': 180}
        fraction = sum(weights.get(st, 0) * min(fr, 1) for st, fr in self.stage_fractions.items()) / sum(weights.values())
        self.progress_bar.set(min(1, fraction))
        template = S.STAGE_LABELS.get(stage, 'Připravuji knihu…')
        self.stage_label.configure(text=template.format(cur=cur, tot=max(total, 1)))
        seconds = int(time.monotonic() - self.started_at)
        self.time_label.configure(text=f'Hotovo {round(100 * fraction)} %   ·   Uplynulo {seconds // 60}:{seconds % 60:02d}')

    def on_close(self):
        if self.busy:
            self.cancel_event.set()
            self.pause_event.set()
            messagebox.showinfo(S.APP_TITLE, S.CLOSE_SAFE)
        self.destroy()


def run_gui():
    App().mainloop()
