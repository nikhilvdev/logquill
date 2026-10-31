// Package logquill is structured logging for Go on LogQuill's Rust core: the same record shape, redaction and tamper-evident
// hash chain as the Python, Node, browser and other bindings. The core runs as WebAssembly inside the process (wazero, no cgo),
// so there is nothing to install besides the module. Redaction and the level filter run in the core before anything is written.
//
//	log, _ := logquill.New(
//		logquill.WithName("web"),
//		logquill.WithPlugins(logquill.Redact(nil, nil, ""), logquill.PII("")),
//		logquill.WithTransports(logquill.NewConsoleTransport(os.Stdout, os.Stderr)),
//	)
//	defer log.Close()
//	log.Info("signed in", logquill.Meta{"user": "alice@example.com", "password": "hunter2"})
package logquill

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
)

// Level is a log level, in order of severity.
type Level int

// The levels.
const (
	Trace Level = iota
	Debug
	Info
	Warn
	Error
	Fatal
)

func (l Level) String() string {
	names := [...]string{"trace", "debug", "info", "warn", "error", "fatal"}
	if l < 0 || int(l) >= len(names) {
		return fmt.Sprintf("level(%d)", int(l))
	}
	return names[l]
}

// Meta is a record's structured fields. Values JSON cannot carry (NaN, a cycle, a channel) are turned into text, not errors.
type Meta map[string]any

// Plugin is a plugin that runs in the core, in order: Redact or PII.
type Plugin map[string]any

// Redact masks the values of keys (case-insensitively, at every depth). nil keys means password, token, secret, api_key and
// authorization; classes (secret, pii, content) add the well-known keys of those classes; replacement "" means "***".
func Redact(keys, classes []string, replacement string) Plugin {
	p := Plugin{"type": "redact"}
	if keys != nil {
		p["keys"] = keys
	}
	if classes != nil {
		p["classes"] = classes
	}
	if replacement != "" {
		p["replacement"] = replacement
	}
	return p
}

// PII masks emails, card numbers, phone numbers, SSNs and API keys inside any string of the record's meta ("***" if replacement is "").
func PII(replacement string) Plugin {
	p := Plugin{"type": "pii"}
	if replacement != "" {
		p["replacement"] = replacement
	}
	return p
}

// Sampling keeps about rate (0 to 1) of the records, chosen at random. Tail sampling is not available through the core's C ABI yet.
func Sampling(rate float64) Plugin {
	return Plugin{"type": "sampling", "rate": rate}
}

// RateLimit drops records once more than maxRecords share a key inside perSeconds. key is "logger_level" (the default, also
// used for ""), "logger" or "message".
func RateLimit(maxRecords int, perSeconds float64, key string) Plugin {
	p := Plugin{"type": "rate_limit", "max_records": maxRecords, "per_seconds": perSeconds}
	if key != "" {
		p["key"] = key
	}
	return p
}

// AdaptiveOptions are the settings of AdaptiveSampling; a zero field keeps the core's default (base rate 0.1, minimum 0.01,
// maximum 1, keep at ERROR, slow at 1000 ms, 100000 bytes per second, a 1 second window).
type AdaptiveOptions struct {
	BaseRate, MinRate, MaxRate float64
	KeepAt                     string
	SlowMs, WindowSeconds      float64
	MaxBytesPerSecond          uint64
}

// AdaptiveSampling keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes written per
// window.
func AdaptiveSampling(o AdaptiveOptions) Plugin {
	p := Plugin{"type": "adaptive_sampling"}
	for k, v := range map[string]float64{"base_rate": o.BaseRate, "min_rate": o.MinRate, "max_rate": o.MaxRate, "slow_ms": o.SlowMs, "window_seconds": o.WindowSeconds} {
		if v != 0 {
			p[k] = v
		}
	}
	if o.KeepAt != "" {
		p["keep_at"] = o.KeepAt
	}
	if o.MaxBytesPerSecond != 0 {
		p["max_bytes_per_second"] = o.MaxBytesPerSecond
	}
	return p
}

// HostPlugin is a plugin written in Go. It sees the record after the core's pipeline (so after redaction), may change it, and
// returns nil to drop it. It runs before the Go transports. Because the record is re-encoded, its keys come out sorted.
type HostPlugin func(record map[string]any) map[string]any

// Transport receives the finished record as a JSON line (no trailing newline), after the core's pipeline and any host plugins.
// Write is called under the logger's write lock, so records arrive one at a time and in chain order. A Transport may also
// have Flush() error and Close() error methods; they are called by Logger.Flush and when the last logger of a group closes.
type Transport interface {
	Write(level Level, line []byte) error
}

// ConfigError is returned by New for a config the core rejects; the message says which option and what is allowed.
type ConfigError struct{ Message string }

func (e *ConfigError) Error() string { return e.Message }

type options struct {
	name          string
	level         Level
	plugins       []Plugin
	contentPolicy string
	tamper        bool
	transports    []Transport
	host          []HostPlugin
}

// Option configures New.
type Option func(*options)

// WithName sets the logger's name (default "app").
func WithName(name string) Option { return func(o *options) { o.name = name } }

// WithLevel sets the lowest level that is kept (default Info).
func WithLevel(level Level) Option { return func(o *options) { o.level = level } }

// WithPlugins adds core plugins, run in order.
func WithPlugins(plugins ...Plugin) Option {
	return func(o *options) { o.plugins = append(o.plugins, plugins...) }
}

// WithContentPolicy sets what happens to prompt, completion and tool content: "off" (the default: withheld), "hash", "truncate" or "full".
func WithContentPolicy(policy string) Option { return func(o *options) { o.contentPolicy = policy } }

// WithTamperEvident chains each record as it is written, so an edited or removed line can be found (VerifyChain).
func WithTamperEvident() Option { return func(o *options) { o.tamper = true } }

// WithTransports adds the transports records are written to.
func WithTransports(transports ...Transport) Option {
	return func(o *options) { o.transports = append(o.transports, transports...) }
}

// WithHostPlugins adds Go plugins that run after the core's pipeline.
func WithHostPlugins(plugins ...HostPlugin) Option {
	return func(o *options) { o.host = append(o.host, plugins...) }
}

// shared is what a logger and its children share.
type shared struct {
	in         *instance
	transports []Transport
	host       []HostPlugin
	chain      *Chain
	writeMu    sync.Mutex
	refs       atomic.Int32
}

// Logger logs through the core. It is safe for use by many goroutines. Close it when done; children keep it alive until they
// are closed too.
type Logger struct {
	sh     *shared
	handle uint32
	name   string
	closed atomic.Bool
}

// New builds a logger. It returns a *ConfigError for a config the core rejects; an unknown option value is an error, not
// ignored, so a typo cannot quietly leave a secret in the log.
func New(opts ...Option) (*Logger, error) {
	o := options{name: "app", level: Info}
	for _, apply := range opts {
		apply(&o)
	}
	config := map[string]any{"name": o.name, "level": o.level.String(), "plugins": o.plugins}
	if o.plugins == nil {
		config["plugins"] = []Plugin{}
	}
	if o.contentPolicy != "" {
		config["content_policy"] = o.contentPolicy
	}
	in, err := newInstance(context.Background())
	if err != nil {
		return nil, err
	}
	sc := in.scratch()
	defer sc.release()
	configPtr, err := sc.text(safeMarshal(config))
	if err != nil {
		_ = in.close()
		return nil, err
	}
	slot, err := sc.slot()
	if err != nil {
		_ = in.close()
		return nil, err
	}
	handle, err := in.call("lq_logger_new", uint64(configPtr), uint64(slot))
	if err != nil {
		_ = in.close()
		return nil, err
	}
	if handle == 0 {
		message := sc.failure(slot, "the logger could not be created")
		_ = in.close()
		return nil, &ConfigError{Message: message.Error()}
	}
	sh := &shared{in: in, transports: o.transports, host: o.host}
	sh.refs.Store(1)
	if o.tamper {
		if sh.chain, err = NewChain(""); err != nil {
			_, _ = in.call("lq_logger_free", handle)
			_ = in.close()
			return nil, err
		}
	}
	return &Logger{sh: sh, handle: uint32(handle), name: o.name}, nil
}

// Name is the logger's name ("web", "web.db", ...).
func (l *Logger) Name() string { return l.name }

// Child returns a logger named "<name>.<suffix>" with fixed on every record, sharing this logger's level, plugins and transports.
func (l *Logger) Child(suffix string, fixed Meta) (*Logger, error) {
	in := l.sh.in
	in.mu.Lock()
	defer in.mu.Unlock()
	sc := in.scratch()
	defer sc.release()
	suffixPtr, err := sc.text(suffix)
	if err != nil {
		return nil, err
	}
	var fixedPtr uint32
	if len(fixed) > 0 {
		if fixedPtr, err = sc.text(safeMarshal(fixed)); err != nil {
			return nil, err
		}
	}
	slot, err := sc.slot()
	if err != nil {
		return nil, err
	}
	handle, err := in.call("lq_logger_child", uint64(l.handle), uint64(suffixPtr), uint64(fixedPtr), uint64(slot))
	if err != nil {
		return nil, err
	}
	if handle == 0 {
		return nil, sc.failure(slot, "the child could not be created")
	}
	l.sh.refs.Add(1)
	return &Logger{sh: l.sh, handle: uint32(handle), name: l.name + "." + suffix}, nil
}

// Trace logs at Trace. It returns whether the record was kept (false: filtered). Like every log method it never panics or
// returns an error: a failure is written to standard error.
func (l *Logger) Trace(message string, meta ...Meta) bool {
	return l.Log(Trace, message, merge(meta), nil)
}

// Debug logs at Debug.
func (l *Logger) Debug(message string, meta ...Meta) bool {
	return l.Log(Debug, message, merge(meta), nil)
}

// Info logs at Info.
func (l *Logger) Info(message string, meta ...Meta) bool {
	return l.Log(Info, message, merge(meta), nil)
}

// Warn logs at Warn.
func (l *Logger) Warn(message string, meta ...Meta) bool {
	return l.Log(Warn, message, merge(meta), nil)
}

// Error logs at Error.
func (l *Logger) Error(message string, meta ...Meta) bool {
	return l.Log(Error, message, merge(meta), nil)
}

// Fatal logs at Fatal.
func (l *Logger) Fatal(message string, meta ...Meta) bool {
	return l.Log(Fatal, message, merge(meta), nil)
}

func merge(parts []Meta) Meta {
	if len(parts) == 1 {
		return parts[0]
	}
	out := Meta{}
	for _, part := range parts {
		for k, v := range part {
			out[k] = v
		}
	}
	return out
}

// Log is the general call: meta is the record's fields, llm an LLM-call block (model, tokens_in, tokens_out, cost_usd, ...).
func (l *Logger) Log(level Level, message string, meta Meta, llm Meta) bool {
	line, kept := l.build(level, message, meta, llm)
	if !kept {
		return false
	}
	l.sh.write(level, line)
	return true
}

func clean(text string) string {
	text = strings.ToValidUTF8(text, "�")
	if strings.ContainsRune(text, 0) {
		text = strings.ReplaceAll(text, "\x00", "␀")
	}
	return text
}

func report(format string, args ...any) { fmt.Fprintf(os.Stderr, "logquill: "+format+"\n", args...) }

// build runs the core's pipeline and returns the finished record as a JSON line.
func (l *Logger) build(level Level, message string, meta, llm Meta) (string, bool) {
	if l.closed.Load() {
		return "", false
	}
	in := l.sh.in
	in.mu.Lock()
	defer in.mu.Unlock()
	sc := in.scratch()
	defer sc.release()
	levelPtr, err := sc.text(level.String())
	if err != nil {
		report("%v", err)
		return "", false
	}
	messagePtr, err := sc.text(clean(message))
	if err != nil {
		report("%v", err)
		return "", false
	}
	var metaPtr, llmPtr uint32
	if len(meta) > 0 {
		if metaPtr, err = sc.text(safeMarshal(meta)); err != nil {
			report("%v", err)
			return "", false
		}
	}
	if len(llm) > 0 {
		if llmPtr, err = sc.text(safeMarshal(llm)); err != nil {
			report("%v", err)
			return "", false
		}
	}
	recordSlot, err := sc.slot()
	if err != nil {
		report("%v", err)
		return "", false
	}
	errorSlot, err := sc.slot()
	if err != nil {
		report("%v", err)
		return "", false
	}
	result, err := in.call("lq_log", uint64(l.handle), uint64(levelPtr), 0, uint64(messagePtr), uint64(metaPtr), uint64(llmPtr), uint64(recordSlot), uint64(errorSlot))
	if err != nil {
		report("%v", err)
		return "", false
	}
	switch code := int32(result); {
	case code < 0:
		report("%v", sc.failure(errorSlot, "the log call failed"))
		return "", false
	case code == 0:
		return "", false
	}
	ptr, _ := in.mem.ReadUint32Le(recordSlot)
	if ptr == 0 {
		return "", false
	}
	line, err := sc.read(ptr)
	if err != nil {
		report("%v", err)
		return "", false
	}
	return line, true
}

// write runs the host plugins, chains the record if asked, and hands it to the transports, one record at a time.
func (s *shared) write(level Level, line string) {
	if len(s.host) > 0 {
		var record map[string]any
		decoder := json.NewDecoder(strings.NewReader(line))
		decoder.UseNumber() // keep each number's own spelling
		if err := decoder.Decode(&record); err != nil {
			report("could not read back a record for the host plugins: %v", err)
			return
		}
		for _, plugin := range s.host {
			var next map[string]any
			func() {
				defer func() {
					if r := recover(); r != nil {
						report("a host plugin panicked and was skipped: %v", r)
						next = record
					}
				}()
				next = plugin(record)
			}()
			if next == nil {
				return
			}
			record = next
		}
		var buffer bytes.Buffer
		encoder := json.NewEncoder(&buffer)
		encoder.SetEscapeHTML(false)
		if err := encoder.Encode(record); err != nil {
			report("a host plugin left a record that cannot be encoded: %v", err)
			return
		}
		line = strings.TrimRight(buffer.String(), "\n")
	}
	if len(s.transports) == 0 && s.chain == nil {
		return
	}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	if s.chain != nil {
		stamped, err := s.chain.Stamp(line)
		if err != nil {
			report("could not chain a record: %v", err)
		} else {
			line = stamped
		}
	}
	for _, transport := range s.transports {
		func() {
			defer func() {
				if r := recover(); r != nil {
					report("%T panicked while writing a record: %v", transport, r)
				}
			}()
			if err := transport.Write(level, []byte(line)); err != nil {
				report("%T failed to write a record: %v", transport, err)
			}
		}()
	}
}

// SetLevel changes the level for this logger, its parent and its children.
func (l *Logger) SetLevel(level Level) error {
	in := l.sh.in
	in.mu.Lock()
	defer in.mu.Unlock()
	sc := in.scratch()
	defer sc.release()
	levelPtr, err := sc.text(level.String())
	if err != nil {
		return err
	}
	slot, err := sc.slot()
	if err != nil {
		return err
	}
	result, err := in.call("lq_logger_set_level", uint64(l.handle), uint64(levelPtr), uint64(slot))
	if err != nil {
		return err
	}
	if int32(result) < 0 {
		return sc.failure(slot, "the level could not be set")
	}
	return nil
}

// Flush asks every transport that can flush to do so.
func (l *Logger) Flush() error {
	l.sh.writeMu.Lock()
	defer l.sh.writeMu.Unlock()
	var errs []error
	for _, transport := range l.sh.transports {
		if f, ok := transport.(interface{ Flush() error }); ok {
			errs = append(errs, f.Flush())
		}
	}
	return errors.Join(errs...)
}

// Close releases this logger. When the last logger of a group (a logger and its children) is closed, the transports are
// flushed and closed and the core's memory is freed. It is safe to call twice.
func (l *Logger) Close() error {
	if !l.closed.CompareAndSwap(false, true) {
		return nil
	}
	sh := l.sh
	sh.in.mu.Lock()
	_, _ = sh.in.call("lq_logger_free", uint64(l.handle))
	sh.in.mu.Unlock()
	if sh.refs.Add(-1) != 0 {
		return nil
	}
	var errs []error
	sh.writeMu.Lock()
	for _, transport := range sh.transports {
		if f, ok := transport.(interface{ Flush() error }); ok {
			errs = append(errs, f.Flush())
		}
		if c, ok := transport.(interface{ Close() error }); ok {
			errs = append(errs, c.Close())
		}
	}
	sh.writeMu.Unlock()
	if sh.chain != nil {
		errs = append(errs, sh.chain.Close())
	}
	errs = append(errs, sh.in.close())
	return errors.Join(errs...)
}
