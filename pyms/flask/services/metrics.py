import time
import logging
from typing import Text

from flask import Blueprint, Response, request
from pyms.flask.services.driver import DriverService

from prometheus_client import generate_latest
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricsExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricsExporter
from opentelemetry.sdk.metrics import Counter, ValueRecorder, MeterProvider

# Based on https://github.com/sbarratt/flask-prometheus
# and https://github.com/korfuri/python-logging-prometheus/

metrics.set_meter_provider(MeterProvider())
meter = metrics.get_meter(__name__)
exporter = PrometheusMetricsExporter()
metrics.get_meter_provider().start_pipeline(meter, exporter, 1)

FLASK_REQUEST_LATENCY = meter.create_metric(
    "http_server_requests_seconds",
    "Flask Request Latency",
    "http_server_requests_seconds",
    float,
    ValueRecorder,
    ("service", "method", "uri", "status"),
)
FLASK_REQUEST_COUNT = meter.create_metric(
    "http_server_requests_count",
    "Flask Request Count",
    "http_server_requests_count",
    int,
    Counter,
    ["service", "method", "uri", "status"],
)
LOGGER_TOTAL_MESSAGES = meter.create_metric(
    "logger_messages_total",
    "Count of log entries by service and level.",
    "logger_messages_total",
    int,
    Counter,
    ["service", "level"],
)


class FlaskMetricsWrapper():
    def __init__(self, app_name):
        self.app_name = app_name

    def before_request(self):  # pylint: disable=R0201
        request.start_time = time.time()

    def after_request(self, response):
        if hasattr(request.url_rule, "rule"):
            path = request.url_rule.rule
        else:
            path = request.path
        request_latency = time.time() - request.start_time
        labels = {
            "service": self.app_name,
            "method": str(request.method),
            "uri": path,
            "status": str(response.status_code),
        }

        FLASK_REQUEST_LATENCY.record(request_latency, labels)
        FLASK_REQUEST_COUNT.add(1, labels)

        return response


class Service(DriverService):
    """
    Adds [Prometheus](https://prometheus.io/) metrics using the [Opentelemetry Client Library](https://opentelemetry-python.readthedocs.io/en/latest/exporter/prometheus/prometheus.html).
    """
    config_resource: Text = "metrics"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.metrics_blueprint = Blueprint("metrics", __name__)
        self.serve_metrics()

    @staticmethod
    def monitor(app_name, app):
        metric = FlaskMetricsWrapper(app_name)
        app.before_request(metric.before_request)
        app.after_request(metric.after_request)

    def serve_metrics(self):
        @self.metrics_blueprint.route("/metrics", methods=["GET"])
        def metrics():  # pylint: disable=unused-variable
            return Response(
                generate_latest(),
                mimetype="text/print()lain",
                content_type="text/plain; charset=utf-8",
            )

    @staticmethod
    def add_logger_handler(logger, service_name):
        logger.addHandler(MetricsLogHandler(service_name))
        return logger


class MetricsLogHandler(logging.Handler):
    """A LogHandler that exports logging metrics for Prometheus.io."""

    def __init__(self, app_name):
        super().__init__()
        self.app_name = str(app_name)

    def emit(self, record):
        labels = {"service": self.app_name, "level": record.levelname}
        LOGGER_TOTAL_MESSAGES.add(1, labels)
