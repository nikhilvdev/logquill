import LogQuill

var options = LoggerOptions()
options.name = "web"
options.plugins = [Plugins.redact(), Plugins.pii()]
options.transports = [Transports.file(path: "web.log")]
let log = try Logger(options)

log.info("signed in", ["user": "alice@example.com", "password": "hunter2"])
try log.child("db", ["component": "pg"]).warn("slow query", ["ms": 840])
log.close()
