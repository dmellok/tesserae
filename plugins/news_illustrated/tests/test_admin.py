"""The widget's page and its settings: the API keys are manifest settings
under Settings > Widgets, everything else is the page's own, kept in
config.json, with a one-time copy of the values an older version kept in
settings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from plugins.news_illustrated import config

PLUGIN = "news_illustrated"
PAGE = f"/plugins/{PLUGIN}/"


def _data_dir(app: Flask) -> Path:
    path: Path = app.config["PLUGIN_REGISTRY"].get(PLUGIN).data_dir
    return path


def _config(app: Flask) -> dict[str, Any]:
    got: dict[str, Any] = json.loads((_data_dir(app) / "config.json").read_text("utf-8"))
    return got


def _form(**changes: str) -> dict[str, str]:
    """The page's form as a browser posts it with the defaults in it."""
    form = {
        name: str(value) for name, value in config.defaults().items() if not isinstance(value, bool)
    }
    form.update({name: "on" for name, value in config.defaults().items() if value is True})
    form.update(changes)
    return form


def _set_keys(app: Flask, **keys: str) -> None:
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    app.config["SETTINGS_STORE"].update_for_namespace(
        "plugins", PLUGIN, keys, plugin.manifest["settings"]
    )


def test_only_the_api_keys_are_manifest_settings(app: Flask, client: FlaskClient) -> None:
    """The keys show under Settings > Widgets, as upstream's ha_core token
    does; the rest is on the widget's page."""
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    fields = plugin.manifest["settings"]
    assert [f["name"] for f in fields] == list(config.KEYS)
    assert all(f.get("secret") is True for f in fields)
    general = client.get("/settings/plugins").get_data(as_text=True)
    card = general[general.index('id="plugin-news_illustrated"') :]
    card = card[: card.index("</form>")]
    for name in config.KEYS:
        assert f'name="{name}"' in card
    assert 'name="sources"' not in card
    body = client.get(PAGE).get_data(as_text=True)
    assert f'action="{PAGE}settings"' in body and 'name="sources"' in body
    form = body.split('id="settings"')[1].split("</form>")[0]
    assert not any(f'name="{name}"' in form for name in config.KEYS), "no key boxes here"


def test_the_page_says_which_keys_are_set_and_links_to_them(
    app: Flask, client: FlaskClient
) -> None:
    _set_keys(app, groq_api_key="gsk-private-test", kie_api_key="kie key with gaps")
    body = client.get(PAGE).get_data(as_text=True)
    assert "gsk-private-test" not in body and "with gaps" not in body

    def state(name: str) -> str:
        return body.split(f'data-ain-key="{name}" data-state="')[1].split('"')[0]

    assert state("groq_api_key") == "set"
    assert state("kie_api_key") == "unusable"
    assert state("gemini_api_key") == "unset"
    assert 'href="/settings/plugins#plugin-news_illustrated"' in body
    groq = body.split("data-ain-groq")[1].split(">")[0]
    assert 'data-has-key="1"' in groq
    kie = body.split('data-ain-prov="kie"')[1].split(">")[0]
    assert 'data-has-key="0"' in kie


def test_a_key_that_no_longer_decrypts_says_so(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = app.config["SETTINGS_STORE"]
    monkeypatch.setattr(store, "unreadable_secrets", lambda *a: {"gemini_api_key_secret"})
    body = client.get(PAGE).get_data(as_text=True)
    line = body.split('data-ain-key="gemini_api_key" data-state="unreadable"')[1]
    line = line.split("</p>")[0]
    assert "can no longer be decrypted" in line and "Enter it again" in line


def test_a_save_writes_config_json_and_returns_to_the_settings(
    app: Flask, client: FlaskClient
) -> None:
    _set_keys(app, groq_api_key="private-test-key")
    result = client.post(
        f"{PAGE}settings",
        data=_form(
            sources="Local | https://example.test/rss\r\nMore | https://example.test/2",
            run_at="07:15",
            summary_words="60",
            summary_service="off",
            provider_order="none",
            image_shape="4:3",
            caption_bg="#FFFFFF",
            caption_fg="#000000",
            groq_model="  some/model  ",
        )
        | {"groq_api_key": "posted-but-ignored"},
    )
    assert result.status_code == 302
    assert result.location == f"{PAGE}#settings"
    saved = _config(app)
    assert saved["sources"] == "Local | https://example.test/rss\nMore | https://example.test/2"
    assert (saved["run_at"], saved["summary_words"], saved["summary_service"]) == (
        "07:15",
        60,
        "off",
    )
    assert (saved["caption_bg"], saved["groq_model"]) == ("#ffffff", "some/model")
    assert "groq_api_key" not in saved
    with client.session_transaction() as session:
        assert ("ok", "Settings saved.") in session["_flashes"]
    # The keys stay in the settings store, untouched by the page.
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    keys = app.config["SETTINGS_STORE"].get_for_runtime(
        "plugins", PLUGIN, plugin.manifest["settings"]
    )
    assert keys["groq_api_key"] == "private-test-key"
    # What the pipeline gets: both, merged.
    mod = plugin.server_module
    cfg = mod.pipeline.Config.from_settings(mod.merged(mod.config.load(_data_dir(app)), keys))
    assert cfg.groq_key == "private-test-key"
    assert cfg.paint.order == () and cfg.painters_ready == []
    assert (cfg.summary_service, cfg.image_shape, cfg.run_at) == ("off", "4:3", "07:15")


def test_unticked_boxes_save_as_off(app: Flask, client: FlaskClient) -> None:
    form = _form()
    for name in ("show_date", "show_headline"):
        del form[name]
    client.post(f"{PAGE}settings", data=form)
    saved = _config(app)
    assert (saved["show_tag"], saved["show_date"], saved["show_headline"]) == (True, False, False)
    look = app.config["PLUGIN_REGISTRY"].get(PLUGIN).server_module.look(saved)
    assert (look["date"], look["title"], look["tag"]) == (False, False, True)


@pytest.mark.parametrize(
    ("field", "value", "words"),
    [
        ("summary_words", "5", "Summary length (words) must be a whole number from 10 to 300"),
        ("summary_words", "301", "from 10 to 300"),
        ("summary_words", "sixty", "from 10 to 300"),
        ("run_at", "25:00", "must be a time such as 06:05"),
        ("run_at", "noon", "must be a time such as 06:05"),
        ("summary_service", "openai", "Summaries by must be one of groq, off"),
        ("image_shape", "5:1", "Picture shape must be one of"),
        ("caption_bg", "red", "Gradient colour must be a colour such as #000000"),
        ("caption_fg", "#fff", "Text colour must be a colour"),
    ],
)
def test_an_invalid_value_saves_nothing(
    app: Flask, client: FlaskClient, field: str, value: str, words: str
) -> None:
    client.post(f"{PAGE}settings", data=_form(sources="First | https://example.test/1"))
    result = client.post(
        f"{PAGE}settings", data=_form(sources="Second | https://example.test/2", **{field: value})
    )
    assert result.status_code == 302 and result.location == f"{PAGE}#settings"
    assert _config(app)["sources"] == "First | https://example.test/1", "nothing saved"
    with client.session_transaction() as session:
        flashes = session["_flashes"]
    category, message = flashes[-1]
    assert category == "error" and message.startswith("Settings not saved: ")
    assert words in message


def test_blank_number_and_time_are_their_defaults(app: Flask, client: FlaskClient) -> None:
    client.post(f"{PAGE}settings", data=_form(summary_words="", run_at=""))
    saved = _config(app)
    assert (saved["summary_words"], saved["run_at"]) == (70, "06:05")


def test_a_hand_made_post_keeps_what_it_does_not_carry(app: Flask, client: FlaskClient) -> None:
    client.post(f"{PAGE}settings", data=_form(image_prompt="Draw {title}", run_at="08:00"))
    client.post(f"{PAGE}settings", data={"sources": "Only | https://example.test/rss"})
    saved = _config(app)
    assert saved["sources"] == "Only | https://example.test/rss"
    assert (saved["image_prompt"], saved["run_at"]) == ("Draw {title}", "08:00")


def test_load_reads_over_the_defaults_and_ignores_junk(tmp_path: Path) -> None:
    assert config.load(tmp_path) == config.defaults()
    assert not (tmp_path / "config.json").exists(), "nothing to migrate from: nothing written"
    (tmp_path / "config.json").write_text(
        json.dumps({"summary_words": 999, "caption_bg": "#ABCDEF", "nonsense": 1, "show_tag": 0}),
        encoding="utf-8",
    )
    got = config.load(tmp_path)
    assert got["summary_words"] == 70 and got["caption_bg"] == "#abcdef"
    assert got["show_tag"] is True and "nonsense" not in got
    (tmp_path / "config.json").write_text("{broken", encoding="utf-8")
    assert config.load(tmp_path, lambda: {"run_at": "09:00"}) == config.defaults()


def test_save_is_atomic_and_checks_first(tmp_path: Path) -> None:
    config.save(tmp_path, {"run_at": "7:05"})
    assert config.load(tmp_path)["run_at"] == "07:05"
    with pytest.raises(ValueError):
        config.save(tmp_path, {"run_at": "07:05", "summary_words": 1})
    assert config.load(tmp_path)["summary_words"] == 70
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"], "no temp file left"


# Alex's settings.json before the move: the page's settings among the keys.
LEGACY = {
    "sources": "Tech | https://hnrss.org/frontpage?points=100\nLocal | https://example.test/rss",
    "run_at": "06:30",
    "summary_words": 55,
    "summary_service": "groq",
    "groq_model": "openai/gpt-oss-20b",
    "summary_prompt": "Tell it like a pirate, in {words} words.",
    "provider_order": "gemini, kie",
    "kie_model": "google/nano-banana-pro",
    "image_prompt": "My own cartoon prompt about {title}: {summary}. {shape}.",
    "image_shape": "3:4",
    "caption_bg": "#112233",
    "caption_fg": "#fafafa",
    "show_tag": True,
    "show_date": False,
    "show_count": False,
    "show_headline": True,
    "show_summary": True,
}


def _legacy_install(app: Flask) -> None:
    """Write the old settings straight into settings.json's section, as the
    old version saved them (secrets as ``<name>_secret``)."""
    store = app.config["SETTINGS_STORE"]
    _set_keys(app, groq_api_key="gsk-live", kie_api_key="kie-live")
    old_fields = [{"name": name} for name in LEGACY]
    store.update_for_namespace("plugins", PLUGIN, dict(LEGACY), old_fields)


def test_the_first_read_copies_the_old_settings_into_config_json(
    app: Flask, client: FlaskClient
) -> None:
    _legacy_install(app)
    store = app.config["SETTINGS_STORE"]
    before = json.loads(json.dumps(store.raw_section("plugins")[PLUGIN]))
    assert not (_data_dir(app) / "config.json").exists()
    body = client.get(PAGE).get_data(as_text=True)
    saved = _config(app)
    assert {name: saved[name] for name in LEGACY} == LEGACY
    # His own prompts survive, and are what the page shows.
    assert saved["summary_prompt"] == "Tell it like a pirate, in {words} words."
    assert "My own cartoon prompt about {title}" in body
    # Unsaved settings get today's defaults.
    assert saved["gemini_model"] == "gemini-2.5-flash-image"
    # settings.json is untouched: old values in place for a rollback, the
    # keys where they were and still encrypted.
    after = store.raw_section("plugins")[PLUGIN]
    assert after == before
    assert after["summary_prompt"] == LEGACY["summary_prompt"]
    assert "groq_api_key_secret" in after and after["groq_api_key_secret"] != "gsk-live"
    assert not any(isinstance(v, str) and "gsk-live" in v for v in saved.values())


def test_the_copy_happens_once(app: Flask, client: FlaskClient) -> None:
    _legacy_install(app)
    client.get(PAGE)
    client.post(f"{PAGE}settings", data=_form(run_at="10:10"))
    # A later change to the old values (a rollback and back) is not copied again.
    app.config["SETTINGS_STORE"].update_for_namespace(
        "plugins", PLUGIN, {"run_at": "11:11"}, [{"name": "run_at"}]
    )
    client.get(PAGE)
    assert _config(app)["run_at"] == "10:10"


def test_a_render_migrates_too_and_uses_the_old_values(app: Flask) -> None:
    _legacy_install(app)
    mod = app.config["PLUGIN_REGISTRY"].get(PLUGIN).server_module
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    keys = app.config["SETTINGS_STORE"].get_for_runtime(
        "plugins", PLUGIN, plugin.manifest["settings"]
    )
    ctx = {"data_dir": str(_data_dir(app)), "preview": True, "settled": False, "cell_id": "c1"}
    with app.app_context():
        out = mod.fetch({}, keys, ctx=ctx)
    assert out["run_at"] == "06:30"
    assert out["look"]["bg"] == "#112233" and out["look"]["date"] is False
    assert _config(app)["image_prompt"] == LEGACY["image_prompt"]


def test_an_old_value_that_no_longer_fits_is_the_default(app: Flask, client: FlaskClient) -> None:
    app.config["SETTINGS_STORE"].update_for_namespace(
        "plugins",
        PLUGIN,
        {"summary_words": 5000, "image_shape": "7:2", "caption_fg": "white", "run_at": "6:05"},
        [{"name": n} for n in ("summary_words", "image_shape", "caption_fg", "run_at")],
    )
    client.get(PAGE)
    saved = _config(app)
    assert (saved["summary_words"], saved["image_shape"], saved["caption_fg"]) == (
        70,
        "auto",
        "#ffffff",
    )
    assert saved["run_at"] == "06:05"


def test_the_admin_page_is_not_a_render_safe_endpoint(app: Flask, client: FlaskClient) -> None:
    app.config["TESTING"] = False
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": False})
    from app.auth import install_gate, set_password

    set_password(store, "test-admin-password")
    install_gate(app, store)
    for response in (client.get(PAGE), client.post(f"{PAGE}settings", data=_form())):
        assert response.status_code == 302
        assert "/login" in response.location
    assert not (_data_dir(app) / "config.json").exists()


def test_the_page_reads_stories_run_then_settings(app: Flask, client: FlaskClient) -> None:
    """Today's stories, the last run, then the settings as one form over four
    cards, saved by one bar (and a plain button without JavaScript)."""
    (_data_dir(app) / "state.json").write_text(
        '{"last_run": {"reason": "manual", "started": 1, "finished": 2, "log": ["Run started"]}}',
        encoding="utf-8",
    )
    body = client.get(PAGE).get_data(as_text=True)
    order = [body.index(f'id="{i}"') for i in ("stories", "last-run", "settings")]
    assert order == sorted(order)
    form = body[body.index('id="settings"') :]
    form = form[: form.index("</form>")]
    for card in ("ain-set-stories", "ain-set-summaries", "ain-set-pictures", "ain-set-frame"):
        assert f'id="{card}"' in form
    assert form.count("data-save-bar") == 1
    assert '<noscript><div class="actions"><button type="submit"' in form
    assert "data-ain-log" in body[body.index('id="last-run"') :]
