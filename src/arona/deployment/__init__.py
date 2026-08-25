"""STM32 deployment orchestration and evidence capture."""

from arona.deployment.app_config import configure_mvp_application
from arona.deployment.preparation import (
    DeploymentPreparation,
    prepare_deployment_application,
)
from arona.deployment.stm32n6 import (
    FirmwareImage,
    NucleoDeploymentConfig,
    Stm32N6Deployer,
    sync_stedgeai_runtime,
)
from arona.deployment.telemetry import (
    TelemetryInstrumentationError,
    configure_fixed_input_smoke,
    instrument_fixed_input_smoke,
    instrument_uart_telemetry,
)

__all__ = [
    "DeploymentPreparation",
    "FirmwareImage",
    "NucleoDeploymentConfig",
    "Stm32N6Deployer",
    "TelemetryInstrumentationError",
    "configure_fixed_input_smoke",
    "configure_mvp_application",
    "instrument_fixed_input_smoke",
    "instrument_uart_telemetry",
    "prepare_deployment_application",
    "sync_stedgeai_runtime",
]
