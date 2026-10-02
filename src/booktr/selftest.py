"""Offline distribution smoke test: packaged assets, native PDF and M4B tools."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from .audio.providers import PcmAudio
from .audio.service import AudiobookService
from .config import Config
from .engine import Engine
from .engine.typeset import run as typeset
from .state import Job


def run() -> Path:
    import customtkinter  # Packaging must carry both theme and font resources.
    assert customtkinter.ThemeManager.theme
    directory = Path(tempfile.mkdtemp(prefix='booktr-selftest-'))
    job = Job(directory / 'job')
    job.set_progress(title='Kontrola BookTr')
    assets = job.dir / 'assets'
    assets.mkdir()
    Image.new('RGB', (200, 300), '#27664d').save(assets / 'cover.png')
    Image.new('RGB', (120, 80), '#bda66d').save(assets / 'figure.png')
    book = {'title':'Kontrola BookTr','source_lang':'cs','chapters':[
        {'id':'ch01','heading':'První kapitola','paragraphs':[
            {'id':'ch01-p001','text':'Česká kniha s poznámkou[[FN:fn-001]].'}]}],
        'footnotes':[{'id':'fn-001','label':'1','text':'Původní poznámka autora.','source_page':2}],
        'cover':{'id':'cover','path':'assets/cover.png','source_page':1},
        'images':[{'id':'figure','path':'assets/figure.png','source_page':2,'after_paragraph':'ch01-p001'}]}
    final = {'book-title':book['title'],'ch01-h000':'První kapitola',
             'ch01-p001':book['chapters'][0]['paragraphs'][0]['text'], 'fn-001':'Původní poznámka autora.'}
    job.write_json('book.json',book)
    job.write_json('final.json',final)
    cfg=Config(output_dir=str(directory))
    pdf=typeset(Engine(job, None, cfg))
    assert pdf.stat().st_size > 1000
    class OfflineVoice:
        identity = {'provider':'offline-self-test','sample_rate':24000}
        max_chars, max_bytes = 1800, None
        def validate(self):
            pass
        def synthesize(self,*args,**kwargs):
            return PcmAudio(b'\x00\x00' * 24000)
        def close(self):
            pass
    audio=AudiobookService(job,cfg,provider=OfflineVoice()).run(book,final)
    assert audio.suffix == '.m4b', 'Bundled FFmpeg did not create M4B'
    assert audio.stat().st_size > 100
    print(f'BookTr offline self-test passed: {directory}', flush=True)
    return directory
