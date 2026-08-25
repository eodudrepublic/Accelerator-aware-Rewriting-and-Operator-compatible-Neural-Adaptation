import subprocess
from collections import deque
from pathlib import Path

from arona.contracts.v1 import DeploymentApplication, StageStatus
from arona.deployment.app_config import FOOD101_CLASSES, configure_mvp_application
from arona.deployment.commands import CommandOutcome, SubprocessCommandRunner, first_error
from arona.deployment.stm32n6 import (
    FirmwareImage,
    NucleoDeploymentConfig,
    Stm32N6Deployer,
    parse_inference_observations,
    sync_stedgeai_runtime,
)
from arona.deployment.telemetry import (
    configure_fixed_input_smoke,
    instrument_fixed_input_smoke,
    instrument_uart_telemetry,
)


class FakeRunner:
    def __init__(self, outcomes: list[CommandOutcome]) -> None:
        self.outcomes = deque(outcomes)
        self.commands: list[list[str]] = []

    def run(
        self,
        command: list[str],
        *,
        working_directory: Path,
        timeout_seconds: int,
        environment: dict[str, str] | None = None,
    ) -> CommandOutcome:
        self.commands.append(command)
        outcome = self.outcomes.popleft()
        return CommandOutcome(
            command=tuple(command),
            working_directory=working_directory,
            exit_code=outcome.exit_code,
            duration_ms=outcome.duration_ms,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
        )


def test_build_captures_command_and_signed_artifact(tmp_path: Path) -> None:
    application_directory = tmp_path / "Application/NUCLEO-N657X0-Q"
    signed_hex = application_directory / "build/Application/NUCLEO-N657X0-Q/Project_sign.hex"
    signed_hex.parent.mkdir(parents=True)
    signed_hex.write_bytes(b":020000040701F2\n")
    (application_directory / "Makefile").write_text("sign:\n", encoding="utf-8")
    model_directory = tmp_path / "generated-model"
    model_directory.mkdir()
    make, gcc, signer = _build_tools(tmp_path)
    runner = FakeRunner([_outcome(stdout="build complete")])

    result = Stm32N6Deployer(runner).build(
        _config(),
        application_directory,
        tmp_path / "evidence",
        make_executable=make,
        gcc_directory=gcc,
        signing_tool=signer,
        model_directory=model_directory,
    )

    assert result.status == StageStatus.SUCCEEDED
    assert result.stages[0].status == StageStatus.SUCCEEDED
    assert result.firmware[0].sha256 is not None
    assert (tmp_path / "evidence/build.json").is_file()
    assert (tmp_path / "evidence/deployment-result.json").is_file()
    assert "sign" in runner.commands[0]
    assert "BUILD_TOP=build" in runner.commands[0]
    assert "MODEL_DIR=../../generated-model" in runner.commands[0]
    assert f"SIGNER={signer.as_posix()} -align" in runner.commands[0]


def test_generate_packages_selected_model_without_overwriting_vendor_model(tmp_path: Path) -> None:
    model = tmp_path / "selected.onnx"
    model.write_bytes(b"onnx")
    support = tmp_path / "official/Model"
    support.mkdir(parents=True)
    (support / "user_neuralart_NUCLEO-N657X0-Q.json").write_text("{}", encoding="utf-8")
    evidence = tmp_path / "evidence"
    generated = evidence / "stedgeai-output"
    generated.mkdir(parents=True)
    for name in (
        "network.c",
        "network_ecblobs.h",
        "stai_network.c",
        "stai_network.h",
        "network_atonbuf.xSPI2.raw",
    ):
        (generated / name).write_bytes(name.encode())
    staged = evidence / "model-files"
    staged.mkdir()
    (staged / "network_data.hex").write_text(":00000001FF\n", encoding="utf-8")
    stedgeai = tmp_path / "stedgeai.exe"
    objcopy = tmp_path / "arm-none-eabi-objcopy.exe"
    stedgeai.touch()
    objcopy.touch()
    runner = FakeRunner([_outcome(stdout="generated"), _outcome(stdout="packaged")])

    result = Stm32N6Deployer(runner).generate(
        _config(),
        model,
        support,
        evidence,
        stedgeai_executable=stedgeai,
        objcopy_executable=objcopy,
    )

    assert result.status == StageStatus.SUCCEEDED
    assert "--output-data-type" in runner.commands[0]
    assert "float32" in runner.commands[0]
    assert "-I" in runner.commands[1]
    assert result.firmware[0].path.endswith("network_data.hex")


def test_build_rejects_unsafe_build_directory(tmp_path: Path) -> None:
    application_directory = tmp_path / "Application/NUCLEO-N657X0-Q"
    application_directory.mkdir(parents=True)
    (application_directory / "Makefile").write_text("sign:\n", encoding="utf-8")
    make, gcc, signer = _build_tools(tmp_path)

    result = Stm32N6Deployer(FakeRunner([])).build(
        _config(),
        application_directory,
        tmp_path / "evidence",
        make_executable=make,
        gcc_directory=gcc,
        signing_tool=signer,
        build_top="../outside",
    )

    assert result.status == StageStatus.FAILED
    assert "Invalid build directory" in (result.reason or "")


def test_sync_runtime_overlays_core_and_build_selects_newest_library(tmp_path: Path) -> None:
    application_directory = tmp_path / "official/Application/NUCLEO-N657X0-Q"
    application_directory.mkdir(parents=True)
    (application_directory / "Makefile").write_text("sign:\n", encoding="utf-8")
    destination = tmp_path / "official/Middlewares/stedgeai-lib"
    destination.mkdir(parents=True)
    core = tmp_path / "STEdgeAI/4.0"
    runtime = core / "Middlewares/ST/AI"
    for component in ("Inc", "Lib", "Misc", "Npu", "Reloc", "SystemPerformance"):
        (runtime / component).mkdir(parents=True)
        (runtime / component / "component.txt").write_text(component, encoding="utf-8")
    version_header = runtime / "Npu/ll_aton/ll_aton_version.h"
    version_header.parent.mkdir(parents=True, exist_ok=True)
    version_header.write_text(
        '#define LL_ATON_VERSION_NAME  "atonn-v1.1.3-275-test"\n',
        encoding="utf-8",
    )
    library = runtime / "Lib/GCC/ARMCortexM55/NetworkRuntime1201_CM55_GCC.a"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"runtime-1201")

    manifest = sync_stedgeai_runtime(application_directory, core, tmp_path / "evidence")

    assert manifest.is_file()
    assert "NetworkRuntime1201_CM55_GCC.a" in manifest.read_text(encoding="utf-8")
    assert (destination / "Npu/ll_aton/ll_aton_version.h").read_bytes() == (
        version_header.read_bytes()
    )

    signed_hex = application_directory / "build/Application/NUCLEO-N657X0-Q/Project_sign.hex"
    signed_hex.parent.mkdir(parents=True)
    signed_hex.write_bytes(b":020000040701F2\n")
    make, gcc, signer = _build_tools(tmp_path)
    runner = FakeRunner([_outcome(stdout="build complete")])
    result = Stm32N6Deployer(runner).build(
        _config(),
        application_directory,
        tmp_path / "build-evidence",
        make_executable=make,
        gcc_directory=gcc,
        signing_tool=signer,
    )

    assert result.status == StageStatus.SUCCEEDED
    assert "LIBS=-lc -lm -lnosys -l:NetworkRuntime1201_CM55_GCC.a" in runner.commands[0]


def test_program_stops_before_write_on_board_mismatch(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.hex"
    firmware.write_text(":00000001FF\n", encoding="utf-8")
    programmer, loader = _program_tools(tmp_path)
    runner = FakeRunner([_outcome(stdout="Board : STM32N6570-DK\n")])
    config = _config(programmer=programmer, external_loader=loader)

    result = Stm32N6Deployer(runner).program(
        config,
        [FirmwareImage(firmware, "test firmware")],
        tmp_path / "evidence",
    )

    assert result.status == StageStatus.FAILED
    assert len(runner.commands) == 1
    assert "Expected NUCLEO-N657X0-Q" in (result.reason or "")


def test_program_records_order_and_requires_later_validation(tmp_path: Path) -> None:
    first = tmp_path / "fsbl.hex"
    second = tmp_path / "application.bin"
    first.write_text(":00000001FF\n", encoding="utf-8")
    second.write_bytes(b"application")
    programmer, loader = _program_tools(tmp_path)
    runner = FakeRunner(
        [
            _outcome(stdout="Board       : NUCLEO-N657X0-Q\n"),
            _outcome(stdout="Download verified successfully\n"),
            _outcome(stdout="Download verified successfully\n"),
        ]
    )

    result = Stm32N6Deployer(runner).program(
        _config(programmer=programmer, external_loader=loader),
        [
            FirmwareImage(first, "FSBL"),
            FirmwareImage(second, "signed application", "0x70100000"),
        ],
        tmp_path / "evidence",
    )

    assert result.status == StageStatus.WARNING
    assert len(result.firmware) == 2
    assert len(runner.commands) == 3
    assert str(first.resolve()) in runner.commands[1]
    assert runner.commands[2][-2:] == ["0x70100000", "-v"]
    assert runner.commands[1][-1] == "-v"
    assert "serial validation" in (result.reason or "")


def test_program_rejects_binary_without_address(tmp_path: Path) -> None:
    firmware = tmp_path / "application.bin"
    firmware.write_bytes(b"application")

    result = Stm32N6Deployer(FakeRunner([])).program(
        _config(),
        [FirmwareImage(firmware, "application")],
        tmp_path / "evidence",
    )

    assert result.status == StageStatus.FAILED
    assert "explicit address" in (result.reason or "")


def test_backup_requires_expected_size_and_records_upload(tmp_path: Path) -> None:
    programmer, loader = _program_tools(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    backup = evidence / "external-flash-0x70000000-16.bin"
    backup.write_bytes(bytes(16))
    runner = FakeRunner(
        [
            _outcome(stdout="Board       : NUCLEO-N657X0-Q\n"),
            _outcome(stdout="Upload verified successfully\n"),
        ]
    )

    result = Stm32N6Deployer(runner).backup_external_flash(
        _config(programmer=programmer, external_loader=loader),
        evidence,
        size_bytes=16,
    )

    assert result.status == StageStatus.SUCCEEDED
    assert len(runner.commands) == 2
    assert "-u" in runner.commands[1]
    assert "0x10" in runner.commands[1]
    assert result.stages[-1].artifacts[-1].sha256 is not None


def test_parse_inference_observations_requires_explicit_telemetry() -> None:
    observations = parse_inference_observations(
        [
            "STM32N6 booted",
            "ARONA_INFERENCE seq=1 latency_ms=2.5 class=pizza score=0.91",
            "Inference: 3ms",
            "ARONA_INFERENCE seq=2 latency_ms=2 class=pasta score=0.75",
        ]
    )

    assert [item.sequence for item in observations] == [1, 2]
    assert [item.latency_ms for item in observations] == [2.5, 2.0]
    assert observations[0].summary == "class=pizza score=0.91"


def test_validate_serial_requires_five_observations(monkeypatch, tmp_path: Path) -> None:
    from arona.deployment import stm32n6

    lines = [
        "NN model: mobilenetv2_food101",
        *[f"ARONA_INFERENCE seq={index} latency_ms=3 class=pizza" for index in range(1, 6)],
    ]
    monkeypatch.setattr(stm32n6, "_capture_serial", lambda *args, **kwargs: lines)

    result = Stm32N6Deployer().validate_serial(
        _config(boot_mode="flash"),
        tmp_path / "evidence",
        minimum_inferences=5,
        capture_seconds=0.1,
        expected_model_name="mobilenetv2_food101",
    )

    assert result.status == StageStatus.SUCCEEDED
    assert len(result.observations) == 5
    assert result.stages[0].status == StageStatus.SUCCEEDED


def test_validate_serial_requires_expected_fixed_input_hash(monkeypatch, tmp_path: Path) -> None:
    from arona.deployment import stm32n6

    model_name = "mobilenetv2_food101"
    lines = [
        (
            f"ARONA_INFERENCE seq={index} latency_ms=3 class=pizza model={model_name} "
            "input=fixed fnv1a=0xfbe51dc5"
        )
        for index in range(1, 6)
    ]
    monkeypatch.setattr(stm32n6, "_capture_serial", lambda *args, **kwargs: lines)

    succeeded = Stm32N6Deployer().validate_serial(
        _config(boot_mode="flash"),
        tmp_path / "succeeded",
        minimum_inferences=5,
        capture_seconds=0.1,
        expected_model_name=model_name,
        expected_input_fnv1a="0xfbe51dc5",
    )
    failed = Stm32N6Deployer().validate_serial(
        _config(boot_mode="flash"),
        tmp_path / "failed",
        minimum_inferences=5,
        capture_seconds=0.1,
        expected_model_name=model_name,
        expected_input_fnv1a="0x00000000",
    )

    assert succeeded.status == StageStatus.SUCCEEDED
    assert failed.status == StageStatus.FAILED
    assert "fixed input" in (failed.reason or "")


def test_command_timeout_preserves_partial_output(monkeypatch, tmp_path: Path) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["tool"],
            timeout=3,
            output=b"partial stdout",
            stderr=b"partial error",
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    outcome = SubprocessCommandRunner().run(
        ["tool"],
        working_directory=tmp_path,
        timeout_seconds=3,
    )

    assert outcome.timed_out
    assert outcome.exit_code is None
    assert outcome.stdout == "partial stdout"
    assert outcome.stderr == "partial error"
    assert first_error(outcome) == "Command timed out."


def test_instrument_image_classification_telemetry_is_idempotent(tmp_path: Path) -> None:
    application_directory = tmp_path / "Application/NUCLEO-N657X0-Q"
    source_path = application_directory / "Src/main.c"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "float nn_top1_output_class_proba;\n    Display_NetworkOutput(ts[1] - ts[0]);\n",
        encoding="utf-8",
    )

    _, first_changed = instrument_uart_telemetry(
        DeploymentApplication.IMAGE_CLASSIFICATION,
        application_directory,
    )
    _, second_changed = instrument_uart_telemetry(
        DeploymentApplication.IMAGE_CLASSIFICATION,
        application_directory,
    )

    source = source_path.read_text(encoding="utf-8")
    assert first_changed
    assert not second_changed
    assert source.count("ARONA_INFERENCE seq=") == 1
    assert "static uint32_t arona_inference_sequence;" in source


def test_instrument_object_detection_telemetry(tmp_path: Path) -> None:
    application_directory = tmp_path / "Application/NUCLEO-N657X0-Q"
    source_path = application_directory / "Src/main.c"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "od_pp_out_t pp_output;\n    Display_NetworkOutput(&pp_output, ts[1] - ts[0]);\n",
        encoding="utf-8",
    )

    _, changed = instrument_uart_telemetry(
        DeploymentApplication.OBJECT_DETECTION,
        application_directory,
    )

    source = source_path.read_text(encoding="utf-8")
    assert changed
    assert "detections=%lu" in source


def test_fixed_input_smoke_is_idempotent_and_keeps_real_inference(tmp_path: Path) -> None:
    for application in (
        DeploymentApplication.IMAGE_CLASSIFICATION,
        DeploymentApplication.OBJECT_DETECTION,
    ):
        application_directory = tmp_path / application / "Application/NUCLEO-N657X0-Q"
        source_path = application_directory / "Src/main.c"
        source_path.parent.mkdir(parents=True)
        source_path.write_text(_official_camera_source(application), encoding="utf-8")

        _, first_changed = instrument_fixed_input_smoke(application, application_directory)
        _, second_changed = instrument_fixed_input_smoke(application, application_directory)

        source = source_path.read_text(encoding="utf-8")
        assert first_changed
        assert not second_changed
        assert "#define ARONA_FIXED_INPUT_SMOKE 1" in source
        assert "ARONA_FixedInput_Init(nn_in, nn_in_len)" in source
        assert "stai_network_run(network_context, STAI_MODE_SYNC)" in source
        assert "model=%s input=fixed fnv1a=0x%08lx" in source
        assert "#if !ARONA_FIXED_INPUT_SMOKE\n    CameraPipeline_IspUpdate();" in source


def test_fixed_input_mode_can_return_to_camera_input(tmp_path: Path) -> None:
    for application in (
        DeploymentApplication.IMAGE_CLASSIFICATION,
        DeploymentApplication.OBJECT_DETECTION,
    ):
        application_directory = tmp_path / application / "Application/NUCLEO-N657X0-Q"
        source_path = application_directory / "Src/main.c"
        source_path.parent.mkdir(parents=True)
        original = _official_camera_source(application)
        source_path.write_text(original, encoding="utf-8")

        instrument_uart_telemetry(application, application_directory)
        expected_camera_source = source_path.read_text(encoding="utf-8")
        source_path.write_text(original, encoding="utf-8")

        _, enabled_changed = configure_fixed_input_smoke(
            application,
            application_directory,
            enabled=True,
        )
        _, disabled_changed = configure_fixed_input_smoke(
            application,
            application_directory,
            enabled=False,
        )

        assert enabled_changed
        assert disabled_changed
        assert source_path.read_text(encoding="utf-8") == expected_camera_source


def test_configure_fixed_mvp_applications(tmp_path: Path) -> None:
    image_application = tmp_path / "image"
    object_application = tmp_path / "object"
    (image_application / "Inc").mkdir(parents=True)
    (object_application / "Inc").mkdir(parents=True)

    image_config = configure_mvp_application(
        DeploymentApplication.IMAGE_CLASSIFICATION,
        image_application,
    ).read_text(encoding="utf-8")
    object_config = configure_mvp_application(
        DeploymentApplication.OBJECT_DETECTION,
        object_application,
    ).read_text(encoding="utf-8")

    assert len(FOOD101_CLASSES) == 101
    assert "#define NB_CLASSES 101" in image_config
    assert '"apple_pie"' in image_config
    assert '"waffles"' in image_config
    assert "POSTPROCESS_OD_YOLO_V8_UI" in object_config
    assert "AI_OD_YOLOV8_PP_TOTAL_BOXES     (1344)" in object_config


def _config(**changes: object) -> NucleoDeploymentConfig:
    values: dict[str, object] = {
        "application": DeploymentApplication.IMAGE_CLASSIFICATION,
    }
    values.update(changes)
    return NucleoDeploymentConfig(**values)  # type: ignore[arg-type]


def _official_camera_source(application: DeploymentApplication) -> str:
    if application == DeploymentApplication.IMAGE_CLASSIFICATION:
        global_value = "float nn_top1_output_class_proba;"
        display_call = "    Display_NetworkOutput(ts[1] - ts[0]);"
    else:
        global_value = "od_pp_out_t pp_output;"
        display_call = "    Display_NetworkOutput(&pp_output, ts[1] - ts[0]);"
    camera_initialization = (
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
    camera_loop = (
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
        """    SCB_CleanInvalidateDCache_by_Addr(nn_in, nn_in_len);
#endif
"""
    )
    return f"""{global_value}
{camera_initialization}  while (1)
  {{
{camera_loop}    stai_network_run(network_context, STAI_MODE_SYNC);
{display_call}
  }}
}}
"""


def _outcome(
    *,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> CommandOutcome:
    return CommandOutcome(
        command=("placeholder",),
        working_directory=Path.cwd(),
        exit_code=exit_code,
        duration_ms=12.5,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def _build_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    make = tmp_path / "make.exe"
    gcc = tmp_path / "gcc"
    signer = tmp_path / "signer.exe"
    make.touch()
    gcc.mkdir()
    signer.touch()
    return make, gcc, signer


def _program_tools(tmp_path: Path) -> tuple[Path, Path]:
    programmer = tmp_path / "STM32_Programmer_CLI.exe"
    loader = tmp_path / "MX25UM51245G_STM32N6570-NUCLEO.stldr"
    programmer.touch()
    loader.touch()
    return programmer, loader
