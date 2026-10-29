package logquill

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"strings"
	"sync"
)

var (
	sharedOnce sync.Once
	sharedInst *instance
	sharedErr  error
)

// sharedInstance is the instance behind the package-level helpers. Calls into it are serialised.
func sharedInstance() (*instance, error) {
	sharedOnce.Do(func() { sharedInst, sharedErr = newInstance(context.Background()) })
	return sharedInst, sharedErr
}

// Call runs the stateless helper `name` (named as the Node addon names it: "canonicalJson", "verifyChain", "piiRedactText", ...)
// and returns its result as JSON. A record, meta or list of records may be given as a Go value, a json.RawMessage, or a string
// holding its JSON text: pass text or json.RawMessage when a number's spelling matters (1 and 1.0 hash differently in a
// chain). A []byte argument is a binary key. The error is the core's message.
func Call(name string, args ...any) (json.RawMessage, error) {
	in, err := sharedInstance()
	if err != nil {
		return nil, err
	}
	in.mu.Lock()
	defer in.mu.Unlock()
	return in.callHelper(name, args)
}

func (in *instance) callHelper(name string, args []any) (json.RawMessage, error) {
	wire := make([]string, len(args))
	for i, arg := range args {
		switch value := arg.(type) {
		case []byte:
			wire[i] = quote(base64.StdEncoding.EncodeToString(value)) // binary keys cross as base64
		case json.RawMessage:
			wire[i] = string(value)
		case string:
			wire[i] = quote(value)
		default:
			wire[i] = safeMarshal(arg)
		}
	}
	sc := in.scratch()
	defer sc.release()
	function, err := sc.text(name)
	if err != nil {
		return nil, err
	}
	argument, err := sc.text("[" + strings.Join(wire, ",") + "]")
	if err != nil {
		return nil, err
	}
	slot, err := sc.slot()
	if err != nil {
		return nil, err
	}
	result, err := in.call("lq_call", uint64(function), uint64(argument), uint64(slot))
	if err != nil {
		return nil, err
	}
	if result == 0 {
		return nil, sc.failure(slot, "the call failed")
	}
	text, err := sc.read(uint32(result))
	return json.RawMessage(text), err
}

func quote(text string) string {
	out, _ := json.Marshal(text)
	return string(out)
}

// safeMarshal is json.Marshal that cannot fail: a value JSON cannot carry is replaced by text, never an error. NaN and the
// infinities become "NaN", "Infinity" and "-Infinity"; a channel, function, cycle or anything else unserializable becomes
// "<unserializable T>" (the type, not the value, so a self-referential value cannot recurse).
func safeMarshal(value any) string {
	if out, err := json.Marshal(value); err == nil {
		return string(out)
	}
	return string(salvage(value, 0))
}

func salvage(value any, depth int) json.RawMessage {
	if depth > 20 {
		return json.RawMessage(`"<too deep>"`)
	}
	if out, err := json.Marshal(value); err == nil {
		return out
	}
	rv := reflect.ValueOf(value)
	switch rv.Kind() {
	case reflect.Float32, reflect.Float64:
		f := rv.Float()
		switch {
		case math.IsNaN(f):
			return json.RawMessage(`"NaN"`)
		case math.IsInf(f, 1):
			return json.RawMessage(`"Infinity"`)
		default:
			return json.RawMessage(`"-Infinity"`)
		}
	case reflect.Map:
		if rv.Type().Key().Kind() == reflect.String {
			parts := make([]string, 0, rv.Len())
			for _, key := range rv.MapKeys() {
				parts = append(parts, quote(key.String())+":"+string(salvage(rv.MapIndex(key).Interface(), depth+1)))
			}
			return json.RawMessage("{" + strings.Join(parts, ",") + "}")
		}
	case reflect.Slice, reflect.Array:
		parts := make([]string, rv.Len())
		for i := range parts {
			parts[i] = string(salvage(rv.Index(i).Interface(), depth+1))
		}
		return json.RawMessage("[" + strings.Join(parts, ",") + "]")
	case reflect.Pointer, reflect.Interface:
		if !rv.IsNil() {
			return salvage(rv.Elem().Interface(), depth+1)
		}
	}
	return json.RawMessage(quote(fmt.Sprintf("<unserializable %T>", value)))
}

// CanonicalJSON is the canonical text of a JSON document: what the tamper chain hashes.
func CanonicalJSON(jsonText string) (string, error) { return callString("canonicalJson", jsonText) }

// PIIRedactText returns text with emails, card numbers, phone numbers, SSNs and API keys replaced ("***" if replacement is "").
func PIIRedactText(text, replacement string) (string, error) {
	return callString("piiRedactText", text, nullable(replacement))
}

// RedactMeta returns meta (a value, JSON text or json.RawMessage) with the values of secret-named keys replaced.
func RedactMeta(meta any, options map[string]any) (json.RawMessage, error) {
	if options == nil {
		return Call("redactMeta", meta)
	}
	return Call("redactMeta", meta, options)
}

// Verification is what verifying a hash chain found.
type Verification struct {
	OK             bool    `json:"ok"`
	RecordsChecked int     `json:"recordsChecked"`
	BrokenAt       *int    `json:"brokenAt,omitempty"` // 1-based position of the first record that failed
	Reason         *string `json:"reason,omitempty"`
	HeadHash       *string `json:"headHash,omitempty"`
}

// VerifyChain checks a hash-chained list of records (JSON text of an array, or any list of records). genesis "" is the standard one.
func VerifyChain(records any, genesis string) (Verification, error) {
	return callVerification("verifyChain", records, nullable(genesis))
}

// VerifySignedChain checks a chain and that its head matches signature; it catches a deleted last line.
func VerifySignedChain(records any, key []byte, signature, genesis string) (Verification, error) {
	return callVerification("verifySignedChain", records, key, signature, nullable(genesis))
}

// SignHead is HMAC-SHA256 of a chain head under key, hex-encoded.
func SignHead(headHash string, key []byte) (string, error) {
	return callString("signHead", headHash, key)
}

// VerifyHeadSignature reports whether signature is what SignHead gives (compared in constant time).
func VerifyHeadSignature(headHash string, key []byte, signature string) (bool, error) {
	raw, err := Call("verifyHeadSignature", headHash, key, signature)
	if err != nil {
		return false, err
	}
	var ok bool
	return ok, json.Unmarshal(raw, &ok)
}

// GenesisHash is the hash a chain starts from.
func GenesisHash() (string, error) { return callString("genesisHash") }

// nullable maps "" to nil, which crosses as JSON null (an omitted optional argument).
func nullable(text string) any {
	if text == "" {
		return nil
	}
	return text
}

func callString(name string, args ...any) (string, error) {
	raw, err := Call(name, args...)
	if err != nil {
		return "", err
	}
	var text string
	if err := json.Unmarshal(raw, &text); err != nil {
		return "", errors.New("logquill: " + name + " did not return text")
	}
	return text, nil
}

func callVerification(name string, args ...any) (Verification, error) {
	raw, err := Call(name, args...)
	if err != nil {
		return Verification{}, err
	}
	var v Verification
	return v, json.Unmarshal(raw, &v)
}
