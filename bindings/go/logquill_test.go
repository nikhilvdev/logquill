package logquill_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	logquill "github.com/nikhilvdev/logquill/bindings/go/v3"
)

// sink collects the lines a logger writes.
type sink struct {
	mu    sync.Mutex
	lines []string
	fail  bool
}

func (s *sink) Write(_ logquill.Level, line []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail {
		return errors.New("disk full")
	}
	s.lines = append(s.lines, string(line))
	return nil
}

func (s *sink) records(t *testing.T) []map[string]any {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]map[string]any, len(s.lines))
	for i, line := range s.lines {
		if err := json.Unmarshal([]byte(line), &out[i]); err != nil {
			t.Fatalf("line %d is not JSON: %v\n%s", i, err, line)
		}
	}
	return out
}

func newLogger(t *testing.T, opts ...logquill.Option) *logquill.Logger {
	t.Helper()
	log, err := logquill.New(opts...)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = log.Close() })
	return log
}

func TestTextHelpersReturnStringsAndKeepNumberSpelling(t *testing.T) {
	got, err := logquill.CanonicalJSON(`{"b":1,"a":2}`)
	if err != nil || got != `{"a": 2, "b": 1}` {
		t.Fatalf("CanonicalJSON = %q, %v", got, err)
	}
	one, _ := logquill.CanonicalJSON(`{"x": 1.0}`)
	two, _ := logquill.CanonicalJSON(`{"x": 1}`)
	if one == two {
		t.Fatalf("1.0 and 1 canonicalise alike: %s", one)
	}
	if masked, _ := logquill.PIIRedactText("mail alice@example.com", ""); masked != "mail ***" {
		t.Fatalf("PIIRedactText = %q", masked)
	}
	meta, err := logquill.RedactMeta(map[string]any{"password": "x", "n": 1}, nil)
	if err != nil || !strings.Contains(string(meta), `"password":"***"`) {
		t.Fatalf("RedactMeta = %s, %v", meta, err)
	}
}

func TestBadCallsAreErrorsWithTheCoresMessage(t *testing.T) {
	cases := map[string]func() error{
		"unknown helper": func() error { _, err := logquill.Call("noSuchHelper"); return err },
		"not valid JSON": func() error { _, err := logquill.CanonicalJSON("not json"); return err },
		"dialect":        func() error { _, err := logquill.Call("createTableSql", "oracle", "t"); return err },
	}
	for want, call := range cases {
		if err := call(); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("want an error containing %q, got %v", want, err)
		}
	}
}

func TestBinaryKeysCrossAsBytes(t *testing.T) {
	key := []byte{0xff, 0x00, 'k'}
	head, _ := logquill.GenesisHash()
	signature, err := logquill.SignHead(head, key)
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := logquill.VerifyHeadSignature(head, key, signature); !ok {
		t.Error("the signature does not verify")
	}
	if ok, _ := logquill.VerifyHeadSignature(head, []byte("other"), signature); ok {
		t.Error("a different key verified")
	}
}

func TestSharedCanonicalJSONCorpus(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "schema", "corpus", "canonical-json.json"))
	if err != nil {
		t.Skipf("schema/corpus is not next to this module: %v", err)
	}
	var corpus struct {
		Cases []struct {
			Name  string `json:"name"`
			Input struct {
				JSON string `json:"json"`
			} `json:"input"`
			Expected struct {
				Text string `json:"text"`
			} `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("no cases")
	}
	for _, c := range corpus.Cases {
		if got, err := logquill.CanonicalJSON(c.Input.JSON); err != nil || got != c.Expected.Text {
			t.Errorf("%s: got %q, %v; want %q", c.Name, got, err, c.Expected.Text)
		}
	}
}

func record(message string) string {
	return `{"schema_version":"2.0","timestamp":"2026-01-01T00:00:00.000Z","level":"INFO","logger":"a","message":"` + message + `","meta":{}}`
}

func TestStampedRecordsVerifyAndAnEditIsFound(t *testing.T) {
	chain, err := logquill.NewChain("")
	if err != nil {
		t.Fatal(err)
	}
	defer chain.Close()
	var records []string
	for _, m := range []string{"a", "b", "c", "d"} {
		stamped, err := chain.Stamp(record(m))
		if err != nil {
			t.Fatal(err)
		}
		records = append(records, stamped)
	}
	verdict, err := logquill.VerifyChain("["+strings.Join(records, ",")+"]", "")
	head, _ := chain.Head()
	if err != nil || !verdict.OK || verdict.RecordsChecked != 4 || verdict.HeadHash == nil || *verdict.HeadHash != head {
		t.Fatalf("verdict = %+v, %v (head %s)", verdict, err, head)
	}
	records[2] = strings.Replace(records[2], `"c"`, `"edited"`, 1)
	verdict, _ = logquill.VerifyChain("["+strings.Join(records, ",")+"]", "")
	if verdict.OK || verdict.BrokenAt == nil || *verdict.BrokenAt != 3 {
		t.Fatalf("an edit was not found: %+v", verdict)
	}
}

func TestASignedHeadCatchesADeletedLastLine(t *testing.T) {
	chain, _ := logquill.NewChain("")
	defer chain.Close()
	var records []string
	for _, m := range []string{"a", "b", "c"} {
		stamped, _ := chain.Stamp(record(m))
		records = append(records, stamped)
	}
	key := []byte("secret")
	signature, err := chain.SignHead(key)
	if err != nil {
		t.Fatal(err)
	}
	full := "[" + strings.Join(records, ",") + "]"
	if v, _ := logquill.VerifySignedChain(full, key, signature, ""); !v.OK {
		t.Errorf("the full chain does not verify: %+v", v)
	}
	if v, _ := logquill.VerifySignedChain("["+strings.Join(records[:2], ",")+"]", key, signature, ""); v.OK {
		t.Error("a deleted last line was not caught")
	}
	if v, _ := logquill.VerifySignedChain(full, []byte("wrong"), signature, ""); v.OK {
		t.Error("the wrong key verified")
	}
}

func TestRedactionHappensBeforeAnythingIsWritten(t *testing.T) {
	s := &sink{}
	log := newLogger(t, logquill.WithPlugins(logquill.Redact(nil, nil, ""), logquill.PII("")), logquill.WithTransports(s))
	if !log.Info("signed in", logquill.Meta{"password": "hunter2", "note": "mail alice@example.com"}) {
		t.Fatal("the record was filtered")
	}
	line := s.lines[0]
	if strings.Contains(line, "hunter2") || strings.Contains(line, "alice@example.com") {
		t.Fatalf("a secret reached the transport: %s", line)
	}
	if meta := s.records(t)[0]["meta"].(map[string]any); meta["password"] != "***" {
		t.Fatalf("meta = %v", meta)
	}
}

func TestTheLevelFiltersAndAChildSharesIt(t *testing.T) {
	log := newLogger(t, logquill.WithLevel(logquill.Warn))
	if log.Info("below") {
		t.Error("info was kept at WARN")
	}
	if !log.Error("above") {
		t.Error("error was filtered")
	}
	child, err := log.Child("db", logquill.Meta{"component": "pg"})
	if err != nil {
		t.Fatal(err)
	}
	defer child.Close()
	if child.Name() != "app.db" {
		t.Errorf("child name = %q", child.Name())
	}
	if err := log.SetLevel(logquill.Error); err != nil {
		t.Fatal(err)
	}
	if child.Warn("filtered") {
		t.Error("the child did not share the new level")
	}
}

func TestAChildAddsFixedMetaAndAnLLMBlockIsCarried(t *testing.T) {
	s := &sink{}
	log := newLogger(t, logquill.WithTransports(s))
	child, _ := log.Child("db", logquill.Meta{"component": "pg"})
	defer child.Close()
	child.Log(logquill.Info, "query", logquill.Meta{"ms": 4}, logquill.Meta{"model": "m", "tokens_in": 3})
	rec := s.records(t)[0]
	meta, llm := rec["meta"].(map[string]any), rec["llm"].(map[string]any)
	if rec["logger"] != "app.db" || meta["component"] != "pg" || meta["ms"] != float64(4) || llm["model"] != "m" || llm["tokens_in"] != float64(3) {
		t.Fatalf("record = %v", rec)
	}
}

func TestPromptContentIsWithheldByDefault(t *testing.T) {
	s := &sink{}
	log := newLogger(t, logquill.WithTransports(s))
	log.Info("call", logquill.Meta{"input_messages": []map[string]string{{"role": "user", "content": "secret prompt"}}})
	if strings.Contains(s.lines[0], "secret prompt") {
		t.Fatalf("content leaked: %s", s.lines[0])
	}
}

func TestAWrongConfigIsAConfigErrorNamingTheProblem(t *testing.T) {
	_, err := logquill.New(logquill.WithContentPolicy("loud"))
	var config *logquill.ConfigError
	if !errors.As(err, &config) || err == nil {
		t.Fatalf("want a *ConfigError, got %v", err)
	}
	if _, err := logquill.New(logquill.WithPlugins(logquill.Plugin{"type": "redactt"})); err == nil || !strings.Contains(err.Error(), "unknown plugin type") {
		t.Errorf("a bad plugin type gave %v", err)
	}
}

func TestValuesJSONCannotCarryNeverMakeALogCallFail(t *testing.T) {
	s := &sink{}
	log := newLogger(t, logquill.WithTransports(s))
	loop := map[string]any{}
	loop["self"] = loop
	ok := log.Info("odd", logquill.Meta{
		"nan": math.NaN(), "inf": math.Inf(-1), "chan": make(chan int), "fn": func() {}, "cycle": loop,
		"bad": string([]byte{0xff, 0xfe}), "nul": "a\x00b",
	})
	if !ok {
		t.Fatal("the record was dropped")
	}
	meta := s.records(t)[0]["meta"].(map[string]any)
	if meta["nan"] != "NaN" || meta["inf"] != "-Infinity" {
		t.Errorf("non-finite floats: %v %v", meta["nan"], meta["inf"])
	}
	for _, key := range []string{"chan", "fn", "cycle", "bad", "nul"} {
		if _, found := meta[key]; !found {
			t.Errorf("%s is missing from %v", key, meta)
		}
	}
}

func TestHostPluginsChangeOrDropRecordsAfterTheCore(t *testing.T) {
	s := &sink{}
	log := newLogger(t,
		logquill.WithPlugins(logquill.Redact(nil, nil, "")),
		logquill.WithHostPlugins(
			func(r map[string]any) map[string]any {
				if r["message"] == "drop me" {
					return nil
				}
				r["meta"].(map[string]any)["added"] = true
				return r
			},
			func(map[string]any) map[string]any { panic("a broken plugin") },
		),
		logquill.WithTransports(s),
	)
	log.Info("drop me")
	log.Info("keep me", logquill.Meta{"password": "p", "n": 1.0})
	records := s.records(t)
	if len(records) != 1 {
		t.Fatalf("records = %v", records)
	}
	meta := records[0]["meta"].(map[string]any)
	if meta["added"] != true || meta["password"] != "***" {
		t.Fatalf("meta = %v", meta)
	}
}

func TestAFailingTransportDoesNotStopTheOthers(t *testing.T) {
	good := &sink{}
	log := newLogger(t, logquill.WithTransports(&sink{fail: true}, good))
	if !log.Info("still logged") || len(good.lines) != 1 {
		t.Fatalf("lines = %v", good.lines)
	}
}

func TestTheTamperChainIsAppliedAsRecordsAreWritten(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.log")
	file, err := logquill.NewFileTransport(path)
	if err != nil {
		t.Fatal(err)
	}
	log := newLogger(t, logquill.WithTamperEvident(), logquill.WithTransports(file))
	for i := 0; i < 10; i++ {
		log.Info("event", logquill.Meta{"i": i})
	}
	_ = log.Close()
	data, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if v, _ := logquill.VerifyChain("["+strings.Join(lines, ",")+"]", ""); !v.OK || v.RecordsChecked != 10 {
		t.Fatalf("verdict = %+v", v)
	}
	lines[4] = strings.Replace(lines[4], `"event"`, `"tampered"`, 1)
	if v, _ := logquill.VerifyChain("["+strings.Join(lines, ",")+"]", ""); v.OK {
		t.Fatal("an edit was not found")
	}
}

func TestManyGoroutinesLogThroughOneLogger(t *testing.T) {
	s := &sink{}
	log := newLogger(t, logquill.WithTamperEvident(), logquill.WithTransports(s))
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				log.Info("busy", logquill.Meta{"g": g, "i": i})
			}
		}()
	}
	wg.Wait()
	if len(s.lines) != 800 {
		t.Fatalf("%d lines written, want 800", len(s.lines))
	}
	if v, _ := logquill.VerifyChain("["+strings.Join(s.lines, ",")+"]", ""); !v.OK {
		t.Fatalf("the chain broke under concurrency: %+v", v)
	}
}

func TestConsoleTransportSendsErrorsToTheErrorStream(t *testing.T) {
	var out, errOut bytes.Buffer
	log := newLogger(t, logquill.WithTransports(logquill.NewConsoleTransport(&out, &errOut)))
	log.Info("fine")
	log.Error("bad")
	if strings.Count(out.String(), "\n") != 1 || strings.Count(errOut.String(), "\n") != 1 || !strings.Contains(errOut.String(), `"bad"`) {
		t.Fatalf("out=%q err=%q", out.String(), errOut.String())
	}
}

func TestClosingTwiceAndUsingAfterCloseAreHarmless(t *testing.T) {
	log, _ := logquill.New()
	if err := log.Close(); err != nil {
		t.Fatal(err)
	}
	if err := log.Close(); err != nil {
		t.Fatal(err)
	}
	if log.Info("after close") {
		t.Error("a closed logger kept a record")
	}
}

// BenchmarkLog measures what one log call costs through WebAssembly: copy the strings in, run the pipeline, copy the record out.
func BenchmarkLog(b *testing.B) {
	log, err := logquill.New(logquill.WithPlugins(logquill.Redact(nil, nil, ""), logquill.PII("")))
	if err != nil {
		b.Fatal(err)
	}
	defer log.Close()
	meta := logquill.Meta{"user_id": 42, "route": "/checkout", "ms": 118, "password": "hunter2"}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		log.Info("checkout completed", meta)
	}
}

// BenchmarkFiltered is the cost of a call the level filters out: the floor.
func BenchmarkFiltered(b *testing.B) {
	log, _ := logquill.New(logquill.WithLevel(logquill.Error))
	defer log.Close()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		log.Debug("dropped", logquill.Meta{"i": i})
	}
}

// BenchmarkHelper is one stateless helper call, for comparison.
func BenchmarkHelper(b *testing.B) {
	for i := 0; i < b.N; i++ {
		if _, err := logquill.CanonicalJSON(`{"b":1,"a":[1,2,3]}`); err != nil {
			b.Fatal(err)
		}
	}
}
