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
        ctype = self.headers.get("Content-Type", "")
        spans = []
        try:
            if "json" in ctype:
                spans = self._from_json(body)
            else:
                spans = self._from_proto(body)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"decode_error": str(e)}), flush=True)
            self.send_response(400)
            self.end_headers()
            return
        for s in spans:
            print(json.dumps(s), flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):  # keep stdout to span JSON only
        pass

    @staticmethod
    def _attrs(attrs):
        out = {}
        for a in attrs or []:
            v = a.get("value", {})
            out[a.get("key")] = v.get("stringValue", v.get("intValue"))
        return out

    def _from_json(self, body):
        req = json.loads(body)
        spans = []
        for rs in req.get("resourceSpans", []):
            res = self._attrs(rs.get("resource", {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    spans.append({
                        "name": sp.get("name"),
                        "trace_id": sp.get("traceId"),
                        "span_id": sp.get("spanId"),
                        "parent_span_id": sp.get("parentSpanId", ""),
                        "kind": sp.get("kind", 1),
                        "attributes": {**res, **self._attrs(
                            sp.get("attributes"))},
                    })
        return spans

    def _from_proto(self, body):
        req = ExportTraceServiceRequest()
        req.ParseFromString(body)
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
        return spans


if __name__ == "__main__":
    sys.stderr.write("receiver listening on 127.0.0.1:4318\n")
    HTTPServer(("127.0.0.1", 4318), Handler).serve_forever()
