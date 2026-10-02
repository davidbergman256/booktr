"""Offline distribution smoke test: packaged assets, native PDF and M4B tools."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from .audio.providers import PcmAudio
from .audio import run_saved, run_source
from .config import Config
from .engine import Engine
from .engine.typeset import run as typeset
from .state import Job


def _configuration_self_test(directory: Path) -> None:
    """Exercise the packaged loader in isolation from every real account."""
    from . import config

    private_dir = directory / 'private-config'
    private_dir.mkdir()
    config_file = private_dir / 'config.json'
    config_file.write_text(json.dumps({'openai_api_key': 'json-selftest-value', 'font_size': 16}),
                           encoding='utf-8')
    synthetic = {'OPENAI_API_KEY': 'dotenv-selftest-openai', 'GOOGLE_API_KEY': 'dotenv-selftest-google',
                 'ELEVENLABS_API_KEY': 'dotenv-selftest-eleven', 'ELEVENLABS_VOICE_ID': 'selftest-narrator'}
    (private_dir / '.env').write_text(''.join(f'{key}={value}\n' for key, value in synthetic.items()),
                                    encoding='utf-8')
    environment = {'BOOKTR_CONFIG_FILE': str(config_file)}
    # mock restores the original environment and app-folder function even when
    # an assertion fails. Only synthetic files are opened during this scope.
    with patch.dict(os.environ, environment, clear=True), patch.object(config, 'app_dir', return_value=private_dir):
        loaded = config.load_config()
        assert loaded.openai_api_key == synthetic['OPENAI_API_KEY'], 'Private dotenv did not load'
        assert loaded.google_api_key == synthetic['GOOGLE_API_KEY'], 'Google dotenv credential did not load'
        assert loaded.elevenlabs_api_key == synthetic['ELEVENLABS_API_KEY'], 'ElevenLabs dotenv credential did not load'
        assert loaded.elevenlabs_voice_id == synthetic['ELEVENLABS_VOICE_ID']
        assert loaded.font_size == 16, 'Private JSON reader settings changed'
        assert dict(os.environ) == environment, 'Configuration mutated the process environment'
        assert all(value not in repr(loaded) for key, value in synthetic.items() if key.endswith('_API_KEY'))


def run() -> Path:
    import customtkinter  # Packaging must carry both theme and font resources.
    assert customtkinter.ThemeManager.theme
    directory = Path(tempfile.mkdtemp(prefix='booktr-selftest-'))
    _configuration_self_test(directory)
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
    job.set_progress(output=str(pdf), status='done')
    original_text=(job.dir / 'final.json').read_bytes()
    class OfflineVoice:
        identity = {'provider':'offline-self-test','sample_rate':24000}
        max_chars, max_bytes = 1800, None
        def validate(self):
            pass
        def synthesize(self,*args,**kwargs):
            return PcmAudio(b'\x00\x00' * 24000)
        def close(self):
            pass
    audio=run_saved(job,cfg,provider=OfflineVoice())
    assert audio.suffix == '.m4b', 'Bundled FFmpeg did not create M4B'
    assert audio.stat().st_size > 100
    assert job.progress()['output'] == str(pdf)
    assert (job.dir / 'final.json').read_bytes() == original_text
    imported=Job.from_pdf(pdf, base_dir=directory / 'narration')
    imported.set_progress(audio_source_kind='czech_pdf')
    imported_audio=run_source(imported,cfg,provider=OfflineVoice())
    assert imported_audio.suffix == '.m4b' and imported_audio.stat().st_size > 100
    assert imported.read_json('book.json')['source_lang'] == 'cs'
    assert not cfg.openai_api_key  # Native PDF narration works without translation credentials.
    print(f'BookTr offline self-test passed: {directory}', flush=True)
    return directory
