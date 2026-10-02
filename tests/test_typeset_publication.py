import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktr.engine import typeset
from booktr.errors import BookTrError


def publication_engine(tmp_path, monkeypatch):
    job_dir = tmp_path / 'job'
    job_dir.mkdir()
    output_dir = tmp_path / 'output'
    output_dir.mkdir()
    book = {'title': 'Test', 'chapters': []}
    job = SimpleNamespace(dir=job_dir, read_json=lambda name: book if name == 'book.json' else {})
    config = SimpleNamespace(output_path=output_dir, page_format='a5', font_size=14)
    engine = SimpleNamespace(job=job, config=config, report=lambda *args: None)
    monkeypatch.setattr(typeset, 'compile_typ', lambda source, dest: dest.write_bytes(b'new PDF'))
    destination = output_dir / 'Kniha – Test (česky).pdf'
    destination.write_bytes(b'previous good PDF')
    return engine, destination


def test_failed_partial_copy_preserves_previous_pdf_and_cleans_temporary_file(tmp_path, monkeypatch):
    engine, destination = publication_engine(tmp_path, monkeypatch)

    def partial_copy(source, target):
        Path(target).write_bytes(b'partial')
        raise OSError('disk full')

    monkeypatch.setattr(typeset.shutil, 'copyfile', partial_copy)
    with pytest.raises(OSError, match='disk full'):
        typeset.run(engine)

    assert destination.read_bytes() == b'previous good PDF'
    assert list(destination.parent.iterdir()) == [destination]


def test_locked_replace_preserves_previous_pdf_and_reports_existing_error(tmp_path, monkeypatch):
    engine, destination = publication_engine(tmp_path, monkeypatch)

    def locked_replace(source, target):
        raise PermissionError('PDF open in another application')

    monkeypatch.setattr(os, 'replace', locked_replace)
    with pytest.raises(BookTrError) as exc:
        typeset.run(engine)

    assert exc.value.code == 'output_locked'
    assert destination.read_bytes() == b'previous good PDF'
    assert list(destination.parent.iterdir()) == [destination]


def test_successful_publication_replaces_previous_pdf_and_cleans_temporary_file(tmp_path, monkeypatch):
    engine, destination = publication_engine(tmp_path, monkeypatch)

    assert typeset.run(engine) == destination
    assert destination.read_bytes() == b'new PDF'
    assert list(destination.parent.iterdir()) == [destination]
