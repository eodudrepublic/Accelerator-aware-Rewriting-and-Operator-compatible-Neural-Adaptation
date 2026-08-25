"""Arrow-key interactive launcher for the ARONA CLI."""

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import choice, clear, prompt
from prompt_toolkit.styles import Style

from arona.reporting.terminal import (
    render_banner,
    render_command_header,
    render_key_values,
    render_notice,
    render_numbered_list,
    render_pipeline_overview,
    render_pipeline_tracker,
    write_terminal,
)

BLUE = "#4ea8d7"
PINK = "#d76f9f"
SELECTOR = "\u203a"
PIPELINE_STAGES = ("Inspect", "Analyze", "Optimize", "Deploy", "Validate")
ACTION_STAGE = {
    "doctor": 0,
    "discover": 0,
    "analyze": 1,
    "optimize": 2,
    "deploy": 2,
    "validate": 4,
    "help": 0,
}
COMPLETED_STAGE = {**ACTION_STAGE, "deploy": 4}

LAUNCHER_STYLE = Style.from_dict(
    {
        "input-selection": "",
        "option": "#e6e6e6",
        "selected-option": f"bold {PINK}",
        "number": BLUE,
        "frame.border": BLUE,
        "frame.label": f"bold {BLUE}",
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": "#8b8b8b",
        "prompt": f"bold {BLUE}",
    }
)

MAIN_ACTIONS = [
    ("doctor", "Check environment        arona doctor"),
    ("discover", "Discover target         arona discover"),
    ("analyze", "Analyze an ONNX model   arona analyze"),
    ("optimize", "Optimize a model        arona optimize"),
    ("deploy", "Optimize and deploy      arona optimize --deploy"),
    ("validate", "Validate board output   arona deployment validate"),
    ("help", "Show all commands        arona --help"),
    ("exit", "Exit ARONA"),
]

IMAGE_CLASSIFICATION_APPLICATION = Path(
    "outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q"
)
IMAGE_CLASSIFICATION_MODEL = Path("outputs/vendor/STM32N6-GettingStarted-ImageClassification/Model")
IMAGE_CLASSIFICATION_FSBL = Path(
    "outputs/vendor/STM32N6-GettingStarted-ImageClassification/FSBL/ai_fsbl.hex"
)
OBJECT_DETECTION_APPLICATION = Path(
    "outputs/vendor/STM32N6-GettingStarted-ObjectDetection/Application/NUCLEO-N657X0-Q"
)
OBJECT_DETECTION_MODEL = Path("outputs/vendor/STM32N6-GettingStarted-ObjectDetection/Model")
OBJECT_DETECTION_FSBL = Path(
    "outputs/vendor/STM32N6-GettingStarted-ObjectDetection/FSBL/ai_fsbl.hex"
)


@dataclass(frozen=True)
class RunResult:
    """Summary retained by the interactive session after a command finishes."""

    action: str
    arguments: tuple[str, ...]
    return_code: int
    duration_seconds: float


def interactive_terminal_available() -> bool:
    """Return whether both input and output are attached to a terminal."""

    return sys.stdin.isatty() and sys.stdout.isatty()


def launch_interactive() -> None:
    """Run the persistent ARONA action launcher until the user exits."""

    last_run: RunResult | None = None
    show_banner = True
    while True:
        clear()
        _render_dashboard(last_run, show_banner=show_banner)
        show_banner = False
        try:
            action = _select_action()
            if action == "exit":
                _render_goodbye()
                return
            arguments = _collect_command(action)
            if arguments is None:
                continue
            if action in {"analyze", "optimize", "deploy", "validate"}:
                clear()
                _render_plan(arguments)
                if not _confirm_run():
                    continue
            clear()
            _render_running_pipeline(action, arguments)
            result = _run_command(action, arguments)
            last_run = result
            clear()
            _render_result_report(result)
            if _select_next() == "exit":
                _render_goodbye()
                return
        except (EOFError, KeyboardInterrupt):
            _render_goodbye()
            return


def _render_dashboard(last_run: RunResult | None = None, *, show_banner: bool = True) -> None:
    model = _model_from_arguments(last_run.arguments) if last_run else None
    workflow = (
        _pipeline_statuses(last_run.action, "succeeded" if last_run.return_code == 0 else "failed")
        if last_run
        else [(stage, "pending") for stage in PIPELINE_STAGES]
    )
    lines = [
        *(render_banner() if show_banner else []),
        render_command_header("Workspace dashboard")[0],
        *render_key_values(
            "Workspace",
            [
                ("model", model.as_posix() if model else "No model selected"),
                (
                    "target",
                    f"NUCLEO-N657X0-Q · {_dashboard_environment(last_run)}",
                ),
            ],
        )[1:],
        render_pipeline_overview(workflow, "Workflow"),
    ]
    if last_run:
        lines.append(
            render_pipeline_overview(
                [
                    (
                        f"Last run: {last_run.action} · {last_run.duration_seconds:.1f} s",
                        "succeeded" if last_run.return_code == 0 else "failed",
                    )
                ],
                "Status",
            )
        )
    write_terminal("\n".join(lines))


def _select_action() -> str:
    return choice(
        message=FormattedText([("class:prompt", "What would you like to do?")]),
        options=MAIN_ACTIONS,
        default="doctor",
        style=LAUNCHER_STYLE,
        symbol=SELECTOR,
        show_frame=True,
        bottom_toolbar=FormattedText(
            [("class:bottom-toolbar.text", "  ↑/↓ move   Enter select   Ctrl+C exit")]
        ),
    )


def _collect_command(action: str) -> list[str] | None:
    if action == "doctor":
        return ["doctor"]
    if action == "discover":
        return ["discover"]
    if action == "help":
        return ["--help"]
    if action == "analyze":
        model = _prompt_existing_file("ONNX model", suffix=".onnx")
        compiler_log = _prompt_existing_file("Compiler log")
        output = _prompt_value("Output directory", "outputs")
        return [
            "analyze",
            str(model),
            "--compiler-log",
            str(compiler_log),
            "--output-directory",
            output,
        ]
    if action == "optimize":
        model = _prompt_existing_file("ONNX model", suffix=".onnx")
        output = _prompt_value("Output directory", "outputs")
        return ["optimize", str(model), "--target", "stedgeai", "--output-directory", output]
    if action == "deploy":
        return _collect_deploy_command()
    if action == "validate":
        application = _select_application()
        serial_port = _prompt_value("Serial port", "COM3")
        inference_count = _prompt_value("Required inference records", "5")
        return [
            "deployment",
            "validate",
            "--application",
            application,
            "--serial-port",
            serial_port,
            "--inference-count",
            inference_count,
        ]
    return None


def _collect_deploy_command() -> list[str]:
    model = _prompt_existing_file("ONNX model", suffix=".onnx")
    application = _select_application()
    core_directory = _prompt_existing_directory(
        "STEdgeAI Core directory",
        os.getenv("STEDGEAI_CORE_DIR", "C:\\ST\\STEdgeAI2\\4.0"),
    )
    fixed_input = _select_input_mode() == "fixed"
    serial_port = _prompt_value("Serial port", "COM3")
    output = _prompt_value("Output directory", "outputs")
    if application == "image_classification":
        application_directory = IMAGE_CLASSIFICATION_APPLICATION
        model_directory = IMAGE_CLASSIFICATION_MODEL
        fsbl = IMAGE_CLASSIFICATION_FSBL
    else:
        application_directory = OBJECT_DETECTION_APPLICATION
        model_directory = OBJECT_DETECTION_MODEL
        fsbl = OBJECT_DETECTION_FSBL
    arguments = [
        "optimize",
        str(model),
        "--target",
        "stedgeai",
        "--deploy",
        "--core-directory",
        str(core_directory),
        "--deployment-application",
        application,
        "--application-directory",
        str(application_directory),
        "--model-support-directory",
        str(model_directory),
        "--fsbl",
        str(fsbl),
        "--serial-port",
        serial_port,
        "--output-directory",
        output,
    ]
    if fixed_input:
        arguments.append("--fixed-input")
    return arguments


def _select_application() -> str:
    return choice(
        message=FormattedText([("class:prompt", "Select the deployment application")]),
        options=[
            ("image_classification", "Image classification"),
            ("object_detection", "Object detection"),
        ],
        default="image_classification",
        style=LAUNCHER_STYLE,
        symbol=SELECTOR,
        show_frame=True,
        bottom_toolbar=FormattedText(
            [("class:bottom-toolbar.text", "  ↑/↓ move   Enter select   Ctrl+C cancel")]
        ),
    )


def _select_input_mode() -> str:
    return choice(
        message=FormattedText([("class:prompt", "Select the inference input")]),
        options=[
            ("fixed", "Deterministic fixed input (recommended without a camera)"),
            ("camera", "Camera input"),
        ],
        default="fixed",
        style=LAUNCHER_STYLE,
        symbol=SELECTOR,
        show_frame=True,
        bottom_toolbar=FormattedText(
            [("class:bottom-toolbar.text", "  ↑/↓ move   Enter select   Ctrl+C cancel")]
        ),
    )


def _prompt_existing_file(label: str, *, suffix: str | None = None) -> Path:
    while True:
        value = prompt(
            FormattedText([("class:prompt", f"{label} {SELECTOR} ")]),
            completer=PathCompleter(expanduser=True),
            complete_while_typing=True,
            style=LAUNCHER_STYLE,
        )
        path = Path(value.strip().strip('"')).expanduser()
        if path.is_file() and (suffix is None or path.suffix.lower() == suffix):
            return path
        requirement = f"Existing {suffix} file required." if suffix else "Existing file required."
        write_terminal("\n".join(render_notice("Invalid path", [requirement], "failed")))


def _prompt_existing_directory(label: str, default: str) -> Path:
    while True:
        value = _prompt_value(label, default)
        path = Path(value.strip().strip('"')).expanduser()
        if path.is_dir():
            return path
        write_terminal(
            "\n".join(render_notice("Invalid path", ["Existing directory required."], "failed"))
        )


def _prompt_value(label: str, default: str) -> str:
    return prompt(
        FormattedText([("class:prompt", f"{label} {SELECTOR} ")]),
        default=default,
        style=LAUNCHER_STYLE,
    ).strip()


def _render_plan(arguments: Sequence[str]) -> None:
    command = subprocess.list2cmdline(["arona", *arguments])
    lines = [
        *render_command_header("Plan", "Review the generated command before execution."),
        "",
        *render_notice("Command ready", [command]),
    ]
    write_terminal("\n".join(lines))


def _confirm_run() -> bool:
    return choice(
        message=FormattedText([("class:prompt", "Run this plan?")]),
        options=[(True, "Run command"), (False, "Back to launcher")],
        default=True,
        style=LAUNCHER_STYLE,
        symbol=SELECTOR,
        show_frame=True,
    )


def _render_running_pipeline(action: str, arguments: Sequence[str]) -> None:
    command = subprocess.list2cmdline(["arona", *arguments])
    lines = [
        *render_command_header(
            "Pipeline tracker",
            "ARONA is processing the selected workflow. Command output follows below.",
        ),
        "",
        *render_pipeline_tracker(_pipeline_statuses(action, "running")),
        "",
        *render_key_values("Run", [("command", command)]),
        "",
    ]
    write_terminal("\n".join(lines))


def _run_command(action: str, arguments: Sequence[str]) -> RunResult:
    started = time.perf_counter()
    environment = {**os.environ, "ARONA_INTERACTIVE_CHILD": "1"}
    completed = subprocess.run(
        [sys.executable, "-m", "arona", *arguments],
        check=False,
        env=environment,
    )
    return RunResult(
        action=action,
        arguments=tuple(arguments),
        return_code=completed.returncode,
        duration_seconds=time.perf_counter() - started,
    )


def _render_result_report(result: RunResult) -> None:
    succeeded = result.return_code == 0
    model = _model_from_arguments(result.arguments)
    output = _argument_value(result.arguments, "--output-directory")
    details: list[tuple[str, object]] = [
        ("status", "Succeeded" if succeeded else "Failed"),
        ("operation", result.action.title()),
    ]
    if model:
        details.append(("model", model.as_posix()))
    if output:
        details.append(("output", output))
    details.extend(
        [
            ("duration", f"{result.duration_seconds:.1f} s"),
            ("exit code", result.return_code),
        ]
    )
    title = f"{result.action.title()} complete" if succeeded else f"{result.action.title()} failed"
    lines = [
        *render_command_header(
            "Run report",
            "Completed workflow summary and recommended next actions.",
        ),
        "",
        *render_pipeline_tracker(
            _pipeline_statuses(result.action, "succeeded" if succeeded else "failed")
        ),
        "",
        *render_notice(
            title,
            [f"{key.title():<10} {value}" for key, value in details],
            "succeeded" if succeeded else "failed",
        ),
        "",
        *render_numbered_list("Recommended next", _next_recommendations(result)),
    ]
    write_terminal("\n".join(lines))


def _select_next() -> str:
    return choice(
        message=FormattedText([("class:prompt", "What next?")]),
        options=[("launcher", "Return to launcher"), ("exit", "Exit ARONA")],
        default="launcher",
        style=LAUNCHER_STYLE,
        symbol=SELECTOR,
        show_frame=True,
    )


def _pipeline_statuses(action: str, outcome: str) -> list[tuple[str, str]]:
    active = ACTION_STAGE[action]
    completed = COMPLETED_STAGE[action] if outcome == "succeeded" else active
    statuses: list[tuple[str, str]] = []
    for index, stage in enumerate(PIPELINE_STAGES):
        if outcome == "running":
            status = "succeeded" if index < active else "running" if index == active else "pending"
        elif outcome == "failed":
            status = "succeeded" if index < active else "failed" if index == active else "pending"
        else:
            status = "succeeded" if index <= completed else "pending"
        statuses.append((stage, status))
    return statuses


def _model_from_arguments(arguments: Sequence[str]) -> Path | None:
    if arguments and arguments[0] in {"analyze", "optimize"} and len(arguments) > 1:
        return Path(arguments[1])
    return None


def _argument_value(arguments: Sequence[str], option: str) -> str | None:
    try:
        return arguments[arguments.index(option) + 1]
    except (ValueError, IndexError):
        return None


def _dashboard_environment(last_run: RunResult | None) -> str:
    if last_run is None:
        return "Ready to inspect"
    return "Last run succeeded" if last_run.return_code == 0 else "Needs attention"


def _next_recommendations(result: RunResult) -> list[str]:
    if result.return_code != 0:
        return ["Review the command output above", "Adjust inputs and run the operation again"]
    recommendations = {
        "doctor": ["Discover the connected target", "Select an ONNX model"],
        "discover": ["Analyze an ONNX model", "Check another environment"],
        "analyze": ["Optimize this model", "Review generated analysis artifacts"],
        "optimize": ["Deploy the optimized model", "Review optimization artifacts"],
        "deploy": ["Review deployment evidence", "Run another model"],
        "validate": ["Review validation evidence", "Return to the dashboard"],
        "help": ["Choose a workflow from the dashboard"],
    }
    return recommendations[result.action]


def _render_goodbye() -> None:
    clear()
    write_terminal("\n".join(render_notice("ARONA closed", ["See you next run."])))
