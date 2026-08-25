"""Idempotent preparation of official STM32N6 applications for deployment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from arona.contracts.v1 import DeploymentApplication
from arona.deployment.app_config import configure_mvp_application
from arona.deployment.stm32n6 import sync_stedgeai_runtime
from arona.deployment.telemetry import (
    configure_fixed_input_smoke,
    instrument_uart_telemetry,
)


@dataclass(frozen=True)
class DeploymentPreparation:
    """Files and decisions produced while preparing one official application."""

    runtime_manifest: Path
    runtime_version: str
    telemetry_changed: bool
    configuration: Path
    fixed_input_changed: bool
    input_mode: str


def prepare_deployment_application(
    application: DeploymentApplication,
    application_directory: Path,
    core_directory: Path,
    output_directory: Path,
    *,
    fixed_input: bool,
) -> DeploymentPreparation:
    """Verify runtime compatibility and apply all source preparation idempotently."""

    runtime_manifest = sync_stedgeai_runtime(
        application_directory,
        core_directory,
        output_directory / "runtime",
    )
    runtime_evidence = json.loads(runtime_manifest.read_text(encoding="utf-8"))
    runtime_version = str(runtime_evidence["ll_aton_version"])

    _, telemetry_changed = instrument_uart_telemetry(application, application_directory)
    configuration = configure_mvp_application(application, application_directory)
    _, fixed_input_changed = configure_fixed_input_smoke(
        application,
        application_directory,
        enabled=fixed_input,
    )
    return DeploymentPreparation(
        runtime_manifest=runtime_manifest,
        runtime_version=runtime_version,
        telemetry_changed=telemetry_changed,
        configuration=configuration,
        fixed_input_changed=fixed_input_changed,
        input_mode="fixed" if fixed_input else "camera",
    )
