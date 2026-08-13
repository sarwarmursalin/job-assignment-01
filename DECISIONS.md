# Engineering decisions

Complete this file as part of the assignment.

## Invariants identified

- Logical event identity is `(deviceId, bootId, sequence)`; the raw audit table must contain at most one row per identity, and sequence numbers are only unique within a single boot, not across boots.
- Current-state ordering must be `(generation, sequence)` only. `deviceTime` is diagnostic metadata and must never influence which event is treated as current.
- A realtime state-change message must be published only after a successful database commit, and only when the commit actually changed `current_state`.
- A slow or broken WebSocket client must be isolated: it must not block `publish()` for other clients, must not block telemetry ingestion, and must not cause unbounded server memory growth.
- The dashboard's snapshot (`GET /api/devices`) is authoritative; WebSocket messages are a supplementary low-latency channel and must be re-fetched behind on every successful connection, including reconnects.

## Incidents fixed

- **PR1 (`6f91d38`)** — `telemetry_events` deduplicated on `(device_id, sequence)` only. Since `sequence` restarts at 1 on every boot, a new boot's early events collided with an older boot's rows and were silently dropped as false duplicates. Fixed with `migration_002`, rebuilding the table with `UNIQUE (device_id, boot_id, sequence)`.
- **PR2 (`112fd04`)** — `current_state` was overwritten based on `excluded.device_time > current_state.device_time`. A newly booted device with a clock behind its predecessor's last reading could never take over current state; a delayed event with a skewed clock could move state backward. Fixed by comparing `(generation, sequence)` instead.
- **PR3 (`f05f982`, `fcbeaa1`, `3165752`)** — `TelemetryService.ingest()` built a preview state and published it *before* the database transaction ran at all, unconditionally — duplicates, stale events, and even failed transactions could all trigger a false broadcast. `RealtimeHub.publish()` also awaited each client's `send_json` directly and sequentially, so one slow client blocked delivery to every other client and, combined with the ordering bug, blocked telemetry ingestion itself. Fixed by reordering publish to run only after a successful commit and only when `current_changed=True`, and by replacing the client set with a bounded, coalescing per-client buffer and dedicated writer tasks. Two follow-up commits hardened this further: `fcbeaa1` guards against a writer task cancelling itself; `3165752` fixes a teardown race where the overflow path closed a client's socket without waiting for its writer task's cancellation to complete — found during a dedicated integration review, not by the original test suite.
- **PR4 (`2a3776a`)** — the dashboard fetched the current-state snapshot once at page load and never again, so a reconnecting client kept showing pre-outage data indefinitely. Fixed by fetching the snapshot again on every successful WebSocket `open`, guarded by a `(generation, sequence)` comparison at merge time so a slower, now-stale in-flight snapshot response cannot overwrite a value already advanced by a WebSocket message.

## Design choices and trade-offs

- **`migration_002` vs. editing `migration_001` in place**: chose an additive migration. `schema_migrations` is version-gated, not content-hash-gated — editing a migration already recorded as applied means it silently never re-runs anywhere it already ran.
- **Bounded, coalescing WebSocket buffer vs. plain FIFO queue**: chose a `dict[(deviceId, metric), message]` buffer (capacity 256) over a plain bounded FIFO. `docs/runtime-contract.md` states the dashboard needs current state, not every raw event, so coalescing avoids wasting a client's limited buffer capacity on messages that would be superseded before ever being sent.
- **Overflow policy**: drop only the offending client rather than attempt partial eviction of other devices' pending state. This is non-lossy specifically because PR4 makes every reconnect re-fetch an authoritative snapshot — the two fixes are deliberately complementary.
- **PR3 commit/PR structure**: the transaction-boundary reorder and the WebSocket backpressure fix were merged as a single unit (two commits) rather than two separate PRs, so `master` would never pass through a state where the reorder alone (still blocking on a slow client) was live.
- **Frontend ordering guard scope (PR4)**: applied the `(generation, sequence)` comparison only at snapshot-merge time, not to every incoming WebSocket message. General client-side message-reordering defense was deliberately out of scope — PR2 and PR3's server-side guarantees already mean the server never publishes an out-of-order message for a given key.

## Schema or API compatibility concerns

- `migration_002` rebuilds `telemetry_events` (SQLite cannot `ALTER` a `UNIQUE` constraint) but is additive and non-destructive; verified by applying both migrations against a real on-disk SQLite file and inspecting the resulting schema and `schema_migrations` rows directly, not just `:memory:` test runs.
- No HTTP response shape changed anywhere across all four PRs — `IngestResult.to_api()`, `BootRegistrationResult.to_api()`, and `DeviceState.to_api()` are byte-for-byte unchanged from `master`. `api.py` and `models.py` are untouched by any of the four PRs.
- PR1 and PR2 both append new tests to `tests/test_database.py` at the same point in the file. This produces a real, but purely textual and additive, git merge conflict when merging both into `master` — confirmed by actually performing the merge in an isolated scratch clone. Resolution is to keep both blocks of new tests; no logic needs reconciling.

## Remaining risks or incomplete work

- PR3's overflow-drop behavior only becomes fully non-lossy once PR4 is merged; if PR3 merges well before PR4, a dropped slow client will reconnect but show stale data until it refreshes or PR4 lands.
- No JavaScript test framework exists in this repository, and none was added. PR4's frontend logic was verified via a throwaway, non-committed Node script exercising the merge/comparator logic against 5 scenarios, a `node --check` syntax pass, and a live check against the running server — not an actual browser-driven reconnect walkthrough.
- No automated test pins the invariant that an event which loses the `(generation, sequence)` ordering race still lands in the raw audit table (`docs/protocol.md`'s "an event from an older boot remains in raw history"). Verified by hand, twice, against the actual database, but not asserted by any committed test.
- No hub-wide shutdown hook closes lingering WebSocket connections when the server process shuts down; cleanup relies on uvicorn's own connection teardown. Pre-existing, not introduced by these PRs, and judged out of scope for the time-box.
- PR4's device-removal handling (a device present locally but absent from a fresh snapshot is dropped) is correct but currently unreachable, since nothing in the backend ever deletes a `current_state` row.
