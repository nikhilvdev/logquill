require "logquill"

log = Logquill::Logger.new(
  name: "web",
  plugins: [{ type: "redact" }, { type: "pii" }],
  transports: [{ type: "file", path: "web.log" }]
)

log.info("signed in", user: "alice@example.com", password: "hunter2")
log.child("db", component: "pg").warn("slow query", ms: 840)
log.close
