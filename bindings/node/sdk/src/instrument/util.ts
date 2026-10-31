import type { Logger } from "../core/logger.js";

/** One reversible method swap. */
interface Patch {
  target: object;
  name: string;
  original: unknown;
}

type AnyFn = (this: unknown, ...args: unknown[]) => unknown;

/**
 * Runs `record` (which logs one call's fields), swallowing any error. A bug in
 * instrumentation must never surface as a failure of the API call it
 * instruments: the point of `instrument` is that call sites, including their
 * error handling, don't change.
 */
export function recordSafely(record: () => void): void {
  try {
    record();
  } catch (err) {
    console.error("logquill/instrument: failed to record a call", err);
  }
}

/**
 * Wraps one non-streaming SDK call. Returns the SDK's own return value
 * unchanged (the Anthropic and OpenAI SDKs return an `APIPromise` with
 * `.withResponse()`/`.asResponse()` that must keep working) and records once
 * when something consumes it through `await`/`.then`. The recording hook is a
 * `then` on that instance, not a second `.then` chained on it: chaining would
 * force the SDK to parse the body even for a caller that only wants
 * `.asResponse()`.
 *
 * shortcut: `.asResponse()`/`.withResponse()` consumers are not recorded;
 * upgrade when a caller needs them.
 */
function wrapCall(original: AnyFn, record: (response: unknown, ms: number) => void): AnyFn {
  return function (this: unknown, ...args: unknown[]) {
    const params = args[0] as { stream?: unknown } | undefined;
    const result = original.apply(this, args) as {
      then?: (...a: unknown[]) => unknown;
    } | null;
    if (params?.stream || typeof result?.then !== "function") return result;

    const started = performance.now();
    const recordOnce = (response: unknown): void => {
      recordSafely(() => {
        record(response, performance.now() - started);
      });
    };
    // A plain Promise has nothing to preserve, and `await` skips an own `then` on one, so chain normally.
    if (result.constructor === Promise) {
      return (result as Promise<unknown>).then((response) => {
        recordOnce(response);
        return response;
      });
    }
    const originalThen = result.then.bind(result);
    let recorded = false;
    (result as { then: unknown }).then = (
      onFulfilled?: (v: unknown) => unknown,
      onRejected?: (e: unknown) => unknown,
    ) =>
      originalThen((response: unknown) => {
        if (!recorded) {
          recorded = true;
          recordOnce(response);
        }
        return onFulfilled ? onFulfilled(response) : response;
      }, onRejected);
    return result;
  };
}

/**
 * One `logquill/instrument` entry: calling it patches, `.uninstrument()` puts
 * everything back. Calling it twice without an intervening `.uninstrument()`
 * throws, so a wrapper is never wrapped twice (which would log every call
 * twice).
 */
export class Instrumenter<Options> {
  private patches: Patch[] = [];

  constructor(
    private readonly name: string,
    private readonly build: (
      logger: Logger,
      options: Options,
    ) => { target: object; method: string; record: (response: unknown, ms: number) => void }[],
  ) {}

  /** Patches the provider SDK so every non-streaming call is logged through `logger.llmCall()`. */
  instrument(logger: Logger, options: Options): void {
    if (this.patches.length > 0) {
      throw new Error(
        `logquill/instrument ${this.name} is already active. Call ${this.name}.uninstrument() first to change its logger.`,
      );
    }
    for (const { target, method, record } of this.build(logger, options)) {
      const original = (target as Record<string, unknown>)[method];
      if (typeof original !== "function") {
        this.uninstrument();
        throw new Error(
          `logquill/instrument ${this.name}: ${method}() was not found on the client you passed. Pass an instance of the official SDK's client.`,
        );
      }
      (target as Record<string, unknown>)[method] = wrapCall(original as AnyFn, record);
      this.patches.push({ target, name: method, original });
    }
  }

  /** Restores every method this patched. Safe to call when nothing is patched. */
  uninstrument(): void {
    for (const patch of this.patches.splice(0).reverse()) {
      (patch.target as Record<string, unknown>)[patch.name] = patch.original;
    }
  }

  /** Whether `instrument()` has been called without a matching `uninstrument()` since. */
  get active(): boolean {
    return this.patches.length > 0;
  }
}

/** `fields` without its `undefined` values, which `llmCall` (under `exactOptionalPropertyTypes`) doesn't accept. */
export function defined<T extends object>(fields: { [K in keyof T]: T[K] | undefined }): T {
  return Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined)) as T;
}

/** The prototype shared by every client's `resource`, so patching it covers all clients, whichever copy of the SDK the app loaded. */
export function protoOf(resource: unknown, path: string): object {
  if (typeof resource !== "object" || resource === null) {
    throw new Error(
      `logquill/instrument: client.${path} was not found. Pass an instance of the official SDK's client (e.g. new Anthropic()).`,
    );
  }
  return Object.getPrototypeOf(resource) as object;
}
