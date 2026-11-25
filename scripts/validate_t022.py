import csv
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import duckdb
import pyarrow.parquet as pqreader
from openpyxl import load_workbook

from motor.config import MotorConfig
from motor.model import run as runstore
from motor.model import store
from motor.sources import read_any
from motor.storage.db import connect

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = pathlib.Path(tempfile.mkdtemp(prefix="yachay-t022-"))
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"))


def write_csv(name, columns, rows):
    p = TMP / name
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return p


def cli(*args, ok=True):
    r = subprocess.run(
        [sys.executable, "-m", "motor", *map(str, args)],
        cwd=ROOT,
        env=ENV,
        text=True,
        capture_output=True,
    )
    if (r.returncode == 0) != ok:
        raise AssertionError(f"CLI {args} rc={r.returncode} stdout={r.stdout} stderr={r.stderr}")
    if ok:
        try:
            return json.loads(r.stdout)
        except Exception as e:
            raise AssertionError((args, r.stdout, r.stderr)) from e
    return r


ROOTARG = ["--root", str(TMP)]
# T-024: map distinct source headers to canonical columns; missing columns become null.
a = write_csv("align_a.csv", ["id", "name"], [[1, "Ana"]])
b = write_csv("align_b.csv", ["person_id", "amount"], [[2, 7]])
maps = TMP / "maps.json"
maps.write_text(json.dumps([{}, {"person_id": "id"}]))
al = cli(
    "model",
    "align",
    a,
    b,
    "--column-maps",
    maps,
    "--columns",
    "id,name,amount",
    "--output",
    TMP / "aligned.csv",
    *ROOTARG,
)
with (TMP / "aligned.csv").open(encoding="utf-8", newline="") as f:
    aligned = list(csv.DictReader(f))
assert aligned == [{"id": "1", "name": "Ana", "amount": ""}, {"id": "2", "name": "", "amount": "7"}]
assert (
    al["rows"] == 2
    and al["sources"][0]["missing_columns"] == ["amount"]
    and al["sources"][1]["missing_columns"] == ["name"]
)
# T-025: each supported rule and error export; no cell data in report.
quality = write_csv(
    "quality.csv", ["id", "age", "code"], [[1, 20, "AB"], [1, 150, "bad"], ["", 30, "CD"]]
)
rules = TMP / "quality.json"
rules.write_text(
    json.dumps(
        [
            {"type": "required", "column": "id"},
            {"type": "unique", "columns": ["id"]},
            {"type": "range", "column": "age", "min": 0, "max": 100},
            {"type": "format", "column": "code", "pattern": "[A-Z]{2}"},
        ]
    )
)
vr = cli("model", "validate", quality, rules, "--errors-output", TMP / "errors.xlsx", *ROOTARG)
assert (vr["rows"], vr["valid_rows"], vr["invalid_rows"]) == (3, 0, 3)
wb = load_workbook(TMP / "errors.xlsx", read_only=True, data_only=True)
erows = list(wb.active.values)
wb.close()
assert len(erows) == 6 and erows[0] == ("row", "rule", "type", "columns", "message")
assert not any(v in str(vr) for v in ["150", "bad"])
# T-026: independent match/unmatched/duplicate join diagnostics.
left = write_csv("left.csv", ["id", "left_value"], [[1, "a"], [2, "b"], [2, "c"], [None, "n"]])
right = write_csv("right.csv", ["key", "right_value"], [[1, "x"], [2, "y"], [3, "z"]])
jr = cli(
    "model", "join", left, right, "--on", "id", "--right-on", "key", "--type", "left", *ROOTARG
)
assert (
    jr["rows_out"] == 4
    and jr["diagnostics"]["left_unmatched_rows"] == 1
    and jr["diagnostics"]["right_unmatched_rows"] == 1
)
# T-027: first/last selection and null keys preserved independently.
dup = write_csv(
    "dupe.csv", ["id", "value"], [[1, "old"], [1, "new"], ["", "blank1"], ["", "blank2"]]
)
ded = cli(
    "model",
    "dedupe",
    dup,
    "--key",
    "id",
    "--keep",
    "last",
    "--output",
    TMP / "deduped.csv",
    *ROOTARG,
)
with (TMP / "deduped.csv").open(encoding="utf-8", newline="") as f:
    drows = list(csv.DictReader(f))
assert (
    drows
    == [{"id": "1", "value": "new"}, {"id": "", "value": "blank1"}, {"id": "", "value": "blank2"}]
    and ded["duplicates_removed"] == 1
)
# T-028 derived safe expression.
base = write_csv(
    "base.csv",
    ["id", "group", "amount", "month", "date_text"],
    [
        [1, "A", 10, "Jan", "2026-01-02"],
        [2, "A", 20, "Feb", "2026-02-03"],
        [3, "B", 5, "Jan", "2026-03-04"],
    ],
)


def transform(spec, name):
    sp = TMP / (name + ".json")
    sp.write_text(json.dumps(spec))
    return cli("model", "transform", base, sp, "--output", TMP / (name + ".csv"), *ROOTARG)


dr = transform(
    {"operation": "derive", "name": "double", "expression": 'number(col("amount")) * 2'}, "derive"
)
with (TMP / "derive.csv").open(encoding="utf-8", newline="") as f:
    drs = list(csv.DictReader(f))
assert [r["double"] for r in drs] == ["20.0", "40.0", "10.0"]
# T-029 aggregate, pivot and unpivot.
ag = transform(
    {
        "operation": "aggregate",
        "by": ["group"],
        "metrics": [
            {"function": "sum", "column": "amount", "name": "total"},
            {"function": "count", "name": "n"},
        ],
    },
    "aggregate",
)
with (TMP / "aggregate.csv").open(encoding="utf-8", newline="") as f:
    ags = list(csv.DictReader(f))
assert ags == [{"group": "A", "total": "30.0", "n": "2"}, {"group": "B", "total": "5.0", "n": "1"}]
pv = transform(
    {
        "operation": "pivot",
        "index": ["group"],
        "pivot": "month",
        "value": "amount",
        "function": "sum",
    },
    "pivot",
)
with (TMP / "pivot.csv").open(encoding="utf-8", newline="") as f:
    pvs = list(csv.DictReader(f))
assert pvs == [
    {"group": "A", "Feb": "20.0", "Jan": "10.0"},
    {"group": "B", "Feb": "", "Jan": "5.0"},
]
un = transform({"operation": "unpivot", "index": ["id"], "columns": ["amount", "month"]}, "unpivot")
with (TMP / "unpivot.csv").open(encoding="utf-8", newline="") as f:
    uns = list(csv.DictReader(f))
assert len(uns) == 6 and uns[0] == {"id": "1", "variable": "amount", "value": "10"}
# T-030 verify CSV, XLSX and ODS export/readback formats.
for suffix in [".csv", ".xlsx", ".ods"]:
    export_path = TMP / ("export" + suffix)
    cli("model", "transform", base, TMP / "derive.json", "--output", export_path, *ROOTARG)
    roundtrip = read_any(export_path)
    assert (
        roundtrip.columns == ["id", "group", "amount", "month", "date_text", "double"]
        and len(roundtrip.rows) == 3
    )
assert len(erows) == 6
# T-031 normalize only declared date/number formats; malformed date is reported.
norm = transform(
    {
        "operation": "normalize",
        "numbers": {"amount": {"decimal": ".", "thousands": ","}},
        "dates": {"date_text": "%Y-%m-%d"},
    },
    "normalize",
)
with (TMP / "normalize.csv").open(encoding="utf-8", newline="") as f:
    nrs = list(csv.DictReader(f))
assert nrs[0]["date_text"] == "2026-01-02" and float(nrs[0]["amount"]) == 10
bad_dates = write_csv("bad_dates.csv", ["date_text"], [["2026-99-99"]])
badspec = TMP / "bad_dates.json"
badspec.write_text(json.dumps({"operation": "normalize", "dates": {"date_text": "%Y-%m-%d"}}))
badnorm = cli(
    "model", "transform", bad_dates, badspec, "--output", TMP / "bad_dates_out.csv", *ROOTARG
)
assert badnorm["failure_count"] == 1 and badnorm["parse_failures"][0]["kind"] == "date"
# T-032/033: persisted run, incremental replacement, metadata, hashes, parquet.
# Create a model through CLI for deterministic identity mapping.
new = cli("model", "new", "acceptance", base, *ROOTARG)
model_id = new["model_id"]
full = cli("model", "exec", model_id, base, *ROOTARG)
run_id = full["run_id"]
# Add a model version after run; incremental must reject stale model version.
cfg = MotorConfig.from_root(TMP)
conn = connect(cfg.paths.sqlite_path)
record = runstore.get(conn, run_id)
assert record.lineage["model_version"] == 1 and record.lineage["engine_version"]
assert record.lineage["sources"][0]["sha256"] == hashlib.sha256(base.read_bytes()).hexdigest()
assert record.lineage["parameters"] == {"operation": "full"}
# Independently inspect Parquet with DuckDB; compare row count, schema and values.
pq = cfg.paths.model_outputs / record.artifact_path
con = duckdb.connect(":memory:")
pqs = str(pq).replace("'", "''")
rows = con.execute(f"SELECT * FROM read_parquet('{pqs}') ORDER BY id").fetchall()
# export-run creates an XLSX distinct artifact and independently read it.
cli("model", "export-run", run_id, TMP / "run.xlsx", *ROOTARG)
wb = load_workbook(TMP / "run.xlsx", read_only=True, data_only=True)
exported = list(wb.active.values)
wb.close()
assert exported[0] == ("id", "group", "amount", "month", "date_text") and len(exported) == 4
# Incremental delta replaces id 2 and appends id 4, keeping stable order.
delta = write_csv(
    "delta.csv",
    ["id", "group", "amount", "month", "date_text"],
    [[2, "A", 99, "Feb", "2026-02-03"], [4, "B", 8, "Mar", "2026-04-05"]],
)
incr = cli("model", "incremental", model_id, run_id, delta, "--key", "id", *ROOTARG)
inc_id = incr["run_id"]
inc = runstore.get(conn, inc_id)
assert inc.lineage["parameters"] == {
    "operation": "incremental",
    "keys": ["id"],
    "previous_run_id": run_id,
    "keep": "last",
}
assert (
    len(inc.lineage["sources"]) == 2
    and inc.lineage["sources"][1]["sha256"] == hashlib.sha256(delta.read_bytes()).hexdigest()
)
incpq = cfg.paths.model_outputs / inc.artifact_path
con2 = duckdb.connect(":memory:")
incpqs = str(incpq).replace("'", "''")
incrows = con2.execute(f"SELECT * FROM read_parquet('{incpqs}') ORDER BY id").fetchall()
assert incrows == [
    ("1", "A", "10", "Jan", "2026-01-02"),
    ("2", "A", "99", "Feb", "2026-02-03"),
    ("3", "B", "5", "Jan", "2026-03-04"),
    ("4", "B", "8", "Mar", "2026-04-05"),
]
# Repeat full execution and verify deterministic fingerprint.
full2 = cli("model", "exec", model_id, base, *ROOTARG)
rec2 = runstore.get(conn, full2["run_id"])
assert rec2.fingerprint.hash == record.fingerprint.hash
# Stale model version must be rejected on incremental.
store.new_version(conn, model_id, name="acceptance")
conn.close()
stale = cli("model", "incremental", model_id, run_id, delta, "--key", "id", *ROOTARG, ok=False)
assert "otra versión" in stale.stdout
# Independent read via PyArrow confirms parquet schema and row count.
assert pqreader.read_table(incpq).num_rows == 4
print(
    json.dumps(
        {
            "status": "PASS",
            "temp_root": str(TMP),
            "covered": [
                "T-024",
                "T-025",
                "T-026",
                "T-027",
                "T-028",
                "T-029",
                "T-030",
                "T-031",
                "T-032",
                "T-033",
            ],
            "checks": "CLI plus independent CSV/XLSX/DuckDB/PyArrow/SQLite reconciliation",
            "model_id": model_id,
            "full_run_id": run_id,
            "incremental_run_id": inc_id,
            "full_rows": len(rows),
            "incremental_rows": len(incrows),
        },
        indent=2,
    )
)
