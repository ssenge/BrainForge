import sys
import types

import pytest

from brainforge import gitops


def test_lock_excludes_a_second_run(brain):
    with gitops.lock(brain.root), pytest.raises(gitops.GitError, match="another BrainForge run"), gitops.lock(brain.root):
        pass
    with gitops.lock(brain.root):  # released once the first `with` exits
        pass


def test_lock_releases_on_exception(brain):
    with pytest.raises(ValueError), gitops.lock(brain.root):
        raise ValueError("boom")
    with gitops.lock(brain.root):  # the failed run didn't leave the lock held
        pass


def test_lock_on_windows(brain, monkeypatch):
    """Simulate the win32 branch: fcntl isn't installed there, so the code path must not need it."""
    calls = []

    def locking(fd, mode, nbytes):
        if mode == fake_msvcrt.LK_NBLCK and calls and calls[-1] == "lock":
            raise PermissionError("already locked")  # what msvcrt.locking raises on Windows (errno EACCES)
        calls.append("lock" if mode == fake_msvcrt.LK_NBLCK else "unlock")

    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=0, locking=locking)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(gitops, "msvcrt", fake_msvcrt, raising=False)

    with gitops.lock(brain.root):
        pass
    assert calls == ["lock"]

    calls.clear()
    with gitops.lock(brain.root), pytest.raises(gitops.GitError, match="another BrainForge run"), gitops.lock(brain.root):
        pass
