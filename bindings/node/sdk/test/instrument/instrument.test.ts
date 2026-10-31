import { afterEach, describe, expect, it, vi } from "vitest";
import { CollectingTransport, Logger, RunPlugin, TraceContextPlugin } from "../../src/index.js";
import { anthropic, inbound, openai, propagate } from "../../src/instrument.js";

// Shaped like the SDKs' APIPromise: a Promise subclass with an extra method
// that must survive instrumentation, and a parse that runs once per consumer.
class FakeAPIPromise<T> extends Promise<T> {
  constructor(
    private readonly value: T,
    private readonly parseSpy: () => void,
  ) {
    super((resolve) => {
      resolve(null as never);
    });
  }
  override then<R1 = T, R2 = never>(
    onFulfilled?: ((v: T) => R1 | PromiseLike<R1>) | null,
    onRejected?: ((e: unknown) => R2 | PromiseLike<R2>) | null,
  ): Promise<R1 | R2> {
    this.parseSpy();
    return Promise.resolve(this.value).then(onFulfilled, onRejected);
  }
  asResponse(): string {
    return "raw-response";
  }
  static get [Symbol.species](): PromiseConstructor {
    return Promise;
  }
}

function fakeAnthropic(parseSpy = () => {}, calls: unknown[] = []) {
  class Messages {
    create(params: unknown): unknown {
      calls.push(params);
      return new FakeAPIPromise(
        {
          model: "claude-fake",
          stop_reason: "end_turn",
          usage: { input_tokens: 10, output_tokens: 4 },
        },
        parseSpy,
      );
    }
  }
  return { messages: new Messages() };
}

function fakeOpenAI() {
  class Completions {
    create(): unknown {
      return Promise.resolve({
        model: "gpt-fake",
        usage: { prompt_tokens: 8, completion_tokens: 3 },
        choices: [{ finish_reason: "stop" }],
      });
    }
  }
  class Responses {
    create(): unknown {
      return Promise.resolve({
        model: "gpt-fake",
        status: "completed",
        incomplete_details: null,
        usage: { input_tokens: 5, output_tokens: 2 },
      });
    }
  }
  return { chat: { completions: new Completions() }, responses: new Responses() };
}

// The `create` currently on a resource's prototype, typed so lint doesn't see `any`.
function createOf(resource: object): unknown {
  return (Object.getPrototypeOf(resource) as { create: unknown }).create;
}

function setup() {
  const sink = new CollectingTransport();
  const logger = new Logger("app", { transports: [sink] });
  return { sink, logger };
}

afterEach(() => {
  anthropic.uninstrument();
  openai.uninstrument();
});

describe("anthropic instrument", () => {
  it("logs one llm record per awaited call and returns the SDK's own promise", async () => {
    const { sink, logger } = setup();
    const client = fakeAnthropic();
    anthropic.instrument(logger, { client });

    const pending = client.messages.create({ model: "claude-fake" }) as FakeAPIPromise<unknown>;
    expect(pending.asResponse()).toBe("raw-response"); // helper methods survive
    const response = await pending;
    await logger.flush();

    expect(response).toMatchObject({ model: "claude-fake" });
    expect(sink.records).toHaveLength(1);
    expect(sink.records[0]?.llm).toMatchObject({
      model: "claude-fake",
      tokens_in: 10,
      tokens_out: 4,
      finish_reason: "end_turn",
    });
    expect(sink.records[0]?.llm?.latency_ms).toBeGreaterThanOrEqual(0);
    expect(sink.records[0]?.meta.provider).toBe("anthropic");
  });

  it("does not parse the body for a caller that never awaits (asResponse only)", () => {
    const { logger } = setup();
    const parse = vi.fn();
    const client = fakeAnthropic(parse);
    anthropic.instrument(logger, { client });

    (client.messages.create({}) as FakeAPIPromise<unknown>).asResponse();

    expect(parse).not.toHaveBeenCalled();
  });

  it("records once however many times the result is consumed", async () => {
    const { sink, logger } = setup();
    const client = fakeAnthropic();
    anthropic.instrument(logger, { client });

    const pending = client.messages.create({}) as Promise<unknown>;
    await pending;
    await pending;
    await logger.flush();

    expect(sink.records).toHaveLength(1);
  });

  it("passes streaming calls through untouched", async () => {
    const { sink, logger } = setup();
    const client = fakeAnthropic();
    anthropic.instrument(logger, { client });

    await client.messages.create({ stream: true });
    await logger.flush();

    expect(sink.records).toHaveLength(0);
  });

  it("never lets a recording failure break the call", async () => {
    const { logger } = setup();
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(logger, "llmCall").mockImplementation(() => {
      throw new Error("boom");
    });
    const client = fakeAnthropic();
    anthropic.instrument(logger, { client });

    await expect(client.messages.create({})).resolves.toMatchObject({ model: "claude-fake" });
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("leaves a failed call's rejection alone and records nothing", async () => {
    const { sink, logger } = setup();
    class Messages {
      create(): unknown {
        return Promise.reject(new Error("429"));
      }
    }
    anthropic.instrument(logger, { client: { messages: new Messages() } });

    await expect(new (Messages as new () => Messages)().create()).rejects.toThrow("429");
    await logger.flush();
    expect(sink.records).toHaveLength(0);
  });

  it("refuses to patch twice, and uninstrument restores the original", async () => {
    const { sink, logger } = setup();
    const client = fakeAnthropic();
    const original = createOf(client.messages);
    anthropic.instrument(logger, { client });

    expect(anthropic.active).toBe(true);
    expect(() => {
      anthropic.instrument(logger, { client });
    }).toThrow(/already active/);

    anthropic.uninstrument();
    expect(anthropic.active).toBe(false);
    expect(createOf(client.messages)).toBe(original);
    await client.messages.create({});
    await logger.flush();
    expect(sink.records).toHaveLength(0);
  });

  it("patches the shared prototype, so a second client is covered too", async () => {
    const { sink, logger } = setup();
    const first = fakeAnthropic();
    anthropic.instrument(logger, { client: first });
    const second = {
      messages: Object.create(
        Object.getPrototypeOf(first.messages) as object,
      ) as typeof first.messages,
    };

    await second.messages.create({});
    await logger.flush();
    expect(sink.records).toHaveLength(1);
  });

  it("names the fix when given something that is not a client", () => {
    const { logger } = setup();
    expect(() => {
      anthropic.instrument(logger, { client: {} as never });
    }).toThrow(/Pass an instance of the official SDK's client/);
    expect(anthropic.active).toBe(false);
  });
});

describe("openai instrument", () => {
  it("covers chat completions and the responses API", async () => {
    const { sink, logger } = setup();
    const client = fakeOpenAI();
    openai.instrument(logger, { client });

    await client.chat.completions.create();
    await client.responses.create();
    await logger.flush();

    expect(sink.records.map((r) => r.llm)).toMatchObject([
      { model: "gpt-fake", tokens_in: 8, tokens_out: 3, finish_reason: "stop" },
      { model: "gpt-fake", tokens_in: 5, tokens_out: 2, finish_reason: "completed" },
    ]);
    expect(sink.records.every((r) => r.meta.provider === "openai")).toBe(true);
  });

  it("restores both methods on uninstrument", () => {
    const { logger } = setup();
    const client = fakeOpenAI();
    const chat = createOf(client.chat.completions);
    const responses = createOf(client.responses);
    openai.instrument(logger, { client });
    openai.uninstrument();
    expect(createOf(client.chat.completions)).toBe(chat);
    expect(createOf(client.responses)).toBe(responses);
  });
});

describe("MCP propagation", () => {
  it("propagate builds a W3C traceparent and optional run id", () => {
    const meta = propagate({ traceId: "a".repeat(32), runId: "run-7" });
    expect(meta["logquill/traceparent"]).toMatch(/^00-a{32}-[0-9a-f]{16}-01$/);
    expect(meta["logquill/run-id"]).toBe("run-7");
    expect(Object.keys(propagate())).toEqual(["logquill/traceparent"]);
  });

  it("client to server shares one trace_id, and server records carry meta.mcp", async () => {
    const sink = new CollectingTransport();
    const server = new Logger("mcp.server", {
      transports: [sink],
      plugins: [new TraceContextPlugin(), new RunPlugin({ runId: "server-run" })],
    });
    const traceId = "b".repeat(32);
    const meta = propagate({ traceId, runId: "client-run" });

    const result = await inbound(meta, { server: "search-srv", tool: "search" }, async () => {
      await Promise.resolve();
      server.info("handling");
      return 42;
    });
    await server.flush();

    expect(result).toBe(42);
    expect(sink.records[0]?.meta).toMatchObject({
      trace_id: traceId,
      run_id: "server-run", // the client's run id never overrides the server's own
      mcp: { server: "search-srv", tool: "search", run_id: "client-run" },
    });
  });

  it("tolerates a request with no _meta and does not leak context afterwards", async () => {
    const sink = new CollectingTransport();
    const server = new Logger("mcp.server", { transports: [sink] });

    await inbound(undefined, { server: "s", tool: "t" }, () => {
      server.info("inside");
    });
    server.info("outside");
    await server.flush();

    expect(sink.records[0]?.meta.mcp).toEqual({ server: "s", tool: "t" });
    expect(sink.records[1]?.meta.mcp).toBeUndefined();
  });

  it("rethrows the handler's error unchanged", async () => {
    await expect(
      inbound(undefined, { server: "s", tool: "t" }, () => {
        throw new Error("tool failed");
      }),
    ).rejects.toThrow("tool failed");
  });
});
