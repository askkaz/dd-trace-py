import pyperf
from ddtrace import Span


def gen_traces(nspans=1, trace_id=None):
    for _ in range(0, nspans):
        Span(None, "test.op", resource="resource", service="service", trace_id=trace_id)


VARIANTS = [dict(nspans=10000), dict(nspans=10000, trace_id=1)]


def time_start_span(loops):
    range_it = range(loops)
    t0 = pyperf.perf_counter()
    for _ in range_it:
        gen_traces(**variant)
    dt = pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata["scenario"] = "start_span"
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items())
        metadata = {}
        runner.bench_time_func(name, time_start_span, metadata=metadata)
