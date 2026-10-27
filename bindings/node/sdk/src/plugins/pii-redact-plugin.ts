import { nativeAddon } from "#native";
import type { PiiRedactor as NativePiiRedactor } from "../../../index.js";
import type { Plugin } from "../core/plugin.js";
import type { LogRecord } from "../core/records.js";

/**
 * Syntactic (not semantic) patterns — matched on shape, so both false
 * positives (a random 9-digit number) and false negatives (anything that
 * doesn't look like these shapes) are expected. Override via `patterns`
 * for anything stricter.
 */
// The same twelve patterns, in the same order, as the Rust core's `PiiRedactor` (they are applied in
// sequence), so a build without the native addon redacts what a build with it does. Whitespace is an
// explicit ASCII class, not `\s`, to match the core.
export const DEFAULT_PII_PATTERNS: Readonly<Record<string, RegExp>> = {
  email: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g,
  ssn: /\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b/g,
  creditCard: /\b(?:[0-9][ -]?){13,16}\b/g,
  phone:
    /\b(?:\+?1[-. \t\n\r\x0b\x0c]?)?\(?[0-9]{3}\)?[-. \t\n\r\x0b\x0c]?[0-9]{3}[-. \t\n\r\x0b\x0c]?[0-9]{4}\b/g,
  openaiKey: /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/g,
  anthropicKey: /\bsk-ant-[A-Za-z0-9_-]{20,}\b/g,
  awsAccessKey: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
  githubToken: /\bgh[pousr]_[A-Za-z0-9]{36,}\b/g,
  slackToken: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g,
  jwt: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
  privateKey:
    /-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----/g,
  bearerToken: /\bBearer[ \t\n\r\x0b\x0c]+[A-Za-z0-9\-_.]{20,}\b/g,
};

const MAX_DEPTH = 50;

/** Raised for a `meta` that contains itself: it cannot be scanned, so the record is withheld (decision D20). */
function circularReference(): Error {
  return new Error(
    "PIIRedactPlugin: meta contains a circular reference and cannot be scanned for PII — the record was withheld instead of written unredacted",
  );
}

/** Options for {@link PIIRedactPlugin}. */
export interface PIIRedactPluginOptions {
  /** Named patterns to scan for. Default `DEFAULT_PII_PATTERNS`. */
  patterns?: Readonly<Record<string, RegExp>>;
  /** Placeholder a matched substring is replaced with. Default `"***"`. */
  replacement?: string;
}

/**
 * Regex-based PII redaction over `meta` **values**, not just keys.
 *
 * Complements `RedactPlugin`, which redacts by exact key match —
 * `PIIRedactPlugin` scans string values (recursively through nested
 * objects/arrays) for emails, SSNs, credit-card numbers, and phone
 * numbers, and redacts matches wherever they appear, regardless of which
 * key holds them (a `notes` field containing a stray SSN is still caught).
 *
 * Detection is pattern-based: fast and dependency-free, but it matches on
 * syntactic shape, not meaning — a random 9-digit number can false-positive
 * as an SSN, and anything that doesn't fit these shapes (a name, a street
 * address) is a false negative. Pass your own `patterns` to extend or
 * replace the defaults.
 *
 * Recursion into nested `meta` structures is depth- and cycle-bounded, so a
 * circular reference or a pathologically deep structure can't hang or
 * crash the caller — it's left unredacted past the bound rather than
 * throwing.
 */
export class PIIRedactPlugin implements Plugin {
  /** Named patterns scanned for in every string `meta` value. */
  readonly patterns: Readonly<Record<string, RegExp>>;
  /** Placeholder a matched substring is replaced with. */
  readonly replacement: string;

  /** Redacts, fail-closed: if it throws, the record is dropped rather than written unredacted (decision D20). */
  readonly failsClosed = true;
  /** The Rust redactor (the core's twelve PII and secret patterns); used when the addon loads and no custom `patterns` were given. */
  private readonly native: NativePiiRedactor | undefined;

  constructor(options: PIIRedactPluginOptions = {}) {
    this.patterns = options.patterns ?? DEFAULT_PII_PATTERNS;
    this.replacement = options.replacement ?? "***";
    // custom regular expressions can only run in JavaScript, so they keep the JavaScript path
    const addon = options.patterns === undefined ? nativeAddon() : null;
    this.native = addon ? new addon.PiiRedactor(this.replacement) : undefined;
  }

  beforeLog(record: LogRecord): LogRecord {
    if (this.native) {
      return {
        ...record,
        meta: JSON.parse(this.native.redactMeta(JSON.stringify(record.meta))) as Record<string, unknown>,
      };
    }
    return { ...record, meta: this.redactValue(record.meta, new Set(), 0) as Record<string, unknown> };
  }

  private redactValue(value: unknown, seen: ReadonlySet<unknown>, depth: number): unknown {
    if (depth > MAX_DEPTH) {
      return this.replacement; // too deep to scan: replace it rather than leave it unredacted
    }
    if (typeof value === "string") {
      return this.redactText(value);
    }
    if (Array.isArray(value)) {
      if (seen.has(value)) {
        throw circularReference();
      }
      const nextSeen = new Set(seen).add(value);
      return value.map((entry) => this.redactValue(entry, nextSeen, depth + 1));
    }
    if (value !== null && typeof value === "object") {
      if (seen.has(value)) {
        throw circularReference();
      }
      const nextSeen = new Set(seen).add(value);
      const result: Record<string, unknown> = {};
      for (const [key, entryValue] of Object.entries(value)) {
        result[key] = this.redactValue(entryValue, nextSeen, depth + 1);
      }
      return result;
    }
    return value;
  }

  /**
   * Replaces every match. Like the core's redactor, matches are found independently over the original
   * text: the leftmost wins (the longer one on a tie) and overlapping later ones are skipped. Applying
   * the patterns one after another would let the phone pattern eat the digits inside a Slack token
   * before the Slack pattern saw it.
   */
  private redactText(text: string): string {
    const spans: [number, number][] = [];
    for (const pattern of Object.values(this.patterns)) {
      // Custom patterns aren't guaranteed to carry the "g" flag, but every occurrence must be
      // replaced: reconstruct with "g" added rather than silently stopping at the first match.
      const global = pattern.global ? pattern : new RegExp(pattern.source, `${pattern.flags}g`);
      for (const match of text.matchAll(global)) {
        if (match[0].length > 0) spans.push([match.index, match.index + match[0].length]);
      }
    }
    if (spans.length === 0) return text;
    spans.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    let out = "";
    let cursor = 0;
    let lastEnd = 0;
    for (const [start, end] of spans) {
      if (start >= lastEnd) {
        out += text.slice(cursor, start) + this.replacement;
        cursor = end;
        lastEnd = end;
      }
    }
    return out + text.slice(cursor);
  }
}
