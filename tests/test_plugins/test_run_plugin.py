from logquill.core.logger import Logger
from logquill.plugins.run_plugin import RunPlugin


def test_generates_a_run_id_when_none_given() -> None:
    plugin = RunPlugin()
    logger = Logger("app.test", plugins=[plugin])

    record = logger.info("step")

    assert record is not None
    assert record["meta"]["run_id"] == plugin.run_id


def test_accepts_an_explicit_run_id() -> None:
    logger = Logger("app.test", plugins=[RunPlugin(run_id="run-123")])

    record = logger.info("step")

    assert record is not None
    assert record["meta"]["run_id"] == "run-123"


def test_step_increments_per_record() -> None:
    logger = Logger("app.test", plugins=[RunPlugin(run_id="run-1")])

    first = logger.info("step one")
    second = logger.info("step two")
    third = logger.info("step three")

    assert first is not None and first["meta"]["step"] == 0
    assert second is not None and second["meta"]["step"] == 1
    assert third is not None and third["meta"]["step"] == 2


def test_does_not_override_an_existing_run_id() -> None:
    logger = Logger("app.test", plugins=[RunPlugin(run_id="run-1")])

    record = logger.info("propagated", run_id="upstream-run")

    assert record is not None
    assert record["meta"]["run_id"] == "upstream-run"


def test_two_instances_track_independent_counters() -> None:
    a = Logger("app.a", plugins=[RunPlugin(run_id="run-a")])
    b = Logger("app.b", plugins=[RunPlugin(run_id="run-b")])

    a.info("a1")
    a.info("a2")
    record_b = b.info("b1")

    assert record_b is not None
    assert record_b["meta"]["step"] == 0
    assert record_b["meta"]["run_id"] == "run-b"
