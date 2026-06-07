# Tool Setup Operator Guide

This procedure starts from no trusted recorded tool offsets and ends with the
turret tools set up relative to Tool #1. After this, Tool #1 is ready to be
used for the workpiece touch-off for a particular job.

## 1. Populate The Tool Setup

1. Open the **Tools** tab.
2. For each physical tool fitted to the turret, select its logical tool row
   `T1` through `T12`.
3. Enter the turret station in **Turret P** and press **Save Tool**.
4. Enter a short **Description** that identifies the actual tool.
5. Use **Clear P** for any logical tool that is not currently fitted in the
   turret.

Only one tool can occupy a turret station. If you assign a tool to a station
that already has another tool, the old assignment is cleared automatically.

## 2. Choose Tool #1 As The Reference

1. Fit Tool #1 in its recorded turret station.
2. Make Tool #1 the active tool using **Set Active**, or use the Manual tab
   toolchanger controls to move to Tool #1.
3. Confirm the current turret station is correct before teaching offsets.

Tool #1 should be a stable reference tool that you are happy to use again when
setting the work offset for a job.

## 3. Prepare A Common Reference

Use the same reference feature for every tool:

- For Z, use the same faced surface, stop, gauge face, or other known Z
  position.
- For X, use the same measured diameter.

Do not change work offsets while teaching the tool table. The goal here is to
make every tool agree with Tool #1, not to set the job zero.

## 4. Teach Tool #1

1. Select `T1` in the Tools tab.
2. Make sure `T1` is the active tool.
3. Touch Tool #1 to the Z reference.
4. Enter the reference coordinate in **Known Z** and press **Teach Z**.
5. Touch Tool #1 to the measured diameter.
6. Enter that diameter in **Measured dia** and press **Teach X Dia**.

The `T1` row should now show the recorded X and Z offsets.

## 5. Teach The Other Turret Tools

For each remaining turret tool:

1. Select the tool row in the Tools tab.
2. Change to that tool and make it active.
3. Touch the same Z reference used for Tool #1.
4. Keep the same **Known Z** value and press **Teach Z**.
5. Touch the same measured diameter used for Tool #1.
6. Keep the same **Measured dia** value and press **Teach X Dia**.

Repeat this until every turret tool you intend to use has non-zero or otherwise
trusted offsets shown in the grid.

## 6. Verify Before Job Touch-Off

1. Check that each fitted turret tool has the correct **Turret P** station.
2. Check that each fitted turret tool has a useful description.
3. Check the X and Z offsets for obvious mistakes.
4. Change back to Tool #1.

Tool setup is saved automatically. At this point, use Tool #1 to touch off the
actual workpiece for the job and set the work offset in the normal way.
