# Tests

Two layers:

## 1. `tests/test_bridge.py` — pytest unit tests for the Python bridge

No Unreal Engine instance required. The TCP socket is mocked. Covers MCP
protocol surface (`initialize`, `tools/list`, `tools/call`, notifications,
unknown methods) and `call_ue` error paths (connection refused, timeout,
non-JSON reply, chunked reads).

Run from the repo root:

```bash
pytest tests/
```

28 tests, runs in well under a second. Safe for CI.

## 2. `examples/smoke_test.py` — live integration smoke test

Requires a running UE 5.7 editor with the UnrealClaudeMCP plugin loaded on
`127.0.0.1:18888`. Now asserts on response shape (not just print) and exits
non-zero on regression.

```bash
python examples/smoke_test.py                          # 7 default checks
python examples/smoke_test.py --bp /Game/Blueprints/BP_X.BP_X
python examples/smoke_test.py --widget /Game/UI/WBP_SmokeTest.WBP_SmokeTest
python examples/smoke_test.py --level /Game/Maps/MyMap
```

Override host/port via `UCMCP_HOST`, `UCMCP_PORT`.
