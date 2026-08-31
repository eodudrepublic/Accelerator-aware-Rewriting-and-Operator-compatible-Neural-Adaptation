# ARONA MVP demo

이 문서는 배포 후보를 재현할 때 쓰는 짧은 실행 절차다. 저장소에는 ST 모델
binary, vendor build output, 보드 flash dump를 넣지 않는다. Git에는 명령, fixture log,
checksum, JSON/Markdown contract와 재생성 절차만 보존한다.

## 1. Clean checkout smoke

보드와 ST toolchain 없이도 CLI, schema, parser, rewrite, deployment contract가 같은
계약으로 동작하는지 확인한다.

```powershell
uv sync --frozen
uv run arona --help
uv run arona optimize --help
uv run pytest
```

기대 결과:

- `pytest`는 hardware marker를 제외한 기본 회귀 테스트를 통과한다.
- `arona optimize --help`에는 `--target`, `--validation-input`, `--deploy`,
  `--deployment-result`와 live STM32N6 deployment 옵션이 표시된다.
- `tests/fixtures/backends/stedgeai/`의 compiler log fixture가 ConMamba fallback,
  XIP memory-feasible 사례, Core 2.2 IR-version 실패 사례를 재현한다.

## 2. Model preparation

공식 모델은 라이선스와 용량 때문에 Git에 넣지 않는다. URL, license, SHA-256은
`models/manifest.json`에 고정돼 있다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download_mvp_models.ps1

uv run python scripts/create_terminal_argmax_variant.py `
  models/downloads/mobilenetv2_a035_128_food101_qdq.onnx `
  outputs/demo/mobilenetv2_a035_128_food101_terminal_argmax.onnx
```

ST Edge AI Core는 터미널 세션마다 다음 스크립트로 불러온다. 앞의 `.`은 현재 PowerShell
세션에 환경변수를 남기기 위해 필요하다.

```powershell
. .\scripts\use_stedgeai.ps1
stedgeai --version
```

특정 설치 버전을 고정하려면:

```powershell
. .\scripts\use_stedgeai.ps1 -Version 4.0
```

사용자 환경변수로 영구 저장하려면:

```powershell
. .\scripts\use_stedgeai.ps1 -Persist
```

## 3. Compiler-in-the-loop fixture demo

아래 명령은 캡처된 `stedgeai` log를 사용해 baseline과 candidate compiler analysis를 비교한다.
실제 compiler를 호출하지 않으므로 clean checkout smoke와 같은 환경에서도 빠르게 재현된다.

```powershell
uv run arona optimize `
  outputs/demo/mobilenetv2_a035_128_food101_terminal_argmax.onnx `
  --target stedgeai `
  --compiler-log tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log `
  --candidate-compiler-log tests/fixtures/backends/stedgeai/conmamba_xip_101/compiler.log `
  --output-directory outputs/demo-runs
```

생성 산출물:

```text
outputs/demo-runs/<run-id>/
├── original-analysis.json
├── optimized-model.onnx
├── optimized-analysis.json
├── rewrite-history.json
├── postprocess.json
├── validation.json
├── run-report.json
└── report.md
```

## 4. Hardware deployment replay

실제 NUCLEO-N657X0-Q 재현은 보드, ST-LINK, STM32CubeProgrammer, ST Edge AI Core 4.0.1,
STM32CubeIDE/CLT와 공식 STM32N6 application checkout이 필요하다. 자세한 wrapper 명령은
`docs/deployment.md`가 기준이다.

실기기 회귀 검증에서 확인한 보드 실행 증거:

| Application | Input | Repeated inference evidence |
| --- | --- | --- |
| Image classification | fixed 128x128x3 input, FNV-1a `0xfbe51dc5` | 1,021 runs, 2-3 ms, mean 2.643 ms |
| Object detection | fixed 256x256x3 input, FNV-1a `0x6c3e9dc5` | 618 runs, 20-21 ms, mean 20.937 ms |

같은 경로를 fresh generate/build/program으로 다시 검증해 MobileNet 343회
(평균 2.647 ms), YOLO26n 150회(평균 20.953 ms)를 확인했다. 고정된 배포 증거 요약은
`tests/fixtures/deployment/nucleo_checkpoint4_e2e/evidence.json`에 있으며, 실행별 toolchain,
firmware와 artifact checksum은 `outputs/` 아래 실행 보고서에 생성된다.

`arona optimize --deploy`는 STM32N6 `generate -> build -> program -> validate`
sequence를 직접 실행한다. Programming이 끝나면 CLI가 멈추므로, 안내에 따라 JP2를 position 1
(Flash boot)로 옮기고 보드 전원을 다시 연결해 COM 포트가 복구된 뒤 확인 입력을 한다. 이미
생성된 `arona deployment validate` 결과를 재사용해야 할 때만 `--deployment-result`를 전달한다.

```powershell
uv run arona optimize `
  outputs/demo/mobilenetv2_a035_128_food101_terminal_argmax.onnx `
  --target stedgeai `
  --validation-input inputs/demo `
  --deploy `
  --application-directory outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q `
  --model-support-directory outputs/vendor/STM32N6-GettingStarted-ImageClassification/Model `
  --fsbl outputs/vendor/STM32N6-GettingStarted-ImageClassification/FSBL/ai_fsbl.hex `
  --serial-port COM5 `
  --inference-count 5 `
  --compiler-log tests/fixtures/backends/stedgeai/conmamba_fallback/compiler.log `
  --candidate-compiler-log tests/fixtures/backends/stedgeai/conmamba_xip_101/compiler.log `
  --output-directory outputs/demo-runs
```

이 명령의 `report.md`에는 compiler before/after, rewrite validation, final decision과 board
deployment status가 함께 표시된다. 로컬 `outputs/` evidence는 clean checkout에는 없을 수 있으며,
없으면 `docs/deployment.md` 절차로 다시 생성한다.

## 5. Known limits for the release candidate

- `--deployment-result`는 기존 validation evidence를 재사용하는 경로다. 제출 시연의 주 경로는
  `--deploy` live sequence다.
- 저장소 fixture는 재배포 가능한 log/JSON/metadata만 포함한다. 모델 binary와 vendor output은
  checksum과 생성 절차로 대체한다.
- 보드 실행 결과는 fixed-input smoke evidence다. baseline/optimized latency를 같은 조건에서
  20회씩 비교하는 성능 실험은 아직 남아 있다.
