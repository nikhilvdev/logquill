import { BaseGraphTransport, type GraphTransportOptions } from "./base-graph-transport.js";

// The three backends differ only in how one parameterized statement reaches the database. Each takes an
// already-built client (or, where the npm package is a standard one, connects lazily), so a test can pass
// a fake and nothing here depends on a graph driver being installed.

/** The part of the `neo4j-driver` `Session` this transport calls. */
export interface Neo4jSessionLike {
  run(query: string, parameters: Record<string, unknown>): Promise<unknown>;
  close(): Promise<void>;
}

/** The part of the `neo4j-driver` `Driver` this transport calls. */
export interface Neo4jDriverLike {
  session(config?: { database?: string }): Neo4jSessionLike;
  close(): Promise<void>;
}

/** Options for {@link Neo4jTransport}. */
export interface Neo4jTransportOptions extends GraphTransportOptions {
  /** An already-built driver (or a fake). Left open on close: its owner closes it. */
  driver?: Neo4jDriverLike;
  /** Used only when no `driver` is given: connect with the `neo4j-driver` package. Default `bolt://localhost:7687`. */
  uri?: string;
  /** `[user, password]` for the connection this transport makes itself. */
  auth?: [string, string];
  database?: string;
}

/** Writes the span tree to Neo4j, the reference backend in this category. */
export class Neo4jTransport extends BaseGraphTransport {
  private driver: Neo4jDriverLike | undefined;
  private readonly owned: boolean;
  private readonly options: Neo4jTransportOptions;

  constructor(options: Neo4jTransportOptions = {}) {
    super(options);
    this.options = options;
    this.driver = options.driver;
    this.owned = options.driver === undefined;
  }

  private async resolve(): Promise<Neo4jDriverLike> {
    if (this.driver) return this.driver;
    const name = "neo4j-driver"; // a variable, so nothing resolves it unless this transport connects itself
    let module: {
      default: {
        driver(uri: string, auth?: unknown): Neo4jDriverLike;
        auth: { basic(user: string, password: string): unknown };
      };
    };
    try {
      module = (await import(name)) as typeof module;
    } catch (cause) {
      throw new Error(
        "Neo4jTransport needs the optional `neo4j-driver` package — install it with `npm install neo4j-driver`, or pass an already-built `driver`",
        { cause },
      );
    }
    const { auth } = this.options;
    this.driver = module.default.driver(
      this.options.uri ?? "bolt://localhost:7687",
      auth ? module.default.auth.basic(auth[0], auth[1]) : undefined,
    );
    return this.driver;
  }

  protected override async execute(query: string, parameters: { rows: unknown[] }): Promise<void> {
    const driver = await this.resolve();
    const session = driver.session(this.options.database ? { database: this.options.database } : {});
    try {
      await session.run(query, parameters);
    } finally {
      await session.close();
    }
  }

  override close(): void {
    super.close();
    // closes only a driver this transport made; the batch still sending finishes first
    if (this.owned && this.driver) void this.driver.close();
  }
}

/** The part of a FalkorDB graph this transport calls. */
export interface FalkorDBGraphLike {
  query(query: string, options?: { params?: Record<string, unknown> }): Promise<unknown>;
}

/** The part of the `falkordb` client this transport calls. */
export interface FalkorDBClientLike {
  selectGraph(name: string): FalkorDBGraphLike;
  close?(): Promise<void>;
}

/** Options for {@link FalkorDBTransport}. */
export interface FalkorDBTransportOptions extends GraphTransportOptions {
  /** An already-built client (or a fake). Left open on close. */
  client?: FalkorDBClientLike;
  /** Used only when no `client` is given: connect with the `falkordb` package. Default `"localhost"`. */
  host?: string;
  /** Default 6379. */
  port?: number;
  /** The graph to write to. Default `"logquill"`. */
  graph?: string;
}

/** Writes the span tree to FalkorDB, the lightweight Redis-based, Cypher-compatible backend. */
export class FalkorDBTransport extends BaseGraphTransport {
  private client: FalkorDBClientLike | undefined;
  private readonly owned: boolean;
  private readonly options: FalkorDBTransportOptions;

  constructor(options: FalkorDBTransportOptions = {}) {
    super(options);
    this.options = options;
    this.client = options.client;
    this.owned = options.client === undefined;
  }

  private async resolve(): Promise<FalkorDBClientLike> {
    if (this.client) return this.client;
    const name = "falkordb";
    let module: {
      FalkorDB: { connect(config: unknown): Promise<FalkorDBClientLike> };
    };
    try {
      module = (await import(name)) as typeof module;
    } catch (cause) {
      throw new Error(
        "FalkorDBTransport needs the optional `falkordb` package — install it with `npm install falkordb`, or pass an already-built `client`",
        { cause },
      );
    }
    this.client = await module.FalkorDB.connect({
      socket: { host: this.options.host ?? "localhost", port: this.options.port ?? 6379 },
    });
    return this.client;
  }

  private graph: FalkorDBGraphLike | undefined;

  protected override async execute(query: string, parameters: { rows: unknown[] }): Promise<void> {
    // selected once, not on every batch
    this.graph ??= (await this.resolve()).selectGraph(this.options.graph ?? "logquill");
    await this.graph.query(query, { params: parameters });
  }

  override close(): void {
    super.close();
    if (this.owned && this.client) void this.client.close?.();
  }
}

/** Options for {@link NeptuneTransport}. */
export interface NeptuneTransportOptions extends GraphTransportOptions {
  /**
   * Runs one openCypher query against Neptune; `parameters` is already JSON text, as the data API wants
   * it. With the AWS SDK for JavaScript v3:
   *
   * ```ts
   * import { NeptunedataClient, ExecuteOpenCypherQueryCommand } from "@aws-sdk/client-neptunedata";
   * const client = new NeptunedataClient({ endpoint: "https://my-neptune:8182", region: "us-east-1" });
   * new NeptuneTransport({
   *   run: (openCypherQuery, parameters) =>
   *     client.send(new ExecuteOpenCypherQueryCommand({ openCypherQuery, parameters })),
   * });
   * ```
   */
  run: (openCypherQuery: string, parameters: string) => Promise<unknown> | unknown;
}

/** Writes the span tree to Amazon Neptune over its openCypher data API. */
export class NeptuneTransport extends BaseGraphTransport {
  private readonly run: NeptuneTransportOptions["run"];

  constructor(options: NeptuneTransportOptions) {
    super(options);
    this.run = options.run;
  }

  protected override async execute(query: string, parameters: { rows: unknown[] }): Promise<void> {
    await this.run(query, JSON.stringify(parameters));
  }
}
