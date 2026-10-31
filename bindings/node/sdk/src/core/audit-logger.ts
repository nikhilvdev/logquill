import { PIIRedactPlugin } from "../plugins/pii-redact-plugin.js";
import { RedactPlugin } from "../plugins/redact-plugin.js";
import { TamperEvidentPlugin } from "../plugins/tamper-evident-plugin.js";
import { Logger, type LoggerOptions } from "./logger.js";
import type { Plugin } from "./plugin.js";

/** Options for {@link AuditLogger}: `LoggerOptions`, plus plugins that run after the built-in three. */
export interface AuditLoggerOptions extends Omit<LoggerOptions, "plugins"> {
  /** Run after the redaction and hash-chain plugins, in the order given. */
  extraPlugins?: Plugin[];
}

/**
 * A `Logger` preset for an audit trail: content capture off, secrets and PII redacted, and every record
 * hash-chained so the log can be checked for tampering afterward. Equivalent to
 *
 * ```ts
 * new Logger(name, { contentPolicy: "off", plugins: [new RedactPlugin(), new PIIRedactPlugin(), new TamperEvidentPlugin()] })
 * ```
 *
 * plus {@link AuditLogger.headHash} and {@link AuditLogger.signHead}. A child shares the plugin
 * instances, so it continues the same chain.
 *
 * Like the rest of the plugin pipeline this is a technical control that commonly supports compliance work
 * (audit trails under SOC 2, HIPAA or PCI-DSS expect something like it), never a compliance guarantee.
 */
export class AuditLogger extends Logger {
  private readonly tamper: TamperEvidentPlugin;

  constructor(name: string, options: AuditLoggerOptions = {}, tamper = new TamperEvidentPlugin()) {
    const { extraPlugins, ...rest } = options;
    super(name, {
      ...rest,
      contentPolicy: rest.contentPolicy ?? "off",
      plugins: [new RedactPlugin(), new PIIRedactPlugin(), tamper, ...(extraPlugins ?? [])],
    });
    this.tamper = tamper;
  }

  /** The hash of the last record chained. */
  get headHash(): string {
    return this.tamper.headHash;
  }

  /**
   * HMAC-SHA256 of the current head under `key`. Keep the signature (and the key) somewhere the log's
   * writer cannot edit: it is what shows the end of the log was not cut off.
   */
  signHead(key: string | Uint8Array): string {
    return this.tamper.signHead(key);
  }
}
