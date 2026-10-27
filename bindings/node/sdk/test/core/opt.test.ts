// Ported from the Python `test_opt.py`: `logger.opt({ lazy, depth })` — values computed only if the record is
// really emitted, and `meta.caller` naming the code that logged, a chosen number of frames up.
import { describe, expect, it } from "vitest";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { CollectingTransport } from "../../src/transports/transport.js";

function setup(level: Level = Level.TRACE) {
  const sink = new CollectingTransport();
  const logger = new Logger("opt", { level, transports: [sink], queue: { maxSize: 100 } });
  return { logger, sink };
}

describe("lazy values", () => {
  it("are not evaluated when the level filters the call out", () => {
    const { logger } = setup(Level.WARN);
    let calls = 0;
    logger.opt({ lazy: true }).debug("filtered", { x: () => ++calls });
    expect(calls).toBe(0);
  });

  it("are evaluated when the record is emitted", async () => {
    const { logger, sink } = setup();
    logger.opt({ lazy: true }).info("emitted", { x: () => 6 * 7 });
    await logger.flush();
    expect(sink.records[0]!.meta.x).toBe(42);
  });

  it("are evaluated on the calling thread, even though dispatch is queued", () => {
    const { logger } = setup();
    let evaluated = false;
    logger.opt({ lazy: true }).info("now", { x: () => (evaluated = true) });
    expect(evaluated).toBe(true); // before any flush: the log call itself ran it
  });

  it("a raising lazy value becomes a placeholder instead of crashing the caller", async () => {
    const { logger, sink } = setup();
    expect(() =>
      logger.opt({ lazy: true }).info("boom", {
        x: () => {
          throw new Error("kaput");
        },
      }),
    ).not.toThrow();
    await logger.flush();
    expect(String(sink.records[0]!.meta.x)).toMatch(/^<lazy value raised .*kaput/);
  });

  it("callables are left alone without lazy", () => {
    const { logger } = setup();
    const fn = () => 1;
    const record = logger.info("plain", { fn });
    expect(typeof record!.meta.fn).toBe("function");
  });
});

describe("caller depth", () => {
  function wrapper(logger: Logger, depth: number) {
    return logger.opt({ depth }).info("from the wrapper");
  }
  function outer(logger: Logger, depth: number) {
    return wrapper(logger, depth);
  }

  it("is not recorded without depth", () => {
    const { logger } = setup();
    expect(logger.info("no depth")!.meta.caller).toBeUndefined();
  });

  it("depth 0 reports the direct caller; depth 1 skips a wrapper and reports its caller", () => {
    const { logger } = setup();
    const direct = outer(logger, 0)!.meta.caller as { function: string };
    const skipped = outer(logger, 1)!.meta.caller as { function: string };
    expect(direct.function).toContain("wrapper");
    expect(skipped.function).toContain("outer");
  });

  it("names the file and line", () => {
    const { logger } = setup();
    const caller = logger.opt({ depth: 0 }).info("where")!.meta.caller as { file: string; line: number };
    expect(caller.file).toContain("opt.test");
    expect(caller.line).toBeGreaterThan(0);
  });

  it("a depth beyond the stack omits the caller instead of raising", () => {
    const { logger } = setup();
    expect(() => logger.opt({ depth: 10_000 }).info("too deep")).not.toThrow();
    expect(logger.opt({ depth: 10_000 }).info("too deep")!.meta.caller).toBeUndefined();
  });

  it("an explicit caller in meta wins", () => {
    const { logger } = setup();
    const record = logger.opt({ depth: 0 }).info("mine", { caller: "my-own" });
    expect(record!.meta.caller).toBe("my-own");
  });

  it("works for every method family", () => {
    const { logger } = setup();
    const view = logger.opt({ depth: 0 });
    for (const method of ["trace", "debug", "info", "warn", "error", "fatal", "thought", "action", "observation", "decision"] as const) {
      expect(view[method]("m")!.meta.caller, method).toBeDefined();
    }
  });
});

describe("the cached view", () => {
  it("the same options return the same instance; different options a different one", () => {
    const { logger } = setup();
    expect(logger.opt({ lazy: true })).toBe(logger.opt({ lazy: true }));
    expect(logger.opt({ depth: 1 })).toBe(logger.opt({ depth: 1 }));
    expect(logger.opt({ lazy: true })).not.toBe(logger.opt({ depth: 1 }));
  });

  it("still behaves correctly across repeated use", async () => {
    const { logger, sink } = setup();
    for (let i = 0; i < 50; i++) logger.opt({ lazy: true }).info("again", { i: () => i });
    await logger.flush();
    expect(sink.records.map((r) => r.meta.i)).toEqual(Array.from({ length: 50 }, (_, i) => i));
  });
});
