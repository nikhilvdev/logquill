/* A C program against include/logquill.h: log through a logger with a file transport, read the returned record, call a
 * helper, and check the error paths. Built and run by tests/c/run.sh. Exits non-zero on the first failed check. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "logquill.h"

#define CHECK(cond, what)                                              \
  do {                                                                 \
    if (!(cond)) {                                                     \
      fprintf(stderr, "FAILED: %s (line %d)\n", what, __LINE__);       \
      return 1;                                                        \
    }                                                                  \
  } while (0)

int main(int argc, char **argv) {
  const char *log_path = argc > 1 ? argv[1] : "smoke.log";
  char config[512];
  snprintf(config, sizeof config,
           "{\"name\":\"c-smoke\",\"plugins\":[{\"type\":\"redact\"}],"
           "\"transports\":[{\"type\":\"file\",\"path\":\"%s\"}]}",
           log_path);

  char *error = NULL;
  struct lq_logger *logger = lq_logger_new(config, &error);
  CHECK(logger != NULL, error ? error : "lq_logger_new");
  CHECK(lq_abi_version() == LQ_ABI_VERSION, "abi version");

  /* a kept record comes back, with the secret redacted */
  char *record = NULL;
  int kept = lq_log(logger, "info", NULL, "hello from C", "{\"password\":\"hunter2\",\"n\":1}", NULL, &record, &error);
  CHECK(kept == 1 && record != NULL, "lq_log kept");
  CHECK(strstr(record, "\"password\":\"***\"") != NULL, "password redacted");
  CHECK(strstr(record, "hunter2") == NULL, "secret not in the record");
  lq_string_free(record);

  /* below the level: dropped, record stays NULL */
  CHECK(lq_logger_set_level(logger, "error", &error) == 0, "set level");
  CHECK(lq_log(logger, "info", NULL, "quiet", NULL, NULL, &record, &error) == 0 && record == NULL, "dropped");

  /* a bad level is an error with a message, not a crash */
  CHECK(lq_log(logger, "loud", NULL, "x", NULL, NULL, &record, &error) == -1 && error != NULL, "bad level");
  lq_string_free(error);
  error = NULL;

  /* a stateless helper */
  char *canonical = lq_call("canonicalJson", "[\"{\\\"b\\\":1,\\\"a\\\":2}\"]", &error);
  CHECK(canonical != NULL, "lq_call");
  CHECK(strcmp(canonical, "\"{\\\"a\\\": 2, \\\"b\\\": 1}\"") == 0, "canonical json");
  lq_string_free(canonical);

  /* unknown helper, NULL arguments */
  CHECK(lq_call("noSuchHelper", "[]", &error) == NULL && error != NULL, "unknown helper");
  lq_string_free(error);
  error = NULL;
  CHECK(lq_call(NULL, NULL, NULL) == NULL, "NULL arguments");

  CHECK(lq_logger_close(logger, 5000) == 1, "close");
  lq_logger_free(logger);
  lq_logger_free(NULL);

  /* the record reached the file, redacted */
  FILE *file = fopen(log_path, "r");
  CHECK(file != NULL, "log file exists");
  char line[1024];
  CHECK(fgets(line, sizeof line, file) != NULL, "log file has a line");
  fclose(file);
  CHECK(strstr(line, "hello from C") != NULL && strstr(line, "hunter2") == NULL, "file line is redacted");

  printf("C smoke test ok (logquill %s)\n", lq_version());
  return 0;
}
