"""This API runs commands on the PC, so only the PC itself gets in without a code."""


def test_local_browser_needs_no_code(client):
    assert client.post("/api/tool", json={"name": "get_time"}).status_code == 200


def test_remote_is_closed_without_configured_code(phone):
    res = phone.post("/api/tool", json={"name": "get_time"})
    assert res.status_code == 403
    assert "ACCESS_CODE" in res.json()["detail"]


def test_remote_needs_the_right_code(phone, settings):
    settings.access_code = "secret-code"
    assert phone.post("/api/tool", json={"name": "get_time"}).status_code == 401
    assert (
        phone.post(
            "/api/tool", json={"name": "get_time"}, headers={"X-Access-Code": "nope"}
        ).status_code
        == 401
    )
    ok = phone.post(
        "/api/tool", json={"name": "get_time"}, headers={"X-Access-Code": "secret-code"}
    )
    assert ok.status_code == 200


def test_tailscale_proxy_is_not_treated_as_local(client, settings):
    # `tailscale serve` connects from 127.0.0.1 but adds forwarding headers.
    settings.access_code = "secret-code"
    res = client.post(
        "/api/tool", json={"name": "get_time"}, headers={"X-Forwarded-For": "100.64.0.7"}
    )
    assert res.status_code == 401
    res = client.post("/api/token", headers={"Tailscale-User-Login": "ada@example.com"})
    assert res.status_code == 401


def test_every_api_route_is_protected(phone):
    assert phone.post("/api/token").status_code == 403
    assert phone.post("/api/confirm/abc", json={"approved": True}).status_code == 403
    assert phone.get("/api/jobs/abc").status_code == 403
    assert phone.get("/api/audit").status_code == 403
    assert phone.get("/api/screenshots/x.png").status_code == 403


def test_frontend_and_health_are_public(phone):
    assert phone.get("/health").json() == {"status": "ok"}
    page = phone.get("/")
    assert page.status_code == 200 and "Mario" in page.text
