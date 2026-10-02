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
