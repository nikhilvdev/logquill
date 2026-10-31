// Traces every legacy Python test (docs/spec/legacy-tests/python-tests.txt) to a Node test or a shared corpus case,
// for parity condition 2 (docs/spec/09, section D). A test title cannot be matched exactly across languages, so this
// is a heuristic and says so: for each Python test it finds the Node test (or corpus case) whose title shares the
// most of its words, records the score and the matched title so a reviewer can check it, and lists everything that
// falls below the bar as unmatched. Tests for features that do not exist in JavaScript are marked n/a with the reason.
//
//   npm run trace-python-tests        (writes docs/stages/stage-6-python-trace.md)
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const repo = join(root, "..", "..");

// ---- the Node side: every test title that ran, and every shared corpus case ---------------------------------
const report = join(mkdtempSync(join(tmpdir(), "lq-trace-")), "vitest.json");
execFileSync("npx", ["vitest", "run", "--reporter=json", `--outputFile=${report}`], { cwd: root, stdio: "ignore" });
const targets = [];
for (const file of JSON.parse(readFileSync(report, "utf8")).testResults) {
  const where = file.name.split("/sdk/test/")[1] ?? file.name;
  for (const test of file.assertionResults) targets.push({ where, title: test.fullName });
}
const corpusDir = join(repo, "schema", "corpus");
for (const name of readdirSync(corpusDir).filter((f) => f.endsWith(".json"))) {
  for (const c of JSON.parse(readFileSync(join(corpusDir, name), "utf8")).cases ?? []) {
    targets.push({ where: `schema/corpus/${name}`, title: `${name.replace(".json", "")}: ${c.name}` });
  }
}

// ---- matching -----------------------------------------------------------------------------------------------
const STOP = new Set("a an the is are of to in on and or not does do it its for with as be by at from that this no test when then only if".split(" "));
const stem = (w) => (w.length > 4 && w.endsWith("ing") ? w.slice(0, -3) : w.length > 4 && w.endsWith("ed") ? w.slice(0, -2) : w.length > 3 && w.endsWith("s") ? w.slice(0, -1) : w);
const words = (s) =>
  s
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .split(" ")
    .filter((w) => w && !STOP.has(w))
    .map(stem);
for (const t of targets) t.words = new Set(words(`${t.where.replace(/.*\//, "")} ${t.title}`));

// areas with no JavaScript counterpart, and why
const NOT_APPLICABLE = [
  [/test_cli|test_webui/, "the CLI and local viewer are the Rust `logquill` binary, shared by every language"],
  [/test_diagnose/, "JavaScript cannot read another frame's local variables"],
  [/apprise/, "Apprise is a Python library"],
  [/crewai|autogen|litellm/, "Python-only framework"],
  [/test_handler\.py/, "the stdlib `logging` handler; Node's equivalents are the pino and winston bridges"],
  [/test_version|test_lazy_imports|aiohttp_sender/, "Python packaging detail"],
  [/otel_metrics_backend/, "covered by the `OTelMetricsBackend` tests, under another name"],
];

// Python tests whose Node counterpart has a different shape, so no title can match. Each names what covers it;
// check the claim by opening the file. (Python-only mechanics such as `sys.exc_info()` frames and OS threads map
// to the nearest Node behaviour.)
const MAPPED = [
  [/test_exceptions\.py/, "sdk/test/core/logger.test.ts :: meta.err -> meta.stack capture (Node has no exc_info frames; an Error in meta.err is the equivalent)"],
  [/test_worker\.py/, "sdk/test/core/dispatch-queue.test.ts (the Node queue is a microtask queue, not a thread; drop_oldest, drop_newest, block, bound, drain and a throwing task are covered there) and the crates/logquill-runtime tests"],
  [/test_shutdown\.py/, "sdk/test/core/shutdown.test.ts, sdk/test/native/features.test.ts and scripts/smoke.mjs (the exit hook that drains the Rust queue)"],
  [/test_timestamp\.py/, "the addon timestamp tests in tests/helpers.test.mjs"],
  [/test_trace_tree\.py/, "schema/corpus/trace-tree.json (34 shared cases) and sdk/test/native/ports-2.test.ts"],
];

const python = readFileSync(join(repo, "docs/spec/legacy-tests/python-tests.txt"), "utf8").split("\n").filter(Boolean);
const rows = python.map((line) => {
  // most entries are `file::test_name`; a few come from a test class as `file:line:    def test_name(self)`
  const parsed = line.match(/^(.*?)::(.+)$/) ?? line.match(/^(.*?):\d+:\s*(?:async\s+)?def\s+(test_\w+)/);
  if (parsed === null) throw new Error(`cannot read the checklist line: ${line}`);
  const [, file, name] = parsed;
  const short = file.replace(/^tests\//, "");
  const test = name.replace(/^test_/, "");
  const na = NOT_APPLICABLE.find(([pattern]) => pattern.test(file));
  if (na) return { short, test, kind: "n/a", note: na[1] };
  const mapped = MAPPED.find(([pattern]) => pattern.test(file));
  if (mapped) return { short, test, kind: "mapped", note: mapped[1] };
  const p = new Set(words(test));
  const stemWords = new Set(words(short.replace(/^test_|\.py$/g, "").replace(/\//g, " ")));
  let best = { score: 0, shared: 0, target: undefined };
  for (const t of targets) {
    let shared = 0;
    for (const w of p) if (t.words.has(w)) shared++;
    if (shared === 0) continue;
    let score = shared / p.size;
    for (const w of stemWords) if (t.words.has(w)) score += 0.05; // same area as the Python file
    if (score > best.score) best = { score, shared, target: t };
  }
  return best.score >= 0.6 && best.shared >= 2
    ? { short, test, kind: "match", score: best.score, target: best.target }
    : { short, test, kind: "unmatched", score: best.score, target: best.target };
});

// ---- the report ----------------------------------------------------------------------------------------------
const count = (kind) => rows.filter((r) => r.kind === kind).length;
const byFile = new Map();
for (const r of rows) (byFile.get(r.short) ?? byFile.set(r.short, []).get(r.short)).push(r);
let out = `# Stage 6 — the legacy Python tests, traced to Node (generated)

Generated by \`npm run trace-python-tests\` in \`bindings/node\`. **Heuristic:** a Python test is matched to the Node test
or shared corpus case whose title shares the most of its words (score = share of the Python test's words found, at
least 60% and at least two words). The matched title is shown so a reviewer can check it; a wrong match is possible,
and an "unmatched" test may well be covered under a very different title. Read it as an audit trail, not a proof.

| | tests |
|---|---|
| Python tests in \`python-tests.txt\` | ${rows.length} |
| matched to a Node test or corpus case | ${count("match")} |
| not applicable to Node (reason given) | ${count("n/a")} |
| mapped by hand to the test that covers it | ${count("mapped")} |
| **unmatched** | ${count("unmatched")} |

`;
for (const [file, list] of [...byFile].sort(([a], [b]) => a.localeCompare(b))) {
  out += `## ${file}\n\n| Python test | Node |\n|---|---|\n`;
  for (const r of list) {
    const cell =
      r.kind === "match"
        ? `${r.score.toFixed(2)} — \`${r.target.where}\` :: ${r.target.title.replace(/\|/g, "\\|")}`
        : r.kind === "mapped"
          ? `mapped — ${r.note}`
        : r.kind === "n/a"
          ? `n/a — ${r.note}`
          : `**unmatched**${r.target ? ` (closest ${r.score.toFixed(2)}: \`${r.target.where}\` :: ${r.target.title.replace(/\|/g, "\\|")})` : ""}`;
    out += `| ${r.test} | ${cell} |\n`;
  }
  out += "\n";
}
writeFileSync(join(repo, "docs/stages/stage-6-python-trace.md"), `${out.trimEnd()}\n`); // one final newline, as the end-of-file hook wants
console.log(`traced ${rows.length} Python tests: ${count("match")} matched, ${count("mapped")} mapped, ${count("n/a")} n/a, ${count("unmatched")} unmatched`);
