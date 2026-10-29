package logquill

import (
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

// FileTransport appends each record as a line to a file. It does not rotate; use the operating system's log rotation
// (logrotate, newsyslog) and call Reopen after it moves the file, or write to a rotating io.Writer through a ConsoleTransport.
type FileTransport struct {
	path string
	mu   sync.Mutex
	file *os.File
}

// NewFileTransport opens (creating if needed) path for appending.
func NewFileTransport(path string) (*FileTransport, error) {
	f := &FileTransport{path: path}
	return f, f.Reopen()
}

// Reopen closes and reopens the file, for after rotation moved it.
func (f *FileTransport) Reopen() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.file != nil {
		_ = f.file.Close()
	}
	file, err := os.OpenFile(f.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	f.file = file
	return err
}

// Write implements Transport.
func (f *FileTransport) Write(_ Level, line []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	_, err := f.file.Write(append(append(make([]byte, 0, len(line)+1), line...), '\n'))
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
