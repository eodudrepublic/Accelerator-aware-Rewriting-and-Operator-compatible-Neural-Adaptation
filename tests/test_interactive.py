from pathlib import Path

from typer.testing import CliRunner

from arona import interactive
from arona.cli import app


def test_no_argument_non_tty_falls_back_to_help() -> None:
    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert "Usage: arona" in result.stdout
    assert "interactive" in result.stdout


def test_launcher_keeps_eight_direct_actions() -> None:
    assert [action for action, _ in interactive.MAIN_ACTIONS] == [
        "doctor",
        "discover",
        "analyze",
        "optimize",
        "deploy",
        "validate",
        "help",
        "exit",
    ]


def test_launcher_runs_selected_command_and_exits(monkeypatch) -> None:
    observed: list[list[str]] = []
    result = interactive.RunResult("doctor", ("doctor",), 0, 0.25)

    monkeypatch.setattr(interactive, "clear", lambda: None)
    monkeypatch.setattr(
        interactive,
        "_render_dashboard",
        lambda last_run=None, show_banner=True: None,
    )
    monkeypatch.setattr(interactive, "_select_action", lambda: "doctor")
    monkeypatch.setattr(interactive, "_render_running_pipeline", lambda action, arguments: None)
    monkeypatch.setattr(
        interactive,
        "_run_command",
        lambda action, arguments: observed.append(list(arguments)) or result,
    )
    monkeypatch.setattr(interactive, "_render_result_report", lambda run: None)
    monkeypatch.setattr(interactive, "_select_next", lambda: "exit")
    monkeypatch.setattr(interactive, "_render_goodbye", lambda: None)

    interactive.launch_interactive()

    assert observed == [["doctor"]]


def test_optimize_launcher_builds_reproducible_command(monkeypatch, tmp_path: Path) -> None:
    model = tmp_path / "model.onnx"
    model.touch()

    monkeypatch.setattr(interactive, "_prompt_existing_file", lambda *args, **kwargs: model)
    monkeypatch.setattr(interactive, "_prompt_value", lambda label, default: default)

    arguments = interactive._collect_command("optimize")

    assert arguments == [
        "optimize",
        str(model),
        "--target",
        "stedgeai",
        "--output-directory",
        "outputs",
    ]


def test_deploy_launcher_uses_selected_application_paths(monkeypatch, tmp_path: Path) -> None:
    model = tmp_path / "model.onnx"
    model.touch()

    monkeypatch.setattr(interactive, "_prompt_existing_file", lambda *args, **kwargs: model)
    monkeypatch.setattr(
        interactive,
        "_prompt_existing_directory",
        lambda *args, **kwargs: tmp_path / "core",
    )
    monkeypatch.setattr(interactive, "_select_application", lambda: "object_detection")
    monkeypatch.setattr(interactive, "_select_input_mode", lambda: "fixed")
    monkeypatch.setattr(interactive, "_prompt_value", lambda label, default: default)

    arguments = interactive._collect_command("deploy")

    assert arguments is not None
    assert arguments[:5] == ["optimize", str(model), "--target", "stedgeai", "--deploy"]
    assert "object_detection" in arguments
    assert str(interactive.OBJECT_DETECTION_APPLICATION) in arguments
    assert str(interactive.OBJECT_DETECTION_MODEL) in arguments
    assert str(interactive.OBJECT_DETECTION_FSBL) in arguments
    assert "--core-directory" in arguments
    assert "--fixed-input" in arguments


def test_deploy_pipeline_completes_every_stage() -> None:
    assert interactive._pipeline_statuses("deploy", "running") == [
        ("Inspect", "succeeded"),
        ("Analyze", "succeeded"),
        ("Optimize", "running"),
        ("Deploy", "pending"),
        ("Validate", "pending"),
    ]
    assert interactive._pipeline_statuses("deploy", "succeeded") == [
        (stage, "succeeded") for stage in interactive.PIPELINE_STAGES
    ]


def test_result_report_contains_model_output_and_recommendation(
    monkeypatch, tmp_path: Path
) -> None:
    rendered: list[str] = []
    model = tmp_path / "model.onnx"
    result = interactive.RunResult(
        "optimize",
        ("optimize", str(model), "--output-directory", "outputs"),
        0,
        1.25,
    )
    monkeypatch.setattr(interactive, "write_terminal", rendered.append)

    interactive._render_result_report(result)

    report = rendered[0]
    assert "Run report" in report
    assert "model.onnx" in report
    assert "outputs" in report
    assert "Deploy the optimized model" in report


def test_returned_dashboard_omits_welcome_banner(monkeypatch) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(interactive, "write_terminal", rendered.append)

    interactive._render_dashboard(show_banner=False)

    dashboard = rendered[0]
    assert dashboard.startswith("ARONA  Workspace dashboard")
    assert "Welcome to ARONA" not in dashboard
