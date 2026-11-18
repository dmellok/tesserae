"""Atlas Cloud tasks survive a local timeout without submitting or charging twice."""

import io
import json

import pytest
from PIL import Image

from app import atlas_images, fal, genai, kie_images


@pytest.fixture
def api(monkeypatch):
    calls = []
    state = {"status": "completed"}
    out = io.BytesIO()
    Image.new("RGB", (16, 16)).save(out, "PNG")

    def request(url, body=None, key="", timeout=60):
        calls.append((url, body, key))
        if url.endswith("generateImage"):
            return {"code": 200, "data": {"id": "paid-task", "status": "processing"}}
        if state["status"] == "timeout":
            raise atlas_images.AtlasError("Atlas Cloud took too long to answer")
        return {
            "code": 200,
            "data": {
                "status": state["status"],
                "error": "Content declined",
                "outputs": ["https://cdn.example/out.png"],
            },
        }

    monkeypatch.setattr(atlas_images, "_json", request)
    monkeypatch.setattr(
        atlas_images,
        "_request",
        lambda url, body=None, key="", timeout=60, content_type="": (
            calls.append((url, body, key))
            or {"code": 200, "data": {"download_url": "https://cdn.example/ref.png"}}
        ),
    )
    monkeypatch.setattr(atlas_images, "_download", lambda url: out.getvalue())
    monkeypatch.setattr(atlas_images, "POLL_S", 0)
    return calls, state, out.getvalue()


def client(tmp_path, charges, **kw):
    return fal.FalClient(
        "fal-key",
        image_service="atlascloud",
        atlas_key="atlas-key",
        task_dir=tmp_path,
        charge=lambda c, m: charges.append((c, m)),
        **kw,
    )


def creates(calls):
    return [b for u, b, _ in calls if u.endswith("generateImage")]


def test_text_to_image_request_and_charge(tmp_path, api):
    calls, _, png = api
    charges = []
    assert client(tmp_path, charges).generate("nano_banana_t2i", "bird", aspect="3:4") == png
    assert creates(calls) == [
        {
            "model": "google/nano-banana/text-to-image",
            "prompt": "bird",
            "aspect_ratio": "3:4",
            "output_format": "png",
        }
    ]
    assert charges == [(4, "nano_banana_atlas_t2i")]
    assert all(k == "atlas-key" for _, _, k in calls)
    assert calls[-1][0] == f"{atlas_images.BASE}/model/prediction/paid-task"


def test_edit_uploads_the_reference_and_pro_asks_for_1k(tmp_path, api):
    calls, _, png = api
    charges = []
    c = client(tmp_path, charges)
    assert c.generate("nano_banana", "paint bird", refs=[fal.data_uri(png)]) == png
    assert c.generate("nano_banana_pro", "paint bird", refs=["https://ref.example/a.jpg"]) == png
    edit, pro = creates(calls)
    assert edit["model"] == "google/nano-banana/edit"
    assert edit["images"] == ["https://cdn.example/ref.png"]
    assert pro["model"] == "google/nano-banana-pro/edit"
    assert pro["images"] == ["https://ref.example/a.jpg"]
    assert pro["resolution"] == "1k"
    assert charges == [(4, "nano_banana_atlas"), (14, "nano_banana_pro_atlas")]


def test_upload_sends_multipart_with_the_decoded_picture(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(b'{"code":200,"data":{"download_url":"https://cdn.example/r.png"}}')

    monkeypatch.setattr(atlas_images.urllib.request, "urlopen", urlopen)
    assert atlas_images._upload(fal.data_uri(b"PNGDATA"), "secret") == "https://cdn.example/r.png"
    req = seen[0]
    assert req.full_url == f"{atlas_images.BASE}/model/uploadMedia"
    assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert b'name="file"' in req.data and b"PNGDATA" in req.data
    assert req.get_header("Authorization") == "Bearer secret"
    assert req.get_header("User-agent") == "tesserae/artwork"


def test_timeout_then_new_client_collects_once(tmp_path, api):
    calls, state, png = api
    charges = []
    state["status"] = "timeout"
    with pytest.raises(fal.FalError, match="paid task paid-task is saved"):
        client(tmp_path, charges, timeout=0.01).generate("nano_banana_t2i", "bird")
    state["status"] = "completed"
    assert client(tmp_path, charges).generate("nano_banana_t2i", "bird") == png
    assert len(creates(calls)) == 1
    assert len(charges) == 1


def test_failed_task_leaves_nothing_behind_and_a_retry_starts_a_new_task(tmp_path, api):
    calls, state, png = api
    state["status"] = "failed"
    with pytest.raises(fal.FalError, match="Content declined"):
        client(tmp_path, []).generate("nano_banana_t2i", "bird")
    assert not list(tmp_path.glob("*.json"))
    state["status"] = "completed"
    assert client(tmp_path, []).generate("nano_banana_t2i", "bird") == png
    assert len(creates(calls)) == 2


def test_refused_submission_is_refunded_and_can_be_retried(tmp_path, api, monkeypatch):
    original = atlas_images._json

    def request(url, *args, **kw):
        if url.endswith("generateImage"):
            raise atlas_images.AtlasRefused("Atlas Cloud: insufficient balance", 402)
        return original(url, *args, **kw)

    monkeypatch.setattr(atlas_images, "_json", request)
    charges, refunds = [], []
    c = client(tmp_path, charges, refund=lambda cents, m: refunds.append((cents, m)))
    with pytest.raises(fal.FalError, match="insufficient balance"):
        c.generate("nano_banana_t2i", "bird")
    assert charges == refunds == [(4, "nano_banana_atlas_t2i")]
    assert c.spent_cents == 0
    assert not list(tmp_path.glob("*.json"))


def test_uncertain_submission_is_never_repeated(tmp_path, api, monkeypatch):
    original = atlas_images._json

    def request(url, *args, **kw):
        if url.endswith("generateImage"):
            raise TimeoutError()
        return original(url, *args, **kw)

    monkeypatch.setattr(atlas_images, "_json", request)
    charges = []
    with pytest.raises(fal.FalError):
        client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    with pytest.raises(fal.FalError, match=r"submission.*unknown"):
        client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    assert len(charges) == 1


def test_a_saved_task_keeps_its_provider_when_the_choice_changes(tmp_path, api):
    calls, state, png = api
    state["status"] = "timeout"
    with pytest.raises(fal.FalError):
        client(tmp_path, [], timeout=0.01).generate("nano_banana_t2i", "bird")
    state["status"] = "completed"
    other = fal.FalClient(
        "fal-key",
        image_service="kie",
        kie_key="kie-key",
        atlas_key="atlas-key",
        task_dir=tmp_path,
    )
    assert other.generate("nano_banana_t2i", "bird") == png
    assert all(u.startswith(atlas_images.BASE) for u, _, _ in calls)


def test_a_saved_kie_task_still_goes_to_kie(tmp_path):
    (tmp_path / "0.json").write_text(json.dumps({"identity": "x", "task_id": "t"}))
    assert fal._saved_provider(tmp_path / "0.json") == "kie"
    (tmp_path / "0.json").write_text(json.dumps({"provider": "atlascloud"}))
    assert fal._saved_provider(tmp_path / "0.json") == "atlascloud"


def test_explicit_choice_never_falls_back_to_other_paid_service(tmp_path):
    with pytest.raises(fal.FalError, match="Atlas Cloud key"):
        fal.FalClient(
            "fal-key", image_service="atlascloud", atlas_key="", task_dir=tmp_path
        ).generate("nano_banana_t2i", "bird")


def test_transport_authenticates_and_names_itself(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(b'{"code":200,"data":{}}')

    monkeypatch.setattr(atlas_images.urllib.request, "urlopen", urlopen)
    atlas_images._json(atlas_images.BASE + "/model/generateImage", {"model": "m"}, "secret")
    atlas_images._json(atlas_images.BASE + "/model/prediction/x", key="secret")
    assert [r.get_method() for r in seen] == ["POST", "GET"]
    assert all(r.get_header("Authorization") == "Bearer secret" for r in seen)
    assert all(r.get_header("User-agent") == "tesserae/artwork" for r in seen)


@pytest.mark.parametrize("code,message", [(401, "unauthorized"), (402, "insufficient balance")])
def test_api_failure_messages_even_on_http_200(monkeypatch, code, message):
    monkeypatch.setattr(
        atlas_images.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps({"code": code, "msg": message}).encode()),
    )
    with pytest.raises(atlas_images.AtlasRefused, match=message):
        atlas_images._json(atlas_images.BASE + "/model/generateImage", {}, "key")


def test_check_key_reads_404_as_a_good_key_and_401_as_a_bad_one(monkeypatch):
    def answer(code):
        def request(url, *a, **k):
            raise atlas_images.AtlasRefused("x", code)

        return request

    monkeypatch.setattr(atlas_images, "_json", answer(404))
    assert atlas_images.check_key("good")[0] is True
    monkeypatch.setattr(atlas_images, "_json", answer(401))
    assert atlas_images.check_key("bad") == (False, "x")
    assert atlas_images.check_key(" ") == (False, "No key to check.")


def test_choose_atlascloud_and_store_its_key_encrypted(app, tmp_path, monkeypatch):
    from tests.test_genai import _signed_in

    client = _signed_in(app)
    response = client.post(
        "/settings/genai",
        data={"image_service": "atlascloud", "atlascloud_api_key": "private-atlas-key"},
    )
    assert response.status_code == 302
    with app.app_context():
        assert atlas_images.configuration() == ("atlascloud", "private-atlas-key")
        assert fal.painting_key({"fal_api_key": "old-key"}) == "old-key"
        assert fal.painting_key({}) == fal.DIRECT_ONLY
    assert "private-atlas-key" not in (tmp_path / "core" / "settings.json").read_text()
    html = client.get("/settings/genai").get_data(as_text=True)
    assert '<option value="atlascloud" selected>' in html
    assert "private-atlas-key" not in html
    assert 'name="atlascloud_api_key"' in html
    client.post("/settings/genai", data={"atlascloud_api_key": "********"})
    with app.app_context():
        assert atlas_images.configuration()[1] == "private-atlas-key"
    monkeypatch.setattr(atlas_images, "check_key", lambda key: (True, f"ok {key[:7]}"))
    response = client.post(
        "/settings/genai/check-atlascloud",
        data={"atlascloud_api_key": "********"},
        follow_redirects=True,
    )
    assert "ok private" in response.get_data(as_text=True)


def test_environment_key_is_used_when_none_is_saved(app, monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "env-key")
    genai.save(app.config["SETTINGS_STORE"], {"image_service": "atlascloud"})
    with app.app_context():
        assert atlas_images.configuration() == ("atlascloud", "env-key")
    assert "atlascloud" in kie_images.SERVICES
