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

LibLathe CAM support is vendored as a submodule. After cloning this repo,
initialize the submodules and install LibLathe into the same Python environment
used to run the HMI:

```bash
git submodule update --init --recursive
python -m pip install -e vendor/LibLathe
```

LibLathe builds C++/pybind11 extensions, so the environment needs a C++ compiler
and Python development headers. The CAM screen imports LibLathe lazily; the rest
of the HMI still launches if the editable LibLathe build is not installed.

The FRED backend imports `fred_client` lazily. By default it looks for the
local client at `../tcl202_dis/rp2040_fred/python` relative to this project.
Set `TCL_LATHE_FRED_PYTHON` to override that path.
