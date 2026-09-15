#!/usr/bin/env python3
"""Exporter side of C-otel (H-A2): SDK spans over OTLP/HTTP to localhost.

Parent span 'worker-dispatch' with child 'host-verify' — the exact
parent-child shape H-A2 demands as evidence.
"""
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider(resource=Resource({"service.name": "swarmo-c2"}))
provider.add_span_processor(BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://127.0.0.1:4318/v1/traces")))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("c2-probe")

with tracer.start_as_current_span("worker-dispatch") as parent:
    parent.set_attribute("task", "t-042")
    parent.set_attribute("attemptId", "att-c2otel")
    time.sleep(0.2)
    with tracer.start_as_current_span("host-verify") as child:
        child.set_attribute("pytest", "4 passed")
        time.sleep(0.1)

provider.force_flush()
provider.shutdown()
print("exported", flush=True)
