# 04 — Formatters, parsers and configuration

A formatter has one method, `format(record) → string`. Transports default to the JSON
formatter. The formatter is chosen per transport.

## JSON line

Compact: no spaces after `,` or `:`. Keys in record order. P: `json.dumps(record,
separators=(",", ":"), default=str)` — note `ensure_ascii` is on, so non-ASCII is
written as `\uXXXX`; an optional `orjson` backend writes UTF-8 and the same compact
shape. J: `JSON.stringify(record)` (UTF-8, `NaN` → `null`, `undefined` dropped). Values
that are not JSON become their string form in P. Equality between languages is by parsed
value, not text (D4). New code: UTF-8 output, non-finite numbers as `null` (decisions
D31, D32 below), integral floats keep `.0`.

## Text line

```
<timestamp> <LEVEL padded to 5> <logger>: <message>[ llm=<json>][ <json of meta>]
<stack, trailing newlines removed>      (only when meta.stack is a non-empty string)
```

- Missing fields render as `?` (timestamp, level, logger); a missing message renders
  empty. This lets the CLI format lines written by other tools.
- `meta.stack` is removed from the printed meta and printed on its own lines after.
- ` llm=` appears only when the `llm` block is non-empty; ` {meta}` only when meta (after
  removing `stack`) is non-empty. Both are compact JSON.
- If serialising meta fails (cycle, bigint), print instead an object mapping each key to
  the value's string form cut to 200 characters (`<unrepresentable <type>>` if even that
  fails).
- Level padding is left-justified: `INFO ` `WARN ` `DEBUG` `TRACE` `ERROR` `FATAL`.

## Logfmt line

`timestamp=… level=… logger=… message=…` then, if `llm` is non-empty, its fields as
`llm.<key>=…`, then each `meta` key. Pairs are joined by a single space; the output is
always one line.

- **Quoting.** A value is quoted when it is empty or contains whitespace, `"`, `=`, `\`
  or any of `U+0000–U+001F`, `U+007F`, `U+0085`, `U+2028`, `U+2029`. Inside quotes: `"`→`\"`,
  `\`→`\\`, newline→`\n`, CR→`\r`, tab→`\t`, any other listed control character→`\uXXXX`
  (lower-case hex, 4 digits).
- **Keys.** Each of those same characters in a key is replaced by `_`; an empty result
  becomes `_`. A `meta` key equal to `timestamp`, `level`, `logger` or `message` is written
  as `meta.<key>`.
- **Values.** `null` → `null`; booleans → `true`/`false`; numbers → their decimal string
  (P `str()`, J `String()`; `1.0` prints `1.0` in P and `1` in J); strings → quoted per the
  rule; arrays and empty objects → quoted compact JSON; anything else → quoted string form
  (`<unrepresentable <type>>` if that fails).
- **Nesting.** A non-empty object nested at depth < 5 is flattened to dotted keys
  (`http.status=200`, each child key sanitised); at depth 5 or deeper, or any array, it is
  emitted as one quoted JSON string. Depth counts from 1 at the top of a `meta` value.
- Never raises on odd values.

### `parse_logfmt(line) → map<string,string>`

Tokenise with `([^\s="]+)(?:=("(?:[^"\\]|\\.)*"|[^\s"]*))?` over the line. A quoted value
(starts and ends with `"`, length ≥ 2) is unescaped: `\\(u[0-9a-fA-F]{4}|.)` with the dot
matching newlines; `uXXXX` → that character; `\"`→`"`, `\\`→`\`, `\n`, `\r`, `\t`; any
other `\c` stays `\c`. A bare key maps to `""`. A repeated key keeps the last value. Values
are always strings.

## `parse(source, pattern, cast)` (regex log extraction)

Streams lines from a path or any iterable of lines; for each line (trailing `\r\n`
removed) searches the pattern and yields the named groups (`None` for an optional group
that did not take part). Lines that do not match are skipped. `cast` maps group name →
function applied to a non-null group.

Errors, raised immediately: pattern with no named group → `parse(): the pattern has no
named groups — wrap the fields you want in (?P<name>...), e.g. r'(?P<level>[A-Z]+)
(?P<message>.*)'` (J uses `(?<name>…)`); `cast` naming a group that does not exist →
`parse(): cast refers to [<names>], which are not named groups in the pattern (groups:
[<names>])`. During iteration a failing cast → `parse(): line <n>: cast for '<name>' failed
on <value> (<error>) — make the cast tolerant of this value or tighten the pattern so the
group only matches what it can convert`. A pattern cannot span lines. Memory stays flat.
The regex flavour is the host language's (this function is deliberately not in the core).

Built-in pattern for text lines (`TEXT_LOG_PATTERN`, Python syntax):

```
^(?P<timestamp>\S+) (?P<level>[A-Z]+)\s+(?P<logger>[^:\s]+): (?P<message>.*?)(?: llm=(?P<llm>\{[^{}]*\}))?(?: (?P<meta>\{.*\}))?$
```

with casts `meta` and `llm` = JSON decode (`TEXT_LOG_CASTS`).

## Configuration

Shape (JSON, or YAML when the optional library is present; the file extension decides):

```json
{
  "name": "app",
  "level": "INFO",
  "transports": [ {"type": "console", "options": {"formatter": "text"}},
                  {"type": "file", "options": {"path": "app.log"}},
                  {"class": "<path>", "options": {...}} ],
  "plugins": [ {"type": "sampling", "options": {"rate": 0.1}} ],
  "async_dispatch": true, "max_queue_size": 10000,
  "backpressure": "drop_oldest", "flush_at_exit": true
}
```

- `name` defaults to the loader's `name` argument (`"app"`); `level` defaults to `INFO`.
- An entry needs `type` (built-in) or `class` (host-language reference), else `Each
  transport/plugin entry needs a 'type' or 'class' key, got <entry>`. An unknown type:
  `Unknown type '<t>' — built-in types are: <sorted names>. Use 'class': '<module.path.ClassName>' for anything else.`
  (J: `Unknown <transport|plugin> type '<t>' — built-in types are: … Use 'class':
  '<module>#<ExportName>' for anything else.`)
- Built-in transports: `console`, `file`, `http` (P imports the HTTP class lazily); J also
  `syslog`. Built-in plugins: `context`, `redact`, `sampling`, `trace_context`, `run`,
  `pii_redact`, `tamper_evident`. (J normalises type names to camelCase and option keys to
  camelCase except the `context` plugin's own fields; `file` needs `options.path`, `http`
  needs `options.url`, `sampling` needs `options.rate`, else `'<type>' needs options.<key>
  (a string), got <v>` / `'sampling' needs options.rate (a number)`.)
- A transport's `options.formatter` may be the string `json`, `text` or `logfmt`; another
  string → `Unknown formatter '<f>' — built-in formatters are: json, logfmt, text.`
- `class`: P a dotted path `pkg.module.Class` (`'class' must be a fully-qualified dotted
  path …`; missing attribute → `<path>: module '<m>' has no attribute '<c>'`); J
  `"<module>#<ExportName>"`, a leading `.` resolved against the config file's directory.
  It constructs arbitrary code; only load trusted config.
- J ignores `async_dispatch` and `flush_at_exit`.
- Level validation errors come from the level parser.

### Loaders

- `logger_from_file(path, name="app")`: `.yaml`/`.yml` need the optional YAML library
  (`logger_from_file(...) with a .yaml/.yml file requires the optional `PyYAML`
  dependency — install with `pip install logquill[yaml]``); anything else is JSON. J also
  requires the parsed value to be an object: `<path>: config must be an object with keys
  like level, transports and plugins`.
- `logger_from_env(prefix="LOGQUILL_", name="app")`: if `<prefix>CONFIG_FILE` is set load
  it; otherwise a default logger. Then, if `<prefix>LEVEL` is set (non-empty), apply it
  last — it always wins over the file.

### Runtime level control

Applying a level string: trim; empty or missing → ignore; invalid →
`logquill: ignoring <source>=<raw> for logger <name> — not a valid level name (TRACE,
DEBUG, INFO, WARN, ERROR, FATAL) or number`, never raise. A signal handler re-reads an env
variable (`LOGQUILL_LEVEL`) on `SIGUSR1` (P) / `SIGUSR2` (J) and returns an uninstall
function (P restores the previous handler; main thread only; Windows → error telling the
caller to use the watchers). File and env watchers poll every 2 s on a daemon/unref'd
timer, apply when the raw value changes (the first poll applies the initial value), treat
a missing file as "no change", and expose `poll_once()` for tests plus idempotent
`start()` and safe `stop()`.
