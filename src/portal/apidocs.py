"""API reference, rendered on the server from the same OpenAPI document that
/api/schema serves, so it works offline and can't drift from the code."""
import re

from django.shortcuts import render
from django.utils.html import escape
from django.utils.safestring import mark_safe
from drf_spectacular.generators import SchemaGenerator

METHODS = ("get", "post", "put", "patch", "delete")


def inline_code(text):
    """The schema's Markdown `code` spans as <code>, everything else escaped."""
    return mark_safe(re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text)))


def api_docs(request):
    schema = SchemaGenerator().get_schema(request=None, public=True)
    endpoints = []
    for path, item in schema["paths"].items():
        for method in METHODS:
            if method not in item:
                continue
            op = item[method]
            endpoints.append({
                "method": method.upper(), "path": path,
                "summary": inline_code((op.get("description") or op.get("summary") or "").strip().split("\n\n")[0]),
                "params": [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"],
                "codes": sorted(op.get("responses", {})),
            })
    return render(request, "portal/api_docs.html", {
        "endpoints": endpoints, "description": inline_code(schema["info"]["description"]),
        "origin": request.build_absolute_uri("/").rstrip("/")})
