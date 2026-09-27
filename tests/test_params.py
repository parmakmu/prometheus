#!/usr/bin/env python3
"""Param values (workflow_params.py) and how the compiler uses them: where values
come from, how each type compiles, and that secrets are never written down."""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), ".pi", "skills", "workflow", "scripts")
sys.path.insert(0, SCRIPTS)
import workflow_params as wp

spec = importlib.util.spec_from_file_location("compile_wave", os.path.join(SCRIPTS, "compile_wave.py"))
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

SPECS = {
    "mail_dir": {"description": "d", "type": "path"},
    "report_dir": {"description": "d", "default": "outputs"},
    "table": {"description": "d", "type": "text"},
    "api_token": {"description": "d", "type": "secret", "env": "TEST_API_TOKEN"},
}
results = []


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def check(name, ok, got=""):
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}"))


with tempfile.TemporaryDirectory() as root:
    for k in ("MAIL_DIR", "TABLE", "TEST_API_TOKEN", "REPORT_DIR"):
        os.environ.pop(k, None)
    v = wp.load(root, SPECS)
    check("unset: nothing but the default", v == {"mail_dir": None, "report_dir": "outputs", "table": None, "api_token": None}, v)
    check("missing lists required params without a value", wp.missing(SPECS, v) == ["mail_dir", "table", "api_token"], wp.missing(SPECS, v))

    problems = wp.set_values(root, SPECS, {"mail_dir": "~/Mail", "table": "invoices"})
    env_text = read(os.path.join(root, ".env"))
    check("set_values writes .env under the variable names", not problems and "MAIL_DIR=" in env_text and "TABLE=" in env_text, env_text)
    problems = wp.set_values(root, SPECS, {"api_token": "s3cret"})
    check("set_values refuses a secret", problems and "is a secret" in problems[0] and "s3cret" not in read(os.path.join(root, ".env")), problems)
    check("set_values refuses an undeclared param", wp.set_values(root, SPECS, {"nope": "x"}) != [])

    with open(os.path.join(root, ".env"), "a", encoding="utf-8") as fh:
        fh.write("TEST_API_TOKEN=from-file\n")
    os.environ["TABLE"] = "from-env"
    v = wp.load(root, SPECS)
    check("the environment wins over .env", v["table"] == "from-env", v["table"])
    check(".env value read", v["mail_dir"] == "~/Mail", v["mail_dir"])
    check("a secret loads as SecretStr", isinstance(v["api_token"], wp.SecretStr) and v["api_token"].get_secret_value() == "from-file", v["api_token"])
    rec = wp.record(SPECS, v)
    check("a run records a secret only as set", rec["api_token"] == "(secret, set)" and "from-file" not in str(rec), rec)
    os.environ.pop("TABLE")
    v = wp.load(root, SPECS)  # TABLE from .env again

    run_dir = os.path.join(root, "runs", "r1")
    check("path: ~ expands", wp.resolve("mail_dir", SPECS["mail_dir"], "~/Mail", run_dir) == os.path.expanduser("~/Mail"))
    check("path: relative resolves inside the run", wp.resolve("report_dir", SPECS["report_dir"], "outputs", run_dir) == os.path.join(run_dir, "outputs"))
    check("text: used as given", wp.resolve("table", SPECS["table"], "invoices", run_dir) == "invoices")
    check("secret: compiles to its variable", wp.resolve("api_token", SPECS["api_token"], v["api_token"], run_dir) == "${TEST_API_TOKEN}")

    plan = {"params": SPECS, "stages": []}
    c = cw.Compiler(root, plan, v, run_dir, {}, set())
    gate = c.gate_command("python3 ../gates/check.py ${table} ${api_token} ${mail_dir}")
    check("gate: text verbatim, secret as ${VAR}, never its value",
          gate.endswith(f"check.py invoices ${{TEST_API_TOKEN}} {os.path.expanduser('~/Mail')}") and "from-file" not in gate, gate)
    c = cw.Compiler(root, plan, wp.load(os.path.join(root, "elsewhere"), SPECS), run_dir, {}, set())
    c.gate_command("x ${api_token} ${table}")
    check("an unset secret is reported by its variable", c.missing_secrets == {"TEST_API_TOKEN"} and c.missing_params == {"table"},
          (c.missing_secrets, c.missing_params))

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
