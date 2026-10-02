import pytest

from booktr.api import ApiClient, parse_json_map
from booktr.config import Config
from booktr.errors import BookTrError
import threading


@pytest.mark.parametrize('raw', ['{"p": null}', '{"p": 3}', '{"p": {"text": "lost"}}'])
def test_translation_map_rejects_non_text_values(raw):
    with pytest.raises(ValueError):
        parse_json_map(raw)


def test_translation_map_accepts_only_actual_text():
    assert parse_json_map('```json\n{"p":"Český text"}\n```') == {'p': 'Český text'}


def test_api_timeout_is_bounded_retry_not_indefinite_offline_loop():
    assert ApiClient._classify(TimeoutError()) == 'retry'


def test_cancelled_api_stops_even_while_offline():
    stop = threading.Event()
    client = ApiClient(Config(openai_api_key='test'), cancel_event=stop)
    client.offline.set()
    stop.set()
    with pytest.raises(BookTrError) as exc:
        client.complete('model', 'system', 'user')
    assert exc.value.code == 'cancelled'


def test_api_errors_do_not_echo_provider_credentials(monkeypatch):
    AuthenticationError = type('AuthenticationError', (Exception,), {})
    client = ApiClient(Config(openai_api_key='test'))
    def fail(*args):
        raise AuthenticationError('Incorrect API key: private-secret')
    monkeypatch.setattr(client, '_request', fail)
    with pytest.raises(BookTrError) as exc:
        client.complete('model', 'system', 'user')
    assert 'private-secret' not in str(exc.value)


def test_api_does_not_retry_already_classified_failures(monkeypatch):
    client = ApiClient(Config(openai_api_key='test'))
    calls = []
    def fail(*args):
        calls.append(1)
        raise BookTrError('unknown', 'truncated')
    monkeypatch.setattr(client, '_request', fail)
    with pytest.raises(BookTrError):
        client.complete('model', 'system', 'user')
    assert len(calls) == 1
