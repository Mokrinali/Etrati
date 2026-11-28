"""Fable's review, №4: rebuilding a document (undo, recovery after a crash) replays every edit,
and a fixed time limit for the whole replay made a long but healthy rebuild look like a
crash. The document then ended up unsavable. A replay keeps going as long as every edit
finishes in the time one edit may take."""
import pytest
from test_pages import build
from test_ui import app  # noqa: F401

from furceli import client


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setitem(client.LONG, "replay", 1.5)
    monkeypatch.setitem(client.LONG, "apply", 1.5)
    c = client.EngineClient()
    yield c
    c.shutdown()


def test_a_long_replay_of_quick_edits_finishes(app, engine, tmp_path):  # noqa: F811
    src = build(tmp_path / "a.pdf")
    info = client.run_task(engine, "replay", (src, "", [("_nap", .6)] * 5))  # 3 s in all
    assert info["mapping"] is None  # what a finished replay answers


def test_one_edit_that_hangs_still_times_out(app, engine, tmp_path):  # noqa: F811
    src = build(tmp_path / "a.pdf")
    with pytest.raises(client.TaskError, match="crash"):
        client.run_task(engine, "replay", (src, "", [("_nap", 4)]))


def test_recovery_after_a_crash_replays_all_the_edits(app, engine, tmp_path):  # noqa: F811
    """The rebuild the client does on its own before the next operation, after a crash."""
    src = build(tmp_path / "a.pdf")
    ops = [("_nap", .6)] * 5
    client.run_task(engine, "replay", (src, "", ops))
    engine.recover_with(src, "", ops)
    with pytest.raises(client.TaskError, match="crash"):
        client.run_task(engine, "_crash", ())
    assert client.run_task(engine, "page_texts", ()) is not None  # rebuilt, then asked
