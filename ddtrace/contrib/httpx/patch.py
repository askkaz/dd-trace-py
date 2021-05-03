import typing

import httpx
from six import ensure_binary
from six import ensure_text

from ddtrace import config
from ddtrace import tracer
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.utils.formats import asbool
from ddtrace.utils.formats import get_env
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


if typing.TYPE_CHECKING:
    from ddtrace import Span
    from ddtrace.vendor.wrapt import BoundFunctionWrapper

config._add(
    "httpx",
    {
        "distributed_tracing": asbool(get_env("httpx", "distributed_tracing", default=True)),
        "split_by_domain": asbool(get_env("httpx", "split_by_domain", default=False)),
        "trace_query_string": asbool(get_env("httpx", "tracee_query_string", default=False)),
    },
)


def _url_to_str(url):
    # type: (httpx.URL) -> str
    scheme, host, port, raw_path = url.raw
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path
    return ensure_text(url)


def _init_span(span, request):
    # type: (Span, httpx.Request) -> None
    if config.httpx.split_by_domain:
        if hasattr(request.url, "netloc"):
            span.service = request.url.netloc
        else:
            service = ensure_binary(request.url.host)
            if request.url.port:
                service += b":" + ensure_binary(str(request.url.port))
            span.service = service

    span.set_tag(SPAN_MEASURED_KEY)
    activate_distributed_headers(tracer, int_config=config.httpx, request_headers=request.headers)

    sample_rate = config.httpx.get_analytics_sample_rate(use_global_config=True)
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _set_span_meta(span, request, response):
    # type: (Span, httpx.Request, httpx.Response) -> None
    set_http_meta(
        span,
        config.httpx,
        method=request.method,
        url=_url_to_str(request.url),
        status_code=response.status_code if response else None,
        query=request.url.query,
        request_headers=request.headers,
        response_headers=response.headers if response else None,
    )


async def _wrapped_async_send(
    wrapped,  # type: BoundFunctionWrapper
    instance,  # type: httpx.AsyncClient
    args,  # type: typing.Tuple[httpx.Request],
    kwargs,  # type: typing.Dict[typing.Str, typing.Any]
):
    # type: (...) -> typing.Coroutine[None, None, httpx.Response]
    req = kwargs.get("request") or args[0]

    with tracer.trace("http.request", service=config.httpx.service, span_type=SpanTypes.HTTP) as span:
        _init_span(span, req)
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            _set_span_meta(span, req, resp)


def _wrapped_sync_send(
    wrapped,  # type: BoundFunctionWrapper
    instance,  # type: httpx.AsyncClient
    args,  # type: typing.Tuple[httpx.Request]
    kwargs,  # type: typing.Dict[typing.Str, typing.Any]
):
    # type: (...) -> httpx.Response
    req = kwargs.get("request") or args[0]

    with tracer.trace("http.request", span_type=SpanTypes.HTTP) as span:
        _init_span(span, req)

        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            _set_span_meta(span, req, resp)


def patch():
    # type: () -> None
    if getattr(httpx, "_datadog_patch", False):
        return

    setattr(httpx, "_datadog_patch", True)

    # TODO: Is this the right method to patch for async?
    #   Do we want to track the
    _w(httpx.AsyncClient, "send", _wrapped_async_send)
    _w(httpx.Client, "send", _wrapped_sync_send)


def unpatch():
    # type: () -> None
    if not getattr(httpx, "_datadog_patch", False):
        return

    setattr(httpx, "_datadog_patch", False)

    _u(httpx.AsyncClient, "send")
    _u(httpx.Client, "send")
