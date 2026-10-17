# Kibana starter dashboard

A ready-to-import Kibana dashboard for logs shipped by
[`ElasticsearchTransport`](../../README.md#sql-nosql-message-queue-and-cloud-native-transports).
It only works because that transport writes [Elastic Common
Schema](https://www.elastic.co/guide/en/ecs/current/ecs-reference.html)
fields (`@timestamp`, `log.level`, `service.name`, …) alongside LogQuill's
own — Kibana never ingests data itself, it only visualizes what's already
in Elasticsearch in a shape it recognizes.

## What's in it

`logquill-dashboard.ndjson` is a [Kibana saved-objects
export](https://www.elastic.co/guide/en/kibana/current/managing-saved-objects.html)
containing:

- **An index pattern** (`logquill-*`), time field `@timestamp`.
- **A saved search** — "LogQuill - recent logs", showing `log.level`,
  `service.name`, and `message`, newest first.
- **Two visualizations** — a stacked count of records per `log.level` over
  time (an error-rate-at-a-glance view), and a breakdown of records per
  `service.name` (LogQuill's logger name).
- **A dashboard** — "LogQuill overview" — combining all three.

## Using it

1. Point `ElasticsearchTransport` at an index matching `logquill-*` (the
   default `index="logs"` won't match the bundled index pattern — either
   name your index e.g. `logquill-app`, or edit the index pattern's title
   after importing to match whatever you actually use):

   ```python
   from logquill import ElasticsearchTransport, Logger

   transport = ElasticsearchTransport(url="http://localhost:9200", index="logquill-app")
   logger = Logger("app", transports=[transport])
   ```

2. In Kibana: **Stack Management → Saved Objects → Import**, and pick
   `logquill-dashboard.ndjson`.
3. Open **Dashboards → LogQuill overview**.

## Known limitations

- **Not tested against a live Kibana instance** — this repo has no Kibana
  to import into and verify against, so the NDJSON was built by hand
  against the documented saved-object schema rather than exported from a
  real Kibana session. The structure matches what Kibana's own exports
  look like, but if your Kibana version's import rejects something, the
  visualizations are simple enough (`date_histogram`/`terms` aggregations
  on a `histogram`/`pie` viz) to rebuild in a couple of minutes by hand
  using the field names above.
- **Aggregating on `log.level.keyword`/`service.name.keyword`** assumes
  Elasticsearch's default dynamic mapping created those `.keyword`
  sub-fields (true out of the box for most setups). If your index has a
  custom mapping without them, point the visualizations at whatever your
  equivalent keyword field is.
- This gives you an error-rate/log-browsing view, not a span-tree
  waterfall — for reconstructing one agent run's exact call tree, use
  `logquill trace`/`logquill serve` (see the
  [CLI](../../README.md#cli) section of the README), which read JSONL or
  SQLite directly and don't need Elasticsearch/Kibana at all.
