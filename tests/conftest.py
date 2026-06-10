import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # mock_api importovatelná z testů

from booktr.config import Config  # noqa: E402
from booktr.state import Job  # noqa: E402


class FakeEngine:
    """Minimální náhrada Engine pro testy jednotlivých fází."""

    def __init__(self, job, api, config):
        self.job = job
        self.api = api
        self.config = config
        self.reports = []

    def report(self, stage, cur, tot):
        self.reports.append((stage, cur, tot))

    def checkpoint_wait(self):
        pass


@pytest.fixture
def config():
    return Config(openai_api_key="test", helper_name="David", helper_phone="123")


@pytest.fixture
def job(tmp_path):
    j = Job(tmp_path / "job")
    j.set_progress(title="Testovací kniha", status="new")
    return j


@pytest.fixture
def fake_engine(job, config):
    from mock_api import MockApi

    return FakeEngine(job, MockApi(), config)


@pytest.fixture
def sample_book():
    def para(ch, n, text):
        return {"id": f"ch{ch:02d}-p{n:03d}", "text": text}

    return {
        "title": "Testovací kniha",
        "source_lang": "en",
        "chapters": [
            {"id": "ch01", "heading": "First", "paragraphs": [
                para(1, 1, "Alice went to the woods."),
                para(1, 2, "It was dark and quiet there."),
            ]},
            {"id": "ch02", "heading": "Second", "paragraphs": [
                para(2, 1, "The next morning was bright."),
            ]},
        ],
    }
