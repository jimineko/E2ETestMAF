from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from maf_e2e.config import Settings


def configure_telemetry(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    provider = TracerProvider(resource=Resource.create({"service.name": "maf-playwright-e2e"}))
    exporter_factory = _exporter_factory(settings)
    if exporter_factory is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter_factory()))
    trace.set_tracer_provider(provider)


def _exporter_factory(settings: Settings) -> Callable[[], SpanExporter] | None:
    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry.exporter import (
                AzureMonitorTraceExporter,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Install the azure-monitor optional dependency to use Application Insights"
            ) from exc
        return lambda: AzureMonitorTraceExporter(
            connection_string=settings.applicationinsights_connection_string
        )
    if settings.otlp_endpoint:
        return lambda: OTLPSpanExporter(endpoint=settings.otlp_endpoint)
    return None
