"""Job queue and state machine backed by the `jobs` table.

The queue is the table itself; no external broker (spec section 6, ADR-007).
"""

from __future__ import annotations

import sqlite3


def enqueue(conn: sqlite3.Connection, *, job_id: str, job_key: str) -> None:
    """Register a newly detected input as `claimed` (section 5, step 4)."""
    conn.execute(
        "INSERT INTO jobs (job_id, job_key, state, created_at) "
        "VALUES (?, ?, 'claimed', COALESCE(strftime('%s','now'), 0))",
        (job_id, job_key),
    )


def mark_queued(conn: sqlite3.Connection, job_id: str) -> None:
    """Advance a claimed job to `queued` after the atomic move (section 5).

    Deliberately narrow: it is the normal move path, never a recovery path.
    Recovering a crashed run is `requeue_expired` (ADR-014).
    """
    conn.execute(
        "UPDATE jobs SET state = 'queued' WHERE job_id = ? AND state = 'claimed'",
        (job_id,),
    )


def requeue_expired(
    conn: sqlite3.Connection, *, now: float, max_attempts: int
) -> list[tuple[str, str]]:
    """Recover jobs whose lease expired (section 5, ADR-014).

    A running job with an expired lease means its worker died. It goes back to
    `queued` while attempts remain; at `max_attempts` it goes to `failed`, so a
    job that always crashes cannot loop forever.

    `claim_next` always writes `lease_until`, so a `running` job without a lease
    cannot be alive: a missing lease counts as expired. Otherwise that state
    would be trapped forever.

    Returns `(job_id, outcome)` pairs, with outcome `requeued` or `failed`.
    """
    rows = conn.execute(
        """
        UPDATE jobs
           SET state = CASE WHEN attempt >= :max_attempts
                            THEN 'failed' ELSE 'queued' END,
               worker_id = NULL, lease_until = NULL, run_id = NULL
         WHERE state = 'running'
           AND (lease_until IS NULL OR lease_until < :now)
        RETURNING job_id, state
        """,
        {"now": now, "max_attempts": max_attempts},
    ).fetchall()
    return [
        (job_id, "failed" if state == "failed" else "requeued")
        for job_id, state in rows
    ]


def claim_next(
    conn: sqlite3.Connection, *, worker_id: str, now: float, ttl: float
) -> str | None:
    """Atomically claim the highest-priority queued job, or None (section 6)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            UPDATE jobs
               SET state = 'running', worker_id = :w,
                   lease_until = :now + :ttl, started_at = :now,
                   attempt = attempt + 1
             WHERE job_id = (
                   SELECT job_id FROM jobs
                    WHERE state = 'queued'
                    ORDER BY priority, created_at
                    LIMIT 1)
            RETURNING job_id
            """,
            {"w": worker_id, "now": now, "ttl": ttl},
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row[0] if row else None


def heartbeat(
    conn: sqlite3.Connection, *, job_id: str, now: float, ttl: float
) -> None:
    """Extend the lease between batches (section 6)."""
    conn.execute(
        "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
        (now + ttl, job_id),
    )


def request_cancel(conn: sqlite3.Connection, job_id: str) -> None:
    """Set the cooperative cancellation flag (section 6)."""
    conn.execute(
        "UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (job_id,)
    )


def is_cancel_requested(conn: sqlite3.Connection, job_id: str) -> bool:
    """Report whether cooperative cancellation was requested (section 6)."""
    row = conn.execute(
        "SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    return bool(row[0]) if row else False
