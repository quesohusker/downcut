# Downcut function list

Source: Vectric public pages for VCarve, Cut2D, Spark, and the compare table, read 5 Nov 2026 (VCarve and Cut2D pages show V12.5).
Names here are ours. Downcut uses no Vectric code, files, clipart, or artwork. The screen layout follows VCarve's workflow so it feels familiar, and every icon is our own drawing.

Status: **P1** and **P2** = built and tested, **P3** = later, **Skip** = no plan to build. Nothing has run on a real machine yet.

Edition notes come from Vectric's compare table. "Pro only" means Cut2D Pro and VCarve Pro have it and the Desktop editions do not. Most functions are not tied to an edition on Vectric's pages, so those rows have no note.

## Job setup

| Function | Status | Note |
|---|---|---|
| Material size and thickness | P1 | |
| Inch or metric units | P1 | |
| Z zero at top or bottom | P1 | |
| XY origin at any corner or center | P1 | |
| Safe Z height | P1 | |
| No job size limit | P1 | Desktop editions cap at 24 x 24 in |
| Save and open projects | P1 | |
| Job setup sheet for the operator | P2 | Pro only. HTML sheet with files, tools, times, warnings, and the simulated cut |
| Multi-sheet projects | P3 | |
| Double-sided jobs | P3 | |
| Rotary jobs | P3 | |

## Import and export

| Function | Status | Note |
|---|---|---|
| Import SVG | P1 | |
| Import DXF | P1 | |
| Import PDF, AI, EPS | P2 | AI must be PDF compatible. EPS reads common PostScript path commands, not all of PostScript. PDF text must be converted to outlines |
| Import SKP | Skip | |
| Import bitmaps (JPEG, PNG, BMP, GIF, TIF) | P3 | |
| Export vectors as DXF and SVG | P2 | |
| Import and export STL, OBJ | P3 | |
| Print scaled views | P3 | |

## Drawing

| Function | Status | Note |
|---|---|---|
| Rectangle, circle, ellipse, polygon, star | P1 | |
| Line, polyline, curve, arc tools | P2 | Curves keep their anchor points for editing |
| Rulers, snap grid, guides | P2 | |
| Smart snapping | P2 | |
| Dimensions | P2 | |
| Vector validator (overlaps, zero-length spans) | P2 | |
| Clean up art (stacked copies, overlapping lines, tiny shapes) | P2 | Review tool with undo. Plain geometry, no network |
| Vector texture generator | P3 | |
| Image tracing | P3 | |
| Bitmap adjust (contrast, gamma, crop) | P3 | |

## Editing

| Function | Status | Note |
|---|---|---|
| Move, scale, rotate, mirror | P1 | |
| Duplicate and delete | P1 | |
| Weld, subtract, intersect | P1 | |
| Offset | P1 | |
| Node editing | P2 | Works on points. Imported curves are many short lines, so use Simplify first |
| Trim, extend | P2 | |
| Fillet, including dog-bone and T-bone corners | P2 | Spark lists these |
| Join open vectors | P2 | DXF import already joins on load |
| Smooth | P2 | Also adds Simplify, which reduces points |
| Remove duplicates | P2 | |
| Trim to boundary | P2 | |

## Text

| Function | Status | Note |
|---|---|---|
| TrueType and OpenType fonts | P1 | |
| Kerning from the font | P1 | |
| Left, center, right alignment | P1 | |
| Single-stroke engraving fonts | P2 | Hershey fonts, from the open Hershey-Fonts package |
| Text on a curve | P2 | Circles center text at the top. Flip puts it at the bottom |
| Manual kerning | P2 | Pair by pair. Rotating, mirroring, or resizing ends live text |
| Spell check | Skip | |

## Layers and layout

| Function | Status | Note |
|---|---|---|
| Layers | P2 | Show, lock, and keep-out per layer |
| Grid array and circular array | P2 | |
| Copies along a path | P2 | |
| True shape nesting | P2 | Pro only. Nests in holes, avoids keep-outs, and checks the gap. It packs by raster search, so it is good, not optimal |
| Keep-out zones | P2 | Pro only. Toolpaths do not route around them. The checker blocks any cut that enters one |
| Toolpath tiling | P2 | Overlap is cut twice, once from each tile |

## 2D toolpaths

| Function | Status | Note |
|---|---|---|
| Profile: outside, inside, on line | P1 | |
| Profile: multi-pass, allowance, climb or conventional | P1 | |
| Profile: tabs and ramped entry | P1 | |
| Profile: continuous ramp around the vector | P2 | Spirals to depth, then one flat lap. Tabs still work |
| Profile: last pass at full depth | P2 | Vectric calls this a separate last pass |
| Keep tool down between depth passes | P2 | Profile and pocket |
| Nested shape order (holes before outsides) | P1 | |
| Pocket with islands | P1 | |
| Drill with peck cycle | P1 | |
| Engrave on the line | P1 | Use profile, side "on" |
| Helical and ramped pocket entry | P2 | Helix falls back to a ramp where it does not fit |
| Pocket finish pass | P2 | |
| Lead-in and lead-out | P2 | Profile only, on closed shapes. Arc or line |
| Merged toolpaths | P2 | Pro only. Same tool only. Split undoes it |
| Array copy of toolpaths | P2 | Pro only |
| Toolpath templates | P2 | Pro only. Through cuts follow the new material thickness |
| Auto-inlay | P2 | End mill pocket and mirrored plug |
| V-carve inlays | P2 | VCarve Pro only. Simulated fit stayed within 0.07 mm on a test star |
| Thread milling | P3 | |
| Quick engrave for diamond drag tools | P3 | |

## 2.5D and decorative toolpaths

| Function | Status | Note |
|---|---|---|
| V-carving with automatic depth | P1 | |
| Flat-bottom V-carving with clearing tool | P1 | |
| V-carve depth limit | P1 | |
| Chamfer | P2 | Spark lists this. Measured in the simulator |
| Fluting | P2 | |
| Raised prism | P2 | |
| Form tools | P3 | |
| Texture toolpath | P3 | |
| Photo engraving | P3 | |

## 3D

| Function | Status | Note |
|---|---|---|
| Import 3D models | P3 | |
| 3D roughing and finishing | P3 | |
| Rest machining | P2 | Pocket only. A smaller end mill cuts what the main tool left |
| Project 2D toolpaths onto a 3D surface | P3 | |

## Tool database

| Function | Status | Note |
|---|---|---|
| End mills, V-bits, drills with feeds and speeds | P1 | |
| Tool editor in the UI | P2 | Tools save to a library on this computer |
| Filter by material and machine | P2 | |
| Cloud account sync | Skip | |

## Preview

| Function | Status | Note |
|---|---|---|
| 2D toolpath display | P1 | |
| Simulated cut, shaded relief view | P1 | Runs from the G-code itself |
| Estimated run time | P1 | |
| Warnings per operation | P1 | |
| Orbit 3D view | P2 | WebGL, from the simulated height map |
| View one depth pass at a time | P2 | Profile and pocket, in 2D and 3D |
| Material textures | P3 | |

## G-code output

| Function | Status | Note |
|---|---|---|
| GRBL post | P1 | |
| Mach3 and Mach4 post | P1 | |
| LinuxCNC post | P1 | |
| One file per tool | P1 | |
| Independent G-code checker | P1 | Not a Vectric function. It adds safety |
| Arc output (G2, G3) | P2 | Off by default. Turn on in the Job tab. Includes helical arcs |
| Custom post editor | P2 | Edits posts in the Vectric .pp format, with a live sample and checker result |
| Import Vectric post processor (.pp) | P2 | Runs the file's own blocks and variables. Rotary, laser, and POST_BASE sections are not supported |
| Import Vectric tool database (.vtdb) | P2 | Reads tools, feeds, materials, and machines |
| Online post library | Skip | |

## Production and automation

| Function | Status | Note |
|---|---|---|
| Plate engraving merged from CSV | P3 | |
| Scripted add-ons, like Gadgets | P3 | Gadgets are Pro only. A store build cannot run downloaded code, so this needs a safe design |
| Rotary wrapping and spiral toolpaths | P3 | |
| Laser toolpaths | P3 | Paid add-on or hybrid feature at Vectric |

## Closest competitor on the Mac

Vectric Spark is a native macOS CNC CAD/CAM product for Apple silicon. Its page lists 2D design, cut-out, pocket, drilling, V-carving, flat-bottom V-carving, and a toolpath preview, plus laser toolpaths. It offers a free trial. Neither page read states a job size limit, and the price did not come through in the fetch.

## Gaps in the source

- The fetch did not capture the per-edition cells for several compare rows, such as 3D modeling and tiling.
- The later part of the VCarve page came through as a summary. Check form tools, plate engraving, and rotary details on the live page.
