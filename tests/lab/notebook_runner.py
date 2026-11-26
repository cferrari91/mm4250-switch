"""
Run measurements/lab's notebooks top to bottom against the fakes, in a
real Jupyter kernel, without changing the notebooks.

    python tests/lab/notebook_runner.py OUT_DIR            # both notebooks, executed copies in OUT_DIR

How (also what tests/lab/test_lab_notebooks.py does):

  1. A temporary folder laid out like the DAQ:
       framework/drivers/                          (empty: the Setup cell looks for it)
       framework/scripts/switch_vna.py             the framework's (from the checkout beside this repo)
       framework/scripts/database_helper.py        the framework's real helper, its
                                                   E:/QCoDeS root pointed at <tmp>/QCoDeS
                                                   (a stand-in with the same layout if the
                                                   framework checkout isn't beside this repo)
       framework/Users/Charlie_Ferrari/lab/        a copy of measurements/lab, plus
                                                   ideals_295K/ = the fake chain's definitions
  2. A python3 kernel started in that lab/ folder.
  3. Before the first cell, a preamble (not part of the notebook) puts fake
     modules in sys.modules: drivers.KeysightVNA_driver.KeysightP5004B and
     drivers.MM4250_QCodes_driver.MM4250 return tests/lab/fakes.py's FakeVNA
     and FakeSwitch, maybell_qcodes.MaybellFridge a FakeFridge (or raises,
     like no VPN). It also wraps the kernel's raw_input so a prompt that says
     "... to line RF3" moves the fake's VNA port-1 cable there; the runner
     then answers the prompt with Enter over the kernel's stdin channel.
  4. Every code cell, in order; any error fails the run. `edits` can flip
     options the way a user would (e.g. "SAVE_DB = False" -> "SAVE_DB = True")
     in the copy only.
  5. A postamble (not part of the notebook) can check the kernel's state.
"""

import json
import re
import shutil
import sys
import textwrap
from pathlib import Path

import nbformat

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LAB = REPO / "measurements" / "lab"


def _real_helper():
    for p in REPO.parents:
        cand = p / "QCoDeS-Measurement-Framework" / "scripts" / "database_helper.py"
        if cand.is_file():
            return cand
    return None


STAND_IN_HELPER = '''
from pathlib import Path
import qcodes as qc
from qcodes.dataset import initialise_or_create_database_at
QCODES_ROOT = Path(r"{root}")

def database_path(project, cooldown, name=None, root=QCODES_ROOT):
    stem = f"{{project}}_Cooldown_{{int(cooldown):02d}}" + (f"_{{name}}" if name else "")
    return Path(root) / project / f"Cooldown_{{int(cooldown):02d}}" / f"{{stem}}.db"

def setup_database(project, cooldown, name=None, root=QCODES_ROOT):
    p = database_path(project, cooldown, name, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    initialise_or_create_database_at(p)
    qc.config.core.db_location = str(p)
    print(f"Recording to {{p}}")
    return p
'''


def make_daq_layout(tmp):
    """The folder layout above; returns (lab folder, QCoDeS root, which helper)."""
    tmp = Path(tmp)
    fw = tmp / "framework"
    (fw / "drivers").mkdir(parents=True)
    (fw / "scripts").mkdir()
    qroot = tmp / "QCoDeS"
    real = _real_helper()
    text = real.read_text() if real else ""
    line = 'QCODES_ROOT = Path("E:/QCoDeS")'
    if real and text.count(line) == 1:
        (fw / "scripts" / "database_helper.py").write_text(
            text.replace(line, f"QCODES_ROOT = Path(r\"{qroot}\")"))
        which = f"real helper ({real}), root -> {qroot}"
    else:
        (fw / "scripts" / "database_helper.py").write_text(STAND_IN_HELPER.format(root=qroot))
        which = "stand-in helper (framework checkout not found)"
    from conftest import FRAMEWORK
    shutil.copy(FRAMEWORK / "scripts" / "switch_vna.py", fw / "scripts" / "switch_vna.py")
    lab = fw / "Users" / "Charlie_Ferrari" / "lab"
    shutil.copytree(LAB, lab, ignore=shutil.ignore_patterns(
        "__pycache__", "local_databases", "Sweeps", "db_backups", ".ipynb_checkpoints",
        "ideals_*"))
    return lab, qroot, which


NOTEBOOK_POINTS = 2001     # POINTS in both notebooks: the fake chain must match it


def preamble(lab, fridge="295", noise=0.0):
    """Kernel code run before the first cell. fridge: kelvin as text, or "novpn"."""
    cfg = json.loads((lab / "cooldowns" / "CD05.json").read_text())
    s = dict(cfg["sweep"], points=NOTEBOOK_POINTS)   # the notebooks' POINTS (not in the file)
    return textwrap.dedent(f'''
        import sys, types
        sys.path.insert(0, r"{HERE}")
        import fakes
        rig = fakes.make_rig({s["start_hz"]!r}, {s["stop_hz"]!r}, {s["points"]!r},
                             thru=({cfg["lines"]["thru"]!r}, {cfg["thru_channel"]!r}), noise={noise!r})
        rig.chain.write_ideals(r"{lab / "ideals_295K"}")
        # fakes uses tests/test_ecal.py, which imports the OLD measurements/ecal.py and
        # plots.py under the same names; forget them so the notebook gets lab/'s.
        for _name in ("ecal", "plots", "vna", "db", "read_db", "sweep_db", "vna_measure"):
            sys.modules.pop(_name, None)
        sys.path[:] = [p for p in sys.path if not p.rstrip("/").endswith("measurements")]
        drivers = types.ModuleType("drivers"); drivers.__path__ = []
        kv = types.ModuleType("drivers.KeysightVNA_driver")
        kv.KeysightP5004B = lambda name, address: rig.vna
        mm = types.ModuleType("drivers.MM4250_QCodes_driver")
        mm.MM4250 = lambda name: rig.switch
        mb = types.ModuleType("maybell_qcodes")
        def _fridge(name, host):
            if {fridge!r} == "novpn":
                raise ConnectionError("no route to 10.179.200.168 (no VPN)")
            return rig.fridge
        mb.MaybellFridge = _fridge
        rig.fridge.kelvin = None if {fridge!r} == "novpn" else float({fridge!r})
        sys.modules.update({{"drivers": drivers, "drivers.KeysightVNA_driver": kv,
                             "drivers.MM4250_QCodes_driver": mm, "maybell_qcodes": mb}})
        _kernel = get_ipython().kernel
        _orig_raw_input = _kernel.raw_input
        PROMPTS = []
        def _raw_input(prompt=""):
            PROMPTS.append(prompt)
            m = __import__("re").search(r" to line (RF\\d+)", prompt)
            if m:
                rig.vna.port1_line = m.group(1)     # the person moves the cable
            return _orig_raw_input(prompt)
        _kernel.raw_input = _raw_input
        print("fakes in place")
    ''')


def run(nb_path, workdir, pre, post="", edits=(), out=None, timeout=600):
    """Execute nb_path's code cells in a kernel started in workdir. Returns the executed notebook."""
    from jupyter_client.manager import start_new_kernel

    nb = nbformat.read(nb_path, as_version=4)
    for old, new in edits:
        hits = [c for c in nb.cells if c.cell_type == "code" and old in c.source]
        if len(hits) != 1:
            raise ValueError(f"edit {old!r} matches {len(hits)} cells")
        hits[0].source = hits[0].source.replace(old, new)
    km, kc = start_new_kernel(kernel_name="python3", cwd=str(workdir))
    log = []

    def execute(code, cell=None):
        outputs = []

        def on_output(msg):
            if msg["msg_type"] in ("stream", "error", "execute_result", "display_data"):
                outputs.append(nbformat.v4.output_from_msg(msg))
                if msg["msg_type"] == "stream":
                    log.append(msg["content"]["text"])

        def on_stdin(msg):
            log.append(f"[prompt] {msg['content']['prompt']}\n[answer] (Enter)\n")
            kc.input("")

        reply = kc.execute_interactive(code, allow_stdin=True, timeout=timeout,
                                       output_hook=on_output, stdin_hook=on_stdin)
        if cell is not None:
            cell.outputs = outputs
            cell.execution_count = reply["content"].get("execution_count")
        if reply["content"]["status"] != "ok":
            tb = "\n".join(reply["content"].get("traceback", []))
            raise RuntimeError(f"cell failed:\n{code}\n\n{_strip_ansi(tb)}")

    try:
        execute(pre)
        for cell in nb.cells:
            if cell.cell_type == "code":
                execute(cell.source, cell)
        if post:
            execute(post)
    finally:
        kc.stop_channels()
        km.shutdown_kernel(now=True)
        if out is not None:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            nbformat.write(nb, out)
    nb.metadata["runner_log"] = "".join(log)
    return nb


def _strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


if __name__ == "__main__":
    import tempfile
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    with tempfile.TemporaryDirectory() as tmp:
        lab, qroot, which = make_daq_layout(tmp)
        print("helper:", which)
        run(lab / "take_data.ipynb", lab, preamble(lab), out=out / "take_data.executed.ipynb")
        run(lab / "stuck_check.ipynb", lab, preamble(lab, fridge="novpn", noise=1e-4),
            out=out / "stuck_check.executed.ipynb")
        print("databases made:", sorted(str(p.relative_to(qroot)) for p in qroot.rglob("*.db")))
    print("executed copies in", out)
