import asyncio
import functools

from ..asyncio import context_provider
from ...compat import stringify
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import http
from ...propagation.http import HTTPPropagator
from ...settings import config


try:
    from aiohttp.web import middleware
    AIOHTTP_2x = True
except ImportError:
    AIOHTTP_2x = False

    def middleware(f):
        return f


CONFIG_KEY = 'datadog_trace'
REQUEST_CONTEXT_KEY = 'datadog_context'
REQUEST_SPAN_KEY = '__datadog_request_span'


@asyncio.coroutine
@middleware
def trace_middleware_2x(request, handler, app=None):
    # application configs
    if app is None:
        app = request.app

    tracer = app[CONFIG_KEY]['tracer']
    service = app[CONFIG_KEY]['service']
    distributed_tracing = app[CONFIG_KEY]['distributed_tracing_enabled']

    # Create a new context based on the propagated information.
    if distributed_tracing:
        propagator = HTTPPropagator()
        context = propagator.extract(request.headers)
        # Only need to active the new context if something was propagated
        if context.trace_id:
            tracer.context_provider.activate(context)

    # trace the handler
    request_span = tracer.trace(
        'aiohttp.request',
        service=service,
        span_type=http.TYPE,
    )

    # Configure trace search sample rate
    # DEV: aiohttp is special case maintains separate configuration from config api
    analytics_enabled = app[CONFIG_KEY]['analytics_enabled']
    if (config.analytics_enabled and analytics_enabled is not False) or analytics_enabled is True:
        request_span.set_tag(
            ANALYTICS_SAMPLE_RATE_KEY,
            app[CONFIG_KEY].get('analytics_sample_rate', True)
        )

    # attach the context and the root span to the request; the Context
    # may be freely used by the application code
    request[REQUEST_CONTEXT_KEY] = request_span.context
    request[REQUEST_SPAN_KEY] = request_span
    try:
        response = yield from handler(request)  # noqa: E999
        return response
    except Exception:
        request_span.set_traceback()
        raise


@asyncio.coroutine
def trace_middleware_1x(app, handler):
    """
    ``aiohttp`` middleware that traces the handler execution.
    Because handlers are run in different tasks for each request, we attach the Context
    instance both to the Task and to the Request objects. In this way:
        * the Task is used by the internal automatic instrumentation
        * the ``Context`` attached to the request can be freely used in the application code
    """
    return functools.partial(trace_middleware_2x, handler=handler, app=app)


trace_middleware = trace_middleware_2x if AIOHTTP_2x else trace_middleware_1x


@asyncio.coroutine
def on_prepare(request, response):
    """
    The on_prepare signal is used to close the request span that is created during
    the trace middleware execution.
    """
    # safe-guard: discard if we don't have a request span
    request_span = request.get(REQUEST_SPAN_KEY, None)
    if not request_span:
        return

    # default resource name
    resource = stringify(response.status)

    if request.match_info.route.resource:
        # collect the resource name based on http resource type
        res_info = request.match_info.route.resource.get_info()

        if res_info.get('path'):
            resource = res_info.get('path')
        elif res_info.get('formatter'):
            resource = res_info.get('formatter')
        elif res_info.get('prefix'):
            resource = res_info.get('prefix')

        # prefix the resource name by the http method
        resource = '{} {}'.format(request.method, resource)

    request_span.resource = resource
    request_span.set_tag('http.method', request.method)
    request_span.set_tag('http.status_code', response.status)
    request_span.set_tag('http.url', request.path)
    request_span.finish()


def trace_app(app, tracer, service='aiohttp-web', distributed_tracing=True):
    """
    Tracing function that patches the ``aiohttp`` application so that it will be
    traced using the given ``tracer``.

    :param app: aiohttp application to trace
    :param tracer: tracer instance to use
    :param service: service name of tracer
    :param distributed_tracing: set to True to enable distributed tracing
    """

    # safe-guard: don't trace an application twice
    if getattr(app, '__datadog_trace', False):
        return
    setattr(app, '__datadog_trace', True)

    # configure datadog settings
    app[CONFIG_KEY] = {
        'tracer': tracer,
        'service': service,
        'distributed_tracing_enabled': distributed_tracing,
        'analytics_enabled': None,
        'analytics_sample_rate': 1.0,
    }

    # the tracer must work with asynchronous Context propagation
    tracer.configure(context_provider=context_provider)

    # add the async tracer middleware as a first middleware
    # and be sure that the on_prepare signal is the last one
    app.middlewares.insert(0, trace_middleware)
    app.on_response_prepare.append(on_prepare)
