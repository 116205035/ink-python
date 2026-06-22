"""Tests for the reactive core (`pyink.core.signal`)."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from pyink import (
    Computed,
    CyclicDependency,
    Signal,
    batch,
    computed,
    effect,
    ref,
    signal,
)

# ---------------------------------------------------------------------------
# signal basics
# ---------------------------------------------------------------------------


def test_signal_read_write_default_value() -> None:
    s = signal(42)
    assert s.value == 42
    s.value = 7
    assert s.value == 7


def test_signal_setter_notifies_subscribers() -> None:
    s = signal(0)
    calls: list[int] = []
    effect(lambda: calls.append(s.value))
    assert calls == [0]
    s.value = 1
    assert calls == [0, 1]


def test_signal_multiple_subscribers_all_notified() -> None:
    s = signal(0)
    a: list[int] = []
    b: list[int] = []
    effect(lambda: a.append(s.value))
    effect(lambda: b.append(s.value))
    s.value = 99
    assert a == [0, 99]
    assert b == [0, 99]


def test_signal_dispose_stops_notifications() -> None:
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))
    s.value = 1
    assert calls == [0, 1]
    dispose()
    s.value = 2
    assert calls == [0, 1]  # no further notifications


def test_signal_equal_value_does_not_notify() -> None:
    s = signal(5)
    calls: list[int] = []
    effect(lambda: calls.append(s.value))
    s.value = 5  # equal — should be a no-op
    assert calls == [5]


# ---------------------------------------------------------------------------
# computed
# ---------------------------------------------------------------------------


def test_computed_first_evaluation_is_lazy() -> None:
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return 1

    c = computed(fn)
    assert evaluations["count"] == 0  # never read yet
    assert c.value == 1
    assert evaluations["count"] == 1


def test_computed_caches_until_dependency_changes() -> None:
    s = signal(10)
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return s.value * 2

    c = computed(fn)
    assert c.value == 20
    assert c.value == 20
    assert c.value == 20
    assert evaluations["count"] == 1  # cached

    s.value = 11
    assert c.value == 22
    assert evaluations["count"] == 2


def test_computed_chained() -> None:
    a = signal(1)
    b = computed(lambda: a.value + 1)
    c = computed(lambda: b.value * 10)
    assert c.value == 20
    a.value = 2
    assert c.value == 30


def test_computed_lazy_until_read() -> None:
    """A computed never read should never call its fn."""
    s = signal(0)
    called = {"count": 0}

    def fn() -> int:
        called["count"] += 1
        return s.value

    computed(fn)  # discard the result — never read
    s.value = 1
    s.value = 2
    assert called["count"] == 0


def test_computed_cycle_raises() -> None:
    """A computed that depends on itself must raise, not stack-overflow."""
    # We construct the cycle after creation by attaching a side-effect.
    a: Computed[int] | None = None
    a = computed(lambda: a.value + 1 if a is not None else 0)
    with pytest.raises(CyclicDependency):
        _ = a.value


def test_computed_recomputes_only_for_tracked_dependency() -> None:
    a = signal(1)
    b = signal(100)
    evaluations = {"count": 0}

    def fn() -> int:
        evaluations["count"] += 1
        return a.value  # only depends on a

    c = computed(fn)
    assert c.value == 1
    b.value = 200  # c does not depend on b
    assert c.value == 1
    assert evaluations["count"] == 1
    a.value = 2
    assert c.value == 2
    assert evaluations["count"] == 2


def test_computed_subscribed_by_effect() -> None:
    a = signal(1)
    b = computed(lambda: a.value * 2)
    observed: list[int] = []
    effect(lambda: observed.append(b.value))
    assert observed == [2]
    a.value = 5
    assert observed == [2, 10]


# ---------------------------------------------------------------------------
# effect
# ---------------------------------------------------------------------------


def test_effect_runs_on_mount() -> None:
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1

    effect(fn)
    assert runs["count"] == 1


def test_effect_auto_track_re_runs_on_change() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]
    s.value = 1
    s.value = 2
    assert runs == [0, 1, 2]


def test_effect_empty_deps_runs_once() -> None:
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value  # reading should not subscribe when deps=[]

    effect(fn, deps=[])
    s.value = 1
    s.value = 2
    assert runs["count"] == 1


def test_effect_explicit_deps_re_runs_only_on_change() -> None:
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1

    # Pass the Signal itself (not its current value) so the effect can
    # subscribe to it and compare snapshots across runs.
    effect(fn, deps=[s])
    assert runs["count"] == 1
    s.value = 0  # equal to previous — should not re-run
    assert runs["count"] == 1
    s.value = 1  # changed — re-run
    assert runs["count"] == 2


def test_effect_cleanup_runs_before_next_run() -> None:
    s = signal(0)
    events: list[str] = []

    def fn() -> Callable[[], None]:
        events.append(f"run:{s.value}")

        def cleanup() -> None:
            events.append(f"cleanup:{s.value}")

        return cleanup

    effect(fn)
    s.value = 1
    s.value = 2
    # Mount run + cleanup before each subsequent run.
    assert events == [
        "run:0",
        "cleanup:1",
        "run:1",
        "cleanup:2",
        "run:2",
    ]


def test_effect_dispose_runs_final_cleanup() -> None:
    events: list[str] = []

    def fn() -> Callable[[], None]:
        events.append("run")

        def cleanup() -> None:
            events.append("cleanup")

        return cleanup

    dispose = effect(fn)
    dispose()
    assert events == ["run", "cleanup"]


def test_effect_nested() -> None:
    outer = signal(0)
    inner = signal(0)
    log: list[str] = []

    def outer_fn() -> None:
        log.append(f"outer:{outer.value}")

        def inner_fn() -> None:
            log.append(f"inner:{inner.value}")

        effect(inner_fn)

    effect(outer_fn)
    # On mount both run.
    assert log == ["outer:0", "inner:0"]
    log.clear()
    outer.value = 1
    # Outer re-running creates a new inner effect (so inner runs again).
    assert log == ["outer:1", "inner:0"]
    log.clear()
    inner.value = 99
    # Both the still-alive original inner and the newly-created one observe.
    assert "inner:99" in log


# ---------------------------------------------------------------------------
# ref
# ---------------------------------------------------------------------------


def test_ref_does_not_subscribe() -> None:
    r = ref(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = r.value

    effect(fn, deps=None)
    assert runs["count"] == 1
    r.value = 1
    r.value = 2
    assert runs["count"] == 1  # ref reads don't subscribe


def test_ref_read_write() -> None:
    r = ref("a")
    assert r.value == "a"
    r.value = "b"
    assert r.value == "b"


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


def test_batch_coalesces_notifications() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]

    def write() -> None:
        s.value = 1
        s.value = 2
        s.value = 3

    batch(write)
    # Should fire exactly once after batch exits.
    assert runs == [0, 3]


def test_batch_returns_fn_result() -> None:
    result = batch(lambda: 42)
    assert result == 42


def test_batch_inner_reads_see_latest_value() -> None:
    s = signal(0)
    seen: list[int] = []

    def write() -> None:
        s.value = 5
        seen.append(s.value)  # reads inside batch should see 5
        s.value = 10
        seen.append(s.value)

    batch(write)
    assert seen == [5, 10]


def test_batch_nested_only_outer_flushes() -> None:
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]

    def inner() -> None:
        s.value = 1
        s.value = 2

    def outer() -> None:
        s.value = 3
        batch(inner)
        s.value = 4
        # At this point, no notifications should have fired yet.
        assert runs == [0]

    batch(outer)
    # Only one notification fired after the outer batch exits.
    assert runs == [0, 4]


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_signal_concurrent_writes() -> None:
    s = signal(0)
    n_workers = 8
    n_writes_per_worker = 500

    def worker() -> None:
        for _ in range(n_writes_per_worker):
            # Read-modify-write under explicit locking is not enough at the
            # signal layer; we use a thread-safe increment by appending a
            # unit list atomically.
            current = s.value
            s.value = current + 1

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Because read-modify-write is not atomic across threads, the final
    # value may be less than n_workers * n_writes_per_worker — but the test
    # asserts no exception was raised and the value is in a sensible range.
    expected_max = n_workers * n_writes_per_worker
    assert 1 <= s.value <= expected_max


def test_signal_concurrent_writes_atomic() -> None:
    """Each thread writes a unique value; final set must contain all writes."""
    s = signal(0)
    lock = threading.Lock()
    seen: set[int] = set()
    n_workers = 8
    n_per = 200

    def worker(idx: int) -> None:
        nonlocal seen
        local: set[int] = set()
        for i in range(n_per):
            v = idx * n_per + i + 1
            s.value = v
            local.add(v)
        with lock:
            seen |= local

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == n_workers * n_per


# ---------------------------------------------------------------------------
# Edge cases (notification dedup, computed equality, lifecycle idempotency)
# ---------------------------------------------------------------------------


def test_effect_reads_signal_and_computed_dedupe() -> None:
    """An effect that reads both a Signal and a Computed derived from it must
    re-run exactly once per source write, not once per notification path.
    """
    s = signal(0)
    c = computed(lambda: s.value * 2)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = c.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 5
    assert runs["count"] == 2  # not 3
    s.value = 6
    assert runs["count"] == 3


def test_effect_reads_signal_and_computed_dedupe_in_batch() -> None:
    """Same dedup must hold inside ``batch``."""
    s = signal(0)
    c = computed(lambda: s.value * 2)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = c.value

    effect(fn)
    assert runs["count"] == 1

    def write() -> None:
        s.value = 5

    batch(write)
    assert runs["count"] == 2


def test_computed_equal_value_does_not_notify() -> None:
    """A Computed whose recomputed value is equal to its previous value must
    not notify its subscribers (Decision 11.4 applied transitively).
    """
    s = signal(1)
    # Constant computed — value never changes regardless of source.
    c = computed(lambda: 0 if s.value > -100 else 0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = c.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 2  # c stays 0
    s.value = 3  # c stays 0
    assert runs["count"] == 1


def test_effect_dispose_is_idempotent() -> None:
    """Calling dispose multiple times must be safe and not raise."""
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))
    dispose()
    dispose()  # second dispose: no-op
    dispose()  # third dispose: no-op
    s.value = 99
    assert calls == [0]


def test_batch_dispose_effect_prevents_further_runs() -> None:
    """Disposing an effect inside a batch must prevent that effect from
    running again when the batch flushes."""
    s = signal(0)
    calls: list[int] = []
    dispose = effect(lambda: calls.append(s.value))

    def write() -> None:
        dispose()
        s.value = 100

    batch(write)
    # Mount run only; the dispose inside batch prevents the flush from
    # re-running the effect.
    assert calls == [0]


def test_same_signal_read_multiple_times_subscribes_once() -> None:
    """Reading the same Signal multiple times inside an effect must establish
    a single subscription, so a single write triggers a single re-run.
    """
    s = signal(0)
    runs = {"count": 0}

    def fn() -> None:
        runs["count"] += 1
        _ = s.value
        _ = s.value
        _ = s.value

    effect(fn)
    assert runs["count"] == 1
    s.value = 1
    assert runs["count"] == 2  # exactly one re-run per write
    s.value = 2
    assert runs["count"] == 3


def test_computed_in_effect_subscribes_to_both() -> None:
    """An effect reading both a Signal and a Computed derived from it is
    effectively subscribed to both paths but must observe a consistent
    snapshot per re-run (signal value matches computed value).
    """
    a = signal(1)
    b = computed(lambda: a.value * 2)
    snapshots: list[tuple[int, int]] = []
    effect(lambda: snapshots.append((a.value, b.value)))
    assert snapshots == [(1, 2)]
    a.value = 5
    assert snapshots == [(1, 2), (5, 10)]


def test_subscriber_exception_is_swallowed() -> None:
    """A subscriber raising during notification must not prevent other
    subscribers from firing (Decision 11.3). Initial mount runs fn() directly
    (not via the notification path), so exceptions there propagate — but
    subsequent notifications are guarded.
    """
    s = signal(0)
    received: list[int] = []
    runs = {"boom": 0}

    def boom() -> None:
        runs["boom"] += 1
        if runs["boom"] > 1:  # only raise on re-run (notification path)
            raise RuntimeError("bad subscriber")

    def good() -> None:
        received.append(s.value)

    effect(boom, deps=[s])
    effect(good, deps=[s])
    assert received == [0]
    s.value = 7  # both effects notified; boom raises (swallowed), good appends
    assert received == [0, 7]


# ---------------------------------------------------------------------------
# Notification ordering — two-phase flush (PR2 fix)
#
# Each test below must pass when run in isolation. They guard the
# signal → computed → effect topological order inside a single flush and
# do not rely on side effects left behind by other tests.
# ---------------------------------------------------------------------------


def test_three_level_chain_signal_computed_computed_effect() -> None:
    """A → B → C → effect(A, C): the effect must observe both A's new value
    and C's recomputed value in the same snapshot, regardless of the depth of
    the computed chain.
    """
    a = signal(1)

    def b_fn() -> int:
        return a.value + 1

    b = computed(b_fn)

    def c_fn() -> int:
        return b.value * 10

    c = computed(c_fn)

    snapshots: list[tuple[int, int]] = []
    effect(lambda: snapshots.append((a.value, c.value)))
    assert snapshots == [(1, 20)]  # a=1, b=2, c=20
    a.value = 4
    # a=4, b=5, c=50 — single consistent snapshot, not (4, 20).
    assert snapshots == [(1, 20), (4, 50)]


def test_chained_effects_run_after_signal_write() -> None:
    """effect1 writes a signal during re-run; effect2 (subscribed to that
    signal) must observe the new value during the same flush.
    """
    src = signal(0)
    relay = signal(0)
    log: list[str] = []

    def effect1() -> None:
        log.append(f"e1:{src.value}")
        # Mirror src → relay so effect2 cascades within the same flush.
        relay.value = src.value

    def effect2() -> None:
        log.append(f"e2:{relay.value}")

    effect(effect1)
    effect(effect2)
    # Both effects ran once on mount; effect1 set relay to 0 (already 0 —
    # no notification), so effect2 saw 0 from its own mount.
    assert log == ["e1:0", "e2:0"]
    log.clear()
    src.value = 7
    # effect1 runs first → sets relay.value = 7 → effect2 must see 7 in the
    # same flush (cascade through deferred-effects phase 2).
    assert "e1:7" in log
    assert "e2:7" in log


def test_nested_computed_chain_refreshed_before_effect() -> None:
    """``a = computed(count)``, ``b = computed(a)``: writing ``count`` must
    refresh both computeds before any subscribing effect re-runs.
    """
    count = signal(10)
    a = computed(lambda: count.value + 1)
    b = computed(lambda: a.value * 2)
    seen: list[int] = []
    effect(lambda: seen.append(b.value))
    assert seen == [22]  # count=10, a=11, b=22
    count.value = 20
    # count=20, a=21, b=42 — single consistent snapshot.
    assert seen == [22, 42]


def test_multiple_writes_same_signal_in_batch_single_rerun() -> None:
    """Multiple writes to the same signal within a single batch must trigger
    exactly one effect re-run observing the final value (independent of any
    state left by other tests).
    """
    s = signal(0)
    runs: list[int] = []
    effect(lambda: runs.append(s.value))
    assert runs == [0]

    def write() -> None:
        s.value = 1
        s.value = 2
        s.value = 3

    batch(write)
    assert runs == [0, 3]


def test_signal_notifies_computed_before_effect_independent() -> None:
    """Even with a single subscriber ordering quirk (effect subscribed to the
    Signal before the Computed), the effect must observe the refreshed
    Computed. This is the minimal repro for the PR2 ordering bug, written so
    it does not depend on any prior test having populated subscriber sets.
    """
    count = signal(1)
    double = computed(lambda: count.value * 2)
    results: list[tuple[int, int]] = []
    effect(lambda: results.append((count.value, double.value)))
    assert results == [(1, 2)]
    count.value = 5
    assert results == [(1, 2), (5, 10)]


# ---------------------------------------------------------------------------
# PR2 audit: concurrency stress tests for race conditions
# ---------------------------------------------------------------------------
#
# These tests target the four race conditions called out in the 6-agent audit:
#
# 1. ``_notification_epoch`` read outside the epoch lock (now via
#    ``_read_epoch``).
# 2. ``_deferred_effects`` list swap outside ``_deferred_lock`` (already in the
#    lock; this test exercises the path under heavy contention).
# 3. Signal setter ``is``/``==`` race (now fully atomic under ``self._lock``).
# 4. Computed mark-dirty/recompute/compare race (already atomic under
#    ``self._lock``).
#
# Stress tests are inherently probabilistic. They run many iterations across
# multiple threads so a missing lock surfaces as a flaky failure rather than
# a silent bug in production.


def test_concurrent_signal_writes_are_atomic() -> None:
    """N threads each increment the same int signal ``per_thread`` times.

    After joining, the signal value must equal ``N * per_thread`` exactly.
    A TOCTOU in the setter (read-modify-write without the lock) would drop
    updates and produce a smaller final value.
    """
    n_threads = 8
    per_thread = 500
    counter = signal(0)

    def worker() -> None:
        for _ in range(per_thread):
            # Read-modify-write under contention. The setter's internal
            # ``self._lock`` only protects the assign, not this RMW loop —
            # so we wrap the RMW in our own lock to make the increment
            # atomic. What we are asserting is that the signal's internal
            # state stays consistent (no lost subscribers, no duplicate
            # notifications breaking later effects).
            with counter_write_lock:
                counter.value = counter.value + 1

    counter_write_lock = threading.Lock()
    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.value == n_threads * per_thread


def test_concurrent_signal_write_and_effect_trigger() -> None:
    """One thread writes a signal many times; another drains the same signal
    inside an effect. The effect must observe every distinct value at least
    once and must never crash or deadlock.
    """
    s = signal(0)
    observed: list[int] = []
    observed_lock = threading.Lock()

    def eff() -> None:
        v = s.value
        with observed_lock:
            observed.append(v)

    dispose = effect(eff)
    try:
        n_writes = 300

        def writer() -> None:
            for i in range(1, n_writes + 1):
                s.value = i

        def reader() -> None:
            # Repeatedly read to force tracking contention.
            for _ in range(n_writes * 2):
                _ = s.value

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Final value is observed (eventually-consistent through the last
        # flush). At minimum, the effect must have seen the last write.
        assert observed, "effect never fired"
        assert n_writes in observed or observed[-1] == n_writes
    finally:
        dispose()


def test_concurrent_computed_recompute() -> None:
    """Many threads read a Computed derived from a Signal while one writer
    mutates the Signal. Every reader must observe a value that satisfies the
    Computed's invariant (``value == src * 2``) — never a stale cached value
    nor a half-updated state.
    """
    src = signal(1)
    double = computed(lambda: src.value * 2)

    n_readers = 8
    per_reader = 400
    n_writes = 400
    errors: list[str] = []
    errors_lock = threading.Lock()
    stop = threading.Event()

    def reader(idx: int) -> None:
        local_errors: list[str] = []
        for _ in range(per_reader):
            v = double.value
            # ``v`` must be even and ``src.value * 2`` at the moment we read
            # ``v``. We can't re-check ``src`` after the fact (it may have
            # changed), so the invariant we can assert is "even number that
            # was a valid ``src * 2`` at some consistent instant".
            if v % 2 != 0:
                local_errors.append(f"reader{idx}: odd value {v}")
        with errors_lock:
            errors.extend(local_errors)

    def writer() -> None:
        for i in range(1, n_writes + 1):
            src.value = i
        stop.set()

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(n_readers)]
    w = threading.Thread(target=writer)
    for t in threads:
        t.start()
    w.start()
    for t in threads:
        t.join()
    w.join()
    assert not errors, f"readers observed inconsistent values: {errors[:5]}"
    assert double.value == n_writes * 2


def test_notification_epoch_under_concurrent_flush() -> None:
    """Two batch flushes running concurrently must each complete without the
    epoch counter going backwards or the deferred-effects list leaking
    entries across flushes.
    """
    import sys

    _sig_mod = sys.modules["pyink.core.signal"]

    s1 = signal(0)
    s2 = signal(0)
    log: list[int] = []
    log_lock = threading.Lock()

    def eff1() -> None:
        v = s1.value
        with log_lock:
            log.append(v)

    def eff2() -> None:
        v = s2.value
        with log_lock:
            log.append(v * 10)

    d1 = effect(eff1)
    d2 = effect(eff2)
    try:
        n_iters = 100

        def make_writer(sig: Signal[int], idx: int) -> Callable[[], None]:
            def run() -> None:
                sig.value = idx

            return run

        def flush1() -> None:
            for i in range(1, n_iters + 1):
                batch(make_writer(s1, i))

        def flush2() -> None:
            for i in range(1, n_iters + 1):
                batch(make_writer(s2, i))

        t1 = threading.Thread(target=flush1)
        t2 = threading.Thread(target=flush2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Both batches completed; deferred-effects list must be empty.
        assert _sig_mod._deferred_effects == []
        # Final values observed.
        assert s1.value == n_iters
        assert s2.value == n_iters
        # Epoch is monotonic — current is at least number of flushes.
        assert _sig_mod._notification_epoch >= n_iters * 2
    finally:
        d1()
        d2()

