# Drill Centreline And Homing Notes

This note records the intended approach for making centreline drilling easy on
the TCL lathe, and the hardware/software decisions to revisit once limit
switches and homing are available.

## Goal

Drills, centre drills, reamers, and taps are axial tools. For these tools the
common operation is simple: put the tool on the spindle centreline, then feed in
Z. The HMI should make this a first-class workflow instead of requiring the
operator to re-teach X by trial each time.

The intended convention is:

- Spindle centreline is X0 in lathe work coordinates.
- Axial tools should have an easy way to set or move to X centreline.
- Centreline setup must be based on machine coordinates once homing exists,
  not on the current job's work offset.

## What Is Typical

For CNC lathes, X0 normally represents the spindle centreline. Tool geometry or
tool offsets make each tool's cutting point agree with that coordinate system.
For axial tools, the useful X target is nearly always centreline.

Homing and soft limits are normally separate concepts:

- Homing gives repeatable machine coordinates.
- Soft limits use that known machine coordinate frame to stop commanded moves
  before physical limits.
- Hard limit switches are safety inputs for overtravel, configuration mistakes,
  lost position, or motion before the machine is homed.

LinuxCNC's homing documentation is a good reference for the general model:
machine origin can be placed wherever is convenient, home switches can be
separate or shared with limit switches, and soft limits should sit inside the
physical limit switch area.

## One Switch Per Axis

One repeatable switch per axis is enough for homing.

That switch can also act as the limit switch at that end of travel if the
controller supports shared home/limit behavior during homing. The homing cycle
should:

1. Move toward the switch at a safe search speed.
2. Detect the switch.
3. Back off.
4. Re-approach or latch at a slower speed if supported.
5. Set machine coordinates using a configured home offset.
6. Move to a final home position clear of the switch.

The opposite-end switch is not required for homing. Its value is hard-limit
safety: it catches runaway motion, incorrect soft limits, lost position, or
manual movement before homing. If the wiring and controller inputs are
available, fitting both ends is still worthwhile. If not, one home/limit switch
per axis plus conservative soft limits is a reasonable first implementation.

Use normally-closed switch wiring if practical, so a broken wire looks like a
fault instead of silently disabling protection.

## Preferred Home Ends

Do not choose the home end based only on whether it is the "minimum" or
"maximum" coordinate end. The coordinate sign can be configured after the fact.
Choose the end that is mechanically repeatable and least likely to collide
during a blind homing move.

For this lathe, the current assumption to verify is:

- Z home: closer to the headstock.
- X home: bottom of X travel on the back-tool cross-slide.

That assumption needs a physical safety check before implementation.

Recommended default:

- Prefer Z homing away from the headstock/chuck if the machine layout allows it.
  This retracts the turret from the most collision-prone area before declaring
  the machine homed.
- Prefer X homing at the retracted/clearance end of travel, away from the
  spindle centreline and work, if that is available.
- If "bottom of X travel" means fully retracted and clear on this back-tool
  machine, it is a good candidate. If it means closest to centreline or closest
  to the work, it is a poor home end.

The final decision should be made by jogging the real machine slowly and
recording which physical direction each signed axis move produces. The HMI
should name switch placement in physical terms, not just min/max terms.

## Centreline Calibration After Homing

Once homing is implemented, add a machine calibration value:

```text
spindle_centreline_x_machine_mm
```

This is the homed machine X coordinate where an axial tool or reference gauge is
on the spindle centreline. It should be stored as machine calibration data, not
as a job work offset and not as an ordinary tool-table edit.

Initial calibration procedure:

1. Home X and Z.
2. Fit a known axial reference tool, centre drill, drill holder, or gauge pin.
3. Indicate or otherwise verify that the tool axis is on the spindle
   centreline.
4. Store the current machine X as `spindle_centreline_x_machine_mm`.
5. Set conservative X soft limits around the measured travel.
6. Test "go to centreline" at slow speed with no workpiece fitted.

This gives the HMI a repeatable absolute reference for axial tools.

## HMI Features To Add

After homing and centreline calibration exist:

- Extend the existing Tools tab type field for centreline-specific workflow
  types such as `Reamer` and `Tap` if those are added to CAM.
- For axial tool types, show centreline actions prominently:
  - `Set X To Centreline`
  - `Go X Centreline`
- Disable these actions unless X is homed and the centreline calibration value
  exists.
- When an axial tool is selected, make direct X touch-off secondary to the
  centreline workflow.
- Add a warning if a drill/reamer/tap program starts with X not at centreline.

The exact tool-offset math should be implemented with tests before adding the
buttons. The important rule is that centreline setup must not depend on the
current work offset for a particular job.

## Backend Work Needed

FRED/Python needs to expose homing and switch state primitives before this can
be reliable:

- Read X/Z home or limit switch inputs.
- Home a single axis with search, backoff, and latch behavior.
- Report homed state for X and Z.
- Reject normal motion or centreline actions when required axes are unhomed.
- Preserve soft-limit enforcement in machine coordinates after homing.

The HMI should keep presenting homing as unavailable until these backend
primitives exist.

## Open Decisions

- Confirm which physical end of Z is safest for homing on the real TCL setup.
- Confirm what "bottom of X travel" means physically on the back-tool
  cross-slide.
- Decide whether to install opposite-end hard limits immediately or rely on one
  switch per axis plus soft limits for the first homing implementation.
- Decide whether centreline calibration is global for all axial holders, or
  whether each axial tool also needs a small per-tool X centreline correction.

## References

- LinuxCNC Homing Configuration:
  https://www.linuxcnc.org/docs/html/config/ini-homing.html
- Warp9 FAQ, Home and Limit Switches:
  https://warp9td.com/index.php/faq/faq-home-and-limit-switches
