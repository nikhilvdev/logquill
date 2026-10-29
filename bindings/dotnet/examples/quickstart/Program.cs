using LogQuill;

using var log = new Logger(new LoggerOptions
{
    Name = "web",
    Plugins = { PluginConfig.Redact(), PluginConfig.Pii() },
    Transports = { TransportConfig.File("web.log") },
});

log.Info("signed in", new { User = "alice@example.com", Password = "hunter2" });
log.Child("db", new { Component = "pg" }).Warn("slow query", new { Ms = 840 });
log.Close();
