# G-code dialects and threading support

This HMI parses G-code into backend-neutral actions and then sends one machine
primitive at a time to FRED. The controller does not execute full old-host
`G81`-`G84` canned cycles directly. Those routines belonged to the old PC host.

The controller primitive we use for threading is a decoded opcode-84
spindle-synchronised Z move, exposed by FredClient as `thread_sync_move(...)`.
The HMI represents that primitive as modern-style `G33`.

## Normal motion flow

For a linear move:

1. `G1 X20 Z-10 F100` is parsed into words and modal state.
2. Units and absolute/incremental mode resolve X/Z into work-coordinate targets.
3. The parser emits `MoveAction(mode="feed", target_x_mm=20, target_z_mm=-10)`.
4. `MachineService.execute_action()` compares the target with the current work
   DRO and computes machine deltas.
5. The backend receives one feed/rapid delta command and the UI waits for
   controller idle before the next action.

## Supported HMI dialect

Current executable program support:

| Code | Meaning |
| --- | --- |
| `G00` | Rapid movement. |
| `G01` | Linear feed movement. |
| `G02/G03` | X/Z arcs, linearised by the HMI before execution. |
| `G04 F...` | Dwell in seconds. |
| `G18` | X/Z plane, accepted for lathe programs. |
| `G20/G21` | Inch/mm units. |
| `G33 Z... K...` | Spindle-synchronised threading pass. `K` is pitch in current units per revolution. |
| `G90/G91` | Absolute/incremental positioning. |
| `G94/G97` | Accepted modal compatibility words. |
| `M03/M04/M05` | Spindle forward/reverse/stop. |
| `M06 I... K...` | Tool change request using logical tool and optional station. |

For CAM-generated programs, `I` comes from the logical tool selected by
Tools-tab metadata such as tool type and nominal drill size. `K` comes from the
current turret station assignment for that logical tool. If a logical tool has
no turret station, CAM omits `K` and program execution uses the manual pending
tool-change flow.

`G33` is intentionally Z-only in this HMI because FredClient currently exposes a
Z synchronized pass primitive:

```python
client.thread_sync_move(z=-15.0, pitch=1.5, slew=61, wait=False)
```

The parser converts `G33 Z<target> K<pitch>` into a `ThreadSyncAction`. The
service converts the target Z into a machine Z delta, checks soft limits, calls
`backend.thread_sync_move(...)`, and waits for normal motion feedback.

## Old host canned cycles

The TCL manual lists these old host cycles:

| Code | Old host meaning | HMI policy |
| --- | --- | --- |
| `G81` | Outside-diameter turning cycle. | Not parsed as a controller command. Use liblathe/CAM generated `G0/G1`. |
| `G82` | Facing/grooving cycle. | Not parsed as a controller command. Use liblathe/CAM generated `G0/G1`. |
| `G83` | Peck drilling cycle. | Not parsed as a controller command. CAM emits explicit peck moves. |
| `G84` | Threading cycle. | Not parsed as a controller command. CAM expands threading into `G0`/`G33`/`G0` passes. |

The old host cycles used the current position as the start or stand-off point,
often 2 mm from the work, then generated multiple lower-level moves internally.
That expansion now belongs in this HMI/CAM layer when we want the behavior.

## Modern mapping and clashes

Some old codes overlap badly with modern CNC meanings:

| Old TCL code | Modern/common meaning | Clash |
| --- | --- | --- |
| `G70` | Often Fanuc lathe finishing cycle. | Old TCL used imperial units. |
| `G71` | Often Fanuc lathe roughing cycle. | Old TCL used metric units. |
| `G81` | Often drilling. | Old TCL used OD turning. |
| `G82` | Often drill with dwell. | Old TCL used facing/grooving. |
| `G83` | Peck drilling. | Similar intent, different parameter semantics. |
| `G84` | Tapping/threading family. | Old TCL used its own threading cycle. |

The HMI should therefore avoid accepting old `G81`-`G84` as executable program
commands. CAM can generate equivalent explicit moves where appropriate.

## Threading CAM sequence

The first threading implementation supports external straight threads.

For each pass CAM emits:

```gcode
G0 X<pass_diameter> Z<start_z>
G33 Z<end_z> K<pitch>
G0 X<retract_diameter>
G0 Z<start_z>
```

The pass depths are scheduled using constant-area/decreasing infeed:

```text
pass_depth_on_diameter = total_depth_on_diameter * sqrt(pass_index / pass_count)
```

Spring passes repeat the final X depth. Threading uses spindle synchronisation
through FRED, so it should not be expanded into ordinary timed `G1` feed moves.
