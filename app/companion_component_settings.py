"""Discover scoped component fields without exposing writable JSON paths."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app import companion_dashboard_settings as base
from app.state.page_store import Page
from app.widget_choices import resolve_choices

_SUPPORTED = base._SUPPORTED_TYPES - {"color"}


class SchemaConflict(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _contains(values: list[Any], value: Any) -> bool:
    return any(_same(v, value) for v in values)


def _overlap(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    return a[: len(b)] == b or b[: len(a)] == a


def _container(doc: dict[str, Any], path: tuple[Any, ...]) -> tuple[dict[str, Any], str]:
    current = doc
    for key in path[:-1]:
        current = current[key]
    return current, path[-1]


def _target_path(canvas: dict[str, Any], target: dict[str, Any]) -> tuple[Any, ...] | None:
    if base._target_container(canvas, target) is None:
        return None
    index = next(i for i, el in enumerate(canvas["els"]) if el["id"] == target["el"])
    prefix: tuple[Any, ...] = ("canvas", "els", index)
    slot, key = target["slot"], target.get("key")
    if slot == "options":
        return (*prefix, "options", key)
    prefix = (*prefix, "bind" if slot == "bind_options" else "sources", target["index"])
    if slot in ("source_options", "bind_options"):
        return (*prefix, "options", key)
    if slot == "source_url":
        return (*prefix, "url")
    return (*prefix, "headers", key) if key else (*prefix, "headers")


@dataclass
class Field:
    id: str
    spec: dict[str, Any]
    paths: list[tuple[Any, ...]]
    definitions: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    declared: bool = False
    readonly: str | None = None
    view: dict[str, Any] = field(default_factory=dict)


class Schema:
    def __init__(self, page: Page, registry: Any):
        self.doc = page.model_dump(mode="json")
        self.fields: dict[str, Field] = {}
        self.inputs: list[Field] = []
        self.components: list[dict[str, Any]] = []
        self.target_defs: dict[tuple[Any, ...], tuple[Any, dict[str, Any]]] = {}
        self.definition_fingerprint: list[Any] = []
        self._discover(registry)
        canvas, specs = base._input_specs(page, registry)
        covered: set[tuple[Any, ...]] = set()
        secret_paths: set[tuple[Any, ...]] = set()
        seen: set[str] = set()
        # Includes invalid/duplicate declarations, so aliases cannot unmask data.
        for spec in specs:
            if spec["secret"]:
                secret_paths.update(p for t in spec["targets"] if (p := _target_path(canvas, t)))
        for spec in specs:
            self.definition_fingerprint.append(["input", spec])
            if spec["name"] in seen:
                continue
            seen.add(spec["name"])
            paths = [p for t in spec["targets"] if (p := _target_path(canvas, t))]
            definitions = [self.target_defs[p] for p in paths if p in self.target_defs]
            spec["required"] = bool(
                spec["required"] or any(d.get("required") for _, d in definitions)
            )
            record = Field("i_" + _hash(spec["name"])[:20], spec, paths, definitions, True)
            if not spec["editable"]:
                record.readonly = (
                    "This declaration is unsupported, duplicated or has missing targets."
                )
            elif any(
                d.get("show_if") or d.get("type", "string") not in _SUPPORTED
                for _, d in definitions
            ):
                record.readonly = "This field needs the web editor."
            elif (
                definitions
                and any(d.get("type", "string") != spec["type"] for _, d in definitions)
                and not all(
                    d.get("type", "string") in base._TEXT_TYPES and spec["type"] in base._TEXT_TYPES
                    for _, d in definitions
                )
            ):
                record.readonly = "The declaration and component field types differ."
            if not record.readonly:
                covered.update(paths)
            self.inputs.append(record)
            self.fields[record.id] = record
        for component in self.components:
            shown: list[Field] = []
            for record in component.pop("_fields"):
                if any(existing.id == record.id for existing in shown):
                    continue
                if any(p in covered for p in record.paths):
                    continue
                if any(_overlap(p, s) for p in record.paths for s in secret_paths):
                    record.spec["secret"] = True
                self.fields[record.id] = record
                shown.append(record)
            component["fields"] = shown
        for record in self.fields.values():
            self._describe(record)
        self.revision = _hash(self.definition_fingerprint)

    def _discover(self, registry: Any) -> None:
        def add(
            identity: Any,
            path: tuple[Any, ...],
            plugin_id: str,
            kind: str,
            fragment: str | None = None,
            source_name: str | None = None,
        ) -> None:
            if not plugin_id:
                return
            plugin = registry.get(plugin_id) if registry else None
            specs = plugin.manifest.get("cell_options", []) if plugin else []
            self.definition_fingerprint.append(
                [identity, plugin_id, plugin.manifest.get("version") if plugin else None, specs]
            )
            component: dict[str, Any] = {
                "id": "c_" + _hash(identity)[:20],
                "label": str(plugin.manifest.get("name", plugin_id)) if plugin else plugin_id,
                "widget_id": plugin_id,
                "kind": kind,
                "available": plugin is not None,
                "fragment": fragment,
                "source_name": source_name,
                "_fields": [],
            }
            names = [s.get("name") for s in specs]
            for definition in specs:
                if not isinstance(definition.get("name"), str):
                    continue
                spec = copy.deepcopy(definition)
                spec.setdefault("type", "string")
                spec.setdefault("label", spec["name"])
                spec.setdefault("help", "")
                spec.setdefault("required", False)
                spec["secret"] = bool(
                    spec.get("secret") or spec.get("mask") or spec["type"] == "password"
                )
                spec.setdefault("choices", [])
                spec["secret"] = spec["secret"] or any(
                    d.get("name") == spec["name"]
                    and (d.get("secret") or d.get("mask") or d.get("type") == "password")
                    for d in specs
                )
                target = (*path, spec["name"])
                self.target_defs[target] = (plugin, spec)
                record = Field(
                    "f_" + _hash([identity, spec["name"]])[:20], spec, [target], [(plugin, spec)]
                )
                if (
                    names.count(spec["name"]) > 1
                    or spec["type"] not in _SUPPORTED
                    or spec.get("show_if")
                ):
                    record.readonly = "This field needs the web editor."
                component["_fields"].append(record)
            self.components.append(component)

        if self.doc["layout_kind"] == "grid":
            for i, cell in enumerate(self.doc["cells"]):
                add(["cell", cell["id"]], ("cells", i, "options"), cell.get("plugin"), "grid_cell")
        elif self.doc.get("canvas"):
            for i, el in enumerate(self.doc["canvas"]["els"]):
                prefix = ("canvas", "els", i)
                plugin_id = el.get("widget") if el["kind"] == "widget" else el.get("source")
                add(
                    ["element", el["id"]],
                    (*prefix, "options"),
                    plugin_id,
                    "code_source" if el["kind"] == "code" else "canvas_element",
                    el.get("fragment") or None,
                )
                for j, source in enumerate(el.get("sources", [])):
                    add(
                        ["source", el["id"], j],
                        (*prefix, "sources", j, "options"),
                        source.get("key"),
                        "code_source",
                        source_name=source.get("name") or None,
                    )
                for j, binding in enumerate(el.get("bind", [])):
                    add(
                        ["binding", el["id"], j],
                        (*prefix, "bind", j, "options"),
                        binding.get("source"),
                        "binding",
                    )

    def _values(self, record: Field) -> tuple[list[Any], list[bool]]:
        values, overrides = [], []
        for path in record.paths:
            container, key = _container(self.doc, path)
            overrides.append(key in container)
            definition = self.target_defs.get(path, (None, record.spec))[1]
            default = definition.get("default", record.spec.get("default"))
            values.append(container.get(key, default))
        return values, overrides

    def _describe(self, record: Field) -> None:
        spec = record.spec
        secret = bool(
            spec["secret"] or any(d.get("secret") or d.get("mask") for _, d in record.definitions)
        )
        if secret and spec["type"] not in base._TEXT_TYPES:
            record.readonly = "This secret field needs the web editor."
        values, overrides = self._values(record)
        mixed = bool(values and any(not _same(values[0], v) for v in values[1:]))
        value = values[0] if values else None
        dynamic = not secret and any(d.get("choices_from") for _, d in record.definitions)
        view = {k: spec[k] for k in ("name", "label", "type", "required", "help")}
        view.update(
            field_id=record.id,
            secret=secret,
            editable=not record.readonly,
            configured=any(not base._empty(v) for v, o in zip(values, overrides, strict=True) if o),
            choices=[] if secret or dynamic else self.choices(record)[0],
            dynamic_choices=bool(dynamic),
            value_state="mixed" if mixed else "uniform" if value is not None else "unset",
            has_override=any(overrides),
            can_clear=not spec["required"] and not record.readonly,
            can_reset=not record.readonly,
            readonly_reason=record.readonly,
        )
        if not secret and not spec.get("_duplicate"):
            if not mixed:
                view["value"] = copy.deepcopy(value)
            defaults = [
                self.target_defs.get(p, (None, spec))[1].get("default", spec.get("default"))
                for p in record.paths
            ]
            if defaults and all(_same(defaults[0], d) for d in defaults[1:]):
                view["default"] = copy.deepcopy(defaults[0])
        for public, keys in (
            ("minimum", ("minimum", "min")),
            ("maximum", ("maximum", "max")),
            ("step", ("step",)),
        ):
            candidates = [
                d[k] for _, d in record.definitions for k in keys if type(d.get(k)) in (int, float)
            ]
            if candidates:
                view[public] = max(candidates) if public == "minimum" else min(candidates)
        record.view = view

    def response(self) -> dict[str, Any]:
        editable = sum(f.view["editable"] for f in self.fields.values())
        readonly = len(self.fields) - editable
        unavailable = sum(not c["available"] for c in self.components)
        components = [{**c, "fields": [f.view for f in c["fields"]]} for c in self.components]
        return {
            "schema_revision": self.revision,
            "inputs": [f.view for f in self.inputs],
            "components": components,
            "coverage": {
                "status": "partial"
                if readonly or unavailable
                else "complete"
                if editable
                else "none",
                "editable_fields": editable,
                "readonly_fields": readonly,
                "unavailable_components": unavailable,
            },
        }

    def choices(self, record: Field) -> tuple[list[dict[str, Any]], bool]:
        definitions = record.definitions or [(None, record.spec)]
        # An explicit declaration's static set is a further restriction.
        if record.declared and record.spec.get("choices"):
            definitions = [*definitions, (None, record.spec)]
        result: list[dict[str, Any]] | None = None
        for plugin, definition in definitions:
            if definition.get("type") not in ("select", "multiselect"):
                continue
            rows, ready = resolve_choices(plugin, definition)
            if not ready:
                return [], False
            result = (
                rows
                if result is None
                else [r for r in result if _contains([x["value"] for x in rows], r["value"])]
            )
        return result or [], True

    def validate(self, record: Field, value: Any, error: str) -> None:
        spec = copy.deepcopy(record.spec)
        spec["editable"] = record.view["editable"]
        old, _ = self._values(record)
        if (
            spec["type"] == "multiselect"
            and isinstance(value, list)
            and any(_contains(value[:i], v) for i, v in enumerate(value))
        ):
            raise base.InvalidSettings(error, "Choose each option only once.")
        if spec["type"] in ("select", "multiselect") and value is not None:
            additions = (
                value if spec["type"] == "multiselect" and isinstance(value, list) else [value]
            )
            # Values already selected on every target remain valid even if a provider
            # is offline or a choice was deleted; order remains exactly as submitted.
            retained = old[0] if old and isinstance(old[0], list) else old[:1]
            for prior in old[1:]:
                prior_values = prior if isinstance(prior, list) else [prior]
                retained = [v for v in retained if _contains(prior_values, v)]
            if any(not _contains(retained, v) for v in additions):
                rows, ready = self.choices(record)
                if not ready:
                    raise base.InvalidSettings(
                        error, "Choices are unavailable. Retry before selecting a new value."
                    )
            else:
                rows = []
            spec["choices"] = rows + [{"value": v} for v in retained]
        spec.setdefault("targets", [])
        try:
            base._validate_input(spec, value)
        except base.InvalidSettings as exc:
            raise base.InvalidSettings(error, str(exc)) from None
        if type(value) in (int, float):
            for _, definition in record.definitions:
                minimum = definition.get("minimum", definition.get("min"))
                maximum = definition.get("maximum", definition.get("max"))
                step = definition.get("step")
                if (minimum is not None and value < minimum) or (
                    maximum is not None and value > maximum
                ):
                    raise base.InvalidSettings(error, "Enter a number within the allowed range.")
                if isinstance(step, (int, float)) and not isinstance(step, bool) and step > 0:
                    quotient = (value - (minimum or 0)) / step
                    if abs(quotient - round(quotient)) > 1e-8:
                        raise base.InvalidSettings(
                            error, "Enter a number matching the allowed step."
                        )

    def apply(self, body: dict[str, Any]) -> Page:
        values, answers, resets = (
            body.get("component_values", {}),
            body.get("inputs", {}),
            body.get("reset_fields", []),
        )
        if (
            not isinstance(values, dict)
            or not isinstance(answers, dict)
            or not isinstance(resets, list)
            or any(not isinstance(r, str) for r in resets)
        ):
            raise base.InvalidSettings(
                "component_values", "Supply objects for field values and an array for resets."
            )
        if (values or resets) and not isinstance(body.get("schema_revision"), str):
            raise base.InvalidSettings(
                "schema_revision", "Supply the current settings schema revision."
            )
        if "schema_revision" in body and body["schema_revision"] != self.revision:
            raise SchemaConflict()
        inputs = {f.spec["name"]: f for f in self.inputs}
        operations: list[tuple[Field, Any, bool, str]] = []
        for name, value in answers.items():
            if name not in inputs:
                raise base.InvalidSettings(f"inputs.{name}", "Unknown dashboard input.")
            operations.append((inputs[name], value, False, f"inputs.{name}"))
        for name, value in values.items():
            if name not in self.fields or self.fields[name].declared:
                raise base.InvalidSettings(f"component_values.{name}", "Unknown component field.")
            operations.append((self.fields[name], value, False, f"component_values.{name}"))
        for name in resets:
            if name not in self.fields:
                raise base.InvalidSettings(f"reset_fields.{name}", "Unknown settings field.")
            operations.append((self.fields[name], None, True, f"reset_fields.{name}"))
        written: list[tuple[Any, ...]] = []
        for record, value, reset, error in operations:
            if any(_overlap(p, old) for p in record.paths for old in written):
                raise base.InvalidSettings(
                    error, "The request writes the same target more than once."
                )
            written.extend(record.paths)
            if not record.view["editable"]:
                raise base.InvalidSettings(error, "Edit this field in the web editor.")
            if not reset:
                self.validate(record, value, error)
            for path in record.paths:
                container, key = _container(self.doc, path)
                if reset:
                    container.pop(key, None)
                elif (
                    record.declared
                    and len(path) == 6
                    and path[-3] == "sources"
                    and path[-1] == "headers"
                ):
                    container[key] = json.loads(value) if value else {}
                elif (
                    record.declared
                    and len(path) == 7
                    and path[-4] == "sources"
                    and path[-2] == "headers"
                ):
                    if value is None:
                        container.pop(key, None)
                    else:
                        container[key] = value
                elif (
                    record.declared
                    and len(path) == 6
                    and path[-3] == "sources"
                    and path[-1] == "url"
                ):
                    container[key] = value or ""
                else:
                    container[key] = copy.deepcopy(value)
        # Required aliases are enforced only when their targets were affected.
        for record in self.fields.values():
            if record.spec["required"] and any(
                _overlap(p, w) for p in record.paths for w in written
            ):
                effective, _ = self._values(record)
                if record.declared and any(
                    t["slot"] in ("source_header", "source_url") for t in record.spec["targets"]
                ):
                    effective = [_container(self.doc, p)[0].get(p[-1]) for p in record.paths]
                if any(base._empty(v) for v in effective):
                    raise base.InvalidSettings(
                        f"inputs.{record.spec['name']}"
                        if record.declared
                        else f"component_values.{record.id}",
                        "This field is required.",
                    )
        return Page.model_validate(self.doc)
