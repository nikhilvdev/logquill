package logquill_test

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

// The README's example is a program that is run here, and the README must contain it word for word, so what the README shows
// is code that works.
func TestReadmeContainsTheQuickstartVerbatim(t *testing.T) {
	example, err := os.ReadFile("examples/quickstart/main.go")
	if err != nil {
		t.Fatal(err)
	}
	readme, err := os.ReadFile("README.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(readme), "```go\n"+strings.TrimRight(string(example), "\n")+"\n```") {
		t.Fatal("README.md does not contain examples/quickstart/main.go verbatim")
	}
}

func TestTheQuickstartRunsAndPrintsRedactedRecords(t *testing.T) {
	command := exec.Command("go", "run", "./examples/quickstart")
	output, err := command.Output()
	if err != nil {
		t.Fatalf("go run: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	if len(lines) != 2 {
		t.Fatalf("want 2 lines, got %d:\n%s", len(lines), output)
	}
	for _, secret := range []string{"hunter2", "alice@example.com"} {
		if strings.Contains(string(output), secret) {
			t.Errorf("%q reached the output:\n%s", secret, output)
		}
	}
	if !strings.Contains(lines[0], `"password":"***"`) || !strings.Contains(lines[0], `"user":"***"`) {
		t.Errorf("first line: %s", lines[0])
	}
	if !strings.Contains(lines[1], `"logger":"web.db"`) || !strings.Contains(lines[1], `"ms":840`) || !strings.Contains(lines[1], `"component":"pg"`) {
		t.Errorf("second line: %s", lines[1])
	}
}
