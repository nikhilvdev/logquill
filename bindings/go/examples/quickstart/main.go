package main

import (
	"os"

	logquill "github.com/nikhilvdev/logquill/bindings/go/v3"
)

func main() {
	log, err := logquill.New(
		logquill.WithName("web"),
		logquill.WithPlugins(logquill.Redact(nil, nil, ""), logquill.PII("")),
		logquill.WithTransports(logquill.NewConsoleTransport(os.Stdout, os.Stderr)),
	)
	if err != nil {
		panic(err)
	}
	defer log.Close()

	log.Info("signed in", logquill.Meta{"user": "alice@example.com", "password": "hunter2"})

	db, _ := log.Child("db", logquill.Meta{"component": "pg"})
	defer db.Close()
	db.Warn("slow query", logquill.Meta{"ms": 840})
}
