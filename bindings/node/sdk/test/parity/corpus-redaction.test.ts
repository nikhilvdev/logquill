// The SDK's own redaction plugins against the shared corpus (`schema/corpus/redaction.json`). The addon's
// redactors are checked against the same file in `tests/corpus.test.mjs`; this checks that the plugins a user
// actually installs (`RedactPlugin`, `PIIRedactPlugin`) behave the same, so a privacy control does not
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { nativeAddon } from "../../src/core/native.js";
import type { LogRecord } from "../../src/core/records.js";
import { PIIRedactPlugin } from "../../src/plugins/pii-redact-plugin.js";
import {
  FIELD_CLASS_KEYS,
  RedactPlugin,
  type FieldClass,
} from "../../src/plugins/redact-plugin.js";

interface Case {
  name: string;
  input: {
    op: string;
    meta?: Record<string, unknown>;
    keys?: string[];
    classes?: FieldClass[];
    replacement?: string;
  };
  expected: { meta?: Record<string, unknown> };
}

const file = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "..",
  "..",
  "schema",
  "corpus",
  "redaction.json",
);
const cases = (JSON.parse(readFileSync(file, "utf8")) as { cases: Case[] }).cases.filter(
  // `redact_local` and `pii_local` redact a local variable's text; there are no locals in JavaScript
  (c) => !c.input.op.endsWith("_local"),
);

const wrap = (meta: Record<string, unknown>): LogRecord =>
  ({
    schema_version: "2.0",
    timestamp: "t",
    level: "INFO",
    logger: "l",
    message: "m",
    meta,
  }) as LogRecord;

describe("the field-class registry in the JavaScript fallback", () => {
  it.skipIf(!nativeAddon())("is the core's registry", () => {
    const fromCore: Record<string, string[]> = { secret: [], pii: [], content: [] };
    for (const { key, class: name } of nativeAddon()!.fieldClasses()) fromCore[name]!.push(key);
    expect(FIELD_CLASS_KEYS).toEqual(fromCore);
  });
});

// Runs with the addon (the Rust redactors) and without it (`LOGQUILL_NO_NATIVE=1`, the JavaScript
// fallbacks): the privacy controls must not depend on which one a platform ends up with.
describe("redaction plugins against the shared corpus", () => {
  for (const { name, input, expected } of cases) {
    it(name, () => {
      const options = {
        ...(input.replacement === undefined ? {} : { replacement: input.replacement }),
      };
      const plugin =
        input.op === "redact"
          ? new RedactPlugin({
              ...options,
              ...(input.keys === undefined ? {} : { keys: input.keys }),
              ...(input.classes === undefined ? {} : { classes: input.classes }),
            })
          : new PIIRedactPlugin(options);
      const redacted = plugin.beforeLog(wrap(structuredClone(input.meta ?? {})));
      expect(redacted?.meta).toEqual(expected.meta);
    });
  }
});
