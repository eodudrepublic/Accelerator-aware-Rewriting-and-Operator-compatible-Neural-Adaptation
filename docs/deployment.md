# STM32N6 deployment wrapper

ARONA의 Day 3 wrapper는 ST 공식 NUCLEO-N657X0-Q application의 build, programming,
inference 검증을 서로 다른 단계로 기록한다. programming 성공만으로 보드 inference까지
성공했다고 판단하지 않는다.

기본 보드·도구·주소 설정은
[`deployment/templates/nucleo-n657x0-q.yaml`](../deployment/templates/nucleo-n657x0-q.yaml)에
고정돼 있다.

## Generate selected models

선정 모델은 원본 ONNX를 덮어쓰지 않고 application별 evidence directory에 생성한다.
`generate`는 Core 4.0.1로 model-specific C source와 external-flash용 `network_data.hex`를
만들며, 실행 명령과 checksum을 함께 보존한다.

`arona optimize --deploy`는 배포 전에 다음 준비를 자동으로 수행한다.

1. `--core-directory` 또는 `STEDGEAI_CORE_DIR`의 Core 버전과 application runtime을 확인하고
   필요한 runtime 파일을 동기화함
2. UART telemetry가 없으면 멱등 방식으로 자동 삽입함
3. 선택한 application의 `app_config.h`를 생성함
4. `--fixed-input` 또는 `--camera-input` 선택을 application source에 적용함
5. 준비가 완료된 경우에만 generate, build, program, validate를 순서대로 실행함

카메라가 없는 환경에서는 `--fixed-input`을 사용한다. 이 경우 application별 기본 FNV-1a
checksum도 validation 조건으로 자동 적용한다. 대화형 런처의 `Optimize and deploy` 작업은
Core 경로와 입력 모드를 실행 전에 질문한다.

```powershell
arona optimize models/downloads/mobilenetv2_a035_128_food101_qdq.onnx `
  --deploy `
  --core-directory C:\ST\STEdgeAI2\4.0 `
  --fixed-input `
  --application-directory outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q `
  --model-support-directory outputs/vendor/STM32N6-GettingStarted-ImageClassification/Model `
  --fsbl outputs/vendor/STM32N6-GettingStarted-ImageClassification/FSBL/ai_fsbl.hex
```

```powershell
arona deployment generate `
  --application image_classification `
  --model-support-directory outputs/vendor/STM32N6-GettingStarted-ImageClassification/Model `
  models/downloads/mobilenetv2_a035_128_food101_qdq.onnx `
  -o outputs/deployment/image-classification/generated

arona deployment generate `
  --application object_detection `
  --model-support-directory outputs/vendor/STM32N6-GettingStarted-ObjectDetection/Model `
  models/downloads/yolo26n_256_coco_person_qdq_int8.onnx `
  -o outputs/deployment/object-detection/generated
```

## Build

선택 모델을 생성한 STEdgeAI Core와 공식 application의 `stedgeai-lib` 버전은 반드시 같아야
한다. Core를 바꾼 뒤에는 application별로 런타임을 동기화한다. 명령은 Core의 `Inc`, `Lib`,
`Misc`, `Npu`, `Reloc`, `SystemPerformance`를 공식 checkout에 overlay하고 모든 원본 파일의
SHA-256을 다시 확인한다. build wrapper는 동기화된 CM55 GCC runtime 중 가장 최신 버전을
링크한다.

```powershell
arona deployment sync-runtime `
  --core-directory C:\ST\STEdgeAI\4.0 `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q `
  -o outputs/deployment/image-classification/runtime-sync
```

반복 inference 증거를 UART로 남기려면 공식 source checkout에 ARONA telemetry를 한 번
삽입한다. 같은 명령을 다시 실행해도 중복 삽입되지 않는다.

```powershell
arona deployment instrument `
  --application image_classification `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q

arona deployment configure `
  --application image_classification `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q
```

```powershell
arona deployment build `
  --application image_classification `
  --jobs 8 `
  --build-top build-arona-mobilenetv2-core401 `
  --model-directory outputs/deployment/image-classification/generated/model-files `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q `
  -o outputs/deployment/image-classification/build
```

wrapper는 CubeIDE에 포함된 make, Arm GCC와 signing tool을 탐지하고 공식 Makefile의 `sign`
target을 실행한다. 명령, stdout/stderr, exit code, duration과 생성된 signed hex checksum을
저장한다. STM32 Signing Tool 2.21 이상의 STM32N6 header v2.3 요구사항에 맞게
`-align`을 전달해 payload를 0x400 boundary에 정렬한다. 기존 dependency tree가 손상됐을 때는
`--build-top build-clean`처럼 새 이름을
지정해 이전 산출물을 삭제하지 않고 clean build를 수행한다.

## Program

먼저 보드의 BOOT0 JP1을 position 1, BOOT1 JP2를 position 2인 development mode로 설정한다.
기존 firmware를 보존해야 하면 write 전에 외부 flash 전체를 백업한다.

```powershell
arona deployment backup `
  --application image_classification `
  -o outputs/deployment/board-backup `
  --timeout-seconds 600
```

하나의 combined hex 또는 여러 firmware component를 순서대로 전달할 수 있다. `.bin`에는
반드시 `@0xADDRESS`를 붙인다.

```powershell
arona deployment program `
  --application image_classification `
  --model models/downloads/mobilenetv2_a035_128_food101_qdq.onnx `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/FSBL/ai_fsbl.hex `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q/build-arona-mobilenetv2-core401/Application/NUCLEO-N657X0-Q/Project_sign.hex `
  outputs/deployment/image-classification/generated/model-files/network_data.hex `
  -o outputs/deployment/image-classification/program
```

write 전에 STM32CubeProgrammer로 보드를 read-only probe하여 정확히
`NUCLEO-N657X0-Q`인지 확인한다. 각 write에 `-v`를 추가해 기록 후 내용을 검증한다.
programming 결과는 inference 전까지 `warning` 상태다.

## Validate

programming 후 BOOT1 JP2를 position 1인 flash boot로 바꾸고 전원을 다시 연결한다. 공식
application에 다음 형식의 UART telemetry를 추가한 build만 반복 inference 증거로 인정한다.

```text
ARONA_INFERENCE seq=1 latency_ms=3 class=pizza score=0.91
```

```powershell
arona deployment validate `
  --application image_classification `
  --serial-port COM5 `
  --inference-count 5 `
  --capture-seconds 30 `
  -o outputs/deployment/image-classification/validate
```

단순 boot banner나 UVC 연결만으로 inference 횟수를 추정하지 않는다.

### Camera가 없을 때

카메라를 연결할 수 없으면 공식 application의 camera/LCD 경로만 건너뛰고 실제 input buffer에
결정적 패턴을 채우는 smoke mode를 사용한다. 이 모드도 `stai_network_run()`과 기존
postprocess를 그대로 실행한다. 각 inference record에 model name, `input=fixed`, 입력의 FNV-1a
checksum이 포함되므로 단순 boot banner와 구분할 수 있다.

```powershell
arona deployment fixed-input `
  --application image_classification `
  outputs/vendor/STM32N6-GettingStarted-ImageClassification/Application/NUCLEO-N657X0-Q
```

128×128×3 입력의 예상 hash는 `0xfbe51dc5`, 256×256×3 입력은 `0x6c3e9dc5`다.
검증 시 모델명과 hash를 함께 요구한다.

```powershell
arona deployment validate `
  --application image_classification `
  --serial-port COM5 `
  --inference-count 5 `
  --expected-model-name mobilenetv2_a035_128_food101_qdq_OE_3_3_1 `
  --expected-input-fnv1a 0xfbe51dc5 `
  -o outputs/deployment/image-classification/fixed-input-validate
```
