"""GUI — tři obrazovky, velká písmena, vše česky.

Pipeline běží ve vlákně a posílá události frontou; Tk smyčka je čte každých
100 ms. Hlavní okno nikdy nespadne kvůli chybě enginu — všechno se promění
v klidnou českou hlášku.
"""
from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox

from . import strings_cs as S
from .api import ApiClient
from .config import load_config
from .engine import Engine
from .errors import BookTrError, action_label, categorize, friendly_message
from .state import Job, list_unfinished

log = logging.getLogger("booktr.gui")

STAGE_WEIGHTS = {
    "ingest": 15, "segment": 2, "stylesheet": 13,
    "translate": 40, "review": 25, "merge": 1, "typeset": 4,
}
STAGE_ORDER = list(STAGE_WEIGHTS)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(S.APP_TITLE)
        self.geometry("720x460")
        self.minsize(640, 420)

        self.font_head = font.Font(family="Segoe UI", size=24, weight="bold")
        self.font_body = font.Font(family="Segoe UI", size=16)
        self.font_button = font.Font(family="Segoe UI", size=20, weight="bold")

        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.pause_event = threading.Event()
        self.config_obj = None
        self.current_job: Job | None = None
        self.output_path: Path | None = None
        self._stage_start: tuple[str, float, int] | None = None

        self.frame = tk.Frame(self)
        self.frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        try:
            self.config_obj = load_config()
        except BookTrError as err:
            self.show_error(err)
        else:
            unfinished = list_unfinished()
            if unfinished:
                self.show_resume(unfinished[0])
            else:
                self.show_start()

        self.after(100, self.poll_queue)

    # ---- pomocné --------------------------------------------------------------
    def clear(self):
        for w in self.frame.winfo_children():
            w.destroy()

    def big_button(self, parent, text, command):
        return tk.Button(parent, text=text, font=self.font_button, command=command,
                         padx=30, pady=14, cursor="hand2")

    # ---- obrazovka 1: start ----------------------------------------------------
    def show_start(self):
        self.clear()
        tk.Label(self.frame, text=S.HEADLINE, font=self.font_head).pack(pady=(70, 40))
        self.big_button(self.frame, S.PICK_BOOK, self.pick_book).pack()

    def show_resume(self, job: Job):
        self.clear()
        tk.Label(self.frame, text=S.RESUME_PROMPT.format(title=job.title),
                 font=self.font_body, justify="center").pack(pady=(60, 30))
        row = tk.Frame(self.frame)
        row.pack()
        self.big_button(row, S.RESUME_YES, lambda: self.start_job(job)).pack(side="left", padx=10)
        tk.Button(row, text=S.RESUME_NO, font=self.font_body,
                  command=self.show_start, padx=20, pady=10).pack(side="left", padx=10)

    def pick_book(self):
        path = filedialog.askopenfilename(
            title=S.PICK_BOOK, filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            job = Job.from_pdf(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.show_error(categorize(exc))
            return
        self.start_job(job)

    # ---- obrazovka 2: průběh ----------------------------------------------------
    def show_progress(self, title: str):
        self.clear()
        tk.Label(self.frame, text=f"„{title}“", font=self.font_body).pack(pady=(30, 6))
        self.offline_banner = tk.Label(self.frame, text=S.OFFLINE_BANNER,
                                       font=self.font_body, fg="white", bg="#b36b00",
                                       padx=12, pady=8)
        # banner se packuje až při výpadku
        self.stage_label = tk.Label(self.frame, text="…", font=self.font_body)
        self.stage_label.pack(pady=(26, 10))

        bar_holder = tk.Frame(self.frame, height=36, bg="#ddd")
        bar_holder.pack(fill="x", padx=60)
        bar_holder.pack_propagate(False)
        self.bar_fill = tk.Frame(bar_holder, bg="#2d7a2d")
        self.bar_fill.place(x=0, y=0, relheight=1.0, relwidth=0.0)

        self.time_label = tk.Label(self.frame, text="", font=self.font_body, fg="#555")
        self.time_label.pack(pady=(10, 20))
        self.pause_btn = tk.Button(self.frame, text=S.PAUSE, font=self.font_body,
                                   command=self.toggle_pause, padx=20, pady=8)
        self.pause_btn.pack()

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.config(text=S.UNPAUSE)
        else:
            self.pause_event.set()
            self.pause_btn.config(text=S.PAUSE)

    # ---- obrazovka 3: hotovo -----------------------------------------------------
    def show_done(self, output: Path):
        self.clear()
        self.output_path = output
        tk.Label(self.frame, text=S.DONE_HEAD, font=self.font_head, fg="#2d7a2d").pack(pady=(70, 12))
        tk.Label(self.frame, text=S.DONE_BODY, font=self.font_body).pack(pady=(0, 30))
        row = tk.Frame(self.frame)
        row.pack()
        self.big_button(row, S.OPEN_BOOK, self.open_book).pack(side="left", padx=10)
        tk.Button(row, text=S.ANOTHER, font=self.font_body,
                  command=self.show_start, padx=20, pady=10).pack(side="left", padx=10)

    def open_book(self):
        if not self.output_path:
            return
        try:
            if sys.platform == "win32":
                import os
                os.startfile(self.output_path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.output_path)])
            else:
                subprocess.Popen(["xdg-open", str(self.output_path)])
        except OSError:
            log.exception("cannot open output")

    # ---- chybová obrazovka --------------------------------------------------------
    def show_error(self, err: BookTrError):
        log.error("showing error %s: %s", err.code, err.detail)
        self.clear()
        msg = friendly_message(err, self.config_obj or object())
        tk.Label(self.frame, text=msg, font=self.font_body, justify="center",
                 wraplength=600).pack(pady=(80, 36))
        label = action_label(err)
        if label == S.QUIT:
            cmd = self.destroy
        elif label == S.PICK_OTHER:
            cmd = self.show_start
        else:  # Zkusit znovu
            cmd = self.retry
        self.big_button(self.frame, label, cmd).pack()

    def retry(self):
        if self.config_obj is None:
            try:
                self.config_obj = load_config()
            except BookTrError as err:
                self.show_error(err)
                return
        if self.current_job is not None:
            self.start_job(self.current_job)
        else:
            self.show_start()

    # ---- běh úlohy -------------------------------------------------------------------
    def start_job(self, job: Job):
        self.current_job = job
        self.pause_event.set()
        self._completed_stages: set[str] = set()
        self._stage_start = None
        self.show_progress(job.title)

        api = ApiClient(
            self.config_obj,
            on_offline=lambda: self.queue.put(("offline", True)),
            on_online=lambda: self.queue.put(("offline", False)),
        )
        engine = Engine(
            job, api, self.config_obj,
            progress_cb=lambda st, c, t: self.queue.put(("progress", st, c, t)),
            pause_event=self.pause_event,
        )

        def work():
            try:
                output = engine.run()
                self.queue.put(("done", output))
            except BookTrError as err:
                self.queue.put(("error", err))
            except Exception as exc:  # noqa: BLE001 — poslední záchrana
                log.exception("worker crashed")
                self.queue.put(("error", categorize(exc)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ---- fronta událostí ----------------------------------------------------------------
    def poll_queue(self):
        try:
            while True:
                event = self.queue.get_nowait()
                self.handle_event(event)
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def handle_event(self, event):
        kind = event[0]
        if kind == "progress":
            _, stage, cur, tot = event
            self.update_progress(stage, cur, tot)
        elif kind == "offline":
            if event[1]:
                self.offline_banner.pack(fill="x", padx=20, pady=(6, 0),
                                         before=self.stage_label)
            else:
                self.offline_banner.pack_forget()
        elif kind == "done":
            self.show_done(event[1])
        elif kind == "error":
            self.show_error(event[1])

    def update_progress(self, stage: str, cur: int, tot: int):
        if not hasattr(self, "stage_label") or not self.stage_label.winfo_exists():
            return
        template = S.STAGE_LABELS.get(stage, "…")
        self.stage_label.config(text=template.format(cur=cur, tot=max(tot, 1)))

        if cur >= tot:
            self._completed_stages.add(stage)
        done_weight = sum(STAGE_WEIGHTS[s] for s in self._completed_stages)
        partial = STAGE_WEIGHTS.get(stage, 0) * (cur / max(tot, 1)) if stage not in self._completed_stages else 0
        fraction = min(1.0, (done_weight + partial) / sum(STAGE_WEIGHTS.values()))
        self.bar_fill.place_configure(relwidth=fraction)

        # hrubý odhad času z rychlosti aktuální fáze
        now = time.monotonic()
        if self._stage_start is None or self._stage_start[0] != stage:
            self._stage_start = (stage, now, cur)
            self.time_label.config(text="")
        else:
            _, t0, c0 = self._stage_start
            done_units = cur - c0
            if done_units >= 2 and tot > cur:
                per_unit = (now - t0) / done_units
                minutes = max(1, round(per_unit * (tot - cur) / 60))
                self.time_label.config(text=S.TIME_LEFT.format(minutes=minutes))

    # ---- zavření okna -----------------------------------------------------------------------
    def on_close(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(S.APP_TITLE, S.CLOSE_SAFE)
        self.destroy()


def run_gui():
    App().mainloop()
