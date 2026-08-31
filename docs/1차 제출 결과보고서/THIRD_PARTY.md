# Third-party open-source software

이 문서는 2026년 오픈소스 개발자대회 「붙임1 SBOM 작성 가이드」의 선별 기준에 따라 ARONA가 최종 Windows x64 검증 환경에서 직접 사용하는 오픈소스를 요약합니다. 출품작 자체인 ARONA, 전이 의존성, GitHub Actions, 별도로 설치하는 비오픈소스 ST 도구와 E2E 검증 입력으로만 사용하는 외부 AI 모델은 포함하지 않습니다. Python 직접 의존성 전체와 전이 의존성의 정확한 버전 및 파일 hash는 각각 `pyproject.toml`과 `uv.lock`을 기준으로 확인할 수 있습니다.

| 번호 | 라이브러리명 | 버전 | 라이선스 | 공식 저장소 URL | 사용 목적 및 주요 기능 |
| --- | --- | --- | --- | --- | --- |
| 1 | GNU Make | 4.4.1_st_20231030-1220 | GPL-3.0-or-later | https://git.savannah.gnu.org/cgit/make.git/ | STM32 firmware build rule 실행 / 별도 설치한 CLI 실행 파일 호출 |
| 2 | GNU Compiler Collection (`arm-none-eabi-gcc`) | 13.3.1 (GNU Tools for STM32 13.3.rel1) | GPL-3.0-or-later | https://gcc.gnu.org/git/gcc.git | STM32N6 firmware 컴파일·링크 / 별도 설치한 CLI 실행 파일 호출 |
| 3 | ONNX | 1.22.0 | Apache-2.0 | https://github.com/onnx/onnx | 모델 로딩·유효성 검사·shape inference·graph rewrite / Python 라이브러리로 불러 씀 |
| 4 | ONNX Runtime | 1.28.0 | MIT | https://github.com/microsoft/onnxruntime | 원본·후보 모델의 출력 동등성 검증 / Python 라이브러리로 불러 씀 |
| 5 | NumPy | 2.4.6 | BSD-3-Clause | https://github.com/numpy/numpy | tensor·고정 입력 생성과 수치 비교 / Python 라이브러리로 불러 씀 |
| 6 | Pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic | backend·pipeline·CLI 데이터 계약과 JSON Schema / Python 라이브러리로 불러 씀 |
| 7 | Typer | 0.27.0 | MIT | https://github.com/fastapi/typer | 명령행 인터페이스와 option 정의 / Python 라이브러리로 불러 씀 |
| 8 | prompt-toolkit | 3.0.53 | BSD-3-Clause | https://github.com/prompt-toolkit/python-prompt-toolkit | 화살표 키 선택·입력 자동완성·대화형 런처 / Python 라이브러리로 불러 씀 |
| 9 | pyserial | 3.5 | BSD-3-Clause | https://github.com/pyserial/pyserial | COM 포트 UART telemetry 수집 / Python 라이브러리로 불러 씀 |
| 10 | uv | 0.10.10 | MIT OR Apache-2.0 | https://github.com/astral-sh/uv | 의존성 설치·lockfile 기반 환경 재현·ARONA 실행 / 별도 설치한 CLI 실행 파일 호출 |
