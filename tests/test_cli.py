from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import onnx
from onnx import TensorProto, helper
from typer.testing import CliRunner

from arona.cli import app
from arona.contracts.v1 import (
    ArtifactKind,
    ArtifactRef,
    DeploymentApplication,
    DeploymentResult,
    DeploymentStage,
    DeploymentStageName,
    InferenceObservation,
    StageStatus,
)

runner = CliRunner()
ROOT = Path(__file__).parents[1]


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "ARONA  Version" in result.stdout
    assert "version:  0.1.0" in result.stdout


def test_help_uses_arona_color_theme() -> None:
    from typer import rich_utils

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Options" in result.stdout
    assert "Commands" in result.stdout
    assert rich_utils.STYLE_OPTION == "bold #4ea8d7"
    assert rich_utils.STYLE_USAGE == "#d76f9f"


def test_schema_export_command(tmp_path: Path) -> None:
    result = runner.invoke(app, ["schema", "export", "--output-directory", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / "device-discovery.schema.json").is_file()
    assert (tmp_path / "device-probe.schema.json").is_file()
    assert (tmp_path / "deployment-result.schema.json").is_file()
    assert (tmp_path / "optimize-request.schema.json").is_file()
    assert (tmp_path / "postprocess.schema.json").is_file()
    assert (tmp_path / "run-report.schema.json").is_file()


def test_analyze_command_writes_json_and_markdown_report(tmp_path: Path) -> None:
    model_path = tmp_path / "model.onnx"
    _write_identity_model(model_path)
    compiler_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log"

    result = runner.invoke(
        app,
        [
            "analyze",
            str(model_path),
            "--compiler-log",
            str(compiler_log),
            "--output-directory",
            str(tmp_path / "outputs"),
        ],
    )

    assert result.exit_code == 0
    assert "software=1530" in result.stdout
    assert "deployable:" in result.stdout
    assert "infeasible" in result.stdout
    run_dirs = list((tmp_path / "outputs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "original-analysis.json").is_file()
    assert (run_dirs[0] / "report.md").is_file()


def test_optimize_command_writes_rewrite_validation_and_comparison(tmp_path: Path) -> None:
    model_path = tmp_path / "model.onnx"
    _write_terminal_argmax_model(model_path)
    baseline_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log"
    candidate_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_xip_101/compiler.log"

    result = runner.invoke(
        app,
        [
            "optimize",
            str(model_path),
            "--compiler-log",
            str(baseline_log),
            "--candidate-compiler-log",
            str(candidate_log),
            "--output-directory",
            str(tmp_path / "outputs"),
        ],
    )

    assert result.exit_code == 0
    assert "selected: optimized" in result.stdout
    run_dirs = list((tmp_path / "outputs").iterdir())
    assert len(run_dirs) == 1
    for filename in (
        "original-analysis.json",
        "optimized-model.onnx",
        "optimized-analysis.json",
        "rewrite-history.json",
        "postprocess.json",
        "validation.json",
        "run-report.json",
        "report.md",
    ):
        assert (run_dirs[0] / filename).is_file()


def test_optimize_command_attaches_deployment_result(tmp_path: Path) -> None:
    model_path = tmp_path / "model.onnx"
    _write_terminal_argmax_model(model_path)
    validation_input = tmp_path / "validation-input"
    validation_input.mkdir()
    deployment_result_path = tmp_path / "deployment-result.json"
    _write_deployment_result(deployment_result_path)
    baseline_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log"
    candidate_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_xip_101/compiler.log"

    result = runner.invoke(
        app,
        [
            "optimize",
            str(model_path),
            "--target",
            "stedgeai",
            "--validation-input",
            str(validation_input),
            "--deploy",
            "--deployment-result",
            str(deployment_result_path),
            "--compiler-log",
            str(baseline_log),
            "--candidate-compiler-log",
            str(candidate_log),
            "--output-directory",
            str(tmp_path / "outputs"),
        ],
    )

    assert result.exit_code == 0
    assert "Board deployment" in result.stdout
    assert "observations:" in result.stdout
    assert "5/5 succeeded" in result.stdout
    run_dirs = list((tmp_path / "outputs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "deployment/deployment-result.json").is_file()
    assert (run_dirs[0] / "deployment-analysis.json").is_file()
    report_markdown = (run_dirs[0] / "report.md").read_text(encoding="utf-8")
    assert "## Board Deployment" in report_markdown
    assert "Latency mean ms: 2.650" in report_markdown


def test_optimize_deploy_runs_live_stm32n6_sequence(monkeypatch, tmp_path: Path) -> None:
    from arona import cli

    model_path = tmp_path / "model.onnx"
    _write_terminal_argmax_model(model_path)
    validation_input = tmp_path / "validation-input"
    validation_input.mkdir()
    application_directory = tmp_path / "official/Application/NUCLEO-N657X0-Q"
    application_directory.mkdir(parents=True)
    model_support_directory = tmp_path / "official/Model"
    model_support_directory.mkdir(parents=True)
    fsbl = tmp_path / "official/FSBL/ai_fsbl.hex"
    fsbl.parent.mkdir(parents=True)
    fsbl.write_text(":00000001FF\n", encoding="utf-8")
    baseline_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log"
    candidate_log = ROOT / "tests/fixtures/backends/stedgeai/conmamba_xip_101/compiler.log"
    calls: list[str] = []

    class FakeDeployer:
        def generate(
            self,
            config: object,
            model: Path,
            support: Path,
            output: Path,
        ) -> DeploymentResult:
            calls.append(f"generate:{model.name}:{support.name}")
            model_files = output / "model-files"
            model_files.mkdir(parents=True)
            (model_files / "network_data.hex").write_text(":00000001FF\n", encoding="utf-8")
            return _deployment_result(
                DeploymentStageName.CODEGEN,
                model=model,
                firmware=[_artifact(model_files / "network_data.hex")],
            )

        def build(
            self,
            config: object,
            application: Path,
            output: Path,
            *,
            jobs: int,
            build_top: str,
            model_directory: Path,
            screen_interface: str,
        ) -> DeploymentResult:
            calls.append(
                f"build:{application.name}:{model_directory.name}:{jobs}:{screen_interface}"
            )
            signed = application / build_top / "Application/NUCLEO-N657X0-Q/Project_sign.hex"
            signed.parent.mkdir(parents=True)
            signed.write_text(":00000001FF\n", encoding="utf-8")
            return _deployment_result(
                DeploymentStageName.LINK,
                firmware=[_artifact(signed)],
            )

        def program(
            self,
            config: object,
            firmware: list[object],
            output: Path,
            *,
            model_path: Path | None,
        ) -> DeploymentResult:
            calls.append(f"program:{len(firmware)}:{model_path.name if model_path else ''}")
            return _deployment_result(DeploymentStageName.PROGRAMMING, model=model_path)

        def validate_serial(
            self,
            config: object,
            output: Path,
            *,
            minimum_inferences: int,
            capture_seconds: float,
            expected_model_name: str | None,
            expected_input_fnv1a: str | None,
        ) -> DeploymentResult:
            calls.append(
                f"validate:{minimum_inferences}:{expected_model_name}:{expected_input_fnv1a}"
            )
            return _deployment_result(
                DeploymentStageName.VALIDATION,
                observations=[
                    InferenceObservation(
                        sequence=index,
                        observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
                        success=True,
                        latency_ms=2.0,
                        summary="input=fixed fnv1a=0xfbe51dc5",
                    )
                    for index in range(1, minimum_inferences + 1)
                ],
            )

    monkeypatch.setattr(cli, "Stm32N6Deployer", FakeDeployer)
    monkeypatch.setattr(
        cli,
        "prepare_deployment_application",
        lambda application, application_directory, core_directory, output_directory, fixed_input: (
            calls.append(
                f"prepare:{core_directory.name}:{fixed_input}:{application_directory.name}"
            )
            or SimpleNamespace(runtime_version="v4.0.1", input_mode="fixed")
        ),
    )

    result = runner.invoke(
        app,
        [
            "optimize",
            str(model_path),
            "--target",
            "stedgeai",
            "--validation-input",
            str(validation_input),
            "--deploy",
            "--core-directory",
            str(tmp_path / "core-4.0"),
            "--fixed-input",
            "--application-directory",
            str(application_directory),
            "--model-support-directory",
            str(model_support_directory),
            "--fsbl",
            str(fsbl),
            "--compiler-log",
            str(baseline_log),
            "--candidate-compiler-log",
            str(candidate_log),
            "--output-directory",
            str(tmp_path / "outputs"),
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Move JP2 to position 1" in result.stdout
    assert calls == [
        "prepare:core-4.0:True:NUCLEO-N657X0-Q",
        "generate:optimized-model.onnx:Model",
        "build:NUCLEO-N657X0-Q:model-files:8:UVCL",
        "program:3:optimized-model.onnx",
        "validate:5:optimized-model:0xfbe51dc5",
    ]
    run_dirs = list((tmp_path / "outputs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "compiler").is_dir()
    assert (run_dirs[0] / "deployment/deployment-result.json").is_file()
    assert (run_dirs[0] / "deployment/generate/model-files/network_data.hex").is_file()
    report = (run_dirs[0] / "run-report.json").read_text(encoding="utf-8")
    assert '"status": "succeeded"' in report
    assert "optimized-model.onnx" in report


def test_deployment_backup_uses_full_flash_timeout(monkeypatch, tmp_path: Path) -> None:
    from arona import cli

    observed_timeout: list[int] = []

    class FakeDeployer:
        def backup_external_flash(
            self,
            config: object,
            output_directory: Path,
        ) -> DeploymentResult:
            observed_timeout.append(config.timeout_seconds)  # type: ignore[attr-defined]
            return _deployment_result(DeploymentStageName.VALIDATION)

    monkeypatch.setattr(cli, "Stm32N6Deployer", FakeDeployer)

    result = runner.invoke(
        app,
        [
            "deployment",
            "backup",
            "--application",
            "image_classification",
            "--output-directory",
            str(tmp_path / "backup"),
        ],
    )

    assert result.exit_code == 0
    assert observed_timeout == [600]


def test_optimize_command_rejects_unknown_target(tmp_path: Path) -> None:
    model_path = tmp_path / "model.onnx"
    _write_terminal_argmax_model(model_path)

    result = runner.invoke(app, ["optimize", str(model_path), "--target", "unknown-backend"])

    assert result.exit_code != 0
    assert not (tmp_path / "outputs").exists()


def _write_identity_model(path: Path) -> None:
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3])
    output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3])
    graph = helper.make_graph(
        nodes=[helper.make_node("Identity", ["input"], ["output"], name="identity_0")],
        name="identity",
        inputs=[input_info],
        outputs=[output_info],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    onnx.save(model, path)


def _write_terminal_argmax_model(path: Path) -> None:
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3])
    output_info = helper.make_tensor_value_info("class_index", TensorProto.INT64, [1])
    graph = helper.make_graph(
        nodes=[
            helper.make_node("Identity", ["input"], ["scores"], name="identity_0"),
            helper.make_node(
                "ArgMax",
                ["scores"],
                ["class_index"],
                name="argmax_0",
                axis=1,
                keepdims=0,
            ),
        ],
        name="terminal_argmax",
        inputs=[input_info],
        outputs=[output_info],
        value_info=[helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 3])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 10
    onnx.save(model, path)


def _write_deployment_result(path: Path) -> None:
    observed_at = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    result = DeploymentResult(
        status=StageStatus.SUCCEEDED,
        application=DeploymentApplication.IMAGE_CLASSIFICATION,
        board="NUCLEO-N657X0-Q",
        serial_port="COM5",
        boot_mode="development",
        stages=[
            DeploymentStage(
                stage=DeploymentStageName.PROGRAMMING,
                status=StageStatus.SUCCEEDED,
                exit_code=0,
                duration_ms=1200.0,
            ),
            DeploymentStage(
                stage=DeploymentStageName.VALIDATION,
                status=StageStatus.SUCCEEDED,
                exit_code=0,
                duration_ms=750.0,
            ),
        ],
        observations=[
            InferenceObservation(
                sequence=sequence,
                observed_at=observed_at,
                success=True,
                latency_ms=2.5 + sequence * 0.05,
                summary=f"fixed-input inference {sequence}",
            )
            for sequence in range(1, 6)
        ],
        reason="fixed-input smoke validation passed",
    )
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _deployment_result(
    stage: DeploymentStageName,
    *,
    model: Path | None = None,
    firmware: list[ArtifactRef] | None = None,
    observations: list[InferenceObservation] | None = None,
) -> DeploymentResult:
    return DeploymentResult(
        status=StageStatus.SUCCEEDED,
        application=DeploymentApplication.IMAGE_CLASSIFICATION,
        board="NUCLEO-N657X0-Q",
        serial_port="COM5",
        boot_mode="development",
        model=_artifact(model) if model is not None else None,
        firmware=firmware or [],
        stages=[
            DeploymentStage(
                stage=stage,
                status=StageStatus.SUCCEEDED,
                exit_code=0,
                duration_ms=10.0,
            )
        ],
        observations=observations or [],
        reason=f"{stage} succeeded",
    )


def _artifact(path: Path) -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.DEPLOYABLE,
        path=str(path),
        sha256="0" * 64,
        size_bytes=path.stat().st_size if path.is_file() else 0,
    )
