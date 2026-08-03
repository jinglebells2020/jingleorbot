#!/usr/bin/env python3
"""Store/session state-machine tests with synthetic embeddings.

Run anywhere with numpy:  python3 test_store.py
"""

import tempfile
from pathlib import Path

import numpy as np

from store import Store, Observation, cosine


def vec(seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def near(v, eps=0.05, seed=999):
    rng = np.random.default_rng(seed)
    out = v + eps * rng.normal(size=v.shape).astype(np.float32)
    return out / np.linalg.norm(out)


def fresh_store(tmp):
    return Store(str(Path(tmp) / "t.db"), absence_close_s=60.0)


def test_known_open_refresh_close(tmp):
    st = fresh_store(tmp)
    pid = st.add_person("enes", now=0)
    st.add_embedding(pid, vec(1), "test", now=0)

    u1 = st.observe(Observation(pid, "enes", 0.7, vec(1)), now=100)
    assert u1.is_new and u1.wants_snap and u1.label == "enes"
    u2 = st.observe(Observation(pid, "enes", 0.5, vec(1)), now=110)
    assert not u2.is_new and u2.session_id == u1.session_id
    assert not u2.wants_snap  # lower score: keep old snap
    u3 = st.observe(Observation(pid, "enes", 0.9, vec(1)), now=120)
    assert not u3.is_new and u3.wants_snap  # better score refreshes snap

    assert st.sweep(now=150) == []          # 30s quiet: still open
    assert st.sweep(now=181) == [u1.session_id]  # >60s: closed
    s = st.recent_sessions(5)[0]
    assert s["ended_at"] == 120 and s["best_score"] == 0.9
    print("ok: known open/refresh/close")


def test_unknown_continuity_and_promote(tmp):
    st = fresh_store(tmp)
    stranger = vec(42)

    u1 = st.observe(Observation(None, None, 0.0, stranger), now=10)
    assert u1.is_new and u1.label == "unknown-%d" % u1.session_id
    # same stranger, slightly different embedding => same session
    u2 = st.observe(Observation(None, None, 0.0, near(stranger)), now=20)
    assert not u2.is_new and u2.session_id == u1.session_id
    # a genuinely different stranger => new session
    u3 = st.observe(Observation(None, None, 0.0, vec(77)), now=30)
    assert u3.is_new and u3.session_id != u1.session_id

    pid = st.promote(u1.session_id, "danil", now=40)
    embs = st.load_embeddings()
    assert any(p == pid and cosine(v, stranger) > 0.9 for p, _, v in embs)
    s = [r for r in st.recent_sessions(10) if r["id"] == u1.session_id][0]
    assert s["label"] == "danil" and s["person_id"] == pid
    # now that they're enrolled, a known observation continues the SAME session
    u4 = st.observe(Observation(pid, "danil", 0.8, stranger), now=50)
    assert not u4.is_new and u4.session_id == u1.session_id
    print("ok: unknown continuity + promote")


def test_two_people_same_frame(tmp):
    st = fresh_store(tmp)
    a = st.add_person("a", now=0); st.add_embedding(a, vec(1), "t", now=0)
    b = st.add_person("b", now=0); st.add_embedding(b, vec(2), "t", now=0)
    ua = st.observe(Observation(a, "a", 0.8, vec(1)), now=10)
    ub = st.observe(Observation(b, "b", 0.8, vec(2)), now=10)
    assert ua.session_id != ub.session_id
    assert len(st.open_sessions()) == 2
    print("ok: two people, same frame, parallel sessions")


def test_restart_closes_stale(tmp):
    st = fresh_store(tmp)
    pid = st.add_person("x", now=0)
    st.observe(Observation(pid, "x", 0.7, vec(5)), now=10)
    db = str(Path(tmp) / "t.db")
    st2 = Store(db)  # simulates service restart
    assert st2.open_sessions() == []
    assert st2.recent_sessions(5)[0]["ended_at"] is not None
    print("ok: restart closes stale sessions")


def test_forget(tmp):
    st = fresh_store(tmp)
    pid = st.add_person("y", now=0)
    st.add_embedding(pid, vec(9), "t", now=0)
    assert st.forget("y") is True
    assert st.load_embeddings() == []
    assert st.forget("y") is False
    print("ok: forget")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        with tempfile.TemporaryDirectory() as tmp:
            t(tmp)
    print("all %d tests passed" % len(tests))
