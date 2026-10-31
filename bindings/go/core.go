package logquill

import (
	"context"
	"crypto/rand"
	_ "embed" // core.wasm
	"errors"
	"fmt"
	"os"
	"sync"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
	"github.com/tetratelabs/wazero/imports/wasi_snapshot_preview1"
)

// coreWasm is crates/logquill-ffi built for wasm32-wasip1 without the runtime feature (no threads, files or sockets):
// the pipeline, the redactors, the hash chain and the helpers. `scripts/build-core.sh` produces it.
//
//go:embed core.wasm
var coreWasm []byte

// abiVersion is the C ABI version this package was written against.
const abiVersion = 1

var (
	runtimeOnce sync.Once
	theRuntime  wazero.Runtime
	theModule   wazero.CompiledModule
	runtimeErr  error
)

// compiled returns the process-wide runtime and the compiled core, compiling on first use. Each Logger and Chain
// instantiates its own copy from it, so loggers do not wait on each other.
func compiled(ctx context.Context) (wazero.Runtime, wazero.CompiledModule, error) {
	runtimeOnce.Do(func() {
		theRuntime = wazero.NewRuntime(ctx)
		if _, err := wasi_snapshot_preview1.Instantiate(ctx, theRuntime); err != nil {
			runtimeErr = fmt.Errorf("logquill: could not set up WASI: %w", err)
			return
		}
		theModule, runtimeErr = theRuntime.CompileModule(ctx, coreWasm)
		if runtimeErr != nil {
			runtimeErr = fmt.Errorf("logquill: could not compile the core (is core.wasm the right build? run scripts/build-core.sh): %w", runtimeErr)
		}
	})
	return theRuntime, theModule, runtimeErr
}

// instance is one instantiation of the core: its own memory, and so its own loggers and chains. Calls into an instance are
// serialised by mu, because a WebAssembly module is single-threaded.
type instance struct {
	mu  sync.Mutex
	ctx context.Context
	mod api.Module
	mem api.Memory
	fns map[string]api.Function
}

var exported = []string{
	"lq_version", "lq_abi_version", "lq_string_free", "lq_buffer_alloc", "lq_buffer_free", "lq_call",
	"lq_chain_new", "lq_chain_stamp", "lq_chain_head", "lq_chain_sign_head", "lq_chain_free",
	"lq_logger_new", "lq_logger_child", "lq_log", "lq_logger_name", "lq_logger_set_level", "lq_logger_flush", "lq_logger_close",
	"lq_logger_free",
}

func newInstance(ctx context.Context) (*instance, error) {
	r, m, err := compiled(ctx)
	if err != nil {
		return nil, err
	}
	config := wazero.NewModuleConfig().
		WithName(""). // anonymous, so any number can coexist
		WithSysWalltime().
		WithSysNanotime().
		WithRandSource(rand.Reader).
		WithStderr(os.Stderr)
	if m.ExportedFunctions()["_initialize"] != nil {
		config = config.WithStartFunctions("_initialize")
	} else {
		config = config.WithStartFunctions()
	}
	mod, err := r.InstantiateModule(ctx, m, config)
	if err != nil {
		return nil, fmt.Errorf("logquill: could not start the core: %w", err)
	}
	in := &instance{ctx: ctx, mod: mod, mem: mod.Memory(), fns: make(map[string]api.Function, len(exported))}
	for _, name := range exported {
		fn := mod.ExportedFunction(name)
		if fn == nil {
			_ = mod.Close(ctx)
			return nil, fmt.Errorf("logquill: core.wasm does not export %s (rebuild it with scripts/build-core.sh)", name)
		}
		in.fns[name] = fn
	}
	if got, _ := in.call("lq_abi_version"); uint32(got) != abiVersion {
		_ = mod.Close(ctx)
		return nil, fmt.Errorf("logquill: core.wasm speaks C ABI %d, this package needs %d", uint32(got), abiVersion)
	}
	return in, nil
}

func (in *instance) close() error { return in.mod.Close(in.ctx) }

// call invokes an export. The caller holds in.mu (or has the instance to itself).
func (in *instance) call(name string, args ...uint64) (uint64, error) {
	results, err := in.fns[name].Call(in.ctx, args...)
	if err != nil {
		return 0, fmt.Errorf("logquill: %s trapped: %w", name, err)
	}
	if len(results) == 0 {
		return 0, nil
	}
	return results[0], nil
}

// scratch tracks the buffers one call allocated in the module's memory, so they are freed together.
type scratch struct {
	in   *instance
	bufs [][2]uint32 // pointer, length
}

func (in *instance) scratch() *scratch { return &scratch{in: in} }

// text copies value (plus a NUL) into the module and returns its pointer.
func (s *scratch) text(value string) (uint32, error) {
	size := uint32(len(value)) + 1
	ptr, err := s.in.call("lq_buffer_alloc", uint64(size))
	if err != nil {
		return 0, err
	}
	if ptr == 0 {
		return 0, errors.New("logquill: the core is out of memory")
	}
	s.bufs = append(s.bufs, [2]uint32{uint32(ptr), size})
	data := make([]byte, size)
	copy(data, value)
	if !s.in.mem.Write(uint32(ptr), data) {
		return 0, errors.New("logquill: could not write into the core's memory")
	}
	return uint32(ptr), nil
}

// optText is text, or 0 (NULL) for nil.
func (s *scratch) optText(value *string) (uint32, error) {
	if value == nil {
		return 0, nil
	}
	return s.text(*value)
}

// slot reserves a 4-byte out-parameter (a `char *` in a 32-bit module), zeroed.
func (s *scratch) slot() (uint32, error) {
	ptr, err := s.in.call("lq_buffer_alloc", 4)
	if err != nil {
		return 0, err
	}
	s.bufs = append(s.bufs, [2]uint32{uint32(ptr), 4})
	s.in.mem.WriteUint32Le(uint32(ptr), 0)
	return uint32(ptr), nil
}

// bytes copies b into the module and returns its pointer.
func (s *scratch) bytes(b []byte) (uint32, error) {
	size := uint32(len(b))
	ptr, err := s.in.call("lq_buffer_alloc", uint64(size))
	if err != nil {
		return 0, err
	}
	s.bufs = append(s.bufs, [2]uint32{uint32(ptr), size})
	if size > 0 && !s.in.mem.Write(uint32(ptr), b) {
		return 0, errors.New("logquill: could not write into the core's memory")
	}
	return uint32(ptr), nil
}

// read returns the C string at ptr and frees it with lq_string_free.
func (s *scratch) read(ptr uint32) (string, error) {
	defer func() { _, _ = s.in.call("lq_string_free", uint64(ptr)) }()
	var out []byte
	for offset := ptr; ; {
		chunk := uint32(256)
		if size := s.in.mem.Size(); offset+chunk > size {
			chunk = size - offset
		}
		data, ok := s.in.mem.Read(offset, chunk)
		if !ok || chunk == 0 {
			return "", errors.New("logquill: an unterminated string came back from the core")
		}
		for i, b := range data {
			if b == 0 {
				return string(append(out, data[:i]...)), nil
			}
		}
		out = append(out, data...)
		offset += chunk
	}
}

// failure turns an error slot into an error: the message the core left in it.
func (s *scratch) failure(slot uint32, fallback string) error {
	ptr, _ := s.in.mem.ReadUint32Le(slot)
	if ptr == 0 {
		return errors.New(fallback)
	}
	message, err := s.read(ptr)
	if err != nil {
		return err
	}
	return errors.New(message)
}

func (s *scratch) release() {
	for _, b := range s.bufs {
		_, _ = s.in.call("lq_buffer_free", uint64(b[0]), uint64(b[1]))
	}
	s.bufs = nil
}

// CoreVersion is the version of the Rust core embedded in this module (core.wasm), for example "3.0.0-rc.3". A release checks it
// against the module's own version, so a core.wasm built from older sources cannot be tagged by mistake.
func CoreVersion() (string, error) {
	in, err := newInstance(context.Background())
	if err != nil {
		return "", err
	}
	defer func() { _ = in.close() }()
	ptr, err := in.call("lq_version")
	if err != nil {
		return "", err
	}
	var version []byte
	for offset := uint32(ptr); ; offset++ {
		b, ok := in.mem.ReadByte(offset)
		if !ok {
			return "", errors.New("logquill: the core's version string runs past the end of its memory")
		}
		if b == 0 {
			return string(version), nil
		}
		version = append(version, b)
	}
}
