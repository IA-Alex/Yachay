"""Hard engine limits (ADR-014/015/016).

These are correctness bounds, not section 16 performance parameters: they do
not depend on load and are not pending calibration.
"""

# Maximum number of executions started for one job_id. `claim_next` increments
# `attempt` on every claim; once an expired lease is found with this many
# attempts, reconciliation marks the job `failed` instead of requeueing it.
MAX_ATTEMPTS = 3

# Maximum accepted request body for the API (ADR-016). A safety bound: it keeps
# the process from buffering an unbounded body. The API spools at most this much
# and never holds it all in memory.
MAX_REQUEST_BYTES = 16 * 1024 * 1024

# Upper bound on the bytes the API discards after refusing an oversized body.
# Draining keeps the client's write from failing with EPIPE, so the refusal
# arrives as a clean 413 instead of a broken connection.
MAX_REQUEST_DRAIN = 4 * MAX_REQUEST_BYTES

# All readers share this bound, including CLI and files placed on disk directly.
MAX_SOURCE_BYTES = MAX_REQUEST_BYTES
MAX_ARCHIVE_ENTRIES = 2048
MAX_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_TABLE_ROWS = 100_000
MAX_TABLE_COLUMNS = 10_000
MAX_TABLE_CELLS = 1_000_000
MAX_DOCUMENT_PAGES = 5_000
MAX_DOCUMENT_ROWS = 100_000
MAX_DOCUMENT_TEXT_CHARS = 32 * 1024 * 1024

# Retention parameters (spec section 12, ADR-017). These are startup values
# pending calibration (spec section 16): they are not validated performance
# parameters. They live here, not in `defaults.toml`, for the same reason as
# `MAX_ATTEMPTS`: that file enters `config_hash`, which enters `job_key`, so a
# retention change would invalidate the deduplication of every input. Garbage
# collection never alters a published result, so it does not participate in
# result equivalence (ADR-006, ADR-011).
DATASET_VERSIONS_TO_KEEP = 3
FAILED_RETENTION_DAYS = 30
QUARANTINE_RETENTION_DAYS = 30

