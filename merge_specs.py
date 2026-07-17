"""Merge every scraped per-endpoint Athena spec into ONE OpenAPI doc and hoist
repeated inline object schemas into components/schemas (+ $ref), so a code
generator emits shared, well-named types instead of Insurance1..Insurance4.

Why: athena serves each endpoint as a standalone doc with everything inlined
(no $ref). datamodel-codegen therefore can't dedupe and invents a class per
occurrence. Hoisting identical structures under one name fixes the *artifact*
duplication. Genuinely divergent schemas (same concept, different fields per
endpoint) still get NameN suffixes -- that divergence is real, not tooling.

Run:  uv run merge_specs.py
Then: uvx --from datamodel-code-generator datamodel-codegen \
        --input output/_merged.yaml --input-file-type openapi \
        --output athena_models.py --output-model-type pydantic_v2.BaseModel \
        --reuse-model --collapse-root-models --formatters black
"""

import glob
import os
import re

import yaml

OUTPUT_GLOB = "output/**/*.yaml"
MERGED_PATH = "output/_merged.yaml"
DOCS_DIR = "docs"

REDOC_HTML = """<!DOCTYPE html>
<html>
  <head>
    <title>athena API reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body { margin: 0; padding: 0; }</style>
  </head>
  <body>
    <redoc spec-url="./openapi.yaml"></redoc>
    <script src="https://cdn.redocly.com/redoc/latest/bundles/redoc.standalone.js"></script>
  </body>
</html>
"""


def singularize(key: str) -> str:
    if key.endswith("ies"):
        return key[:-3] + "y"
    if key.endswith("ses"):  # statuses -> status, addresses -> address
        return key[:-2]
    if key.endswith("s") and not key.endswith("ss"):
        return key[:-1]
    return key


def pascal(key: str) -> str:
    parts = re.split(r"[_\-]", key)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Schema"


class Hoister:
    def __init__(self) -> None:
        self.components: dict[str, dict] = {}
        self.sig_to_name: dict[tuple, str] = {}

    def signature(self, props: dict) -> tuple:
        # Structural identity = the set of property names. Two objects with the
        # same fields are the same type regardless of which endpoint or key
        # exposed them, so they collapse to one shared component.
        return tuple(sorted(props))

    def hoist(self, schema, hint: str):
        if not isinstance(schema, dict):
            return schema
        # athena emits invalid property-level `required: true/false` scalars inside
        # individual property schemas; valid OpenAPI only allows an object-level
        # `required: [names]` list. Drop the scalar form so codegen can parse.
        if isinstance(schema.get("required"), bool):
            del schema["required"]
        if schema.get("type") == "array" or "items" in schema:
            schema["items"] = self.hoist(schema.get("items") or {}, singularize(hint))
            return schema
        props = schema.get("properties")
        if "properties" in schema and not isinstance(props, dict):
            del schema["properties"]  # athena sometimes emits null/scalar here
            return schema
        if not props:
            return schema  # scalars / opaque objects: nothing to name

        # bottom-up: hoist children first, keyed by their own property name
        for k, v in list(props.items()):
            props[k] = self.hoist(v, k)

        sig = self.signature(props)
        if sig in self.sig_to_name:
            return {"$ref": f"#/components/schemas/{self.sig_to_name[sig]}"}

        base = pascal(singularize(hint))
        name, i = base, 1
        while name in self.components:  # same name, different structure -> suffix
            i += 1
            name = f"{base}{i}"
        self.components[name] = schema
        self.sig_to_name[sig] = name
        return {"$ref": f"#/components/schemas/{name}"}

    def process_operation(self, path: str, method: str, op: dict) -> None:
        seg = next(
            (s for s in reversed(path.split("/")) if s and not s.startswith("{")),
            "schema",
        )
        base = pascal(singularize(seg))

        rb = (op.get("requestBody") or {}).get("content") or {}
        for media in rb.values():
            if "schema" in media:
                media["schema"] = self.hoist(media["schema"], f"{base}Request")

        for code, resp in (op.get("responses") or {}).items():
            for media in (resp.get("content") or {}).values():
                if "schema" in media:
                    media["schema"] = self.hoist(media["schema"], f"{base}Response")


def write_docs(merged: dict) -> None:
    """Emit a self-contained Redoc site under docs/ for GitHub Pages."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "openapi.yaml"), "w") as fh:
        yaml.safe_dump(merged, fh, sort_keys=False, allow_unicode=True)
    with open(os.path.join(DOCS_DIR, "index.html"), "w") as fh:
        fh.write(REDOC_HTML)


def merge() -> dict:
    merged = {
        "openapi": "3.0.0",
        "info": {"title": "athena API collection (merged)", "version": "0.1"},
        "paths": {},
        "components": {"schemas": {}},
    }
    hoister = Hoister()
    files = sorted(f for f in glob.glob(OUTPUT_GLOB, recursive=True)
                   if not f.endswith("_merged.yaml"))
    for f in files:
        doc = yaml.safe_load(open(f))
        for path, methods in (doc.get("paths") or {}).items():
            dest = merged["paths"].setdefault(path, {})
            for method, op in methods.items():
                if not isinstance(op, dict) or method in dest:
                    continue  # first definition wins on duplicate path/method
                hoister.process_operation(path, method, op)
                dest[method] = op

    merged["components"]["schemas"] = hoister.components
    with open(MERGED_PATH, "w") as fh:
        yaml.safe_dump(merged, fh, sort_keys=False, allow_unicode=True)
    print(f"{len(files)} specs -> {MERGED_PATH}")
    print(f"paths: {len(merged['paths'])}  hoisted schemas: {len(hoister.components)}")
    return merged


def main() -> None:
    write_docs(merge())
    print(f"docs -> {DOCS_DIR}/index.html")


if __name__ == "__main__":
    main()
