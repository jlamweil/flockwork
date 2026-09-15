#!/usr/bin/env python3
"""Minimal OTLP/HTTP trace receiver for C-otel (H-A2).

Listens on 127.0.0.1:4318, decodes ExportTraceServiceRequest protobufs,
prints one JSON line per span: name, trace_id, span_id, parent_span_id.
Run under the venv python (needs opentelemetry-proto).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        req = ExportTraceServiceRequest()
        try:
            req.ParseFromString(body)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"decode_error": str(e)}), flush=True)
            self.send_response(400)
            self.end_headers()
            return
        spans = []
        for rs in req.resource_spans:
            for ss in rs.scope_spans:
                for sp in ss.spans:
                    spans.append({
                        "name": sp.name,
                        "trace_id": sp.trace_id.hex(),
                        "span_id": sp.span_id.hex(),
                        "parent_span_id": sp.parent_span_id.hex(),
                        "kind": sp.kind,
                    })
        for s in spans:
            print(json.dumps(s), flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):  # keep stdout to span JSON only
        pass


if __name__ == "__main__":
    sys.stderr.write("receiver listening on 127.0.0.1:4318\n")
    HTTPServer(("127.0.0.1", 4318), Handler).serve_forever()
