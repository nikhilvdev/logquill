import {
  closeSync,
  fstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { dirname } from "node:path";
import { gzipSync } from "node:zlib";
import type { Formatter } from "../core/formatter.js";
import { nativeAddon } from "../core/native.js";
import type { LogRecord } from "../core/records.js";
import { fernetEncrypt } from "./fernet.js";
import { Transport } from "./transport.js";

/** Options for {@link FileTransport}. */
export interface FileTransportOptions {
  /** Turns a `LogRecord` into the string this transport writes. Defaults to `JSONFormatter`. */
  formatter?: Formatter;
  /** Rotate once the file reaches this many bytes. `0` disables rotation. Default 10MB. */
  maxBytes?: number;
  /** How many rotated backups (`.1`, `.2`, ...) to keep. The oldest is deleted once exceeded. Default 5. */
  backupCount?: number;
  /**
   * Encrypts each line (a Fernet token, same format as `logquill-python`'s `encrypt_key`)
   * before it is written. Generate one with `generateFernetKey()`; read lines back with
   * `fernetDecrypt()`. A local log file usually isn't encrypted at rest the way cloud sinks are.
   */
  encryptKey?: string;
  /** Gzips each file as it is rotated out, so backups are `app.log.1.gz`, `app.log.2.gz`, ... Default: backups are left uncompressed. */
  compression?: "gz";
}

/** Appends formatted records to a file, rotating when it exceeds `maxBytes`. */
export class FileTransport extends Transport {
  /** Path of the file records are appended to. */
  readonly path: string;
  /** File is rotated once it reaches this many bytes. `0` disables rotation. */
  readonly maxBytes: number;
  /** How many rotated backups (`.1`, `.2`, ...) are kept. */
  readonly backupCount: number;
  private readonly encryptKey: string | undefined;
  private readonly backupSuffix: string;
  private fd: number;

  constructor(path: string, options: FileTransportOptions = {}) {
    super(options.formatter);
    this.path = path;
    this.maxBytes = options.maxBytes ?? 10 * 1024 * 1024;
    this.backupCount = options.backupCount ?? 5;
    this.encryptKey = options.encryptKey;
    this.backupSuffix = options.compression === "gz" ? ".gz" : "";
    if (this.encryptKey !== undefined) {
      fernetEncrypt(this.encryptKey, ""); // fail on a bad key now, not on the first log call
    }
    mkdirSync(dirname(path), { recursive: true });
    // The Rust file transport does the same job (rotation, gzip, Fernet) on its own thread. It
    // formats with the core's JSON formatter, so a custom formatter keeps the JavaScript path.
    const addon = options.formatter === undefined ? nativeAddon() : null;
    // (the WebAssembly build has no file transport, so it keeps the JavaScript one too)
    if (addon?.fileTransport) {
      this.nativeHandle = addon.fileTransport(path, {
        maxBytes: this.maxBytes,
        backupCount: this.backupCount,
        ...(options.compression === undefined ? {} : { compression: options.compression }),
        ...(this.encryptKey === undefined ? {} : { encryptKey: this.encryptKey }),
      });
      this.fd = -1;
    } else {
      this.fd = openSync(this.path, "a");
    }
  }

  /** Appends one formatted line to the file, rotating first if `maxBytes` has been exceeded. */
  write(formatted: string, _record?: LogRecord): void {
    if (this.nativeHandle) {
      this.nativeHandle.write(formatted);
      return;
    }
    writeSync(
      this.fd,
      (this.encryptKey === undefined ? formatted : fernetEncrypt(this.encryptKey, formatted)) +
        "\n",
    );
    if (this.maxBytes > 0 && fstatSync(this.fd).size >= this.maxBytes) {
      this.rotate();
    }
  }

  private rotate(): void {
    closeSync(this.fd);

    if (this.backupCount > 0) {
      for (let index = this.backupCount - 1; index > 0; index--) {
        const src = `${this.path}.${String(index)}${this.backupSuffix}`;
        const dst = `${this.path}.${String(index + 1)}${this.backupSuffix}`;
        // rename replaces an existing destination, and a missing source is simply skipped:
        // no exists-then-act step for another process to race.
        try {
          renameSync(src, dst);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
      }
      const backup = `${this.path}.1${this.backupSuffix}`;
      if (this.backupSuffix === "") {
        renameSync(this.path, backup);
      } else {
        // shortcut: gzips synchronously on rotation (at most maxBytes), upgrade to streaming if rotation stalls dispatch
        writeFileSync(backup, gzipSync(readFileSync(this.path)));
        unlinkSync(this.path);
      }
    } else {
      unlinkSync(this.path);
    }

    this.fd = openSync(this.path, "a");
  }

  override close(): void {
    if (this.nativeHandle) {
      this.nativeHandle.close();
      return;
    }
    closeSync(this.fd);
  }
}
