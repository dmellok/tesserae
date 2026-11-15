"""Paid kie tasks survive a local timeout without submitting or charging twice."""

import io
import json
import threading

import pytest
from PIL import Image

from app import artwork, fal, genai, kie_images


@pytest.fixture
def api(monkeypatch):
    calls = []
    state = {"status": "success"}
    out = io.BytesIO()
    Image.new("RGB", (16, 16)).save(out, "PNG")

    def request(url, body=None, key="", timeout=60):
        calls.append((url, body, key))
        if url.endswith("credit"):
            return {"data": 100}
        if "upload" in url:
            return {"data": {"downloadUrl": "https://cdn.example/ref.png"}}
        if url.endswith("createTask"):
            return {"data": {"taskId": "paid-task"}}
        if state["status"] == "timeout":
            raise kie_images.KieError("kie.ai took too long to answer")
        return {
            "data": {
                "state": state["status"],
                "failMsg": "Content declined",
                "resultJson": json.dumps({"resultUrls": ["https://cdn.example/out.png"]}),
            }
        }

    monkeypatch.setattr(kie_images, "_json", request)
    monkeypatch.setattr(kie_images, "_download", lambda url: out.getvalue())
    monkeypatch.setattr(kie_images, "POLL_S", 0)
    return calls, state, out.getvalue()


def client(tmp_path, charges, **kw):
    return fal.FalClient(
        "fal-key",
        image_service="kie",
        kie_key="kie-key",
        task_dir=tmp_path,
        charge=lambda c, m: charges.append((c, m)),
        **kw,
    )


def test_selected_provider_and_edit_upload(tmp_path, api):
    calls, _, png = api
    charges = []
    c = client(tmp_path, charges)
    assert c.generate("nano_banana", "paint bird", refs=[fal.data_uri(png)], aspect="3:4") == png
    create = next(b for u, b, k in calls if u.endswith("createTask"))
    assert create == {
        "model": "google/nano-banana-edit",
        "input": {
            "prompt": "paint bird",
            "image_urls": ["https://cdn.example/ref.png"],
            "output_format": "png",
            "aspect_ratio": "3:4",
        },
    }
    assert charges == [(2, "nano_banana_kie")]
    assert all(k == "kie-key" for _, _, k in calls)


def test_timeout_then_new_client_collects_once(tmp_path, api):
    calls, state, png = api
    charges = []
    state["status"] = "timeout"
    with pytest.raises(fal.FalError, match=r"paid task.*saved"):
        client(tmp_path, charges, timeout=0.01).generate("nano_banana_t2i", "bird")
    assert "paid-task" in (tmp_path / "0.json").read_text()
    state["status"] = "success"
    assert client(tmp_path, charges).generate("nano_banana_t2i", "bird") == png
    assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1
    assert charges == [(2, "nano_banana_kie_t2i")]
    # A crash after download but before the widget saves the picture is safe too.
    assert client(tmp_path, charges).generate("nano_banana_t2i", "bird") == png
    assert len(charges) == 1


def test_failed_task_is_reported_without_paying_again(tmp_path, api):
    calls, state, _ = api
    state["status"] = "fail"
    charges = []
    for _ in range(2):
        with pytest.raises(fal.FalError, match="Content declined"):
            client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1
    assert len(charges) == 1


def test_uncertain_submission_is_never_repeated(tmp_path, api, monkeypatch):
    original = kie_images._json

    def request(url, *args, **kw):
        if url.endswith("createTask"):
            raise TimeoutError()
        return original(url, *args, **kw)

    monkeypatch.setattr(kie_images, "_json", request)
    charges = []
    with pytest.raises(fal.FalError):
        client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    with pytest.raises(fal.FalError, match=r"submission.*unknown"):
        client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    assert len(charges) == 1


def test_explicit_choice_never_falls_back_to_other_paid_service(tmp_path):
    with pytest.raises(fal.FalError, match=r"kie.ai key"):
        fal.FalClient("fal-key", image_service="kie", kie_key="", task_dir=tmp_path).generate(
            "nano_banana_t2i", "bird"
        )


@pytest.mark.parametrize("cap,ceiling,message", [(2, 10, "budget"), (10, 2, "ceiling")])
def test_budget_blocks_new_task_but_allows_collection(tmp_path, api, cap, ceiling, message):
    calls, state, png = api
    c = artwork.Collection("kie_test", "paintings", make=lambda j: (b"", {}), label="Kie test")
    artwork.host_configure(
        ledger=genai.Ledger(tmp_path / "ledger"), ceilings=lambda: (ceiling, ceiling)
    )
    try:
        state["status"] = "timeout"
        f = c._fal_for(tmp_path, "key", threading.Event(), cap)
        f.image_service, f.kie_key, f.task_dir = "kie", "key", tmp_path / "tasks"
        f._timeout = 0.01
        with pytest.raises(fal.FalError):
            f.generate("nano_banana_t2i", "bird")
        assert c.budget_used(tmp_path) == 2
        state["status"] = "success"
        f = c._fal_for(tmp_path, "key", threading.Event(), cap)
        f.image_service, f.kie_key, f.task_dir = "kie", "key", tmp_path / "tasks"
        f._timeout = 0.01
        assert f.generate("nano_banana_t2i", "bird") == png
        assert c.budget_used(tmp_path) == 2
        with pytest.raises(artwork.ArtworkError, match=message):
            f.generate("nano_banana_t2i", "another bird")
        assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1
    finally:
        artwork.host_configure()


def test_running_job_payload_survives_restart_at_cap(tmp_path, api, monkeypatch):
    calls, _state, png = api
    entered, release = threading.Event(), threading.Event()

    def make(job):
        assert job.payload == {"bird": "robin"}
        raw = job.fal.generate("nano_banana_t2i", "robin")
        entered.set()
        release.wait(3)
        return raw, {}

    monkeypatch.setattr(kie_images, "configuration", lambda: ("kie", "key"))
    c = artwork.Collection("kie_resume", "birds", make=make, label="Birds")
    c.start(c.job("robin", {"bird": "robin"}, "Robin", tmp_path, fal.DIRECT_ONLY), 2)
    assert entered.wait(3)
    rows = json.loads((tmp_path / "birds" / "queue.json").read_text())
    assert rows[0]["payload"] == {"bird": "robin"}
    c.stop("robin", tmp_path)
    monkeypatch.delitem(artwork._COLLECTIONS, ("kie_resume", "birds"))
    restarted = artwork.Collection("kie_resume", "birds", make=make, label="Birds")
    assert restarted.resume(tmp_path, fal.DIRECT_ONLY) == 1
    release.set()
    end = __import__("time").monotonic() + 3
    while restarted.path("robin", tmp_path) is None and __import__("time").monotonic() < end:
        __import__("time").sleep(0.01)
    assert restarted.picture("robin", tmp_path)[0] == png
    assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1


def test_transport_authenticates_create_and_poll(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(b'{"code":200,"data":{}}')

    monkeypatch.setattr(kie_images.urllib.request, "urlopen", urlopen)
    kie_images._json(kie_images.BASE + "/jobs/createTask", {"input": {}}, "secret")
    kie_images._json(kie_images.BASE + "/jobs/recordInfo?taskId=paid", key="secret")
    assert [r.get_method() for r in seen] == ["POST", "GET"]
    assert all(r.get_header("Authorization") == "Bearer secret" for r in seen)


@pytest.mark.parametrize("code,message", [(401, "Invalid key"), (402, "Insufficient credits")])
def test_api_failure_messages_even_on_http_200(monkeypatch, code, message):
    monkeypatch.setattr(
        kie_images.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps({"code": code, "msg": message}).encode()),
    )
    with pytest.raises(kie_images.KieError, match=message):
        kie_images._json(kie_images.BASE + "/jobs/createTask", {}, "key")


def test_credits_refuse_payment(tmp_path, api, monkeypatch):
    calls, _, _ = api
    monkeypatch.setattr(kie_images, "credits", lambda key: 0)
    charges = []
    with pytest.raises(fal.FalError, match="insufficient credits"):
        client(tmp_path, charges).generate("nano_banana_t2i", "bird")
    assert not calls and not charges


def test_pro_keeps_aspect_and_1k_resolution(tmp_path, api):
    calls, _, png = api
    charges = []
    assert (
        client(tmp_path, charges).generate(
            "nano_banana_pro", "bird", refs=["https://ref.example/bird.jpg"], aspect="16:9"
        )
        == png
    )
    body = next(b for u, b, k in calls if u.endswith("createTask"))
    assert body["model"] == "nano-banana-pro"
    assert body["input"]["image_input"] == ["https://ref.example/bird.jpg"]
    assert body["input"]["resolution"] == "1K"
    assert body["input"]["aspect_ratio"] == "16:9"
    assert charges == [(9, "nano_banana_pro_kie")]


def test_widget_fallback_cannot_submit_after_a_pending_task(tmp_path, api):
    calls, state, _ = api
    state["status"] = "timeout"
    charges = []
    c = client(tmp_path, charges, timeout=0.01)
    for prompt in ("named spacecraft", "unnamed spacecraft"):
        with pytest.raises(fal.FalError, match="paid task"):
            c.generate("nano_banana_t2i", prompt)
    assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1
    assert len(charges) == 1


def test_render_now_collects_paid_task_when_daily_budget_is_spent(tmp_path, api, monkeypatch):
    import time

    calls, state, png = api
    monkeypatch.setattr(kie_images, "configuration", lambda: ("kie", "key"))

    def make(job):
        job.fal._timeout = 0.01
        return job.fal.generate("nano_banana_t2i", "robin"), {}

    c = artwork.Collection("kie_render", "birds", make=make, label="Birds")
    settings = {"gen_daily_usd": "0.02"}
    ctx = {"data_dir": tmp_path, "page_name": "Birds"}
    state["status"] = "timeout"
    art = c.open(ctx, settings)
    art.request("robin", {"bird": "robin"})
    art.finish(ctx)
    deadline = time.monotonic() + 3
    while c.state("robin", tmp_path) == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert c.budget_used(tmp_path) == 2
    c.clear_error("robin", tmp_path)
    state["status"] = "success"
    assert c.render_now(tmp_path, fal.DIRECT_ONLY, settings) == (1, 1)
    deadline = time.monotonic() + 3
    while c.path("robin", tmp_path) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert c.picture("robin", tmp_path)[0] == png
    assert len([u for u, _, _ in calls if u.endswith("createTask")]) == 1
