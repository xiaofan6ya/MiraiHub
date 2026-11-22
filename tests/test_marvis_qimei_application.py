"""Persistent regressions for authoritative QIMEI application and fail-closed.

Private package imports, disposable synthetic snapshots, captured urllib
requests only. No application startup, real credentials, or network access.
"""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

import pytest

CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
A, B = (c * 32 for c in "AB")
Q1, Q2, Q3 = (c * 24 + "3000" + c * 8 for c in "abc")
GUID_A = "11111111-1111-4111-8111-111111111111"
GUID_B = "22222222-2222-4222-8222-222222222222"
GUID_NEW = "33333333-3333-4333-8333-333333333333"
SIGN_ARGS = dict(agent_tag="main", conversation_id="conv-synthetic",
                 response_id="rid-synthetic", scene="agent_chat",
                 model="main-auto", nonce="nonce-synthetic", openid=A)


@pytest.fixture
def app(tmp_path, monkeypatch):
    name = "_qimei_application_" + tmp_path.name.replace("-", "_")
    package = types.ModuleType(name)
    package.__path__ = [str(CORE)]
    monkeypatch.setitem(sys.modules, name, package)
    snapshot = tmp_path / "state.json"
    monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setenv("QIMEI_STATE_DIR", str(tmp_path / "unused-legacy"))
    monkeypatch.setenv("QIMEI_TABLE_PATH", "")
    monkeypatch.setenv("QIMEI_POOL_PATH", "")
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "0")
    monkeypatch.delenv("MARVIS_QIMEI36", raising=False)
    monkeypatch.delenv("MARVIS_USKEY_FORCE_SIDECAR", raising=False)
    monkeypatch.setenv("MARVIS_DEBUG_HEADERS", "0")
    modules = {}

    def load(module):
        if module not in modules:
            modules[module] = importlib.import_module(name + "." + module)
        return modules[module]

    def write():
        snapshot.write_text(json.dumps({
            "schema": 1, "revision": 1, "table": {A: Q1, B: Q2},
            "pool": {q: {"owners": [u], "real": True, "source": "synthetic",
                         "created_at": "2026-01-01T00:00:00Z",
                         "last_rotated": "2026-01-01T00:00:00Z",
                         "last_used_at": None} for u, q in [(A, Q1), (B, Q2)]},
            "metadata": {"unrelated": {"keep": True}}
        }), encoding="utf-8")

    yield types.SimpleNamespace(load=load, path=snapshot, write=write)
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)


@pytest.mark.parametrize("contents", [None, "{broken"])
def test_required_snapshot_error_never_reaches_sidecar(app, monkeypatch, contents):
    u = app.load("uskey")
    qa = app.load("qimei_assign")
    if contents is not None:
        app.path.write_text(contents, encoding="utf-8")
    calls = []
    monkeypatch.setattr(u, "_http_sign", lambda **kw: calls.append(kw) or {"qimei36": Q3})
    with pytest.raises((qa.QimeiPoolError, u.SidecarError)):
        u.sign(account_key=A, **SIGN_ARGS)
    assert calls == []


def test_required_snapshot_missing_assignment_never_derives_an_identity(app):
    app.write()
    u = app.load("uskey")
    with pytest.raises((app.load("qimei_assign").QimeiPoolError, u.SidecarError)):
        u.qimei_for("C" * 32)


def test_snapshot_disallows_shared_override(app, monkeypatch):
    app.write()
    monkeypatch.setenv("MARVIS_QIMEI36", Q3)
    u = app.load("uskey")
    with pytest.raises((app.load("qimei_assign").QimeiPoolError, u.SidecarError)):
        u.sign(account_key=A, **SIGN_ARGS)


def test_snapshot_local_sign_failure_never_changes_device_via_fallback(app, monkeypatch):
    app.write()
    u = app.load("uskey")
    calls = []
    monkeypatch.setattr(u, "_local_sign", lambda **kw: (_ for _ in ()).throw(RuntimeError("synthetic")))
    monkeypatch.setattr(u, "_http_sign", lambda **kw: calls.append(kw) or {"qimei36": Q3})
    with pytest.raises(u.SidecarError):
        u.sign(account_key=A, **SIGN_ARGS)
    assert calls == []


def test_snapshot_forced_sidecar_is_rejected(app, monkeypatch):
    app.write()
    u = app.load("uskey")
    monkeypatch.setenv("MARVIS_USKEY_FORCE_SIDECAR", "1")
    calls = []
    monkeypatch.setattr(u, "_http_sign", lambda **kw: calls.append(kw) or {"qimei36": Q3})
    with pytest.raises(u.SidecarError):
        u.sign(account_key=A, **SIGN_ARGS)
    assert calls == []


def capture_requests(app, monkeypatch):
    client = app.load("client")
    captured = []

    class Opener:
        def open(self, req, **kwargs):
            captured.append(req)
            return types.SimpleNamespace(status=200, headers={"Content-Type": "text/event-stream"})

    monkeypatch.setattr(client, "_opener", lambda: Opener())
    monkeypatch.setattr(client, "_egress_proxy", lambda account_id: "")
    # Preserve the current account+conversation contract without importing the
    # application pool/DB. Existing upstream tests cover that base derivation.
    monkeypatch.setattr(client, "conversation_id_for", lambda account_id, body:
                        "%s:%s" % (account_id, body["metadata"]["conversation_id"]))
    monkeypatch.setattr(client.endpoints.uskey_signer, "sign_for",
                        lambda *args: pytest.fail("managed request reached legacy signer"))

    def send(uid, guid, account_id, conversation="thread-1"):
        credential = {"uid": uid, "openid": uid, "access_token": "synthetic-token",
                      "guid": guid, "refresh_token": "synthetic-refresh"}
        client.open_chat_stream(credential, "cn", {"model": "main-auto", "messages": [],
                                "metadata": {"conversation_id": conversation}},
                                account_id=account_id)
        return captured[-1]

    return client, captured, send


def profile(app, request):
    headers = {k.lower(): v for k, v in request.header_items()}
    ext = json.loads(headers["ual-access-marvisext"])
    local = app.load("uskey_local")
    decoded = local.decode_uskey(ext["uskey"])
    tlv = decoded["tlv"]
    for offset in (local.OFF_QIMEI1, local.OFF_QIMEI2):
        assert tlv[offset:offset + 18].hex() == ext["qimei36"]
    assert headers["ual-access-guid"] == headers["x-device-guid"] == ext["guid"]
    assert headers["ual-access-requestid"] == headers["marvis-response-id"]
    info = app.load("uskey").bus_info(
        agent_tag=headers["marvis-agenttag"],
        conversation_id=headers["marvis-conversation-id"],
        response_id=headers["marvis-response-id"], scene=headers["marvis-scene"],
        model=json.loads(request.data)["model"], nonce=ext["nonce"],
        openid=headers["ual-access-openid"])
    assert tlv[local.OFF_MD5:local.OFF_MD5 + 16] == hashlib.md5(info.encode()).digest()
    return (ext["qimei36"], ext["guid"], decoded["dev18"],
            headers["marvis-conversation-id"], headers["marvis-response-id"], ext["nonce"])


def test_snapshot_rotation_applies_to_actual_http_header_and_both_tlv_copies(app, monkeypatch):
    app.write()
    _client, _captured, send = capture_requests(app, monkeypatch)
    a = profile(app, send(A, GUID_A, 1))
    b = profile(app, send(B, GUID_B, 2))
    app.load("qimei_assign").rotate_ex(A, generator=lambda: Q3)
    aa = profile(app, send(A, GUID_A, 1))
    bb = profile(app, send(B, GUID_B, 2))
    assert a[0] == Q1 and aa[0] == Q3
    assert b[:4] == bb[:4]
    assert a[1] == aa[1]  # A QIMEI-only rotation retains its credential GUID.


def test_managed_reset_updates_one_account_as_a_single_identity(app, monkeypatch):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    identity.enroll_ex(B, guid=GUID_B)
    client, captured, send = capture_requests(app, monkeypatch)
    a1 = profile(app, send(A, GUID_A, 1))
    a2 = profile(app, send(A, GUID_A, 1))
    b1 = profile(app, send(B, GUID_B, 2))
    other = profile(app, send(A, GUID_A, 1, "thread-2"))
    assert a1[:4] == a2[:4]
    assert a1[3] != other[3]
    assert all(a1[i] != b1[i] for i in range(4))
    assert a1[4] != a2[4] and a1[5] != a2[5]
    identity.reset_ex(A, guid=GUID_NEW, generator=lambda: Q3)
    count = len(captured)
    with pytest.raises(client.UpstreamError) as exc:
        send(A, GUID_A, 1)
    assert exc.value.kind == "signer_unavailable"
    assert len(captured) == count
    a3 = profile(app, send(A, GUID_NEW, 1))
    b2 = profile(app, send(B, GUID_B, 2))
    assert all(a1[i] != a3[i] for i in range(4))
    assert b1[:4] == b2[:4]


@pytest.mark.parametrize("contents", [None, "{broken"])
@pytest.mark.parametrize("managed", ["0", "1"])
def test_managed_snapshot_failure_sends_no_request_even_if_random_fallback_enabled(app, monkeypatch, contents, managed):
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", managed)
    monkeypatch.setenv("MARVIS_USKEY_FAIL_CLOSED", "0")
    if contents is not None:
        app.path.write_text(contents, encoding="utf-8")
    client, captured, send = capture_requests(app, monkeypatch)
    with pytest.raises(client.UpstreamError) as exc:
        send(A, GUID_A, 1)
    assert exc.value.kind == "signer_unavailable"
    assert captured == []


def test_reset_between_capture_and_mint_never_mixes_revisions(app, monkeypatch):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    _client, _captured, send = capture_requests(app, monkeypatch)
    local = app.load("uskey_local")
    original = local.mint_uskey
    calls = []

    def mint(*args, **kwargs):
        if not calls:
            calls.append(True)
            identity.reset_ex(A, guid=GUID_NEW, generator=lambda: Q3)
        return original(*args, **kwargs)

    monkeypatch.setattr(local, "mint_uskey", mint)
    old = profile(app, send(A, GUID_A, 1))
    new = profile(app, send(A, GUID_NEW, 1))
    assert old[0:2] == (Q1, GUID_A)
    assert new[0:2] == (Q3, GUID_NEW)
    assert all(old[i] != new[i] for i in range(4))


@pytest.mark.parametrize("camel", [False, True])
def test_refresh_keeps_existing_whitespace_normalization(app, monkeypatch, camel):
    monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", "")
    client = app.load("client")
    seen = []

    def request(*args, **kwargs):
        seen.append(kwargs)
        return {"code": 0, "user_info": {"access_token": "new-token",
                "refresh_token": "new-refresh", "expires_in": "7200"}}

    monkeypatch.setattr(client, "_request", request)
    cred = {"guid": " " + GUID_A + " "}
    for snake, camel_name, value in [("openid", "openId", A),
                                    ("access_token", "accessToken", "token"),
                                    ("refresh_token", "refreshToken", "refresh")]:
        cred[camel_name if camel else snake] = " " + value + " "
    client.refresh_credentials(credential=cred)
    headers = seen[0]["headers"]
    user = json.loads(seen[0]["raw_body"])["userInfo"]
    assert headers["Ual-Access-Openid"] == user["openId"] == A
    assert headers["Ual-Access-Access-Token"] == user["accessToken"] == "token"
    assert headers["Ual-Access-Guid"] == GUID_A


def test_managed_wallet_and_refresh_reject_old_login_guid_after_reset(app, monkeypatch):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    client = app.load("client")
    seen = []

    def request(url, **kwargs):
        seen.append(kwargs)
        if "wallet" in url:
            return {"code": 0, "data": {"total_tokens": 1}}
        return {"code": 0, "user_info": {"access_token": "new-token",
                "refresh_token": "new-refresh", "expires_in": "7200"}}

    monkeypatch.setattr(client, "_request", request)
    cred = {"uid": A, "openid": A, "guid": GUID_A,
            "access_token": "synthetic-token", "refresh_token": "synthetic-refresh"}
    client.fetch_wallet(cred)
    client.refresh_credentials(credential=cred)
    assert all(item["headers"]["Ual-Access-Guid"] == GUID_A for item in seen)
    identity.reset_ex(A, guid=GUID_NEW, generator=lambda: Q3)
    count = len(seen)
    for call in (lambda: client.fetch_wallet(cred),
                 lambda: client.refresh_credentials(credential=cred)):
        with pytest.raises(client.UpstreamError):
            call()
    assert len(seen) == count
    cred["guid"] = GUID_NEW
    client.fetch_wallet(cred)
    client.refresh_credentials(credential=cred)
    assert all(item["headers"]["Ual-Access-Guid"] == GUID_NEW for item in seen[count:])


@pytest.mark.parametrize("operation", ["rotate", "rotate-due"])
def test_qimei_only_mutations_cannot_break_managed_identity(app, monkeypatch, operation):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    qa = app.load("qimei_assign")
    before = app.path.read_bytes()
    generated = []

    def generate():
        generated.append(True)
        return Q3

    with pytest.raises(qa.QimeiPoolError):
        if operation == "rotate":
            qa.rotate_ex(A, generator=generate)
        else:
            qa.rotate_due(older_than_days=1, generator=generate)
    assert generated == []
    assert app.path.read_bytes() == before
    assert identity.resolve({"uid": A, "guid": GUID_A}).qimei36 == Q1


def test_legacy_fallback_cannot_share_a_managed_account_device(app, monkeypatch):
    app.write()
    data = json.loads(app.path.read_text())
    data["table"].pop(B)
    data["pool"].pop(Q2)
    app.path.write_text(json.dumps(data))
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    qa = app.load("qimei_assign")
    before = app.path.read_bytes()
    with pytest.raises(qa.QimeiCapacityError):
        qa.assign_ex(B, generator=False)
    assert app.path.read_bytes() == before
    assert identity.resolve({"uid": A, "guid": GUID_A}).qimei36 == Q1


def test_malformed_managed_namespace_cannot_be_treated_as_an_unmanaged_pool(app):
    app.write()
    data = json.loads(app.path.read_text())
    data["metadata"]["marvis_managed_identity_v1"] = None
    app.path.write_text(json.dumps(data))
    qa = app.load("qimei_assign")
    with pytest.raises(qa.QimeiPoolError):
        qa.assign_ex("C" * 32, generator=False)


@pytest.mark.parametrize("prune", [False, True])
def test_account_removal_atomically_removes_managed_record_and_preserves_other_accounts(app, monkeypatch, prune):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    identity.enroll_ex(B, guid=GUID_B)
    qa = app.load("qimei_assign")
    b = identity.resolve({"uid": B, "guid": GUID_B})
    if prune:
        import sqlite3
        db_path = app.path.parent / "synthetic.sqlite"
        with sqlite3.connect(db_path) as db:
            db.execute("create table accounts (id integer, platform text, uid text, status text)")
            db.execute("insert into accounts values (2, 'marvis', ?, 'active')", (B,))
        qa.reconcile(db_path=str(db_path), prune=True, generator=False)
    else:
        qa.release(A)
    with pytest.raises(identity.DeviceIdentityError):
        identity.resolve({"uid": A, "guid": GUID_A})
    assert identity.resolve({"uid": B, "guid": GUID_B}) == b


@pytest.mark.parametrize("reset", [False, True])
def test_commit_uncertainty_requires_readback_and_never_retries_generation(app, monkeypatch, reset):
    app.write()
    monkeypatch.setenv("QIMEI_MANAGED_IDENTITY", "1")
    identity = app.load("device_identity")
    identity.enroll_ex(A, guid=GUID_A)
    support = app.load("qimei_assign")._snapshot_support()
    original = support.write_snapshot
    generated = []

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise support.SnapshotCommitUncertain("synthetic post-replace durability error")

    def generate():
        generated.append(True)
        return Q3

    monkeypatch.setattr(support, "write_snapshot", uncertain)
    with pytest.raises(identity.DeviceIdentityCommitUncertain):
        if reset:
            identity.reset_ex(A, guid=GUID_NEW, generator=generate)
        else:
            identity.enroll_ex(B, guid=GUID_B)
    if reset:
        context = identity.resolve({"uid": A, "guid": GUID_NEW})
        assert context.qimei36 == Q3 and context.generation == 2
        assert generated == [True]
    else:
        assert identity.resolve({"uid": B, "guid": GUID_B}).qimei36 == Q2
        assert generated == []
