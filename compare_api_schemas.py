"""Compare `api`'s pydantic Athena models against the scraped Athena OpenAPI specs.

What it checks
--------------
- Request models  -> validated against the endpoint's requestBody (or query params):
    * field we mark OPTIONAL but Athena marks REQUIRED   -> BUG risk (send failures)
    * field we mark REQUIRED  but Athena marks optional   -> we're stricter than Athena (usually fine)
    * field on our model that Athena doesn't define       -> typo / removed field
- Response models -> validated against the 200 response schema:
    * field on our model that Athena doesn't define       -> typo / renamed / will always be None
    * field we mark REQUIRED (no default)                 -> Athena marks ~nothing required in
      responses, so a required response field can blow up validation if Athena omits it.

Athena's swagger declares `required` lists on requestBodies but almost never on responses
(12 / 841 response schemas). So "is this response field really optional?" is NOT answerable
from the spec -- the only automatable signal there is "does the field exist" + "are we being
dangerously strict". That's surfaced below.

Run: uv run compare_api_schemas.py [path-to-api-repo]
"""

import ast
import sys
from pathlib import Path

import yaml

SPEC_DIR = Path(__file__).parent / "output"
API_REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "../../oula-health/api")
SCHEMA_DIR = API_REPO / "app/schemas/emr/athena"

# model -> how to find its Athena schema in the specs.
#   kind="requestBody": pull requestBody schema (+ required list) for file/path/method
#   kind="params":      pull the operation parameters (name/required) for file/path/method
#   kind="response":    pull the 200 response schema, optionally descending into `pointer`
#                       (list of property names) to reach a nested object's props.
MAPPING = {
    "Patient": dict(kind="response", file="patient/patient.yaml",
                    path="/v1/{practiceid}/patients/enhancedbestmatch", method="get"),
    "Insurance": dict(kind="response", file="patient/patient.yaml",
                      path="/v1/{practiceid}/patients/enhancedbestmatch", method="get",
                      pointer=["insurances"]),
    "CustomField": dict(kind="response", file="patient/patient.yaml",
                        path="/v1/{practiceid}/patients/enhancedbestmatch", method="get",
                        pointer=["customfields"]),
    "LabResult": dict(kind="response", file="charts/lab-analyte.yaml",
                      path="/v1/{practiceid}/chart/{patientid}/analytes", method="get"),
    "AppointmentChange": dict(kind="response", file="appointments/appointment.yaml",
                              path="/v1/{practiceid}/appointments/changed", method="get"),
    "AppointmentProviderChange": dict(kind="response", file="appointments/appointment.yaml",
                                      path="/v1/{practiceid}/appointments/changed", method="get"),
    "AthenaPatientCreateRequest": dict(kind="requestBody", file="patient/patient.yaml",
                                       path="/v1/{practiceid}/patients", method="post"),
    "AthenaPatientSearchRequest": dict(kind="params", file="patient/patient.yaml",
                                       path="/v1/{practiceid}/patients/enhancedbestmatch", method="get"),
    "AthenaPatientPrivacyCreate": dict(kind="requestBody",
                                       file="patient/privacy-information-verification.yaml",
                                       path="/v1/{practiceid}/patients/{patientid}/privacyinformationverified",
                                       method="post"),
}


# ---- our pydantic models (AST, no runtime import needed) -------------------
def parse_models(schema_dir: Path) -> dict[str, dict[str, bool]]:
    """Return {ModelName: {field_name: is_required}}, resolving in-file inheritance."""
    raw: dict[str, tuple[list[str], dict[str, bool]]] = {}
    for py in schema_dir.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            fields: dict[str, bool] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields[stmt.target.id] = stmt.value is None  # required iff no default
            raw[node.name] = (bases, fields)

    resolved: dict[str, dict[str, bool]] = {}

    def build(name: str) -> dict[str, bool]:
        if name in resolved:
            return resolved[name]
        bases, fields = raw.get(name, ([], {}))
        merged: dict[str, bool] = {}
        for b in bases:
            if b in raw:
                merged.update(build(b))
        merged.update(fields)
        resolved[name] = merged
        return merged

    for name in raw:
        build(name)
    return resolved


# ---- athena spec extraction ------------------------------------------------
def descend_to_object(schema: dict) -> dict:
    """Unwrap array->items until we reach an object schema."""
    while isinstance(schema, dict) and schema.get("type") == "array":
        schema = schema.get("items", {})
    return schema or {}


def spec_fields(cfg: dict) -> tuple[set[str], set[str]]:
    """Return (all_field_names, required_field_names) for a mapped model."""
    doc = yaml.safe_load((SPEC_DIR / cfg["file"]).read_text())
    op = doc["paths"][cfg["path"]][cfg["method"]]

    if cfg["kind"] == "params":
        params = [p for p in op.get("parameters", []) if p.get("in") in ("query", "body")]
        allf = {p["name"] for p in params}
        req = {p["name"] for p in params if p.get("required")}
        return allf, req

    if cfg["kind"] == "requestBody":
        schema = op["requestBody"]["content"]["application/x-www-form-urlencoded"]["schema"]
        schema = descend_to_object(schema)
        allf = set(schema.get("properties", {}))
        req = set(schema.get("required", []) or [])
        return allf, req

    # response
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    schema = descend_to_object(schema)
    for key in cfg.get("pointer", []):
        schema = descend_to_object(schema.get("properties", {}).get(key, {}))
    allf = set(schema.get("properties", {}))
    req = set(schema.get("required", []) or [])
    return allf, req


# ---- compare ---------------------------------------------------------------
def main() -> int:
    models = parse_models(SCHEMA_DIR)
    problems = 0
    for model, cfg in MAPPING.items():
        if model not in models:
            print(f"!! {model}: not found in {SCHEMA_DIR}")
            continue
        our = models[model]
        spec_all, spec_req = spec_fields(cfg)
        is_req_side = cfg["kind"] in ("requestBody", "params")

        lines = []
        for fname, required in our.items():
            if fname not in spec_all:
                lines.append(f"    UNKNOWN   {fname!r}: not defined in Athena spec")
            elif is_req_side and not required and fname in spec_req:
                lines.append(f"    LOOSE     {fname!r}: we mark optional, Athena REQUIRES it")
            elif is_req_side and required and fname not in spec_req:
                lines.append(f"    STRICT    {fname!r}: we require, Athena says optional")
            elif not is_req_side and required:
                lines.append(f"    STRICT    {fname!r}: required in our model, "
                             f"Athena does not guarantee (response field)")

        # request fields Athena requires that we don't even model
        if is_req_side:
            for missing in sorted(spec_req - set(our)):
                lines.append(f"    MISSING   {missing!r}: Athena REQUIRES, absent from our model")

        header = f"{model}  ({cfg['kind']} @ {cfg['method'].upper()} {cfg['path']})"
        if lines:
            problems += len(lines)
            print(header)
            print("\n".join(lines))
        else:
            print(f"{header}  OK")
        print()
    print(f"total findings: {problems}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
