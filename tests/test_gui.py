"""Real desktop widget smoke checks; skip CI machines without a display."""
import json
import tkinter

import pytest

import booktr.gui as gui
from booktr import config as settings


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'app_dir', lambda: tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'openai_api_key': 'test'}))
    monkeypatch.setenv('BOOKTR_CONFIG_FILE', str(path))
    monkeypatch.setattr(gui, 'list_unfinished', lambda: [])
    monkeypatch.setattr(gui, 'list_ready_books', lambda: [], raising=False)
    try:
        window = gui.App()
    except tkinter.TclError:
        pytest.skip('No Tk display available')
    window.update()
    yield window
    window.destroy()


def widgets(root):
    for child in root.winfo_children():
        yield child
        yield from widgets(child)


def test_main_actions_are_readable_and_fit_small_window(app):
    app.geometry('780x580')
    app.update()
    buttons = [w for w in widgets(app.frame) if isinstance(w, gui.ctk.CTkButton)]
    assert {w.cget('text') for w in buttons} == {'Nastavení', 'Přeložit knihu', 'Audiokniha'}
    for button in buttons:
        assert button.winfo_height() >= 44
        assert button.winfo_rooty() + button.winfo_height() <= app.winfo_rooty() + app.winfo_height()


def test_reader_can_save_settings_without_touching_provider_keys(app, tmp_path):
    gui.show_settings(app)
    app.update()
    window = app.settings_window
    controls = list(widgets(window))
    selector = next(w for w in controls if isinstance(w, gui.ctk.CTkSegmentedButton))
    selector.set('20')
    advanced = next(w for w in controls if isinstance(w, gui.ctk.CTkRadioButton) and w.cget('value') == 'advanced')
    advanced.invoke()
    save = next(w for w in controls if isinstance(w, gui.ctk.CTkButton) and w.cget('text') == 'Uložit nastavení')
    save.invoke()
    assert app.config_obj.font_size == 20
    assert app.config_obj.voice_mode == 'advanced'
    assert json.loads((tmp_path / 'preferences.json').read_text())['font_size'] == 20
    assert json.loads((tmp_path / 'config.json').read_text()) == {'openai_api_key': 'test'}


def _saved_translation(tmp_path):
    from booktr.state import Job
    job = Job(tmp_path / 'saved-book')
    job.set_progress(title='Příběh', status='done', output_mode='pdf',
                     output=str(tmp_path / 'Příběh.pdf'))
    job.write_json('book.json', {'title': 'Story', 'source_lang': 'en', 'chapters': [
        {'id': 'ch01', 'heading': 'Chapter 1', 'paragraphs': [
            {'id': 'ch01-p001', 'text': 'The story begins.'}]}]})
    job.write_json('final.json', {'book-title': 'Příběh', 'ch01-h000': 'Kapitola 1',
                                  'ch01-p001': 'Příběh začíná.'})
    return job


def _use_local_narrator(tmp_path, monkeypatch):
    from test_audio import FakeProvider
    provider = FakeProvider()
    monkeypatch.setattr('booktr.audio.service.make_provider', lambda *a, **kw: provider)
    monkeypatch.setattr('booktr.audio.service.find_ffmpeg', lambda: None)
    (tmp_path / 'config.json').write_text(json.dumps({'openai_api_key': '', 'output_dir': str(tmp_path)}))

    def forbidden(*a, **kw):
        raise AssertionError('saved Czech narration must never create a translation engine')
    monkeypatch.setattr(gui, 'ApiClient', forbidden)
    monkeypatch.setattr(gui, 'Engine', forbidden)
    return provider


def _finish_audio(app):
    app.worker.join(timeout=5)
    assert not app.worker.is_alive()
    app.poll_queue()
    app.update()
    assert not app.busy


def test_finished_pdf_can_be_narrated_without_openai_or_translation(app, tmp_path, monkeypatch):
    job = _saved_translation(tmp_path)
    final_before = (job.dir / 'final.json').read_bytes()
    provider = _use_local_narrator(tmp_path, monkeypatch)
    app.current_job = job
    app.mode = 'pdf'
    app.show_done(tmp_path / 'Příběh.pdf')
    button = next(w for w in widgets(app.frame) if isinstance(w, gui.ctk.CTkButton)
                  and w.cget('text') == 'Vytvořit audioknihu')
    button.invoke()
    _finish_audio(app)
    assert app.output_path.suffix == '.wav' and app.output_path.is_file()
    assert app.pdf_path == tmp_path / 'Příběh.pdf'
    assert (job.dir / 'final.json').read_bytes() == final_before
    assert job.progress()['output_mode'] == 'audio_saved'
    assert 'Příběh začíná.' in ' '.join(provider.calls)
    assert 'The story begins.' not in ' '.join(provider.calls)


def test_main_audiobook_action_reopens_saved_czech_book(app, tmp_path, monkeypatch):
    job = _saved_translation(tmp_path)
    _use_local_narrator(tmp_path, monkeypatch)
    monkeypatch.setattr(gui, 'list_ready_books', lambda: [job])
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kw: '')
    app.geometry('780x580')
    audio = next(w for w in widgets(app.frame) if isinstance(w, gui.ctk.CTkButton)
                 and w.cget('text') == 'Audiokniha')
    audio.invoke()
    app.update()
    buttons = [w for w in widgets(app.frame) if isinstance(w, gui.ctk.CTkButton)]
    select = next(w for w in buttons if w.cget('text') == 'Namluvit')
    assert any(w.cget('text') == 'Vybrat české PDF nebo text' for w in buttons)
    for button in buttons:
        assert button.winfo_rooty() + button.winfo_height() <= app.winfo_rooty() + app.winfo_height()
    select.invoke()
    _finish_audio(app)
    assert app.current_job.dir == job.dir
    assert app.output_path.is_file()
    assert app.pdf_path == tmp_path / 'Příběh.pdf'


def test_imported_pdf_reopened_from_library_keeps_matching_reading_copy(app, tmp_path, monkeypatch):
    import typst
    from booktr.audio import prepare_source
    from booktr.config import Config
    from booktr.state import Job
    template = tmp_path / 'native.typ'
    template.write_text('#set text(font: "Arial")\n= Kapitola 1\nČeská řeka teče klidně.')
    original_pdf = tmp_path / 'Řeka.pdf'
    original_pdf.write_bytes(typst.compile(str(template)))
    job = Job.from_pdf(original_pdf, base_dir=tmp_path / 'narration')
    job.set_progress(audio_source_kind='czech_pdf', source_display_path=str(original_pdf))
    prepare_source(job, Config(output_dir=str(tmp_path)))
    original_pdf.unlink()  # The saved reading copy must still pair with the audio.
    _use_local_narrator(tmp_path, monkeypatch)
    app.start_job(job, 'audio_saved')
    _finish_audio(app)
    assert app.pdf_path == job.source_pdf and app.pdf_path.is_file()
    assert any(isinstance(w, gui.ctk.CTkButton) and w.cget('text') == 'Otevřít také PDF'
               for w in widgets(app.frame))


def test_finished_text_audiobook_does_not_offer_to_create_it_again(app, tmp_path):
    app.current_job = _saved_translation(tmp_path)
    app.mode = 'audio_only'
    app.pdf_path = None
    app.show_done(tmp_path / 'Příběh.wav')
    buttons = {w.cget('text') for w in widgets(app.frame) if isinstance(w, gui.ctk.CTkButton)}
    assert 'Přehrát audioknihu' in buttons
    assert 'Vytvořit audioknihu' not in buttons


def test_combined_job_retries_only_audio_after_pdf_is_finished(app, tmp_path, monkeypatch):
    from test_audio import FakeProvider
    from booktr.errors import BookTrError
    job = _saved_translation(tmp_path)
    pdf = tmp_path / 'Příběh.pdf'

    def completed_translation(engine):
        engine.job.set_progress(status='done', output=str(pdf))
        return pdf

    class FailedSpeech(FakeProvider):
        def synthesize(self, *args, **kwargs):
            raise BookTrError('auth', 'speech unavailable')

    monkeypatch.setattr(gui.Engine, 'run', completed_translation)
    monkeypatch.setattr('booktr.audio.providers.make_provider', lambda *a, **kw: FailedSpeech())
    monkeypatch.setattr('booktr.audio.service.make_provider', lambda *a, **kw: FailedSpeech())
    app.start_job(job, 'audio')
    _finish_audio(app)
    assert job.progress()['output_mode'] == 'audio_saved'
    assert app.mode == 'audio_saved'
    _use_local_narrator(tmp_path, monkeypatch)
    app.retry()
    _finish_audio(app)
    assert app.output_path.is_file() and app.output_path.suffix == '.wav'
    assert app.pdf_path == pdf
