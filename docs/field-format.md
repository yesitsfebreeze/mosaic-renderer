# Face RLE field

The field contains 32 × 32 × 32 cells in cube-local coordinates. The cube's body
diagonal is vertical in the world; storage remains aligned with its local X, Y and
Z axes. Three canonical column sets provide the six face views. Opposite faces
read the same intervals in reverse order.

Geometry is the union of explicit occupied boxes within cells. All three axis
stores are built from the same temporary cells, so selecting another tracing axis
does not change a surface. Only the compressed columns, runs and lookup tables are
uploaded to the GPU. The temporary dense cells are released after construction.

## Coordinates and partial cells

Cells may be labelled 1–32 in an interface. Internal cell coordinates are 0–31,
and boundary coordinates are **0–32 inclusive**. An endpoint is stored as an
unsigned integer in units of 1/256 cell. The last boundary is 8192, which fits in
16 bits. Empty space has no run. Material ID zero denotes air.

An occupied interval is half-open: `[start, end)`. From the opposite face it is
`[32 − end, 32 − start)`. A full-depth interval stays `[0, 32)`; `[4.25, 11.75)`
becomes `[20.25, 27.75)`. Property values continue to use the same physical local
point, so reversing a face does not reverse or replace the underlying field.

Each cell's explicit lower and upper bounds are integers from 0 through 256
relative to that cell. Their three positive extents determine its occupied volume.
A quarter-cell endpoint remainder describes a length, not automatically a quarter
of a cell's volume. The current geometry supports axis-aligned partial boxes; it
does not claim arbitrary curved subcell reconstruction or a fluid solver.

## Storage buffer

All offsets below address 32-bit words in one buffer. Floating-point values use
their IEEE-754 bit patterns, without numeric conversion to integers.

### Header: 16 words

| Word | Meaning |
| --- | --- |
| 0 | Grid size, 32 |
| 1 | Column table offset, 16 |
| 2 | Run pool offset |
| 3 | Total run count across all three axes |
| 4 | Material table offset |
| 5 | Material count, 256 |
| 6 | State table offset |
| 7 | State count |
| 8 | Property table offset |
| 9 | Property count |
| 10 | Complete buffer length in words |
| 11–15 | Reserved, zero |

### Columns: 3 × 32 × 32 records, two words each

The axis index is 0, 1 or 2 for X, Y or Z. Its transverse coordinate indices are
`uAxis = (axis + 1) % 3` and `vAxis = (axis + 2) % 3`.

```text
columnIndex = axis * 32 * 32 + u + 32 * v
column      = { firstRun, runCount }
```

`firstRun` is a record index relative to the run pool, not a word offset. Runs
are ordered by increasing depth and never overlap. A column has at most 32 runs
because the constructor accepts at most one occupied box per cell. Empty columns
have count zero.

### Runs: four words each

| Word | Low bits | High bits |
| --- | --- | --- |
| 0 | Bits 0–15: start depth in 1/256 cells | Bits 16–31: end depth in 1/256 cells |
| 1 | Bits 0–7: material ID | Bits 8–31: state index |
| 2 | Bits 0–15: lower U fraction | Bits 16–31: lower V fraction |
| 3 | Bits 0–15: upper U fraction | Bits 16–31: upper V fraction |

The transverse fractions are relative to the column's cell and range from 0
through 256. They preserve a partial box's width and height when another axis
becomes the tracing direction. Axial endpoints alone would incorrectly expand
these cross sections to full cells.

Two runs merge only when their depth endpoints touch and their material, state
index and both transverse bounds are identical. Gaps, material interfaces, state
changes and varying partial cross sections remain separate records. The constructor
first counts the runs, then allocates and fills exactly the required storage.

The native `field_validate` check decodes every input cell through all three packed
axis stores. It checks occupied and empty cells, exact bounds, material/state
identity, sorted endpoints and run-count limits before returning the field.

### Materials: 256 records, eight words each

The existing material layout is retained: linear albedo and opacity as a `vec4`,
then metallic, roughness, base emission and F0 as a second `vec4`. These are shared
material definitions. Changing local properties is separate from this table.

### States: two words each

```text
state = { firstProperty, propertyCount }
```

`firstProperty` is an index relative to the property record pool. State zero is a
valid state; the reference scene uses it as an empty property list. Missing
properties use the consumer's documented default. IDs must be unique within a
state.

### Properties: 20 words each

| Words | Meaning |
| --- | --- |
| 0 | Arbitrary unsigned property ID, including zero |
| 1 | Component count, 1–4 |
| 2–3 | Reserved, zero |
| 4–7 | Base value, `vec4` |
| 8–11 | X gradient, `vec4` per cell |
| 12–15 | Y gradient, `vec4` per cell |
| 16–19 | Z gradient, `vec4` per cell |

At cube-local point `p`, the value is:

```text
value(p) = base + gradientX * p.x + gradientY * p.y + gradientZ * p.z
```

Unused components are zero in the reference scene. This is an explicitly affine
field, shared across axes. A non-affine profile requires additional states/segments
or a future sample representation; endpoint depths alone cannot describe it.

The renderer currently consumes ID 1 as temperature in kelvin, ID 2 as an emission
multiplier, and ID 3 as an RGB tint. Other IDs retain their data for future
consumers. Property lookup changes appearance; it never changes occupied bounds.
Cooling a full cell therefore cannot silently shrink it into a smaller cell.

## Updates and limits

The current demonstration is static. Construction rebuilds all three projections
together, then uploads one persistent buffer. Incremental simulation and dirty
column updates are future work. A simulation must conserve its own amount of
matter and update geometry consistently; a temperature gradient is not a mass
gradient.

Compression depends on the number of compatible runs. The column table is 24 KiB;
fragmented input can require 98,304 runs across the three axes. The representation
does not guarantee a memory saving over every dense field or an unmeasured FPS
target.
