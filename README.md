<div align="center">

# ◈ TokenScope

**English** | [简体中文](README.zh-CN.md)

### Your models. Your machines. One clear view.

A lightweight, local-first **CC-Switch usage & cost dashboard**<br>
for Codex, Claude Code, and multi-machine workflows.

**Python-powered · No frontend build · MIT licensed**

[Explore the animated demo](https://sleepaviator.github.io/tokenscope/) · [Quick start](#quick-start) · [How counting works](#accounting-and-limitations)

</div>

[![TokenScope dashboard showing synthetic daily token usage, estimated costs, and monthly leading models](assets/demo-preview.png)](https://sleepaviator.github.io/tokenscope/)

*Entirely synthetic demo data. Click the snapshot to explore the animated, interactive dashboard.*

---

## From scattered usage to a single picture

See which models account for your tokens, how recorded costs change day by day,
and which model leads each month—without opening a statistics page on every machine.

| See it | Do it |
| :--- | :--- |
| **Adaptive tokens + cost** | Compare model-colored token bars with an estimated-USD line. One day uses hourly slots; short ranges use 1–12-hour slots sized to the chart width, and longer ranges use daily bars. The session heatmap follows the same slots. |
| **Period-aware model leaders** | Read the top model, token share, and its cost for a day, seven-day week, full month, or custom range directly on the chart. |
| **Focused exploration** | Narrow the date range and select one or several model names. |
| **Session heatmap** | Explore session titles by date with adaptive jet colors for token usage. |
| **Session drill-down** | Click a title for token components, requests, cost, and date/model breakdowns. Shared filters apply throughout. |
| **Projects across machines** | Merge matching project paths, including cloud-relative folders, and drill down into their models and conversations. |
| **Click-to-sort tables** | Click any table column heading to sort; click again to reverse. Numeric values sort numerically, including the full session list before pagination. |
| **Response speed** | Compare per-response TPS and measured first-token latency over the last 7 days, last 30 days, or all history. Explore daily calendar tiles, weekday/hour patterns, and model statistics with timing coverage counts. |
| **macOS menu bar TPS** | Read combined output TPS and the average per contributing session across configured machines and providers, with explicit coverage gaps. |
| **One or many machines** | Collect locally or read remote statistics through your own private SSH configuration. |
| **Desktop packaging** | Build standalone macOS, Windows, and Linux bundles. Existing GitHub downloads may lag the source; this update does not publish a new app. |
| **Refresh on your terms** | Choose 5 seconds to 10 minutes in the browser, aligned to the clock. |
| **Portable reports** | Download a chart PNG with its model-color legend, or generate PNG/SVG charts, CSV tables, and JSON/Markdown summaries. |
| **Optional private history** | Retain 28 days of numeric minute history and lifetime daily totals locally; source databases remain read-only. |

The live webpage needs **only Python's standard library**. No Node, database server,
cloud account, or permanently running service. Start one command; stop with Ctrl+C.

## Try it without connecting anything

Use **语言 / Language** at the top of the webpage to switch between English and
Simplified Chinese. Your browser remembers the selection; date/model filters stay
unchanged. Share a Chinese view with `?lang=zh-CN` or an English view with `?lang=en`.
Model names, source names, conversation titles and recorded USD amounts are preserved.

The [interactive demo](https://sleepaviator.github.io/tokenscope/) uses **entirely synthetic data**: 90 days,
three fictional models, and two fictional sources. It has animated chart reveals,
a replay button, working date/model filters, and reduced-motion support. It never
reads your configuration, databases, session files, or SSH settings.

```sh
python -m http.server 8877 --bind 127.0.0.1 --directory docs
```

Open `http://localhost:8877`. To regenerate the demo, run `python build_demo.py`.
Its deterministic generator reads only the dashboard's source assets—not real usage.
The animated demo is an HTML page; GitHub's README itself does not execute JavaScript.

## Quick start

### Desktop apps

**This is a source and demo update, not a new desktop release.** Existing downloads
at [GitHub Releases](https://github.com/SleepAviator/tokenscope/releases) are older
and do not contain all features described here. Use the Python quick start below
for the latest dashboard. Packaging scripts also support Windows and Linux;
available downloads depend on the release.

- **macOS:** choose the **macOS-arm64** archive for Apple silicon or
  **macOS-x86_64** for Intel. Unzip and move `TokenScope.app` to Applications.
  Open it to start the dashboard and menu bar meter. Closing its launcher window
  keeps the meter running; choose **Stop and quit** to stop it. **Machine settings…** edits the private config,
  stored under `~/Library/Application Support/TokenScope/`. Dashboard snapshots and
  the launcher's bounded diagnostic log stay in RAM.
- **Windows x64 bundles, when available:** unzip and double-click `launch-tokenscope.bat`. It keeps a console
  open for collection status; press Ctrl+C there to stop. Settings are stored in
  `%APPDATA%\TokenScope\config.ini`; dashboard snapshots stay in RAM.
- **Linux x64 bundles, when available:** extract the `.tar.gz` and run `./launch-tokenscope.sh` in a terminal.
  First enter the extracted `TokenScope-Linux-x86_64` folder. Press Ctrl+C to stop.
  Config uses the XDG config directory, defaulting to `~/.config/tokenscope/`;
  dashboard snapshots stay in RAM. The binary is built on Ubuntu
  22.04 and requires a compatible glibc.

Desktop downloads are not Developer ID signed/notarized; macOS Gatekeeper may block
them, and Windows SmartScreen may warn about unsigned executables. This source-only
update does not resolve or re-test older downloads. Use the Python source when a
download is blocked; do not disable system security protections. GitHub Actions
builds for this public repo do not require a paid GitHub plan.

The app listens on the LAN by default, like `python app.py`: it has no login or TLS.
Use it only on a trusted network; other LAN devices can view the dashboard and change
its shared refresh interval.

### Menu bar TPS meter

The macOS launcher displays two compact rows, **`120 t/s`** above **`40 t/s`**:
total output TPS across contributing sessions on top, and the average (total divided
by their count) below. The narrow layout fits crowded menu bars. It refreshes every
second independently of the dashboard's historical collection interval. The dropdown
shows sessions, machine/provider readings, freshness and incomplete or pending coverage,
plus **Open dashboard**, **Machine settings…**, **Show launcher** and **Stop and quit**.
Use **Show menu bar TPS meter** in the launcher to show or hide it. Your choice is
remembered; reopening TokenScope from the Dock brings the launcher back.
No login service or extra dashboard panel is installed.

One-second readings stay in RAM. The macOS launcher also retains 28 days of numeric
minute aggregates in `~/Library/Application Support/TokenScope/meter-history`,
saved every 15 minutes and at clean shutdown. Historical token totals count input
and output, including cached input once; rate history is the mean of observed TPS
readings. Missing coverage remains explicit. These small JSONL journals contain no
message content, session identifiers, models or source paths.
For ten-minute rate history, up to 600 one-second samples remain in RAM; older
ranges use the saved minute aggregates. Recent history also keeps one hour of
five-second rate integrals and timestamped usage counts in RAM. The history API
uses 120 time buckets, including 30-second buckets for one hour. Older minute-only
values are explicitly marked as coarse. Short-term sampling adds no disk writes.

The launcher separately saves lifetime **daily usage totals** in
`~/Library/Application Support/TokenScope/daily-usage`, once after initial collection,
every 24 hours, and on clean quit. Each private JSON file contains one usage date's
machine/app/model token and request totals, recorded cost, and source collection time.
There is no database, prompt, session identity or raw source path in these logs.
They restore roughly correct lifetime totals when a machine is offline after a restart.
The dashboard marks archived coverage and its age; missing hourly/session detail
remains unavailable. Reconnection replaces covered dates, including deduplicated-away
counts, rather than adding the archive to fresh totals. Entire older dates no longer
present in the source remain archived. Copied records in an offline daily archive
cannot be rechecked until the source reconnects. A crash can lose changes since the
last checkpoint; this is not a backup of raw usage records.

The meter passively follows Codex and Claude native logs and recent timed CC-Switch
requests on every configured local/SSH source, without filtering service providers.
Readers advance incrementally; remote probes run read-only over persistent SSH
connections without installing a remote agent. Keep the usual source paths and SSH
settings in your private configuration. Applications without observable token usage
and timing remain explicitly uncovered.
`codex_native_tps = false` also disables live Codex timing on that source and marks
its coverage incomplete; the browser's estimate checkbox controls historical display only.
For live Codex logs, `codex_home` takes precedence over that source process's
`CODEX_HOME` environment variable, then defaults to `~/.codex`. Without explicit
`codex_session_roots`, the live reader uses `sessions` and `archived_sessions`
under that home. Explicit semicolon-separated roots override this derivation.
Historical collection keeps its existing log-path defaults; set `codex_session_roots`
as well when historical logs live elsewhere.

An optional **Codex side-chat telemetry pilot** receives live HTTP JSON logs at
`127.0.0.1:4319/v1/logs` on the source machine. Set `codex_otel_port = 4319` in that
machine's private TokenScope `[source:...]` section; omitted or `0` disables it.
The receiver starts and stops with that source's live worker. It discards prompt and
tool bodies, keeps counters, timing and session identities in RAM, and sends only the
usual numeric snapshots over SSH. It creates no database, cache or export files.
In that machine's private Codex configuration, merge these settings into `[otel]`:

```toml
[otel]
exporter = { otlp-http = { endpoint = "http://127.0.0.1:4319/v1/logs", protocol = "json" } }
log_user_prompt = false
```

Restart Codex when convenient for these exporter settings to take effect; TokenScope
does not restart it automatically. Finish or save temporary side chats first, because
restarting may lose them. Leave the trace exporter unchanged and do not enable
traces for this pilot. Provider routing stays unchanged, and no login service is added.
See the [Codex telemetry configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

An ephemeral fork was verified with Codex **0.160.1**. For this version, a session's
`codex.websocket_request` supplies the request start: its source `event.timestamp`
minus the recorded `duration_ms`. A matching `response.completed` supplies the end
and generated `output_token_count`; reasoning already included in that total is
counted once. Missing session identities or usable response intervals remain explicit
coverage gaps. Other Codex versions or transports may omit the required boundaries.
Exporter batching can delay readings; output arriving after the five-second window is
excluded with a coverage warning.

Each response's output is estimated uniformly over its recorded generation interval.
The part overlapping the trailing **five seconds**, divided by five, contributes to
its session's TPS. Sessions contributing to that same window form the average's
denominator; idle sessions do not. Input/cache tokens are excluded, and reasoning
tokens already included in output are counted once. Native boundaries exclude known
tool execution and idle gaps, but scheduling and first-token delay can remain.
Usage reports can arrive after generation, so these **≈** estimates can lag or miss a
response that is already outside the window; they are not instantaneous server timings.
During Codex thinking, native logs may contain no new output count until a model
response finishes. `— t/s` then means usage is awaiting a report, not zero speed.
Reported reasoning tokens enter the output rate once that usage becomes available.

Pending usage, missing timing and disconnected sources are coverage gaps, rather than
zero-speed samples. Codex desktop activity without readable native token usage also
marks coverage incomplete: the meter cannot count or average that output. The detected
gap remains until readable native activity appears or the monitor restarts.
Disconnected sources expire after three missed one-second
heartbeats. Unattributed output can contribute to the total, but makes the session
average unavailable. Startup and reconnect do not replay retained historical usage.
The live meter never substitutes the dashboard's cached data.
CC-Switch can create a new ID for each request when a client supplies no session ID;
these records require reliable native identity evidence before entering the session average.

For a source checkout or Windows/Linux server, enable the same collection/API with:

```sh
python app.py --live-meter --config config.ini
```

`GET /api/live` is available only through a loopback connection, even while the
dashboard is reachable over the LAN/tailnet. Without `--live-meter` it returns a
disabled snapshot. The native menu UI is macOS-only. Its compact presentation is
adapted from [Token Meter](https://github.com/splunk/token-meter), with MIT attribution
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The live snapshot's `received_output` contains up to 60 completed one-second RAM
buckets of newly reported output tokens. Repeated usage reports and copied logs count
once; cumulative response updates add only their increase. This is a receipt-time
count, separate from the five-second TPS estimate. Batched reports can produce spikes,
and unobserved network-stream chunks cannot be reconstructed.
For history outside the macOS launcher, pass `--meter-history /private/history-folder`.
`GET /api/live/history?range=3600` returns bounded numeric history through loopback;
supported ranges run from 600 seconds (10 minutes) through 2419200 seconds (28 days).
The ten-minute response also supplies `rate_buckets` with 600 one-second positions;
missing RAM readings are null gaps. Used-token `buckets` retain their recorded minute
resolution outside the recent RAM window; recent counts use their recorded request
timestamps. Each range supplies 120 `buckets`; `coarse_rate` and `coarse_tokens`
identify older minute-only values. Counts without finer timestamps are kept together
and are never divided into invented token arrivals. For daily accounting persistence outside the macOS launcher, pass
`--daily-archive /private/daily-usage-folder`; source readers stay read-only.

Codex native-log TPS estimates are **enabled by default**. Uncheck **Include Codex
native-log TPS estimates** in the per-session section to hide them (saved per browser).
To stop scanning native logs on a machine, set `codex_native_tps = false` in that
`[source:...]` section of your private INI configuration, then restart and refresh.
Only unique exact session ID, usage-event second and input, cached-input and output
token counts are joined. No nearest-timestamp guessing is used.
The estimated interval runs from logged user/tool input to generated output, excluding
completed tool execution and gaps between turns; client scheduling/TTFT can remain.
It is not a server stopwatch measurement or instantaneous decoding speed.
Missing/ambiguous boundaries remain unavailable. Rates containing estimates have an
**≈** prefix. Tokens, costs and request counts never change with this switch.
Older snapshots need a refresh. Native estimates remain separate in CSV/JSON
(`native_tps_count/sum/max`); diagnostic `request_tps.csv` retains recorded timings only.

Per-session tables and drill-downs include average and maximum response TPS and timing
coverage. Each recorded rate is output tokens divided by recorded response seconds
(`duration_ms`, otherwise `latency_ms`), including time to first token. Average TPS is
the arithmetic mean of successful timed response rates; maximum is the fastest
response average, not instantaneous streaming speed. Session idle gaps and input/cache
tokens are excluded. Untimed, failed, and zero-output responses are excluded from TPS,
not token accounting. Missing timing displays as unavailable, and date/model filters apply.

The **Per-response speed analysis** panel compares the last 7 days, last 30 days,
and all history, intersected with the global date/model filters. Windows include
today in dashboard-host time. It shows response-level mean, median and maximum
TPS; mean, median and P95 first-token latency; and sample counts for both metrics.
Percentiles use linear interpolation between sorted individual measurements.
First-token latency comes only from positive recorded `first_token_ms` values,
never from total response duration. Daily calendar tiles and weekday/hour tiles
can show either metric; gray means unavailable. The native-log estimate switch
affects TPS only, not measured first-token latency.

Requires Python 3.9+ and an existing CC-Switch database.

```sh
git clone https://github.com/SleepAviator/tokenscope.git
cd tokenscope
# From the project folder (Windows: use copy instead of cp)
cp config.example.ini config.ini
python app.py
```

Open `http://localhost:8765`, or use the computer's LAN IP and port from another
device. Tailscale devices can use `http://YOUR_TAILSCALE_IPV4:8765` (the host's
`100.x.x.x` address), provided the tailnet's access policy allows TCP port 8765.
No public port forwarding or Tailscale Funnel is needed. Ctrl+C stops the foreground server and its collection workers. No permanent
service is installed. The web dashboard uses only Python's standard library.

```sh
python app.py --host 127.0.0.1 --port 8765 --interval 300 --config config.ini
```

Default binding is `0.0.0.0`. **Trusted LAN or tailnet only: no app authentication or TLS.** Permitted
visitors can view usage and change the shared refresh interval. Do not expose it
to the Internet. Use `--host 127.0.0.1` for local-only access.

The server refreshes immediately and then on local clock boundaries. The webpage
accepts intervals from 5 to 600 seconds; 300 means :00, :05, :10, etc. Busy refreshes
skip boundaries rather than overlap. Collection continues until Ctrl+C even if the
browser closes. Filters are local to each browser. Leaders and their costs use the
selected dates and models, including ties. Single-day and seven-day selections use
day and week totals; full calendar months use month totals. Other explicit ranges
use one selected-range total, even across month boundaries. With no date bounds,
the dashboard shows one leader per month.

The dashboard holds its display data and one last-successful statistics snapshot per
configured machine in RAM. Refreshes create no temporary CSV exports or dashboard
cache files, and the macOS launcher retains only the latest 64 KiB of server diagnostics
in memory. If a machine
is disconnected or collection fails, other machines still refresh. An alert marks
the retained data as stale and source details show its original collection time.
Machines with no successful snapshot are marked unavailable and totals incomplete.
Reconnection replaces that machine's snapshot and clears its warning. Snapshots
remain private for the current run. With the launcher's daily archive enabled, restart
restores daily totals while collecting fresh data; without it, offline machines remain
unavailable until they reconnect. Unexpected whole-refresh
failures preserve the previous in-memory result. Existing cache files and exports are
left intact. `--cache PATH` can explicitly load an existing snapshot at startup;
it is read-only and never rewritten. The collector reads existing CC-Switch databases
and native logs; TokenScope creates no database and does not modify these sources.
Manual `update.py` exports remain available when requested. Restart
after editing configuration. Each refresh reads all retained CC-Switch statistics;
it is not an incremental ingestion process. Sync CC-Switch first for fresh imports.

## Per-project usage

**Show per-project usage** reveals a token ranking and exact totals for each saved working folder: input/cache/output tokens, recorded estimated cost, requests, distinct sessions, and average/maximum response TPS. Click a project for date, model, and conversation breakdowns; click a conversation to open its existing session detail.

The date/model filters and native-log TPS preference apply to both views. Folder identities are hashed before export; only the folder name is displayed. Projects with the same name and path merge across machines and apps, with all contributing machines listed. Recognized Dropbox, OneDrive, Google Drive, iCloud Drive, and Box roots use the path relative to that cloud service, so different account or machine root paths are acceptable. Different relative folders or cloud services stay separate; names alone never trigger a merge. Cloud-relative folder/name spelling must agree, including case. Existing cached keys need a successful refresh; disconnected machines retain their older identities until they reconnect. Exact session/message-ID matches must agree on a folder; missing or conflicting metadata and historical rollups remain unassigned, with coverage shown explicitly. Conversations remain distinct by machine, application, and session identity.

## Per-session usage

Check **Show per-session usage** in the filter bar to reveal the heatmap and table; uncheck it
to hide them without changing your data selection. Every table column heading is
clickable: click to sort, then click again to reverse. The session table sorts the
full matching list before showing 50 sessions at a time. The existing quick-sort
selector remains available.

Each row groups a recorded session ID within one machine and application. Usage is
first filtered by the shared date range and model selection, then summed per session.
A session spanning several days therefore shows **only its selected-day usage**,
not lifetime usage. Model switches remain in the same session, with selected models
listed together. Fresh input, cache read/write, output, total tokens, requests and
estimated USD are shown separately. Activity dates are first/last *selected* days.

Click a conversation title in either the table or heatmap to open its token components,
requests, recorded cost, and breakdowns by date and model. The heatmap includes all
matching sessions, uses an adaptive linear jet scale over observed session-day totals,
and leaves missing records blank. Hover or focus a cell for exact values.

Saved Codex names and Claude Code custom titles are read locally; missing names show
**Title unavailable**, without generating summaries or substituting message text.
Claude title lookup supports inline events, per-session `custom-title.json`, and
Claude / Claude-3p desktop metadata. When CC-Switch assigns request-scoped session
IDs, the collector joins `request_id = session:<message.id>` to the Claude log's
conversation ID. An exact match, or multiple exact candidates that all have the same
saved title, can supply a title. Conflicting or missing titles remain unresolved.
The original usage session identity is unchanged. No timestamp guessing is used, and token/cost accounting is
preserved. Message bodies are not exported; only IDs and saved titles are retained.
Optional `codex_home` and `claude_projects` source settings select metadata locations.
`codex_home` also determines the live Codex log defaults described above.
Codex subagents are labeled **Subagent of: [parent task title]** using saved parent
metadata. Missing parent titles are explicitly marked unavailable. Sibling agents
retain distinct session identities even though their displayed titles match.
Session IDs are SHA-256 hashed before export and identity includes machine and app,
so identical titles do not merge. Hashes are pseudonymous, not anonymous.
**Saved titles can be sensitive:** they appear in your local output and are visible
to LAN viewers. Never publish your generated output. The public demo uses fictional
titles and deterministic synthetic statistics only.
Some sources use request-scoped session IDs. Requests without usable IDs and
historical rollups cannot be assigned to sessions; their tokens remain in the daily
charts and are reported as lacking session detail. Imported older snapshots require
one successful refresh with the updated collector to populate new fields. Static
exports also include `session_daily_usage.csv`.

## Optional remote sources

Add source sections to your private `config.ini`. Each needs a unique display name,
`transport = ssh`, `host` (your SSH destination), `python` (remote interpreter path),
and `database` (remote database path). OpenSSH with key/agent authentication and
remote Python with SQLite support are required. No remote connection names or
addresses are shipped. The collector executes a read-only reader in memory, without
installing software or copying the database. Keep all connection details private.

## Static charts and summaries

```sh
python -m pip install -r requirements.txt
python update.py
python update.py --hosts local
python -m unittest discover -s . -p 'test_*.py'
```

Matplotlib is required only for static charts. Outputs overwrite fixed filenames
under `output/local/` for one source or `output/overall/` for multiple sources:
token/cost PNG and SVG charts, a machines chart, daily and hourly CSVs, diagnostic request TPS
CSV, and JSON/Markdown summaries. TPS is not plotted. All configured sources must
succeed; failures are reported rather than presenting partial results as complete.

## Accounting and limitations

- Total tokens = fresh input + cache read + cache creation + output. Cache handling
  honors CC-Switch's input-token semantics; reasoning tokens are not independently
  available in this schema and are not added as an estimate.
- Costs are recorded CC-Switch USD estimates, not invoices or subscription fees.
  Zero recorded cost can mean missing pricing rather than free usage.
- Timestamped requests from all machines use the dashboard host's local date and hour.
  Historical date-only rollups keep their source-local date and appear under
  **Unknown hour** when one day is selected; they cannot be rebucketed precisely.
- Matching imported session/proxy rows are deduplicated using app, model, token
  counts and a 600-second window. Identical cross-host request IDs count once;
  conflicting values fail. Rollups lack original IDs and may overlap across hosts.
- Provider metadata can be joined from the first Codex session header. Session
  text is not exported. Missing metadata remains explicitly unknown.
- The public version preserves source model names and ships no private model
  alias mappings or per-request corrections.
- Claude Desktop gateway rows are grouped with Claude Code; this is not coverage
  of every Desktop conversation. Source data quality limits reporting accuracy.
- Diagnostic TPS uses output tokens / recorded request duration, with latency
  fallback. Requests without positive timing and output have no TPS sample.

## Privacy and publishing

Only source files, tests, this README, requirements, the example configuration and
`.gitignore` belong in Git. Never publish real configuration, databases, generated
outputs, screenshots of private usage, SSH files, credentials, or session logs.
The launcher's private `meter-history/` journals and `daily-usage/` archives also
stay outside Git; they are not part of the public demo or downloads.
The database reader selects usage fields and provider display names, not provider
settings, credentials or prompts. The title reader also reads saved conversation
names. Aggregates reveal usage, source display names and saved titles to dashboard
viewers; they are not anonymous.

`.gitignore` is a safeguard, not a secret scanner: review staged files before every
push. Publish this folder as a fresh repository, not a parent project or private Git
history. `docs/` contains only the synthetic demo and its public assets.

## Project map

```text
app.py            Foreground web server and refresh orchestration
collect.py        Read-only CC-Switch statistics reader
update.py         Accounting, summaries, and static chart generation
meter_history.py  Optional private minute history and recent RAM buckets
daily_archive.py  Optional private lifetime daily totals
web.*             Dashboard interface, shared with the demo
config.example.ini  Public local-only configuration template
build_demo.py     Deterministic fictional-data demo builder
docs/             Static demo, ready for GitHub Pages
test_*.py         Accounting, HTTP-control, and demo tests
```

## License & attribution

[MIT](LICENSE) · Copyright © 2026 Crear12.

An independent companion to [CC-Switch](https://github.com/farion1231/cc-switch),
not an official billing tool or an affiliated product. Collection depends on the
supported CC-Switch SQLite schema; schema changes may require an update.
