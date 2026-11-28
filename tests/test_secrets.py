"""Fable's review, №6: an engine taken over by a PDF must not get the AI key. DPAPI keeps out
other users, not a process of low integrity of the same user, and an engine reads whatever
the user may read: the key lives in a file that processes of low integrity may not read."""
import os

import pytest
from PySide6.QtCore import QSettings
from test_ui import app  # noqa: F401

from furceli import ai, client


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("view/zoom", 100)  # so that the file exists once the key has gone from it
    return s


def text(settings):
    settings.sync()
    with open(settings.fileName(), "rb") as f:
        return f.read()


def test_the_engine_cannot_read_the_ai_key(app, settings):  # noqa: F811
    ai.save_key(settings, "sk-ant-test-123")
    assert ai.load_key(settings) == "sk-ant-test-123"
    assert b"ai" not in text(settings)  # not among the settings, which an engine may read
    c = client.EngineClient()
    try:
        assert client.run_task(c, "_peek", (settings.fileName(),)) == "read"  # it does read
        assert client.run_task(c, "_peek", (ai.key_file(settings),)) == "PermissionError"
    finally:
        c.shutdown()


def test_a_key_kept_the_old_way_moves(settings):
    import base64

    import win32crypt
    blob = win32crypt.CryptProtectData(b"sk-ant-old", "ETRATI", None, None, None, 0)
    settings.setValue("ai/key", base64.b64encode(blob).decode())
    assert ai.load_key(settings) == "sk-ant-old"
    assert b"ai" not in text(settings) and os.path.exists(ai.key_file(settings))
    assert ai.load_key(settings) == "sk-ant-old"


@pytest.fixture
def seen(monkeypatch):
    """Everything the window sends to engines."""
    sent = []
    real = client.EngineClient.submit

    def submit(self, key, priority, op, *args):
        sent.append(repr((op, args)))
        return real(self, key, priority, op, *args)
    monkeypatch.setattr(client.EngineClient, "submit", submit)
    return sent


def test_signing_keeps_the_key_from_the_engine(app, seen, tmp_path):  # noqa: F811
    from test_pages import build

    from furceli import files, signing
    pfx = str(tmp_path / "c.pfx")
    signing.make_certificate(pfx, "PFX-secret-pw", "ნინო ტესტი")
    c = client.EngineClient()
    try:
        src, tmp = build(tmp_path / "a.pdf"), files.outbox("signed.pdf")
        signing.sign_through(c, src, tmp, "", pfx, "PFX-secret-pw",
                             {"page": 0, "box": (300, 50, 500, 110), "reason": "ვეთანხმები"})
    finally:
        c.shutdown()
    v = signing.verify(tmp)
    assert len(v) == 1 and v[0]["intact"] and "ნინო ტესტი" in v[0]["signer"]
    assert seen and not any("PFX-secret-pw" in s or "c.pfx" in s for s in seen)


def test_the_window_signs_only_what_it_delivers(tmp_path):
    """A lying engine cannot get the key put to a digest of another document."""
    from test_pages import build

    from furceli import signing
    from furceli.engine import EngineError
    pfx = str(tmp_path / "c.pfx")
    signing.make_certificate(pfx, "pw", "Test")
    signer = signing.load_signer(pfx, "pw")
    out = str(tmp_path / "p.pdf")
    prep = signing.prepare(build(tmp_path / "a.pdf"), out, "", signing.certificate(signer), {})
    with pytest.raises(EngineError, match="sign_failed"):
        signing.sign_prepared(signer, out, dict(prep, digest=bytes(len(prep["digest"]))))
    signing.finish(out, prep, signing.sign_prepared(signer, out, prep), "")
    assert signing.verify(out)[0]["intact"]


def test_the_engine_no_longer_signs_or_makes_keys(app, tmp_path):  # noqa: F811
    c = client.EngineClient()
    try:
        for op, args in (("sign", ("a", "b", "", "c.pfx", "pw", {})),
                         ("make_certificate", ("c.pfx", "pw", "x"))):
            with pytest.raises(client.TaskError, match="bad_op"):
                client.run_task(c, op, args)
    finally:
        c.shutdown()


def test_forgetting_the_key(settings):
    ai.save_key(settings, "sk-ant-test-123")
    ai.forget_key(settings)
    assert ai.load_key(settings) == "" and not os.path.exists(ai.key_file(settings))
