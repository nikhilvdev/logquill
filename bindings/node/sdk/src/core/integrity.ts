import { createHmac } from "node:crypto";
import { requireNative } from "./native.js";

/** What verifying a hash chain found. */
export interface ChainVerification {
  ok: boolean;
  /** Records looked at, including the one the chain broke on. */
  recordsChecked: number;
  /** 1-based position of the first record that failed. */
  brokenAt?: number | undefined;
  /** Why it failed, in words you can act on. */
  reason?: string | undefined;
  /** The chain head as of the last record verified. */
  headHash?: string | undefined;
}

/** Options for the chain verifiers. */
export interface VerifyChainOptions {
  /** The hash the chain started from, if it was not the default. */
  genesisHash?: string;
}

/**
 * The raw lines of a JSON-lines log as the text of one JSON array. Records are verified from the text
 * they were written as, not from parsed objects: a number's spelling (`1.0` against `1`) is part of what
 * the hash covers, and `JSON.parse` would erase it.
 */
function arrayText(lines: Iterable<string> | string): string {
  const list = typeof lines === "string" ? lines.split("\n") : lines;
  const records: string[] = [];
  for (const line of list) {
    const trimmed = line.trim();
    if (trimmed !== "") records.push(trimmed);
  }
  return `[${records.join(",")}]`;
}

/**
 * Verifies a hash-chained log given as its lines (or the whole file as one string). Catches an edited,
 * removed or reordered record; use {@link verifySignedChain} to also catch a cut-off end. Rust core.
 */
export function verifyChain(
  lines: Iterable<string> | string,
  options: VerifyChainOptions = {},
): ChainVerification {
  return requireNative("verifyChain").verifyChain(arrayText(lines), options.genesisHash);
}

/** HMAC-SHA256 of `headHash` under `key`, hex-encoded (what `TamperEvidentPlugin.signHead` returns). */
export function signHead(headHash: string, key: string | Uint8Array): string {
  return createHmac("sha256", key).update(headHash).digest("hex");
}

/** Whether `signature` is what {@link signHead} gives for `headHash` and `key`, compared in constant time. */
export function verifyHeadSignature(
  headHash: string,
  key: string | Uint8Array,
  signature: string,
): boolean {
  return requireNative("verifyHeadSignature").verifyHeadSignature(
    headHash,
    Buffer.from(key),
    signature,
  );
}

/**
 * Verifies a hash-chained log and that its head matches `signature`, so a log with its last lines cut
 * off fails too. The signature and key must be kept where the log's writer cannot edit them.
 */
export function verifySignedChain(
  lines: Iterable<string> | string,
  key: string | Uint8Array,
  signature: string,
  options: VerifyChainOptions = {},
): ChainVerification {
  return requireNative("verifySignedChain").verifySignedChain(
    arrayText(lines),
    Buffer.from(key),
    signature,
    options.genesisHash,
  );
}
