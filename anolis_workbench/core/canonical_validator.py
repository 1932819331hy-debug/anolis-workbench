"""Save-time validation for canonical projects (#255).

Two jobs the schemas cannot do on their own:

1. **Cross-artifact coherence** — provider ids appear in the profile, in every
   runtime variant's `providers[]`, and as config files; they must agree.
2. **Mirror install.sh's hard gates** so a project that will be refused at
   `sudo install.sh` time is refused at SAVE time instead, with a message that
   names the fix. The three gates are: the `manual` variant must be inert,
   every provider command must resolve to a PINNED kind, and config path
   tokens must carry the profile's own machine_id.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from anolis_workbench.core import canonical, machine_profile


def _resolve_workbench_port(default: int = 3010) -> int:
    raw = os.getenv("ANOLIS_WORKBENCH_PORT")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_project(document: dict[str, Any], project_dir: Any = None) -> list[str]:
    """Errors that must block a save. Empty list means the project is valid.

    `project_dir` enables the existence check on referenced files. It is worth
    doing at save time because the profile is a manifest: a reference to a file
    that isn't there blocks EVERY deploy of the project — including variants
    that have nothing to do with it — and the only symptom is a deploy-time
    error naming a file the user never knowingly added.
    """
    errors: list[str] = []
    profile = _as_mapping(document.get("profile"))
    variants = _as_mapping(document.get("variants"))
    providers = _as_mapping(document.get("providers"))

    machine_id = profile.get("machine_id")
    if not isinstance(machine_id, str) or machine_id == "":
        errors.append("machine-profile is missing machine_id.")
        machine_id = ""

    errors.extend(f"machine-profile: {message}" for message in machine_profile.schema_errors(profile))
    errors.extend(_reference_containment_errors(profile))
    errors.extend(_variant_errors(profile, variants, machine_id, providers))
    errors.extend(_provider_coherence_errors(profile, variants, providers))
    errors.extend(_pinned_provider_errors(profile, variants))
    errors.extend(_port_errors(variants))
    errors.extend(_bind_errors(variants))
    if project_dir is not None:
        errors.extend(_missing_behavior_errors(profile, project_dir))
    return errors


def _missing_behavior_errors(profile: dict[str, Any], project_dir: Any) -> list[str]:
    """Behaviour trees are authored outside the workbench, so the profile can
    name one that was never added. Provider configs and runtime variants are
    written by this same save, so they are deliberately not checked here."""
    behaviors = profile.get("behaviors")
    if not isinstance(behaviors, list):
        return []
    errors: list[str] = []
    for ref in behaviors:
        if not isinstance(ref, str) or machine_profile.containment_error(ref) is not None:
            continue
        if not (project_dir / ref).is_file():
            errors.append(
                f"Behavior tree '{ref}' is declared in the machine-profile but is not in the "
                "project. Add the file, or remove the variant that uses it — while it is missing "
                "NO variant of this project can be deployed."
            )
    return errors


def project_warnings(document: dict[str, Any]) -> list[str]:
    """Non-blocking observations. install.sh warns (never fails) when a pinned
    provider is referenced by no runtime config, so neither do we."""
    profile = _as_mapping(document.get("profile"))
    variants = _as_mapping(document.get("variants"))
    profile_providers = _as_mapping(profile.get("providers"))

    run_somewhere: set[str] = set()
    for doc in variants.values():
        if not isinstance(doc, dict):
            continue
        entries = doc.get("providers")
        if isinstance(entries, list):
            run_somewhere.update(str(e.get("id")) for e in entries if isinstance(e, dict) and e.get("id") is not None)
    return [
        f"Provider '{pid}' is declared in the machine-profile but run by no runtime variant."
        for pid in sorted(set(profile_providers) - run_somewhere)
    ]


def _reference_containment_errors(profile: dict[str, Any]) -> list[str]:
    """Every profile-relative reference must stay inside the project directory —
    the same rule imports enforce, applied before we WRITE these paths."""
    errors: list[str] = []
    for ref in machine_profile.referenced_files(profile):
        problem = machine_profile.containment_error(ref)
        if problem is not None:
            errors.append(f"machine-profile reference {problem}")
    return errors


def _variant_errors(
    profile: dict[str, Any], variants: dict[str, Any], machine_id: str, providers: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    declared = profile.get("runtime_profiles")
    if not isinstance(declared, dict) or not declared:
        return ["machine-profile declares no runtime_profiles."]

    if canonical.MANUAL_VARIANT not in declared:
        errors.append(
            f"No '{canonical.MANUAL_VARIANT}' runtime variant. install.sh boots the inert "
            f"'{canonical.MANUAL_VARIANT}' variant and refuses a profile without one."
        )

    # Every declared variant needs a document, or the profile ends up naming a
    # config file nothing writes — which blocks EVERY deploy of the project and
    # is invisible in the UI, since a variant with no document is not rendered.
    for variant in sorted(set(declared) - set(variants)):
        errors.append(
            f"Runtime variant '{variant}' is declared in the machine-profile but has no configuration in this document."
        )

    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            errors.append(f"Runtime variant '{variant}' is not a mapping.")
            continue
        for message in canonical.runtime_config_errors(doc):
            errors.append(f"Runtime variant '{variant}': {message}")
        if machine_id:
            declared_kinds = {pid: entry.get("kind") for pid, entry in providers.items() if isinstance(entry, dict)}
            errors.extend(
                f"Runtime variant '{variant}': {p}"
                for p in canonical.assert_deploy_tokens(machine_id, doc, declared_kinds)
            )

    # GATE 1 (install.sh verify_inert_runtime_config): the manual variant must
    # be inert, or a fresh install is refused outright.
    manual = variants.get(canonical.MANUAL_VARIANT)
    if isinstance(manual, dict):
        violation = canonical.inertness_violation(manual)
        if violation is not None:
            errors.append(
                f"The '{canonical.MANUAL_VARIANT}' variant is not inert ({violation}). "
                "install.sh refuses to install a non-inert variant — author automation "
                f"into the '{canonical.AUTOMATION_VARIANT}' variant instead."
            )
    return errors


def _provider_coherence_errors(
    profile: dict[str, Any], variants: dict[str, Any], providers: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    profile_providers = profile.get("providers")
    profile_ids = set(profile_providers) if isinstance(profile_providers, dict) else set()
    document_ids = set(providers)

    for pid in sorted(profile_ids - document_ids):
        errors.append(f"Provider '{pid}' is declared in the machine-profile but has no config.")
    for pid in sorted(document_ids - profile_ids):
        errors.append(f"Provider '{pid}' has a config but is not declared in the machine-profile.")

    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            continue
        entries = doc.get("providers")
        if not isinstance(entries, list):
            continue
        variant_ids = [str(e.get("id")) for e in entries if isinstance(e, dict) and e.get("id") is not None]
        if len(variant_ids) != len(set(variant_ids)):
            errors.append(f"Runtime variant '{variant}' has duplicate provider ids.")
        for pid in sorted(set(variant_ids) - profile_ids):
            errors.append(
                f"Runtime variant '{variant}' runs provider '{pid}' which the machine-profile does not declare."
            )
    return errors


def _pinned_provider_errors(profile: dict[str, Any], variants: dict[str, Any]) -> list[str]:
    """GATE 2 (install.sh #226 cross-validation).

    install.sh resolves the kind from the RENDERED COMMAND, not from the config
    filename, so this must key on the command token too — checking the derived
    `kind` instead would let a config whose command names a different provider
    sail through here and hard-fail at bundle time.
    """
    components = profile.get("components")
    if not isinstance(components, dict):
        # No components at all: deploy fails earlier with a clearer
        # "resolve component pins" message. Don't double-report.
        return []
    pinned = canonical.pinned_kinds(profile)

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for variant, doc in sorted(variants.items()):
        if not isinstance(doc, dict):
            continue
        for entry in doc.get("providers", []) or []:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id", "<unknown>"))
            kind = canonical.command_kind(entry.get("command"))
            if kind is None or kind in pinned or (pid, kind) in seen:
                continue
            seen.add((pid, kind))
            known = ", ".join(sorted(pinned)) or "(none pinned)"
            errors.append(
                f"Provider '{pid}' in variant '{variant}' runs kind '{kind}', which is not in the "
                f"profile's pinned components ({known}). install.sh refuses a provider command that "
                "does not resolve to a pinned component."
            )
    return errors


def _port_errors(variants: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reserved = _resolve_workbench_port()
    for variant, doc in variants.items():
        if not isinstance(doc, dict):
            continue
        http = doc.get("http")
        if not isinstance(http, dict):
            continue
        port = http.get("port")
        if isinstance(port, str):
            try:
                port = int(port)
            except ValueError:
                port = None
        if port == reserved:
            errors.append(
                f"Runtime variant '{variant}' uses HTTP port {reserved}, which conflicts "
                "with the workbench control server."
            )
    return errors


def _bind_errors(variants: dict[str, Any]) -> list[str]:
    """A non-loopback bind without auth is refused by install.sh — but LATE.

    Its check runs in the config phase, AFTER the binaries on the target have
    been replaced, so the operator is left mid-install. Catch it at save time
    while it is still a one-field fix.

    Read from the SERIALIZED text, because install.sh's reader is an awk that
    takes the raw field: it never strips quotes, so `bind: ""` and
    `bind: "[::1]"` are values it cannot recognise as loopback, and
    `auth_enabled: 'true'` is not the string `true` it compares against.
    """
    errors: list[str] = []
    for variant, doc in sorted(variants.items()):
        if not isinstance(doc, dict):
            continue
        text = yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        bind = canonical.http_value_text(text, "bind")
        # install.sh: `case "${bind:-127.0.0.1}" in 127.* | ::1 | localhost)`.
        # The default only applies when the field is absent entirely.
        if bind == "" or bind == "localhost" or bind == "::1" or bind.startswith("127."):
            continue
        if canonical.http_value_text(text, "auth_enabled") == "true":
            continue
        if canonical.http_value_text(text, "allow_insecure_bind") == "true":
            continue
        errors.append(
            f"Runtime variant '{variant}' binds HTTP to {bind}, which install.sh does not read as "
            "loopback, with authentication disabled. Set http.auth_enabled: true, or keep bind on "
            "an unquoted 127.0.0.1 and let install.sh do the LAN rewrite (which turns "
            "authentication on for you)."
        )
    return errors


__all__ = ["validate_project"]
