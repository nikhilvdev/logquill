package logquill

import (
	"context"
	"errors"
)

// Chain is a hash chain: each record stamped through it carries meta.prev_hash and meta.hash, so a log can be checked later for an
// edited or removed line (VerifyChain). Safe to share between goroutines. Close it to release its memory.
type Chain struct {
	in     *instance
	handle uint32
}

// NewChain starts a chain from genesis ("" is the standard genesis hash).
func NewChain(genesis string) (*Chain, error) {
	in, err := newInstance(context.Background())
	if err != nil {
		return nil, err
	}
	sc := in.scratch()
	defer sc.release()
	var g *string
	if genesis != "" {
		g = &genesis
	}
	genesisPtr, err := sc.optText(g)
	if err != nil {
		_ = in.close()
		return nil, err
	}
	slot, err := sc.slot()
	if err != nil {
		_ = in.close()
		return nil, err
	}
	handle, err := in.call("lq_chain_new", uint64(genesisPtr), uint64(slot))
	if err != nil || handle == 0 {
		if err == nil {
			err = sc.failure(slot, "the chain could not be created")
		}
		_ = in.close()
		return nil, err
	}
	return &Chain{in: in, handle: uint32(handle)}, nil
}

func (c *Chain) text(name string, record *string, key []byte) (string, error) {
	c.in.mu.Lock()
	defer c.in.mu.Unlock()
	if c.handle == 0 {
		return "", errors.New("logquill: the chain is closed")
	}
	sc := c.in.scratch()
	defer sc.release()
	slot, err := sc.slot()
	if err != nil {
		return "", err
	}
	var result uint64
	switch name {
	case "lq_chain_stamp":
		ptr, perr := sc.text(*record)
		if perr != nil {
			return "", perr
		}
		result, err = c.in.call(name, uint64(c.handle), uint64(ptr), uint64(slot))
	case "lq_chain_head":
		result, err = c.in.call(name, uint64(c.handle), uint64(slot))
	default: // lq_chain_sign_head
		ptr, perr := sc.bytes(key)
		if perr != nil {
			return "", perr
		}
		result, err = c.in.call(name, uint64(c.handle), uint64(ptr), uint64(len(key)), uint64(slot))
	}
	if err != nil {
		return "", err
	}
	if result == 0 {
		return "", sc.failure(slot, "the call failed")
	}
	return sc.read(uint32(result))
}

// Stamp chains record (a record as JSON text) now and returns it, as JSON text, with the hashes set.
func (c *Chain) Stamp(record string) (string, error) { return c.text("lq_chain_stamp", &record, nil) }

// Head is the hash of the last record chained.
func (c *Chain) Head() (string, error) { return c.text("lq_chain_head", nil, nil) }

// SignHead is HMAC-SHA256 of the head under key, hex-encoded: a signature that a deleted last line breaks.
func (c *Chain) SignHead(key []byte) (string, error) { return c.text("lq_chain_sign_head", nil, key) }

// Close frees the chain. It is safe to call twice.
func (c *Chain) Close() error {
	c.in.mu.Lock()
	defer c.in.mu.Unlock()
	if c.handle == 0 {
		return nil
	}
	_, _ = c.in.call("lq_chain_free", uint64(c.handle))
	c.handle = 0
	return c.in.close()
}
