# Minimal test fixture for run_python_file. Demonstrates the round-trip
# pattern from docs/HANDOFF.md: emit a marker line via unreal.log so the
# caller can retrieve the result through get_log_lines (since
# FPythonCommandEx::ExecuteFile does not capture stdout).
#
# Run from any MCP client:
#   {"jsonrpc":"2.0","id":1,"method":"run_python_file",
#    "params":{"path":"<repo>/examples/hello_run_python_file.py"}}

import json
import unreal

# Do something the caller can verify -- read the project name and emit it
# alongside a token in a marker line. Callers grep get_log_lines output
# for "__HELLO_RPF__" to retrieve.
result = {
    "ok": True,
    "project": unreal.Paths.get_project_file_path(),
    "engine_root": unreal.Paths.engine_dir(),
    "from": "run_python_file fixture",
}

unreal.log("__HELLO_RPF__" + json.dumps(result) + "__END_HELLO_RPF__")
