#!/usr/bin/env python3
"""A deterministic mock AI service for the seekdb competition test suites.

The competition harness serves the same mock on ``0.0.0.0:8000`` while the
official mysqltest suites run.  This local copy implements the identical wire
protocols so the suites can be replayed on a developer machine or in CI:

``/compatible-mode/v1/chat/completions``
    OpenAI-compatible chat completion.  The request body carries ``model`` and
    ``messages``; the reply exposes ``choices[0].message.content``.

``/compatible-mode/v1/embeddings``
    OpenAI-compatible embedding.  The request body carries ``model`` and
    ``input`` (a string or a list of strings); the reply exposes
    ``data[i].embedding``.

``/v1/rerank``
    SiliconFlow-compatible rerank.  The request body carries ``model``,
    ``query`` and ``documents``; the reply exposes ``results[i].index`` and
    ``results[i].relevance_score``.

``/api/generate``
    Ollama-native generation.  The reply exposes ``response``.

``/api/embed``
    Ollama-native embedding.  The reply exposes ``embeddings``.

The service is intentionally dependency free: it only uses the Python standard
library so it can be started on a bare runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_DIMENSION = 768


def _seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return struct.unpack("<Q", digest[:8])[0]


def _unit_vector(text: str, dimension: int) -> list:
    """Build a stable pseudo embedding for ``text``."""
    state = _seed(text) or 1
    values = []
    for _ in range(dimension):
        # xorshift64*, kept in a 64 bit unsigned domain.
        state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
        state ^= state >> 7
        state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
        state &= 0xFFFFFFFFFFFFFFFF
        values.append(((state >> 11) / float(1 << 53)) * 2.0 - 1.0)
    norm = sum(v * v for v in values) ** 0.5
    if norm == 0.0:
        norm = 1.0
    return [round(v / norm, 8) for v in values]


def _as_text_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                for item in value]
    return [str(value)]


def _messages_to_prompt(messages) -> str:
    if isinstance(messages, str):
        return messages
    parts = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                chunk.get("text", "") for chunk in content if isinstance(chunk, dict)
            )
        parts.append(str(content if content is not None else ""))
    return "\n".join(part for part in parts if part)


def _score(query: str, document: str) -> float:
    """A deterministic relevance score in [0, 1]."""
    if not query:
        return 0.5
    if document.strip().lower() == query.strip().lower():
        return 1.0
    if query.strip().lower() in document.lower():
        return 0.9
    tokens = [token for token in re.split(r"\W+", query.lower()) if token]
    if tokens:
        hits = sum(1 for token in tokens if token in document.lower())
        if hits:
            return 0.1 + 0.5 * hits / len(tokens)
    # Deterministic fallback so repeated runs never reorder the results.
    return (_seed(document) % 1000) / 10000.0


class MockAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "seekdb-mock-ai/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence the default logger
        sys.stderr.write("[mock-ai] " + (fmt % args) + "\n")

    # -- helpers ---------------------------------------------------------
    def _send(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.rstrip("/").endswith("healthz"):
            self._send({"status": "ok"})
            return
        self._send({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802 - http.server API
        path = self.path.split("?")[0].rstrip("/")
        request = self._read_json()
        try:
            if path.endswith("/chat/completions"):
                self._handle_openai_complete(request)
            elif path.endswith("/embeddings"):
                self._handle_openai_embed(request)
            elif path.endswith("/rerank"):
                self._handle_rerank(request)
            elif path.endswith("/api/generate"):
                self._handle_ollama_generate(request)
            elif path.endswith("/api/embed"):
                self._handle_ollama_embed(request)
            else:
                self._send({"error": "not found", "path": path}, status=404)
        except Exception as exc:  # noqa: BLE001 - the mock must never hang
            self._send({"error": {"message": str(exc)}}, status=500)

    # -- protocol handlers -----------------------------------------------
    def _handle_openai_complete(self, request):
        prompt = _messages_to_prompt(request.get("messages"))
        model = request.get("model") or "mock-model"
        content = "mock completion for: %s" % prompt
        self._send({
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(content),
                      "total_tokens": len(prompt) + len(content)},
        })

    def _handle_openai_embed(self, request):
        texts = _as_text_list(request.get("input"))
        dimension = int(request.get("dimensions") or DEFAULT_DIMENSION)
        if dimension <= 0:
            dimension = DEFAULT_DIMENSION
        model = request.get("model") or "mock-model"
        data = []
        for index, text in enumerate(texts):
            data.append({
                "object": "embedding",
                "index": index,
                "embedding": _unit_vector(text, dimension),
            })
        self._send({
            "object": "list",
            "model": model,
            "data": data,
            "usage": {"prompt_tokens": sum(len(t) for t in texts),
                      "total_tokens": sum(len(t) for t in texts)},
        })

    def _handle_rerank(self, request):
        query = request.get("query") or ""
        documents = request.get("documents") or []
        model = request.get("model") or "mock-rerank-model"
        results = []
        for index, document in enumerate(documents):
            text = document if isinstance(document, str) else json.dumps(document, ensure_ascii=False)
            results.append({"index": index, "relevance_score": _score(query, text)})
        # The upstream service returns the documents ordered by score.
        results.sort(key=lambda item: (-item["relevance_score"], item["index"]))
        self._send({
            "id": "rerank-mock",
            "model": model,
            "results": results,
            "usage": {"total_tokens": len(query)},
        })

    def _handle_ollama_generate(self, request):
        prompt = request.get("prompt") or ""
        model = request.get("model") or "mock-model"
        response = "mock ollama response for: %s" % prompt
        self._send({
            "model": model,
            "created_at": "1970-01-01T00:00:00Z",
            "response": response,
            "done": True,
            "done_reason": "stop",
        })

    def _handle_ollama_embed(self, request):
        texts = _as_text_list(request.get("input") or request.get("prompt"))
        dimension = int(request.get("dimensions") or DEFAULT_DIMENSION)
        if dimension <= 0:
            dimension = DEFAULT_DIMENSION
        self._send({
            "model": request.get("model") or "mock-model",
            "embeddings": [_unit_vector(text, dimension) for text in texts],
        })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), MockAIHandler)
    server.daemon_threads = True
    sys.stderr.write("[mock-ai] listening on %s:%d\n" % (args.host, args.port))
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
