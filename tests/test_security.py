"""The Gemini key stays on the server, and public AI endpoints can't drain the quota."""

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.main as main
from app.main import MAX_MESSAGE_CHARS, app
from app.security import AI_PER_DAY, AI_PER_MINUTE, MAX_BODY_BYTES, AiQuota, client_ip

KEY = "test-gemini-key-do-not-leak"
NOW = datetime(2026, 11, 16, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeGemini:
    """Stands in for google.genai.Client and counts calls."""

    def __init__(self, error: Exception | None = None):
        self.models = self
        self.calls = 0
        self.error = error

    def generate_content(self, **_):
        self.calls += 1
        if self.error:
            raise self.error
        return type("Response", (), {"text": json.dumps({"reply": "Sure thing.", "changes": {}})})()


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(main, "gemini_client", lambda: fake)
    monkeypatch.setattr(main, "ai_quota", AiQuota())
    monkeypatch.setattr(main, "local_now", lambda prefs: NOW.astimezone(ZoneInfo(prefs.timezone)))
    return fake


@pytest.fixture
def client(gemini):
    client = TestClient(app, base_url="https://testserver")
    page = client.get("/").text
    client.headers["X-CSRF-Token"] = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    return client


def chat(client, message, ip="203.0.113.7"):
    return client.post("/api/chat", json={"message": message, "events": []}, headers={"CF-Connecting-IP": ip}).json()


# -------------------------------------------------------- the key itself ---
def test_the_api_key_never_reaches_the_page_or_responses(client):
    assert KEY not in client.get("/").text
    assert KEY not in json.dumps(chat(client, "hi"))
    assert KEY not in client.get("/static/app.js").text


def test_gemini_failures_log_no_details(client, gemini, caplog):
    gemini.error = RuntimeError(f"boom with key={KEY} and the message 'my secret plans'")
    with caplog.at_level(logging.WARNING):
        reply = chat(client, "my secret plans")["reply"]
    assert reply  # the keyword parser answered instead
    assert "RuntimeError" in caplog.text and KEY not in caplog.text and "secret plans" not in caplog.text


# --------------------------------------------------------------- quotas ---
def test_each_visitor_gets_a_few_ai_calls_a_minute(client, gemini, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    for _ in range(AI_PER_MINUTE):
        assert "simpler parser" not in chat(client, "hello")["reply"]
    limited = chat(client, "hello")
    assert "simpler parser" in limited["reply"] and gemini.calls == AI_PER_MINUTE
    assert "simpler parser" not in chat(client, "hello", ip="198.51.100.9")["reply"]  # someone else is fine


def test_quota_windows_per_minute_per_day_and_site_wide(monkeypatch):
    quota = AiQuota()
    assert all(quota.allow("a", now=t) for t in range(AI_PER_MINUTE))
    assert not quota.allow("a", now=AI_PER_MINUTE)  # sixth call in the same minute
    assert quota.allow("a", now=61)  # a minute later
    day = AiQuota()
    calls = [day.allow("b", now=i * 61) for i in range(AI_PER_DAY + 1)]
    assert calls.count(True) == AI_PER_DAY
    monkeypatch.setenv("AI_DAILY_LIMIT", "3")
    site = AiQuota()
    assert [site.allow(f"visitor{i}", now=i * 61) for i in range(4)] == [True, True, True, False]


def test_screenshots_count_toward_the_quota(client, monkeypatch):
    monkeypatch.setattr(main, "ai_quota", type("Full", (), {"allow": lambda self, visitor: False})())
    image = {"data": "data:image/png;base64,iVBORw0KGgo=", "type": "image/png"}
    reply = client.post("/api/chat", json={"message": "", "image": image, "events": []}).json()["reply"]
    assert "a lot of screenshots" in reply


# ---------------------------------------------------------- request size ---
def test_oversized_and_chunked_requests_are_refused(client):
    too_big = client.post(
        "/api/chat", content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"}
    )
    assert too_big.status_code == 413
    chunked = client.post(
        "/api/chat", content=iter([b'{"message": "hi"}']), headers={"content-type": "application/json"}
    )
    assert chunked.status_code == 411


def test_long_messages_reach_gemini_whole_and_longer_ones_get_a_clear_warning(client, gemini, monkeypatch):
    seen = {}
    real_turn = main.coach_turn

    def recording_turn(message, *args):
        seen["message"] = message
        return real_turn(message, *args)

    monkeypatch.setattr(main, "coach_turn", recording_turn)
    longest = "a" * MAX_MESSAGE_CHARS
    chat(client, longest)
    assert seen["message"] == longest  # nothing cut
    too_long = client.post("/api/chat", json={"message": longest + "a", "events": []})
    assert too_long.status_code == 400 and "2,001 characters; the limit is 2,000" in too_long.json()["error"]
    assert gemini.calls == 1  # the refused message never reached Gemini or used quota
    textarea = re.search(r"<textarea[^>]*>", client.get("/").text).group(0)
    assert 'data-max-length="2000"' in textarea and "maxlength" not in textarea  # the browser never cuts it


# -------------------------------------------------------- who's asking ---
def request_from(peer: str, **headers) -> Request:
    raw = [(k.replace("_", "-").lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


def test_on_render_only_cloudflares_header_identifies_the_visitor(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    proxy = "10.226.0.196"
    assert client_ip(request_from(proxy, cf_connecting_ip="81.97.145.24")) == "81.97.145.24"
    # Headers a client can write are ignored, so spoofing them can't reset the limits.
    spoofed = request_from(proxy, x_forwarded_for="1.2.3.4", true_client_ip="5.6.7.8")
    assert client_ip(spoofed) == proxy
    assert client_ip(request_from(proxy, cf_connecting_ip="1.2.3.4, 5.6.7.8")) == proxy  # not one valid IP
    assert client_ip(request_from(proxy, cf_connecting_ip="not-an-ip")) == proxy


def test_off_render_the_header_is_ignored(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    assert client_ip(request_from("127.0.0.1", cf_connecting_ip="81.97.145.24")) == "127.0.0.1"


def test_ipv6_visitors_are_grouped_by_their_slash_64(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    one = client_ip(request_from("10.0.0.1", cf_connecting_ip="2001:db8:1:2::aaaa"))
    two = client_ip(request_from("10.0.0.1", cf_connecting_ip="2001:db8:1:2:ffff::1"))
    assert one == two == "2001:db8:1:2::/64"
