# Read-only save evidence diagnostic

This manual diagnostic supports review of the unconfirmed save-on attempt in run
39681856158 (job 119506059753), reported at 2026-10-21 21:49:40 UTC. It never
repeats that command. Adding or merging this workflow does not execute it.
Execution and any subsequent deployment require separate review.

The only trigger is `workflow_dispatch`, with no inputs. Both the job and script
require `refs/heads/main` and that event. The token has only `contents: read`.
The workflow checks out Schematic at
`f27f43679e34a4975e8e1b18cdf6528877c6e66d`; the script additionally verifies the
SHA-256 of `scripts/server_deploy.py` before importing its `sftp_session` helper.
That helper uses the existing SFTP mappings, explicit known hosts, RejectPolicy,
no SSH agent and no discovered keys. Only the diagnostic step receives those
existing SFTP secrets. No RCON credentials are mapped.

The script checks directory/file types, opens only `<SERVER_SFTP_PATH>/logs/latest.log`
with `rb`, and performs one read of at most the final 1,048,576 bytes. It does not
prefetch, retry, list directories, read another file, write remote data, rename,
delete, restart, deploy, or invoke RCON. The configured server root is the existing
mapping, not a dispatch input. Remote metadata and content are never printed or
uploaded as artifacts. Transport logging and exception details are suppressed.

Only exact, allowlisted server-thread INFO save messages are classified, optionally
with the exact `[Rcon: ...]` wrapper. Chat, player-source wrappers, extra message
text, other loggers, malformed bytes, and partial lines are ignored. Output is
limited to fixed categories, canonical UTC timestamps and one fixed result.
Duplicate events are collapsed.

## Date/time and interpretation limits

The inclusive target window is **2026-10-21 21:49:30–21:49:50 UTC**. Accepted
stamps explicitly contain that date and either `Z` or `+00:00`, at whole-second
precision. Ordinary Minecraft `HH:MM:SS` stamps cannot establish a date or timezone.
Relevant clock-only candidates in the clock window produce
`save-evidence: unconfirmed-date-time` and a failing exit code; their timestamps
and messages are not emitted. The runner date, file mtime, and the known workflow
attempt time are deliberately not used to invent the missing date/time evidence.
An unfamiliar format, missing window, rotated/truncated log, or disabled/conflicting
save evidence cannot produce success. This diagnostic may therefore remain
inconclusive on the actual server's default logging format.

Exit zero means only that the bounded sample contains a recognized enabled/already
on message with an explicit UTC stamp and no recognized contradictory or clock-only
candidate. It does **not** authenticate log contents, attribute the entry to this
particular RCON connection, establish current saving state, prove complete history,
or authorize deployment. A server can forge log lines. SFTP lacks an atomic
no-follow open; type checks and before/after size/mtime checks detect some but not
all concurrent replacement or same-size rewrites. Review the fixed evidence and
these limitations before making any further decision.

## Trigger and downstream review

Only this new workflow, script, its offline tests and this document change. Existing
workflows and pack/release versions remain unchanged. Opening the Draft PR triggers
existing PR CI, which does not execute this diagnostic. Existing CI does not collect
this new test file: run it locally and the manual diagnostic runs it before opening
SFTP. No additional automatic test trigger is introduced.

The changed paths match neither Release's pack-path push filters nor Server tuning's
push filters. Server update listens only to Release completion; Server tuning
listens only to Server update completion. Neither subscribes to this diagnostic's
name. The new concurrency group is separate so a diagnostic cannot replace a
pending operation in the existing SFTP concurrency group. It is not a server lock;
concurrent live activity is another reason this sample cannot establish current state.

Offline verification:

```sh
python3 -m unittest discover -s tests -p 'test_inspect_save_evidence.py' -v
```

No live server access is needed for these tests. Merge, dispatch, and deployment
remain separate decisions; do not use the old `diag-server-logs` branch.
