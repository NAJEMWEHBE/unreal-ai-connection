# Tests

Two layers:

## 1. Bridge unit tests (`tests/test_bridge*.py`)

No Unreal Engine instance required. The TCP socket is mocked. Covers MCP
protocol surface (`initialize`, `tools/list`, `tools/call`, notifications,
unknown methods), `call_ue` error paths (connection refused, timeout,
non-JSON reply, chunked reads, EOF without terminator), parameterised
round-trips across all 80 tools, and main-loop fault tolerance.

Run from the repo root:

```bash
pip install pytest pytest-cov
pytest tests/                                 # 258 tests, < 1 second
pytest tests/ --cov=bridge --cov-report=term-missing   # with coverage
```

Bridge coverage is currently 99% (only the `__main__` guard is unreached).
GitHub Actions runs this suite on every push and PR (see
`.github/workflows/tests.yml`).

## 2. `examples/smoke_test.py` — live integration smoke test

Requires a running UE 5.7 editor with the UnrealClaudeMCP plugin loaded on
`127.0.0.1:18888`. Now asserts on response shape (not just print) and exits
non-zero on regression.

```bash
python examples/smoke_test.py                          # 15 default checks
python examples/smoke_test.py --bp /Game/Blueprints/BP_X.BP_X
python examples/smoke_test.py --widget /Game/UI/WBP_SmokeTest.WBP_SmokeTest
python examples/smoke_test.py --level /Game/Maps/MyMap
```

Override host/port via `UCMCP_HOST`, `UCMCP_PORT`.
