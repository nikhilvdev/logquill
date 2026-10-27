import { randomUUID } from "node:crypto";
import { requireNative } from "../../core/native.js";
import type { LogRecord } from "../../core/records.js";
import { BatchingTransport, type BatchingTransportOptions } from "../batching-transport.js";

/** One record reduced to exactly what the shared statement's `$rows` parameter needs. */
export interface GraphRow {
  /** The node's key: the record's `span_id`, or a fresh unique id for a record without one. */
  key: string;
  props: Record<string, unknown>;
  /** The `Session` node (the run or trace) the record belongs to, if any. */
  session_key: string | null;
  /** The key of the record's parent span, if any. */
  parent_key: string | null;
}

/** Options for every graph transport. */
export interface GraphTransportOptions extends BatchingTransportOptions {
  /** `meta` key naming the run a record belongs to. Default `"run_id"`. */
  runKey?: string;
  /** `meta` key naming the trace, used when there is no run. Default `"trace_id"`. */
  traceKey?: string;
}

/**
 * Shared base for every graph-database sink. Turning a record into a node, `parent_span_id` into a
 * `PARENT_OF` edge and `run_id` / `trace_id` into a `Session` node is the same everywhere, and lives in the
 * Rust core (`graphRow`, `CYPHER`); a backend only runs one parameterized Cypher statement. A Cypher
 * traversal from any root (a node with no incoming `PARENT_OF`) reproduces the span tree that was logged,
 * and switching backends changes which transport is constructed, never a call site.
 *
 * A record with its own `span_id` is keyed by it, so redelivery merges onto the same node. A record
 * without one (an ordinary line) gets a fresh random key: it still becomes a node with its edge, but is
 * not idempotent under a retry. Batched like every other sink: one `UNWIND $rows` statement per flush.
 */
export abstract class BaseGraphTransport extends BatchingTransport<GraphRow> {
  readonly runKey: string;
  readonly traceKey: string;

  constructor(options: GraphTransportOptions = {}) {
    super(options);
    this.runKey = options.runKey ?? "run_id";
    this.traceKey = options.traceKey ?? "trace_id";
  }

  protected override toItem(_formatted: string, record: LogRecord): GraphRow {
    const addon = requireNative("a graph transport");
    const row = JSON.parse(addon.graphRow(JSON.stringify(record), this.runKey, this.traceKey)) as GraphRow;
    if (row.key === addon.LEAF_PLACEHOLDER) row.key = `leaf-${randomUUID().replaceAll("-", "")}`;
    return row;
  }

  protected override sizeOf(item: GraphRow): number {
    return Buffer.byteLength(JSON.stringify(item.props)) + item.key.length + 32;
  }

  protected override async sendBatch(batch: readonly GraphRow[]): Promise<void> {
    const rows = batch.map(({ key, props, session_key, parent_key }) => ({
      key,
      props,
      session_key,
      parent_key,
    }));
    await this.execute(requireNative("a graph transport").CYPHER, { rows });
  }

  /**
   * Runs `query` (always the shared statement) against this backend's driver with `parameters`. Called
   * only with a non-empty batch; a thrown error is reported by the batching base, never to the caller.
   */
  protected abstract execute(query: string, parameters: { rows: unknown[] }): Promise<void> | void;
}
