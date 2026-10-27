import { Level, levelName, parseLevel, type LevelInput } from "../core/levels.js";
import { compact, requireNative } from "../core/native.js";
import type { Logger } from "../core/logger.js";
import type { Plugin } from "../core/plugin.js";
import type { LogRecord } from "../core/records.js";

// Alerting on the Rust core: the de-duplication and the HTTP or SMTP sending happen on a worker thread, so a
// slow webhook or mail server never holds up the event loop. These are alternatives to `SlackAlertPlugin`,
// `PagerDutyAlertPlugin` and `EmailAlertPlugin`, which stay as they are and take a custom `dedupeKey`; the Rust
// ones collapse by `level:logger:message`, or by message alone with `byMessage`. They need the native runtime.

type Addon = ReturnType<typeof requireNative>;
type Sender = ReturnType<Addon["slackSender"]>;

/** Options shared by the Rust alert plugins. */
export interface RustAlertOptions {
  /** A record at or above this level raises an alert. Default `ERROR`. */
  threshold?: LevelInput;
  /** How long a window collapses repeats before one summary with a count is sent. Default 300,000 (5 minutes). */
  dedupeWindowMs?: number;
  /** Distinct errors tracked at once; past this a new one still alerts, but its repeats are not collapsed. Default 500. */
  maxTrackedKeys?: number;
  /** Collapse by message alone instead of level, logger and message. */
  byMessage?: boolean;
}

/** Base for the Rust alert plugins: hands every written record to the core's `Alerting` service. */
export class RustAlertPlugin implements Plugin {
  private readonly alerting: InstanceType<Addon["Alerting"]>;

  protected constructor(addon: Addon, sender: Sender, options: RustAlertOptions) {
    this.alerting = new addon.Alerting(
      sender,
      compact({
        threshold: levelName(parseLevel(options.threshold ?? Level.ERROR)),
        windowSeconds: options.dedupeWindowMs === undefined ? undefined : options.dedupeWindowMs / 1000,
        maxTrackedKeys: options.maxTrackedKeys,
        byMessage: options.byMessage,
      }),
    );
  }

  afterLog(record: LogRecord): void {
    this.alerting.observe(JSON.stringify(record));
  }

  /** Waits for queued alerts to be sent, without blocking the event loop. Returns whether they all were. */
  flush(timeoutMs?: number): Promise<boolean> {
    return this.alerting.flush(timeoutMs);
  }

  /** Sends what is pending and stops. Call it at shutdown. */
  close(): void {
    this.alerting.close();
  }
}

/** Options for {@link RustSlackAlertPlugin}. */
export interface RustSlackAlertOptions extends RustAlertOptions {
  /** A Slack incoming-webhook URL. */
  webhookUrl: string;
  /** Seconds to wait for Slack. Default 5. */
  timeout?: number;
}

/** Posts alerts to a Slack incoming webhook, from Rust. */
export class RustSlackAlertPlugin extends RustAlertPlugin {
  constructor(options: RustSlackAlertOptions) {
    const addon = requireNative("RustSlackAlertPlugin", "Alerting");
    super(addon, addon.slackSender(options.webhookUrl, options.timeout), options);
  }
}

/** Options for {@link RustPagerDutyAlertPlugin}. */
export interface RustPagerDutyAlertOptions extends RustAlertOptions {
  /** The Events API v2 routing (integration) key. */
  routingKey: string;
  /** Seconds to wait for PagerDuty. Default 5. */
  timeout?: number;
  /** Override the Events API endpoint, for a proxy or a test. */
  url?: string;
}

/** Triggers PagerDuty events, from Rust. */
export class RustPagerDutyAlertPlugin extends RustAlertPlugin {
  constructor(options: RustPagerDutyAlertOptions) {
    const addon = requireNative("RustPagerDutyAlertPlugin", "Alerting");
    super(addon, addon.pagerdutySender(options.routingKey, options.timeout, options.url), options);
  }
}

/** Options for {@link RustEmailAlertPlugin}. */
export interface RustEmailAlertOptions extends RustAlertOptions {
  smtpHost: string;
  smtpPort: number;
  fromAddr: string;
  toAddrs: string[];
  username?: string;
  password?: string;
  /** Upgrade the connection with STARTTLS. Default true. */
  useTls?: boolean;
  /** Seconds to wait for the server. Default 10. */
  smtpTimeout?: number;
}

/** Sends alert emails through an SMTP server, from Rust. */
export class RustEmailAlertPlugin extends RustAlertPlugin {
  constructor(options: RustEmailAlertOptions) {
    const addon = requireNative("RustEmailAlertPlugin", "Alerting");
    super(
      addon,
      addon.emailSender({
        smtpHost: options.smtpHost,
        smtpPort: options.smtpPort,
        fromAddr: options.fromAddr,
        toAddrs: options.toAddrs,
        ...compact({
          username: options.username,
          password: options.password,
          useTls: options.useTls,
          timeout: options.smtpTimeout,
        }),
      }),
      options,
    );
  }
}

/** Options for {@link RustLevelWatcher}: exactly one of `file` and `env`. */
export interface RustLevelWatcherOptions {
  /** A file whose trimmed contents are the level (a name in any case, or a weight). A missing file means no change. */
  file?: string;
  /** An environment variable holding the level. */
  env?: string;
  /** Seconds between looks. Default 2. */
  intervalSeconds?: number;
  /** Called with a complaint when the value is not a level. Default: standard error. */
  onInvalid?: (message: string) => void;
}

/**
 * Changes a logger's level while the process runs, with no restart: a Rust thread polls a file or an environment
 * variable and sets the level when it changes to a valid one. An invalid value is reported and ignored. The
 * watcher never keeps the process alive.
 */
export class RustLevelWatcher {
  private readonly watcher: InstanceType<Addon["LevelWatcher"]>;

  constructor(logger: Logger, options: RustLevelWatcherOptions) {
    const addon = requireNative("RustLevelWatcher", "LevelWatcher");
    this.watcher = new addon.LevelWatcher(
      compact({
        file: options.file,
        env: options.env,
        logger: logger.name,
        intervalSeconds: options.intervalSeconds,
      }),
      (level: string) => logger.setLevel(level),
      options.onInvalid,
    );
  }

  /** Looks once, now. */
  pollOnce(): void {
    this.watcher.pollOnce();
  }

  /** Starts polling in the background. */
  start(): this {
    this.watcher.start();
    return this;
  }

  /** Stops polling. Safe to call more than once. */
  stop(): void {
    this.watcher.stop();
  }
}
