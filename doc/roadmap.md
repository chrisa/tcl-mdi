# TCL Lathe HMI Roadmap

## Summary

Build a new Kivy touchscreen HMI for the TCL lathe in `tcl-mdi`. The app should feel closer to gmoccapy than to a small DRO utility, but it must not depend on LinuxCNC, HAL, or gmoccapy at runtime. The lathe already has its own real-time controller, so the Python/Kivy application is responsible for operator workflow, state display, program parsing, preview, and high-level command sequencing over USB.

The first milestone must produce a usable manual control panel with:

- Basic X/Z DRO.
- Jog controls.
- Spindle command and spindle RPM feedback.
- Both real FRED USB control and an offline simulator backend.

Later milestones add g-code loading, MDI, preview, tool table support, tool changer support, homing, and CAM integration.

## Design Principles

- Keep the UI backend-neutral. Screens should bind to machine state and issue high-level commands, not call USB methods directly.
- Treat the FRED controller as the real-time command executor. The HMI queues one controller command at a time, polls status, and updates feedback.
- Keep LinuxCNC only as a reference for desired workflows, defaults, and tool table format. Do not import or run LinuxCNC.
- Reuse useful ideas from `rotary-controller-python`, especially large DRO presentation, touch-friendly controls, and Kivy structure, without inheriting its DRO/ELS-specific board model.
- Make simulator support first-class so UI work and tests are possible without the lathe connected.
- Prefer conservative machine behavior. Disable motion controls when disconnected, busy, in error, or in an unsupported state.

## Proposed Package Structure

Create a new Python package under `tcl-mdi`, for example:

```text
tcl-mdi/
  pyproject.toml
  tcl_lathe_hmi/
    app.py
    main.py
    config/
    machine/
    backends/
    ui/
    gcode/
    tools/
    tests/
  doc/
    roadmap.md
```

Recommended responsibilities:

- `ui/`: Kivy screens, layouts, widgets, preview canvas, dialogs, and touchscreen interaction.
- `machine/`: backend-independent machine state, command models, command queue, and safety gating.
- `backends/fred.py`: real USB backend using `FredUsbClient` from `tcl202_dis/rp2040_fred/python`.
- `backends/sim.py`: deterministic simulator backend for tests and offline development.
- `gcode/`: parser, canonical action model, preview path generation, and execution planning.
- `tools/`: tool table records, offsets, active tool, and turret station tracking.
- `config/`: machine limits, jog increments, feed/spindle defaults, USB VID/PID, display preferences.

## Machine Backend Interface

The UI should depend on a small backend protocol rather than the FRED client directly.

Initial interface:

```python
class MachineBackend:
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll(self) -> MachineState: ...
    def jog_delta(
        self,
        *,
        x_mm: float = 0.0,
        z_mm: float = 0.0,
        mode: str = "feed",
        feed: int = 100,
        slew: int = 61,
    ) -> None: ...
    def set_spindle(
        self,
        *,
        on: bool,
        rpm: float = 0.0,
        forward: bool = True,
    ) -> None: ...
    def wait_idle(self, timeout_ms: int | None = None) -> None: ...
```

Initial `MachineState`:

- X position in mm using display diameter semantics.
- Z position in mm.
- Raw X/Z counts when available.
- Actual spindle RPM.
- Commanded spindle state: off, forward, reverse, target RPM.
- USB connected flag.
- Controller busy flag.
- Controller error flag.
- Homed flags for X/Z, initially false or unsupported.
- Active logical tool number.
- Active physical turret station, if known.

Future backend methods:

- `home(axis)`.
- `select_tool(tool_number, station)`.
- `run_action(action)`.
- `clear_error()`.
- `reset_controller_state()`.

## Real FRED Backend

Use the current Python API:

- `FredUsbClient(vid=0x2E8A, pid=0x000A, ...)`.
- `enable_polling(period_ms=10..33, rpm_service="remote")`.
- `refresh(timeout_ms=0)`, `latest_snapshot()`, `next_snapshot()`.
- `rapid_move_delta(x_mm=..., z_mm=..., slew=..., wait=False)`.
- `feed_move_delta(x_mm=..., z_mm=..., feed=..., slew=..., wait=False)`.
- `controller_status()`.
- `wait_idle(timeout_ms=...)`.
- `set_spindle(on=..., rpm=..., forward=..., wait=False)`.

Control policy:

- Use one active command at a time.
- Do not issue a jog, program move, spindle command, or tool command while the controller reports busy.
- After every command, poll `controller_status()` until idle or error.
- Refresh telemetry after command completion.
- On USB or protocol error, disconnect the backend, mark the machine unavailable, and disable controls.

Default values from the existing LinuxCNC/FRED work:

- USB VID: `0x2E8A`.
- USB PID: `0x000A`.
- X counts per mm: `100.0`, subject to calibration.
- Z counts per mm: `100.0`, subject to calibration.
- Jog slew: `61`.
- Jog feed: `100`.
- Spindle maximum command: approximately `127 * 24 RPM`, subject to calibration.
- X display uses diameter semantics. FRED handles controller radius conversion.

## Simulator Backend

The simulator should support all milestone 1 UI behavior without hardware:

- Maintain X/Z positions in memory.
- Apply jog deltas after a short simulated busy interval.
- Ramp or step spindle RPM toward the requested RPM.
- Expose connected, busy, idle, and error states.
- Allow forced disconnect/error states from a debug or test hook.

The simulator is not a physics model. It exists to make UI development, automated tests, and demonstrations possible.

## Roadmap

### Milestone 1: Manual Machine Panel

Goal: a usable Kivy app for manual machine control on a roughly 22 inch touchscreen.

Features:

- Main operator screen with large X/Z DRO.
- Actual spindle RPM display.
- USB/controller status display: disconnected, connected, busy, idle, error.
- Jog controls for X+, X-, Z+, Z-.
- Jog increment selector:
  - `1.000 mm`
  - `0.100 mm`
  - `0.010 mm`
  - `0.001 mm`
- Feed/rapid jog mode selector.
- Jog feed value control.
- Spindle controls:
  - forward start
  - reverse start
  - stop
  - target RPM entry
  - actual RPM feedback
  - at-speed indicator if enough feedback exists
- Backend selector or configuration for simulator vs FRED USB.
- Disable unsafe controls while disconnected, busy, or in error.

Milestone 1 acceptance:

- App launches without LinuxCNC installed or running.
- Simulator mode shows DRO updates when jog buttons are used.
- Simulator mode shows spindle RPM change when spindle controls are used.
- Real USB mode connects to FRED and displays live X/Z/RPM feedback.
- Real USB jog sends one delta command and waits for idle before accepting another machine command.
- Real USB spindle start/stop sends the corresponding FRED command and updates feedback.

### Milestone 2: MDI, Program Loading, and Preview

Goal: add gmoccapy-style program interaction without full LinuxCNC semantics.

Features:

- Program screen with g-code text editor.
- Load/save g-code files.
- Ad hoc MDI entry with command history.
- Parse and preview before execution.
- Preview window in X/Z lathe view.
- Execute, pause, stop, and reset controls.

Initial supported g-code:

- `G0`, `G1`.
- `G18`.
- `G20`, `G21`.
- `G90`, `G91`.
- `F`.
- `S`.
- `M3`, `M4`, `M5`.
- `M6` as a tool-change request.
- Comments.

Canonical actions:

- `MoveAction`: rapid/feed linear move.
- `SpindleAction`: start, reverse, stop, set speed.
- `ToolChangeAction`: request logical tool and optional turret station.
- `DwellAction`: timed dwell.
- `ThreadSyncAction`: G33-style spindle-synchronised Z pass.
- `MessageAction`: operator prompt or unsupported-code note.

Execution policy:

- Convert target positions to X/Z deltas against the latest known position.
- Execute one canonical action at a time.
- Wait for controller idle after every machine action.
- Stop program execution on unsupported g-code, controller error, disconnect, or tool-change uncertainty.

### Milestone 3: Tool Table and Tool Changer

Goal: manage tool offsets and prepare for automatic turret control.

Features:

- Tool table screen.
- Import/export LinuxCNC-style tool table rows similar to the existing `lathe.tbl`.
- Store logical tool number separately from physical turret station.
- Manage X/Z offsets per tool.
- Select active tool.
- Apply active tool offsets to work-coordinate display and g-code execution.
- Add manual tool-change confirmation flow.
- Add automatic turret flow once exposed through the Python FRED API.
- Persist current turret position across restarts
- Support manual update of current turret position
- When a centre-drill / drill is selected, in manual mode when machine is homed, support move to centre-line.

Important distinction:

- Logical tool number is the machining/tool-offset identity.
- Physical turret station is the machine position the turret must move to.
- These must not be collapsed into one field.

There is no feedback of current turret position; the user will need to confirm
the HMI and machine are in sync.

### Milestone 4: Homing, Limits, and Recovery

Goal: make the HMI aware of machine readiness and motion bounds.

Features:

- Homing screen and homed state in the status area.
- X/Z home actions once FRED/Python supports limit switch and homing primitives.
- Soft limits from machine configuration.
- Block preview/execution moves outside configured limits.
- Work coordinate and machine coordinate display modes.
- Persistent work offsets.
- Fault recovery screen for:
  - USB disconnect
  - controller error
  - motion timeout
  - stale telemetry

Until homing is supported by the backend, the UI should present homing as unavailable rather than pretending it can home.

### Milestone 5: Advanced G-code and CAM

Goal: grow from manual/MDI control into simple conversational lathe work.

Features:

- Arc support in preview and execution.
- Lathe-specific cycles after the low-level controller behavior is validated.
- Threading and other trick g-codes only after corresponding FRED actions are exposed safely.
- CAM integration for simple operations:
  - facing
  - roughing
  - profiling
  - parting
- Likely CAM candidate: `LibLathe`, with output routed through the same canonical action, preview, and execution pipeline as loaded g-code.

## UI Layout Direction

The first screen should be the actual operator panel, not a landing page.

Recommended first-screen structure:

- Left or top status band:
  - backend connection
  - controller state
  - active mode
  - active tool
  - homing state
- Large central DRO:
  - X
  - Z
  - spindle RPM
- Right-side manual controls:
  - jog increment selector
  - feed/rapid selector
  - X/Z jog buttons
  - spindle target RPM
  - spindle forward/reverse/stop
- Bottom navigation:
  - Manual
  - MDI
  - Program
  - Tools
  - Setup

Touch targets should be large enough for shop use. Numeric entry should use an on-screen keypad rather than requiring a keyboard.

## Testing Strategy

Milestone 1:

- Unit-test simulator state transitions.
- Unit-test command queue gating: no second command while busy.
- Unit-test FRED backend using a fake `FredUsbClient`.
- Smoke-test Kivy app startup in simulator mode.
- Manual hardware acceptance for real USB mode.

Milestone 2:

- Unit-test g-code parser against representative files.
- Unit-test canonical action output.
- Unit-test preview path generation.
- Test unsupported commands fail before execution.

Milestone 3:

- Unit-test tool table import/export.
- Unit-test offset application.
- Unit-test logical tool vs turret station behavior.

Milestone 4 and later:

- Unit-test soft limit checks.
- Unit-test homing unavailable/supported states.
- Add hardware validation checklists for every new real-machine primitive.

## Open Technical Work

- Confirm final package name and executable command.
- Decide config file format. A simple TOML or YAML file is enough initially.
- Decide whether to vendor, package, or path-depend on `fred_client`.
- Expose FRED turret/tool-change support in the Python API when ready.
- Expose homing and limit switch support in the FRED/Python API when hardware support lands.
- Calibrate X/Z counts per mm and spindle RPM mapping against the physical machine.

## Assumptions

- The app is developed in `tcl-mdi`.
- The runtime does not depend on LinuxCNC.
- Kivy is the UI framework.
- FRED USB is the real machine backend.
- Simulator support ships in the first milestone.
- Millimetres are the default units.
- X uses lathe diameter display semantics.
- Homing, automatic tool changing, and advanced threading are not first-milestone requirements.
