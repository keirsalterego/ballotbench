"""API reference, rendered on the server from the same OpenAPI document that
/api/schema serves, so it works offline and can't drift from the code."""
from django.shortcuts import render
from drf_spectacular.generators import SchemaGenerator

METHODS = ("get", "post", "put", "patch", "delete")


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
                "summary": (op.get("description") or "").strip().split("\n\n")[0],
                "params": [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"],
                "codes": sorted(op.get("responses", {})),
            })
    return render(request, "portal/api_docs.html", {"endpoints": endpoints, "info": schema["info"]})
