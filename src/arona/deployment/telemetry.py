"""Instrument official STM32N6 applications with explicit UART evidence."""

from __future__ import annotations

from pathlib import Path

from arona.contracts.v1 import DeploymentApplication

TELEMETRY_PREFIX = "ARONA_INFERENCE seq="
FIXED_INPUT_MARKER = "ARONA_FIXED_INPUT_SMOKE"

_CAMERA_INITIALIZATION = (
    "  /*** Camera Init "
    "************************************************************/\n"
    "  uint32_t pitch_nn = 0;\n"
    "  CameraPipeline_Init((uint32_t *[2]) {&lcd_bg_area.XSize, "
    "&lcd_fg_area.XSize}, (uint32_t *[2]) {&lcd_bg_area.YSize, "
    "&lcd_fg_area.YSize}, &pitch_nn);\n"
    "\n"
    "  Display_init();\n"
    "\n"
    "  /* Start LCD Display camera pipe stream */\n"
    "  CameraPipeline_DisplayPipe_Start(lcd_bg_buffer, CMW_MODE_CONTINUOUS);\n"
)

_CAMERA_LOOP_INPUT = (
    """    CameraPipeline_IspUpdate();

#if DCMIPP_NN_NEEDS_CROP
    /* Start NN camera single capture Snapshot into intermediate buffer */
    CameraPipeline_NNPipe_Start(dcmipp_out_nn, CMW_MODE_SNAPSHOT);
#else
    /* Start NN camera single capture Snapshot directly into NN input */
    CameraPipeline_NNPipe_Start(nn_in, CMW_MODE_SNAPSHOT);
#endif

    while (cameraFrameReceived == 0) {};
    cameraFrameReceived = 0;

    uint32_t ts[2] = { 0 };

#if DCMIPP_NN_NEEDS_CROP
    /*
     * Crop the image: the DCMIPP hardware requires output dimensions to be
     * multiples of 16, so we crop the padded buffer into the NN input buffer.
     */
    SCB_InvalidateDCache_by_Addr(dcmipp_out_nn, sizeof(dcmipp_out_nn));
"""
    "    img_crop(dcmipp_out_nn, nn_in, pitch_nn, STAI_NETWORK_IN_1_WIDTH, "
    "STAI_NETWORK_IN_1_HEIGHT, STAI_NETWORK_IN_1_CHANNEL);\n"
    "    SCB_CleanInvalidateDCache_by_Addr(nn_in, nn_in_len);\n"
    "#endif\n"
)

_FIXED_INPUT_SUPPORT = """#define ARONA_FIXED_INPUT_SMOKE 1

static uint32_t arona_fixed_input_fnv1a;

static uint32_t ARONA_FixedInput_Init(stai_ptr input, uint32_t length)
{
  uint8_t *bytes = (uint8_t *) input;
  uint32_t hash = 2166136261u;

  for (uint32_t index = 0; index < length; index++)
  {
    uint8_t value = (uint8_t) ((index * 37u + 17u) & 0xffu);
    bytes[index] = value;
    hash = (hash ^ value) * 16777619u;
  }
  SCB_CleanInvalidateDCache_by_Addr(input, length);
  return hash;
}
"""


class TelemetryInstrumentationError(ValueError):
    """Raised when a known official source layout cannot be instrumented safely."""


def instrument_uart_telemetry(
    application: DeploymentApplication,
    application_directory: Path,
) -> tuple[Path, bool]:
    """Insert idempotent per-inference UART telemetry into an official main.c."""

    source_path = application_directory / "Src/main.c"
    if not source_path.is_file():
        raise TelemetryInstrumentationError(
            f"Official application source is missing: {source_path}"
        )

    source = source_path.read_text(encoding="utf-8")
    if TELEMETRY_PREFIX in source:
        return source_path, False

    if application == DeploymentApplication.IMAGE_CLASSIFICATION:
        global_anchor = "float nn_top1_output_class_proba;\n"
        loop_anchor = "    Display_NetworkOutput(ts[1] - ts[0]);\n"
        telemetry = (
            loop_anchor
            + '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu class=%s\\n",\n'
            + "           (unsigned long) ++arona_inference_sequence,\n"
            + "           (unsigned long) (ts[1] - ts[0]),\n"
            + "           nn_top1_output_class_name);\n"
        )
    elif application == DeploymentApplication.OBJECT_DETECTION:
        global_anchor = "od_pp_out_t pp_output;\n"
        loop_anchor = "    Display_NetworkOutput(&pp_output, ts[1] - ts[0]);\n"
        telemetry = (
            loop_anchor
            + '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu detections=%lu\\n",\n'
            + "           (unsigned long) ++arona_inference_sequence,\n"
            + "           (unsigned long) (ts[1] - ts[0]),\n"
            + "           (unsigned long) pp_output.nb_detect);\n"
        )
    else:  # pragma: no cover - exhaustive with the current enum
        raise TelemetryInstrumentationError(f"Unsupported application: {application}")

    source = _replace_once(
        source,
        global_anchor,
        global_anchor + "static uint32_t arona_inference_sequence;\n",
        "application globals",
    )
    source = _replace_once(source, loop_anchor, telemetry, "inference loop")
    source_path.write_text(source, encoding="utf-8")
    return source_path, True


def instrument_fixed_input_smoke(
    application: DeploymentApplication,
    application_directory: Path,
) -> tuple[Path, bool]:
    """Replace the camera path with deterministic input while retaining real NPU inference."""

    source_path, _ = instrument_uart_telemetry(application, application_directory)
    source = source_path.read_text(encoding="utf-8")
    if FIXED_INPUT_MARKER in source:
        return source_path, False

    source = _replace_once(
        source,
        "static uint32_t arona_inference_sequence;\n",
        "static uint32_t arona_inference_sequence;\n" + _FIXED_INPUT_SUPPORT,
        "telemetry globals",
    )
    source = _replace_once(
        source,
        _CAMERA_INITIALIZATION,
        """#if !ARONA_FIXED_INPUT_SMOKE
"""
        + _CAMERA_INITIALIZATION
        + """#else
  arona_fixed_input_fnv1a = ARONA_FixedInput_Init(nn_in, nn_in_len);
#endif
""",
        "camera initialization",
    )
    source = _replace_once(
        source,
        _CAMERA_LOOP_INPUT,
        """#if !ARONA_FIXED_INPUT_SMOKE
"""
        + _CAMERA_LOOP_INPUT
        + """#else
    uint32_t ts[2] = { 0 };
#endif
""",
        "camera loop input",
    )

    if application == DeploymentApplication.IMAGE_CLASSIFICATION:
        display_call = "    Display_NetworkOutput(ts[1] - ts[0]);\n"
        old_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu class=%s\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           nn_top1_output_class_name);\n"
        )
        new_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu class=%s '
            'model=%s input=fixed fnv1a=0x%08lx\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           nn_top1_output_class_name,\n"
            "           STAI_NETWORK_ORIGIN_MODEL_NAME,\n"
            "           (unsigned long) arona_fixed_input_fnv1a);\n"
        )
    elif application == DeploymentApplication.OBJECT_DETECTION:
        display_call = "    Display_NetworkOutput(&pp_output, ts[1] - ts[0]);\n"
        old_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu detections=%lu\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           (unsigned long) pp_output.nb_detect);\n"
        )
        new_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu detections=%lu '
            'model=%s input=fixed fnv1a=0x%08lx\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           (unsigned long) pp_output.nb_detect,\n"
            "           STAI_NETWORK_ORIGIN_MODEL_NAME,\n"
            "           (unsigned long) arona_fixed_input_fnv1a);\n"
        )
    else:  # pragma: no cover - exhaustive with the current enum
        raise TelemetryInstrumentationError(f"Unsupported application: {application}")

    source = _replace_once(
        source,
        display_call,
        "#if !ARONA_FIXED_INPUT_SMOKE\n" + display_call + "#endif\n",
        "display output",
    )
    source = _replace_once(source, old_telemetry, new_telemetry, "UART telemetry")
    source_path.write_text(source, encoding="utf-8")
    return source_path, True


def configure_fixed_input_smoke(
    application: DeploymentApplication,
    application_directory: Path,
    *,
    enabled: bool,
) -> tuple[Path, bool]:
    """Apply the requested camera/fixed-input mode without duplicating instrumentation."""

    if enabled:
        return instrument_fixed_input_smoke(application, application_directory)

    source_path, telemetry_changed = instrument_uart_telemetry(
        application,
        application_directory,
    )
    source = source_path.read_text(encoding="utf-8")
    if FIXED_INPUT_MARKER not in source:
        return source_path, telemetry_changed

    source = _restore_camera_input(application, source)
    source_path.write_text(source, encoding="utf-8")
    return source_path, True


def _restore_camera_input(application: DeploymentApplication, source: str) -> str:
    source = _replace_once(
        source,
        "static uint32_t arona_inference_sequence;\n" + _FIXED_INPUT_SUPPORT,
        "static uint32_t arona_inference_sequence;\n",
        "fixed-input support",
    )
    source = _replace_once(
        source,
        "#if !ARONA_FIXED_INPUT_SMOKE\n"
        + _CAMERA_INITIALIZATION
        + "#else\n"
        + "  arona_fixed_input_fnv1a = ARONA_FixedInput_Init(nn_in, nn_in_len);\n"
        + "#endif\n",
        _CAMERA_INITIALIZATION,
        "fixed-input camera initialization",
    )
    source = _replace_once(
        source,
        "#if !ARONA_FIXED_INPUT_SMOKE\n"
        + _CAMERA_LOOP_INPUT
        + "#else\n"
        + "    uint32_t ts[2] = { 0 };\n"
        + "#endif\n",
        _CAMERA_LOOP_INPUT,
        "fixed-input camera loop",
    )

    if application == DeploymentApplication.IMAGE_CLASSIFICATION:
        display_call = "    Display_NetworkOutput(ts[1] - ts[0]);\n"
        fixed_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu class=%s '
            'model=%s input=fixed fnv1a=0x%08lx\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           nn_top1_output_class_name,\n"
            "           STAI_NETWORK_ORIGIN_MODEL_NAME,\n"
            "           (unsigned long) arona_fixed_input_fnv1a);\n"
        )
        camera_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu class=%s\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           nn_top1_output_class_name);\n"
        )
    elif application == DeploymentApplication.OBJECT_DETECTION:
        display_call = "    Display_NetworkOutput(&pp_output, ts[1] - ts[0]);\n"
        fixed_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu detections=%lu '
            'model=%s input=fixed fnv1a=0x%08lx\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           (unsigned long) pp_output.nb_detect,\n"
            "           STAI_NETWORK_ORIGIN_MODEL_NAME,\n"
            "           (unsigned long) arona_fixed_input_fnv1a);\n"
        )
        camera_telemetry = (
            '    printf("ARONA_INFERENCE seq=%lu latency_ms=%lu detections=%lu\\n",\n'
            "           (unsigned long) ++arona_inference_sequence,\n"
            "           (unsigned long) (ts[1] - ts[0]),\n"
            "           (unsigned long) pp_output.nb_detect);\n"
        )
    else:  # pragma: no cover - exhaustive with the current enum
        raise TelemetryInstrumentationError(f"Unsupported application: {application}")

    source = _replace_once(
        source,
        "#if !ARONA_FIXED_INPUT_SMOKE\n" + display_call + "#endif\n",
        display_call,
        "fixed-input display output",
    )
    return _replace_once(
        source,
        fixed_telemetry,
        camera_telemetry,
        "fixed-input UART telemetry",
    )


def _replace_once(source: str, anchor: str, replacement: str, label: str) -> str:
    occurrences = source.count(anchor)
    if occurrences != 1:
        raise TelemetryInstrumentationError(
            f"Expected one {label} anchor in official main.c, found {occurrences}."
        )
    return source.replace(anchor, replacement, 1)
