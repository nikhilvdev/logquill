package logquill

import (
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
)

// ConsoleTransport writes each record as a line to out, and ERROR and FATAL records to errOut (os.Stdout and os.Stderr in
// the usual use).
type ConsoleTransport struct {
	out, errOut io.Writer
}

// NewConsoleTransport writes records to out, and Error and Fatal records to errOut. A nil errOut sends everything to out.
func NewConsoleTransport(out, errOut io.Writer) *ConsoleTransport {
	if errOut == nil {
		errOut = out
	}
	return &ConsoleTransport{out: out, errOut: errOut}
}

// Write implements Transport.
func (c *ConsoleTransport) Write(level Level, line []byte) error {
	target := c.out
	if level >= Error {
		target = c.errOut
	}
	_, err := target.Write(append(append(make([]byte, 0, len(line)+1), line...), '\n'))
	return err
}

// FileTransport appends each record as a line to a file. NewFileTransport never rotates (use the operating system's log
// rotation and call Reopen after it moves the file); NewRotatingFileTransport rotates by size the way the Rust file transport
// does for the other bindings.
type FileTransport struct {
	path        string
	maxBytes    int64
	backupCount int
	mu          sync.Mutex
	file        *os.File
	size        int64
}

// NewFileTransport opens (creating if needed) path for appending. It does not rotate.
func NewFileTransport(path string) (*FileTransport, error) {
	f := &FileTransport{path: path}
	return f, f.Reopen()
}

// NewRotatingFileTransport is NewFileTransport that rotates once the file reaches maxBytes: path becomes path.1, the previous
// path.1 becomes path.2, and so on up to backupCount files (0 deletes the file instead of keeping it). maxBytes of 0 never
// rotates. Rotated files are not compressed.
func NewRotatingFileTransport(path string, maxBytes int64, backupCount int) (*FileTransport, error) {
	if maxBytes < 0 || backupCount < 0 {
		return nil, fmt.Errorf("logquill: NewRotatingFileTransport needs maxBytes and backupCount of at least 0, got %d and %d", maxBytes, backupCount)
	}
	f := &FileTransport{path: path, maxBytes: maxBytes, backupCount: backupCount}
	return f, f.Reopen()
}

// Reopen closes and reopens the file, for after rotation moved it.
func (f *FileTransport) Reopen() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.open()
}

func (f *FileTransport) open() error {
	if f.file != nil {
		_ = f.file.Close()
	}
	file, err := os.OpenFile(f.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	f.file = file
	f.size = 0
	if err == nil {
		if info, statErr := file.Stat(); statErr == nil {
			f.size = info.Size()
		}
	}
	return err
}

// rotate moves the live file to path.1 (shifting older ones up) and opens a fresh one. It reopens the file whatever happens, so
// a failed rename never leaves the transport without a file.
func (f *FileTransport) rotate() error {
	_ = f.file.Close()
	f.file = nil
	var failure error
	note := func(err error) {
		if err != nil && failure == nil {
			failure = err
		}
	}
	backup := func(index int) string { return fmt.Sprintf("%s.%d", f.path, index) }
	if f.backupCount > 0 {
		for index := f.backupCount - 1; index >= 1; index-- {
			if _, err := os.Stat(backup(index)); err == nil {
				_ = os.Remove(backup(index + 1))
				note(os.Rename(backup(index), backup(index+1)))
			}
		}
		_ = os.Remove(backup(1))
		note(os.Rename(f.path, backup(1)))
	} else {
		note(os.Remove(f.path))
	}
	note(f.open())
	return failure
}

// Write implements Transport.
func (f *FileTransport) Write(_ Level, line []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.file == nil {
		return errors.New("logquill: the file transport has no open file; call Reopen")
	}
	n, err := f.file.Write(append(append(make([]byte, 0, len(line)+1), line...), '\n'))
	f.size += int64(n)
	if err == nil && f.maxBytes > 0 && f.size >= f.maxBytes {
		err = f.rotate()
	}
	return err
}

// Flush syncs the file to disk.
func (f *FileTransport) Flush() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.file.Sync()
}

// Close closes the file.
func (f *FileTransport) Close() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.file.Close()
}
