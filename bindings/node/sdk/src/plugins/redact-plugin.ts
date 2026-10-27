import { nativeAddon } from "#native";
import type { KeyRedactor as NativeKeyRedactor } from "../../../index.js";
import type { Plugin } from "../core/plugin.js";
import type { LogRecord } from "../core/records.js";

/** `meta` keys (case-insensitive) `RedactPlugin` replaces by default. */
export const DEFAULT_REDACTED_KEYS: readonly string[] = ["password", "token", "secret", "api_key", "authorization"];

/** A class of sensitive field: its well-known keys can be redacted by class instead of listed one by one. */
export type FieldClass = "secret" | "pii" | "content";

/** Options for {@link RedactPlugin}. */
export interface RedactPluginOptions {
  /** Keys (case-insensitive) to redact, at every depth. Default `DEFAULT_REDACTED_KEYS`. */
  keys?: readonly string[];
  /**
   * Field classes whose well-known keys are redacted as well (`"secret"`, `"pii"`, `"content"`), so a rule can
   * target a class rather than list keys.
   */
  classes?: readonly FieldClass[];
  /** Placeholder a matched value is replaced with. Default `"***"`. */
  replacement?: string;
}

/**
 * The well-known keys of each field class: the core's registry (`fieldClasses()` in the native addon).
 * A test keeps the two equal.
 */
export const FIELD_CLASS_KEYS: Readonly<Record<FieldClass, readonly string[]>> = {
  secret: ["password", "token", "secret", "api_key", "authorization"],
  pii: ["email", "ssn", "phone"],
  content: [
    "input_messages",
    "output_messages",
    "system_instructions",
    "tool_arguments",
    "tool_result",
    "state_diff",
  ],
};

/** Nesting this deep is checked; below it the subtree is replaced outright rather than left unredacted. */
const MAX_DEPTH = 50;

/**
 * Replaces sensitive `meta` values, matched by key (case-insensitive) at every depth, with a placeholder.
 * Runs in the Rust core when the addon is available (the same code as every other binding, including field
 * classes); otherwise a JavaScript version with the same depth rule. If it fails the record is dropped, not
 * written unredacted.
 */
export class RedactPlugin implements Plugin {
  readonly failsClosed = true;
  private readonly keys: ReadonlySet<string>;
  /** Placeholder a matched value is replaced with. */
  readonly replacement: string;
  private readonly native: NativeKeyRedactor | undefined;

  constructor(options: RedactPluginOptions = {}) {
    const listed = options.keys ?? DEFAULT_REDACTED_KEYS;
    const byClass = (options.classes ?? []).flatMap((name) => FIELD_CLASS_KEYS[name]);
    this.keys = new Set([...listed, ...byClass].map((key) => key.toLowerCase()));
    this.replacement = options.replacement ?? "***";
    const addon = nativeAddon();
    if (addon) {
      this.native = new addon.KeyRedactor({
        ...(options.keys === undefined ? {} : { keys: [...options.keys] }),
        ...(options.classes === undefined ? {} : { classes: [...options.classes] }),
        replacement: this.replacement,
      });
    }
  }

  beforeLog(record: LogRecord): LogRecord {
    if (this.native) {
      return { ...record, meta: JSON.parse(this.native.redactMeta(JSON.stringify(record.meta))) as Record<string, unknown> };
    }
    return { ...record, meta: this.walkObject(record.meta, 0) };
  }

  // The same walk as the core's `KeyRedactor`: a matching key is replaced whatever it holds; anything
  // nested deeper than MAX_DEPTH is replaced outright rather than left unchecked.
  private walkObject(object: Record<string, unknown>, depth: number): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(object)) {
      out[key] = this.keys.has(key.toLowerCase()) ? this.replacement : this.walk(item, depth + 1);
    }
    return out;
  }

  private walk(value: unknown, depth: number): unknown {
    if ((typeof value === "string" || (typeof value === "object" && value !== null)) && depth > MAX_DEPTH) {
      return this.replacement;
    }
    if (Array.isArray(value)) return value.map((item) => this.walk(item, depth + 1));
    if (typeof value === "object" && value !== null) {
      return this.walkObject(value as Record<string, unknown>, depth);
    }
    return value;
  }
}
