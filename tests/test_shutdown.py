from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from logquill import Logger
from logquill.runtime import shutdown
from logquill.transports.transport import CollectingTransport

_SCRIPT = """
import sys
from logquill import {imports}

log = Logger("app", transports=[FileTransport(sys.argv[1])], {options})
for i in range(200):
    log.info("record", i=i)
# no flush(), no close(): the exit hook has to do it
"""


def _run(tmp_path: Path, *, imports: str, options: str) -> list[str]:
    path = tmp_path / "out.log"
    script = textwrap.dedent(_SCRIPT).format(imports=imports, options=options)
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=60)
    return path.read_text().splitlines() if path.exists() else []


def test_exit_hook_flushes_an_async_queue_the_script_never_closed(tmp_path: Path) -> None:
    lines = _run(tmp_path, imports="Logger, FileTransport", options="async_dispatch=True")

    assert len(lines) == 200


def test_exit_hook_sends_a_batching_transports_unsent_batch(tmp_path: Path) -> None:
    path = tmp_path / "sent.txt"
    script = textwrap.dedent(
        """
        import sys
        from logquill import Logger, HTTPTransport

        def sender(url, batch):
            with open(sys.argv[1], "a") as f:
                f.write("\\n".join(batch) + "\\n")

        transport = HTTPTransport("http://unused", batch_size=1000, sender=sender)
        log = Logger("app", transports=[transport])
        log.info("one")
        log.info("two")
        """
    )

    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=60)

    assert len(path.read_text().splitlines()) == 2


def test_flush_at_exit_false_leaves_the_logger_unregistered() -> None:
    opted_out = Logger("app", flush_at_exit=False)
    default = Logger("app")

    assert opted_out not in shutdown._loggers
    assert default in shutdown._loggers


def test_shutdown_drains_the_queue_and_closes_each_shared_transport_once() -> None:
    class CountingTransport(CollectingTransport):
        close_calls = 0

        def close(self) -> None:
            type(self).close_calls += 1
            super().close()

    sink = CountingTransport()
    parent = Logger("app", transports=[sink], async_dispatch=True)
    child = parent.child("child")
    parent.info("a")
    child.info("b")

    shutdown.shutdown(timeout=2.0)
    shutdown.shutdown(timeout=2.0)  # safe to call twice

    assert len(sink.records) == 2
    assert CountingTransport.close_calls == 1


def test_an_explicitly_closed_logger_is_not_closed_again_by_the_hook() -> None:
    class CountingTransport(CollectingTransport):
        close_calls = 0

        def close(self) -> None:
            type(self).close_calls += 1
            super().close()

    sink = CountingTransport()
    logger = Logger("app", transports=[sink])
    child = logger.child("child")  # shares the transport, registers separately

    logger.close()
    shutdown.shutdown()

    assert child is not None
    assert CountingTransport.close_calls == 1


def test_a_transport_that_raises_on_close_does_not_stop_the_others() -> None:
    class Exploding(CollectingTransport):
        def close(self) -> None:
            raise RuntimeError("cannot close")

    good = CollectingTransport()
    logger = Logger("app", transports=[Exploding(), good])

    shutdown.shutdown()

    assert good.closed is True
    assert logger is not None


def test_registering_does_not_keep_a_logger_alive() -> None:
    import gc
    import weakref

    logger = Logger("app")
    ref = weakref.ref(logger)

    del logger
    gc.collect()

    assert ref() is None
