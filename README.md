# TCL Lathe HMI

Milestone 1 is a Kivy touchscreen HMI with a DRO, jog controls, spindle
controls, a simulator backend, and a FRED USB backend.

Run in simulator mode:

```bash
./run.sh --backend sim
```

Run against FRED USB:

```bash
./run.sh --backend fred
```

The FRED backend imports `fred_client` lazily. By default it looks for the
local client at `../tcl202_dis/rp2040_fred/python` relative to this project.
Set `TCL_LATHE_FRED_PYTHON` to override that path.
