"""Persistent snapshot-only rotation regressions; synthetic temporary state only."""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import types

import pytest


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
A, B, C, D = (letter * 32 for letter in "ABCD")
Q1, Q2, Q3, Q4, Q5 = (letter * 24 + "3000" + letter * 8 for letter in "abcde")
STAMP = "2026-01-01T00:00:00Z"
NOW = dt.datetime(2026, 11, 21, 12, tzinfo=dt.timezone.utc)


def _entry(*owners, rotated=STAMP, real=True):
    return {"owners": list(owners), "real": real, "source": "synthetic",
            "created_at": STAMP, "last_rotated": rotated, "last_used_at": None}


@pytest.fixture
def rotation(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="qimei-rotation-regression-") as temporary:
        root = Path(temporary)
        snapshot = root / "state.json"
        monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", str(snapshot))
        monkeypatch.setenv("QIMEI_STATE_DIR", str(root / "absent-legacy"))
        monkeypatch.setenv("QIMEI_TABLE_PATH", "")
        monkeypatch.setenv("QIMEI_POOL_PATH", "")
        name = "qimei_rotation_%x" % id(root)
        package = types.ModuleType(name)
        package.__path__ = [str(CORE)]
        monkeypatch.setitem(sys.modules, name, package)

        def load(filename):
            spec = importlib.util.spec_from_file_location(name + "." + filename, CORE / (filename + ".py"))
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, spec.name, module)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        qa, uskey = load("qimei_assign"), load("uskey")

        def write(*, table=None, pool=None, revision=7):
            value = {"schema": 1, "revision": revision,
                     "table": {A: Q1, B: Q1} if table is None else table,
                     "pool": {Q1: _entry(A, B)} if pool is None else pool,
                     "metadata": {"audit": "synthetic-preserved"}}
            snapshot.write_text(json.dumps(value), encoding="utf-8")
            return value

        def read():
            return json.loads(snapshot.read_text(encoding="utf-8"))

        yield types.SimpleNamespace(qa=qa, uskey=uskey, root=root, path=snapshot, write=write, read=read)
        for module in tuple(sys.modules):
            if module.startswith(name + "."):
                sys.modules.pop(module, None)


def _api(qa, name):
    function = getattr(qa, name, None)
    assert callable(function), "snapshot-only QIMEI rotation API is not implemented"
    return function


def test_single_rotation_preserves_shared_owner_and_old_metadata_and_reloads_lookup(rotation, monkeypatch):
    state = rotation.write()
    qa = rotation.qa
    assert rotation.uskey.qimei_for(A) == Q1
    monkeypatch.setattr(qa, "_now", lambda: "2026-11-21T12:00:00Z")
    result = _api(qa, "rotate_ex")(" " + A.lower() + " ", generator=lambda: Q2.upper())
    committed = rotation.read()
    assert committed["revision"] == 8
    assert committed["table"] == {A: Q2, B: Q1}
    assert committed["pool"][Q1] == {**state["pool"][Q1], "owners": [B]}
    assert committed["pool"][Q2] == {
        "owners": [A], "real": True, "source": "generated",
        "created_at": "2026-11-21T12:00:00Z", "last_rotated": "2026-11-21T12:00:00Z",
        "last_used_at": "2026-11-21T12:00:00Z"}
    assert committed["metadata"] == state["metadata"]
    assert result["revision"] == 8 and result["rotated"] == 1
    assert result["qimei36"] == Q2 and result["uid"] == A
    assert rotation.uskey.qimei_for(A) == Q2
    assert rotation.uskey.qimei_for(B) == Q1


def test_rotation_retains_the_now_unowned_old_entry(rotation):
    rotation.write(table={A: Q1}, pool={Q1: _entry(A)})
    _api(rotation.qa, "rotate_ex")(A, generator=lambda: Q2)
    committed = rotation.read()
    assert committed["pool"][Q1] == _entry()
    assert committed["pool"][Q2]["owners"] == [A]
    assert all(len(entry["owners"]) <= 2 for entry in committed["pool"].values())


@pytest.mark.parametrize("generated", [Q1, Q2, Q1.upper(), "sensitive-invalid", "e" * 36, "", None, False])
def test_repeated_existing_or_invalid_generator_values_preserve_snapshot_bytes(rotation, generated):
    rotation.write(pool={Q1: _entry(A, B), Q2: _entry()})
    before = rotation.path.read_bytes()
    with pytest.raises(rotation.qa.QimeiGenError) as caught:
        _api(rotation.qa, "rotate_ex")(A, generator=lambda: generated)
    assert "sensitive" not in str(caught.value)
    assert rotation.path.read_bytes() == before


@pytest.mark.parametrize("generator", [False, 0, True, "sensitive-generator"])
def test_rotation_rejects_disabled_or_noncallable_generator_without_writes(rotation, generator):
    rotation.write()
    before = rotation.path.read_bytes()
    with pytest.raises(rotation.qa.QimeiGenError):
        _api(rotation.qa, "rotate_ex")(A, generator=generator)
    assert rotation.path.read_bytes() == before


def test_default_rotation_generator_is_used_without_sharing_or_derived_fallback(rotation, monkeypatch):
    rotation.write()
    monkeypatch.setattr(rotation.qa, "generate_real_qimei", lambda: Q2)
    monkeypatch.setattr(rotation.qa, "_pick_shareable", lambda *_: pytest.fail("rotation shared identity"))
    monkeypatch.setattr(rotation.qa, "derived_qimei", lambda *_: pytest.fail("rotation derived identity"))
    assert _api(rotation.qa, "rotate_ex")(A)["qimei36"] == Q2


def test_generator_exception_is_sanitized_and_never_commits(rotation):
    rotation.write()
    before = rotation.path.read_bytes()

    def failed():
        raise RuntimeError("sensitive endpoint/id generator detail")

    with pytest.raises(rotation.qa.QimeiGenError) as caught:
        _api(rotation.qa, "rotate_ex")(A, generator=failed)
    assert "sensitive" not in str(caught.value) and caught.value.__cause__ is None
    assert rotation.path.read_bytes() == before


@pytest.mark.parametrize("uid", [None, "", " ", "_comment", C])
def test_rotation_requires_an_existing_real_assignment(rotation, uid):
    rotation.write()
    before = rotation.path.read_bytes()
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_ex")(uid, generator=lambda: Q2)
    assert rotation.path.read_bytes() == before


def test_rotation_rejects_nonreal_binding(rotation):
    rotation.write(table={A: Q1}, pool={Q1: _entry(A, real=False)})
    before = rotation.path.read_bytes()
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_ex")(A, generator=lambda: Q2)
    assert rotation.path.read_bytes() == before


@pytest.mark.parametrize("action", ["rotate_ex", "rotate_due"])
def test_rotation_refuses_legacy_mode_before_locks_or_files(rotation, monkeypatch, action):
    monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", "")
    monkeypatch.setattr(rotation.qa, "load_state", lambda *_: pytest.fail("legacy rotation loaded state"))
    monkeypatch.setattr(rotation.qa, "_locked", lambda *_: pytest.fail("legacy rotation created a lock"))
    with pytest.raises(rotation.qa.QimeiPoolError):
        function = _api(rotation.qa, action)
        function(A) if action == "rotate_ex" else function(older_than_days=1)
    assert list(rotation.root.iterdir()) == []


@pytest.mark.parametrize("action", ["rotate_ex", "rotate_due"])
def test_rotation_preview_does_not_lock_generate_save_or_change_any_file(rotation, monkeypatch, action):
    rotation.write()
    before = {path.name: path.read_bytes() for path in rotation.root.iterdir()}
    for name in ("_locked", "generate_real_qimei", "save_state"):
        monkeypatch.setattr(rotation.qa, name, lambda *_args, **_kwargs: pytest.fail("preview side effect"))
    function = _api(rotation.qa, action)
    result = (function(A, dry_run=True) if action == "rotate_ex" else
              function(older_than_days=1, now=NOW, dry_run=True))
    assert result["dry_run"] and result["rotated"] == 0 and result["eligible"] >= 1
    assert result["revision"] == 7
    assert {path.name: path.read_bytes() for path in rotation.root.iterdir()} == before


def test_due_rotation_orders_oldest_then_uid_limits_and_commits_once(rotation, monkeypatch):
    old = "2026-10-01T00:00:00Z"
    newer = "2026-11-01T00:00:00+00:00"
    rotation.write(table={C: Q1, B: Q2, A: Q2, D: Q3},
                   pool={Q1: _entry(C, rotated=newer), Q2: _entry(A, B, rotated=old),
                         Q3: _entry(D, rotated="2026-11-21T00:00:00Z")})
    original_save, saves = rotation.qa.save_state, []

    def save(state):
        saves.append(state.revision)
        original_save(state)

    monkeypatch.setattr(rotation.qa, "save_state", save)
    values = iter((Q4, Q5))
    result = _api(rotation.qa, "rotate_due")(older_than_days=10, now=NOW,
                                            max_accounts=2, generator=lambda: next(values))
    committed = rotation.read()
    assert result["uids"] == [A, B]
    assert (result["eligible"], result["selected"], result["rotated"], result["revision"]) == (3, 2, 2, 8)
    assert committed["table"] == {A: Q4, B: Q5, C: Q1, D: Q3}
    assert committed["pool"][Q2] == _entry(rotated=old)
    assert committed["pool"][Q4]["last_rotated"] == "2026-11-21T12:00:00Z"
    assert committed["pool"][Q5]["owners"] == [B]
    assert saves == [7]


@pytest.mark.parametrize("failure", ["raise", "duplicate", "existing"])
def test_due_batch_generation_failure_rolls_back_all_accounts_and_revision(rotation, failure):
    rotation.write()
    before = rotation.path.read_bytes()
    calls = []

    def generator():
        calls.append(True)
        if len(calls) == 1:
            return Q2
        if failure == "raise":
            raise RuntimeError("sensitive second generator failure")
        return Q2 if failure == "duplicate" else Q1

    with pytest.raises(rotation.qa.QimeiGenError):
        _api(rotation.qa, "rotate_due")(older_than_days=1, now=NOW, generator=generator)
    assert len(calls) == 2
    assert rotation.path.read_bytes() == before


def test_due_cutoff_is_inclusive_and_timezone_offsets_are_compared_as_utc(rotation):
    rotation.write(table={A: Q1, B: Q2},
                   pool={Q1: _entry(A, rotated="2026-11-20T20:00:00+08:00"),
                         Q2: _entry(B, rotated="2026-11-20T12:00:00.000001Z")})
    result = _api(rotation.qa, "rotate_due")(older_than_days=1, now=NOW, generator=lambda: Q3)
    assert result["uids"] == [A]
    assert rotation.read()["table"] == {A: Q3, B: Q2}


def test_due_skips_unassigned_and_nonreal_entries_without_writes(rotation, monkeypatch):
    rotation.write(table={A: Q1}, pool={Q1: _entry(A, real=False), Q2: _entry()})
    before = {path.name: path.read_bytes() for path in rotation.root.iterdir()}
    monkeypatch.setattr(rotation.qa, "generate_real_qimei", lambda: pytest.fail("empty batch generated"))
    monkeypatch.setattr(rotation.qa, "save_state", lambda *_: pytest.fail("empty batch saved"))
    result = _api(rotation.qa, "rotate_due")(older_than_days=1, now=NOW)
    assert result["selected"] == result["rotated"] == result["eligible"] == 0
    assert result["revision"] == 7 and result["status"] == "unchanged"
    # A writer may create its stable coordination lock, but state is unchanged.
    assert rotation.path.read_bytes() == before["state.json"]


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-01-01", "2026-01-01T00:00:00", "", "2026-99-99T00:00:00Z"])
def test_due_rejects_bad_or_naive_rotation_timestamps_without_generating(rotation, monkeypatch, stamp):
    rotation.write(table={A: Q1}, pool={Q1: _entry(A, rotated=stamp)})
    before = rotation.path.read_bytes()
    monkeypatch.setattr(rotation.qa, "generate_real_qimei", lambda: pytest.fail("invalid age generated"))
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_due")(older_than_days=1, now=NOW)
    assert rotation.path.read_bytes() == before


class IntSubclass(int):
    pass


@pytest.mark.parametrize("days", [True, False, None, 0, -1, float("nan"), float("inf"), "1", IntSubclass(1), 1e100,
                                 10 ** 1000])
def test_due_threshold_requires_finite_positive_exact_days(rotation, monkeypatch, days):
    monkeypatch.setattr(rotation.qa, "load_state", lambda *_: pytest.fail("invalid threshold loaded state"))
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_due")(older_than_days=days, now=NOW)
    assert list(rotation.root.iterdir()) == []


@pytest.mark.parametrize("maximum", [True, False, 0, -1, 1001, 1.0, "1", IntSubclass(1)])
def test_due_account_limit_is_an_exact_bounded_positive_integer(rotation, maximum):
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_due")(older_than_days=1, max_accounts=maximum, now=NOW)
    assert list(rotation.root.iterdir()) == []


@pytest.mark.parametrize("now", ["2026-11-21T12:00:00Z", dt.datetime(2026, 11, 21),
                               dt.date(2026, 11, 21), True])
def test_due_explicit_clock_requires_a_timezone_aware_datetime(rotation, now):
    with pytest.raises(rotation.qa.QimeiPoolError):
        _api(rotation.qa, "rotate_due")(older_than_days=1, now=now)
    assert list(rotation.root.iterdir()) == []


def test_due_transaction_holds_writer_lock_for_load_generation_and_save(rotation, monkeypatch):
    rotation.write()
    qa = rotation.qa
    lock, load, save = qa._locked, qa.load_state, qa.save_state
    held = []

    @contextlib.contextmanager
    def observed_lock(directory):
        with lock(directory):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def observed_load(*args, **kwargs):
        assert held
        return load(*args, **kwargs)

    def observed_save(*args, **kwargs):
        assert held
        return save(*args, **kwargs)

    values = iter((Q2, Q3))

    def generate():
        assert held
        return next(values)

    monkeypatch.setattr(qa, "_locked", observed_lock)
    monkeypatch.setattr(qa, "load_state", observed_load)
    monkeypatch.setattr(qa, "save_state", observed_save)
    assert _api(qa, "rotate_due")(older_than_days=1, now=NOW, generator=generate)["rotated"] == 2


def test_concurrent_rotations_reload_under_lock_and_preserve_both_updates(rotation):
    rotation.write()
    function = _api(rotation.qa, "rotate_ex")
    barrier = threading.Barrier(3)
    errors = []

    def rotate(uid, value):
        try:
            barrier.wait(timeout=2)
            function(uid, generator=lambda: value)
        except BaseException as error:
            errors.append(type(error).__name__)

    threads = [threading.Thread(target=rotate, args=(A, Q2)),
               threading.Thread(target=rotate, args=(B, Q3))]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert rotation.read()["table"] == {A: Q2, B: Q3}
    assert rotation.read()["revision"] == 9


@pytest.mark.parametrize("argv", [["--rotate", A], ["--rotate-due-days", "1", "--max-rotate-accounts", "1"]])
def test_cli_summary_hides_all_identity_values_fragments_and_generator_errors(rotation, monkeypatch, argv):
    rotation.write()
    monkeypatch.setattr(rotation.qa, "generate_real_qimei", lambda: Q2)
    output, errors = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(errors)
    rotation.qa.logger.addHandler(handler)
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert rotation.qa.main([*argv, "--summary-only"]) == 0
        report = json.loads(output.getvalue())
        assert report["rotated"] == 1 and report["revision"] == 8
        assert "uids" not in report and "rotations" not in report and "uid" not in report
        for identity in (A, B, Q1, Q2, rotation.qa.mask_uid(A), rotation.qa.mask_qimei(Q1)):
            assert identity not in output.getvalue() + errors.getvalue()
        before = rotation.path.read_bytes()
        output.seek(0)
        output.truncate()

        def failed():
            raise RuntimeError("sensitive uid=" + A + " qimei=" + Q2)

        monkeypatch.setattr(rotation.qa, "generate_real_qimei", failed)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert rotation.qa.main(["--rotate", A, "--summary-only"]) != 0
        assert "sensitive" not in output.getvalue() + errors.getvalue()
        assert A not in output.getvalue() + errors.getvalue()
        assert rotation.path.read_bytes() == before
    finally:
        rotation.qa.logger.removeHandler(handler)


def test_cli_dry_run_no_generate_is_read_only_and_mutually_exclusive(rotation):
    rotation.write()
    before = {path.name: path.read_bytes() for path in rotation.root.iterdir()}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert rotation.qa.main(["--rotate", A, "--dry-run", "--no-generate", "--summary-only"]) == 0
    assert json.loads(output.getvalue())["rotated"] == 0
    assert {path.name: path.read_bytes() for path in rotation.root.iterdir()} == before
    with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit):
        rotation.qa.main(["--rotate", A, "--assign", B])


def test_explicit_empty_cli_rotation_is_a_sanitized_rotation_failure(rotation):
    rotation.write()
    before = rotation.path.read_bytes()
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = rotation.qa.main(["--rotate", "", "--summary-only"])
    assert code == 3
    assert json.loads(output.getvalue()) == {"ok": False, "status": "rotation_failed"}
    assert rotation.path.read_bytes() == before


@pytest.mark.parametrize("argv", [
    ["--rotate-due-days", "sensitive-identity"],
    ["--rotate-due-days", "1", "--max-rotate-accounts", "sensitive-identity"],
    ["--rotate", A, "--sensitive-unknown-argument"],
    ["--rotate-due-days"],
    ["--rotate-due-days", "1", "sensitive-extra-positional"],
])
def test_summary_only_parser_failures_never_echo_any_argument(rotation, argv):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = rotation.qa.main([*argv, "--summary-only"])
    assert code == 2
    assert json.loads(output.getvalue()) == {"ok": False, "status": "argument_error"}
    assert errors.getvalue() == ""
    assert "sensitive" not in output.getvalue()
    assert A not in output.getvalue()
    assert list(rotation.root.iterdir()) == []


def test_ordinary_argument_parser_behavior_is_preserved(rotation):
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors), pytest.raises(SystemExit) as caught:
        rotation.qa.main(["--rotate-due-days", "ordinary-invalid-value"])
    assert caught.value.code == 2
    assert "ordinary-invalid-value" in errors.getvalue()


@pytest.mark.parametrize("summary_flag", ["--summary", "--summary-on", "--summary-only=sensitive-identity"])
def test_summary_option_abbreviations_and_invalid_attached_values_are_also_private(rotation, summary_flag):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = rotation.qa.main(["--rotate-due-days", "sensitive-identity", summary_flag])
    assert code == 2
    assert json.loads(output.getvalue()) == {"ok": False, "status": "argument_error"}
    assert errors.getvalue() == ""
