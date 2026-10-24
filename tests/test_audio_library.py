"""Saved Czech books remain usable without reopening translation work."""
from pathlib import Path

from booktr import state


def test_text_import_preserves_exact_utf8_and_reuses_identical_source(tmp_path):
    source = tmp_path / 'Říční příběh.txt'
    source.write_text('Kapitola 1\n\nŽluťoučký kůň.\n', encoding='utf-8')
    first = state.Job.from_text(source, base_dir=tmp_path / 'narration')
    assert (first.dir / 'source.txt').read_bytes() == source.read_bytes()
    assert first.title == 'Říční příběh'
    first.write_json('final.json', {'p1': 'Hotový text.'})
    resumed = state.Job.from_text(source, base_dir=tmp_path / 'narration')
    assert resumed.dir == first.dir
    assert resumed.read_json('final.json') == {'p1': 'Hotový text.'}
    source.write_text('Jiný příběh.', encoding='utf-8')
    changed = state.Job.from_text(source, base_dir=tmp_path / 'narration')
    assert changed.dir != first.dir
    assert not changed.exists('final.json')


def test_library_includes_completed_translation_and_import_but_not_partial_book(tmp_path):
    translated = state.Job(tmp_path / 'translated')
    translated.set_progress(title='Příběh', status='done', output_mode='pdf')
    translated.write_json('book.json', {'chapters': []})
    translated.write_json('final.json', {'book-title': 'Příběh'})
    imported = state.Job(tmp_path / 'narration' / 'text-book')
    imported.set_progress(title='Český text', status='done', output_mode='audio_only')
    imported.write_json('book.json', {'chapters': []})
    imported.write_json('final.json', {'book-title': 'Český text'})
    partial = state.Job(tmp_path / 'partial')
    partial.write_json('book.json', {'chapters': []})
    assert {job.title for job in state.list_ready_books(tmp_path)} == {'Příběh', 'Český text'}


def test_unfinished_text_audio_and_saved_translation_resume_without_source_pdf(tmp_path):
    imported = state.Job(tmp_path / 'narration' / 'text-book')
    (imported.dir / 'source.txt').write_text('Český příběh.', encoding='utf-8')
    imported.set_progress(title='Text', status='running', output_mode='audio_only',
                          audio_source_kind='czech_text')
    saved = state.Job(tmp_path / 'translated')
    saved.write_json('book.json', {'chapters': []})
    saved.write_json('final.json', {'book-title': 'Příběh'})
    saved.set_progress(title='Překlad', status='running', output_mode='audio_saved')
    assert {job.title for job in state.list_unfinished(tmp_path)} == {'Text', 'Překlad'}


def test_interrupted_text_copy_preserves_previous_source_and_cleans_temporary(tmp_path, monkeypatch):
    source = tmp_path / 'source.txt'
    source.write_text('Nový český příběh.', encoding='utf-8')
    job = state.Job.from_text(source, base_dir=tmp_path / 'narration')
    target = job.dir / 'source.txt'
    target.write_bytes(b'older source')
    original_copy = state.shutil.copyfile

    def failed_copy(src, dst):
        Path(dst).write_bytes(b'partial')
        raise OSError('disk full')

    monkeypatch.setattr(state.shutil, 'copyfile', failed_copy)
    try:
        state.Job.from_text(source, base_dir=tmp_path / 'narration')
    except OSError:
        pass
    else:
        raise AssertionError('incomplete source should never be accepted')
    assert target.read_bytes() == b'older source'
    assert not list(job.dir.glob('.source-*.tmp'))
    monkeypatch.setattr(state.shutil, 'copyfile', original_copy)
    state.Job.from_text(source, base_dir=tmp_path / 'narration')
    assert target.read_bytes() == source.read_bytes()
