"""Stage 5 exit criterion: hostile `meta` never crashes the caller and never writes a bad line."""

import json

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from logquill import FileTransport, Logger  # noqa: E402


class Explodes:
    def __str__(self):
        raise RuntimeError("no string for you")


def cyclic():
    node = {"name": "loop"}
    node["self"] = node
    return node


def deep(levels):
    value = "bottom"
    for _ in range(levels):
        value = {"k": value}
    return value


# JSON-ish values plus the things that break serialisers.
leaves = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**40), max_value=10**40),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(),
    st.binary(max_size=50),
    st.sampled_from([Explodes(), object(), {1, 2}, (1, "two"), 2**200, float("nan")]),
    st.builds(cyclic),
    st.builds(deep, st.integers(min_value=90, max_value=400)),
)
values = st.recursive(
    leaves,
    lambda inner: st.one_of(
        st.lists(inner, max_size=5),
        st.dictionaries(st.one_of(st.text(), st.integers(), st.none(), st.booleans()), inner, max_size=5),
    ),
    max_leaves=25,
)


def test_a_python_transport_gets_plain_records_directly_and_sanitised_copies_otherwise():
    from logquill import _native
    from logquill.transports.transport import CollectingTransport

    assert _native.is_json_safe({"a": [1, 2.5, "x", None, True, {"b": "c"}]})
    for unsafe in (float("nan"), (1, 2), len, 2**70, {1: "int key"}, deep(40), cyclic()):
        assert not _native.is_json_safe({"v": unsafe})

    sink = CollectingTransport()
    log = Logger("p", transports=[sink], async_dispatch=False)
    plain = log.info("plain", n=1)
    assert sink.records[0] is plain  # handed the caller's own dict: no round trip through the core
    log.info("hostile", loop=cyclic(), f=len)
    assert sink.records[1]["meta"]["loop"]["self"] == "<circular reference>"
    assert sink.records[1]["meta"]["f"].startswith("<built-in function len")
    log.close()
    log.info("after close")
    assert len(sink.records) == 2  # a closed logger writes nothing, on this path too


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(meta=st.dictionaries(st.text(min_size=1), values, max_size=6), asynchronous=st.booleans())
def test_hostile_meta_never_raises_and_every_written_line_is_json(tmp_path, meta, asynchronous):
    path = tmp_path / f"{abs(hash(repr(sorted(meta)))) % 10**9}.log"
    log = Logger("adv", transports=[FileTransport(path)], async_dispatch=asynchronous)
    log.info("hello", **{k: v for k, v in meta.items() if k != "message"})
    log.close()
    if path.exists():
        # split on "\n" only: `splitlines()` also breaks at U+0085 and U+2028, which JSON allows raw
        for line in filter(None, path.read_text().split("\n")):
            record = json.loads(line)
            assert record["message"] == "hello" and isinstance(record["meta"], dict)
