# AI usage record

Complete this file even when no AI tool was used.

## Tools used

- Claude Code (Sonnet 5), used interactively for the entire assignment: repository investigation, requirement analysis, design discussion, implementation, code review, testing, and git/GitHub operations (branching, committing, pushing, PR creation). All actions were reviewed and explicitly approved at each step; nothing was committed or pushed without a specific request to do so.

## Important prompts or prompt summaries

- An initial six-phase request to independently verify the repository state, read the protocol/runtime-contract docs, analyze all six problem areas from first principles (requirement, current behavior, why it's wrong, failure scenario, smallest fix, tests, risks), challenge that analysis for alternative designs, and produce an implementation plan — before making any changes.
- For each of PR1–PR4: a request for a plan (exact bug, violated invariant, files, proposed fix, tests) and explicit approval of that plan before implementation began.
- Two dedicated design-challenge requests before implementation: whether to fix the wrong `UNIQUE` constraint by editing `migration_001` directly or adding `migration_002`, with an explicit request to challenge the initial preference; and a detailed design request for the bounded WebSocket queue — contents, capacity, coalescing, overflow/disconnect behavior, writer-task lifecycle, cleanup, concurrency safety, and tests.
- After each implementation: a request to run the relevant tests and the full check script, review the complete diff for accidental changes, and report results and risks before committing.
- A request to revisit PR ordering specifically to avoid a known-unsafe intermediate state on `master`, resulting in PR3's transaction-boundary and backpressure fixes being merged as one unit.
- Multiple requests for a full, read-only integration review — merging PR1+PR2+PR3 (and later +PR4) together in a disposable scratch clone that never touched the real repository's branches or refs — to catch issues invisible to per-PR review, with findings classified BLOCKER/SHOULD FIX/ACCEPTABLE.
- A request to fix one SHOULD FIX finding from that integration review as a new, separate follow-up commit rather than amending existing history.
- Explicit, narrow authorization for each git-affecting action (branch, commit, push, PR) with instructions never to push `master`, merge, rebase, or amend without being told to.

## Generated output rejected or corrected

- **A concurrency gap found only via deeper review**: the original `RealtimeHub._disconnect_and_close()` cancelled a client's writer task and immediately closed its WebSocket without waiting for that cancellation to complete. All tests passed against this version — the gap (a possible concurrent send/close on the same connection) was only surfaced by an explicitly-requested integration review comparing the code against documented invariants, not by test results. Fixed in a separate commit (`3165752`) that awaits the cancelled task, suppressing `CancelledError`, before closing the socket.
- **An assumption verified wrong by actually testing it**: initial reasoning (based on disjoint diff line ranges) suggested PR1 and PR2 would merge cleanly. Performing the actual merge in a scratch clone showed a real, if purely textual, conflict in `tests/test_database.py`, since both PRs append tests at the same point in the file. Documented as a known conflict rather than left as an untested assumption.
- **A workflow correction, not a content defect**: during PR3 implementation, a proposed defensive guard in `RealtimeHub._remove()` was rejected by an accidental tool denial, clarified as accidental in a later turn, and then applied as originally intended and re-verified.

## Verification performed

- Every PR: full local test suite (`./scripts/check.sh`) run before commit; complete diff reviewed for accidental or out-of-scope changes before every commit.
- PR1: migrations additionally applied against a real on-disk SQLite file (not only `:memory:`), with the resulting schema and `schema_migrations` rows inspected directly.
- PR2: all four `(generation, sequence)` ordering branches plus both directions of `deviceTime` manipulation covered by dedicated tests.
- PR3: the new async/realtime tests repeated five times to rule out flakiness given their timing-sensitive nature; the asyncio suspension-point reasoning behind each test's determinism traced by hand.
- Cross-PR: full read-only integration verification by merging PR1+PR2+PR3 (and later PR4) together in an isolated scratch git clone that never touched the real repository — resolving the known test conflict, running the full merged suite, and reading the merged source files against `docs/protocol.md` and `docs/runtime-contract.md` directly.
- PR4: `node --check` for syntax validity; a throwaway, non-committed Node script exercising the merge/comparator logic against five scenarios; the real server started locally and checked via `curl` to confirm the served file and the `/api/devices` response shape matched what the frontend consumes.
- Before every push or PR creation: branch tips, working-tree cleanliness, and (post-push) remote ref state verified via `git rev-parse` / `git ls-remote`, with `master` and sibling branches explicitly re-confirmed unchanged each time.
