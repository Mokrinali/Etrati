"""Fable's review, №5: an engine taken over by a PDF may turn a folder of its own into a
junction to the user's files. What the window then removes "from the engine's folder" must
not be the user's file: the window has the rights the engine lacks."""
import os
import shutil
import _winapi

import pytest

from furceli import files


@pytest.fixture
def trap(tmp_path):
    """(engine file, the user's file it leads to once its folder became a junction)."""
    tmp = files.outbox("a.pdf")
    with open(tmp, "wb") as f:
        f.write(b"engine")
    user = tmp_path / "user"
    user.mkdir()
    victim = user / "a.pdf"
    victim.write_bytes(b"the user's own")
    folder = os.path.dirname(tmp)
    shutil.rmtree(folder)
    _winapi.CreateJunction(str(user), folder)
    yield tmp, victim
    if os.path.lexists(folder):
        os.rmdir(folder)  # the junction only


def test_discard_does_not_reach_through_a_junction(trap):
    tmp, victim = trap
    files.discard(tmp)
    assert victim.read_bytes() == b"the user's own"


def test_saving_does_not_remove_the_users_file(trap, tmp_path):
    tmp, victim = trap
    files.put_in_place(tmp, str(tmp_path / "saved.pdf"))
    assert victim.exists()


def test_delivering_does_not_remove_the_users_file(trap, tmp_path):
    tmp, victim = trap
    files.deliver_all([(tmp, str(tmp_path / "new.pdf"))])
    assert victim.exists()


def test_the_engines_own_files_still_go():
    tmp = files.outbox("b.pdf")
    with open(tmp, "wb") as f:
        f.write(b"engine")
    files.discard(tmp)
    assert not os.path.exists(tmp)


def test_files_outside_the_engines_folder_still_go(tmp_path):
    p = tmp_path / "x.tmp"
    p.write_bytes(b"x")
    files.discard(str(p))
    assert not p.exists()
