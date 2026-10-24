def test_changed_fingerprint_discards_only_affected_work(job):
    assert job.ensure_fingerprint('translation', 'first', ['draft.json'], ['translate']) is False
    job.write_json('draft.json', {'p': 'old'})
    job.write_json('book.json', {'source': 'keep'})
    job.save_chunk('translate', 'one', {'p': 'old'})
    job.save_chunk('ocr', 'one', {'text': 'keep'})
    assert job.ensure_fingerprint('translation', 'first', ['draft.json'], ['translate']) is True
    assert job.exists('draft.json')
    assert job.ensure_fingerprint('translation', 'second', ['draft.json'], ['translate']) is False
    assert not job.exists('draft.json')
    assert not job.has_chunk('translate', 'one')
    assert job.exists('book.json')
    assert job.has_chunk('ocr', 'one')


def test_initial_fingerprint_invalidates_legacy_artifacts(job):
    job.write_json('draft.json', {'p': 'legacy'})
    job.ensure_fingerprint('translation', 'new', ['draft.json'], ['translate'])
    assert not job.exists('draft.json')


def test_corrupt_fingerprint_metadata_rebuilds_derived_work(job):
    job.write_text('fingerprints.json', 'unfinished {')
    job.write_json('draft.json', {'p': 'old'})
    assert job.ensure_fingerprint('translation', 'new', ['draft.json'], []) is False
    assert not job.exists('draft.json')


def test_interrupted_initial_source_copy_is_repaired(tmp_path):
    from booktr.state import Job
    source = tmp_path / 'input.pdf'
    source.write_bytes(b'complete-source-pdf')
    job = Job.from_pdf(source, base_dir=tmp_path / 'jobs')
    job.source_pdf.write_bytes(b'partial')
    repaired = Job.from_pdf(source, base_dir=tmp_path / 'jobs')
    assert repaired.source_pdf.read_bytes() == source.read_bytes()
