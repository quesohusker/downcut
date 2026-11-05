from __future__ import annotations

import hashlib
import sqlite3

from .model import MM_PER_IN

RATE_MM_MIN = {0: 60.0, 1: 1.0, 2: 1000.0, 3: MM_PER_IN * 60.0, 4: MM_PER_IN, 5: MM_PER_IN * 12.0}
NEEDED = {"tool_geometry", "tool_cutting_data", "tool_entity"}


def _open(data: bytes) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    image = bytearray(data)
    if image[:16] == b"SQLite format 3\x00" and image[18] == 2:
        image[18] = image[19] = 1
    try:
        connection.deserialize(bytes(image))
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    except sqlite3.DatabaseError as error:
        raise ValueError("That file is not a Vectric tool database.") from error
    if not NEEDED <= tables:
        raise ValueError("That file is not a Vectric tool database.")
    connection.row_factory = sqlite3.Row
    return connection


def _length(value, units) -> float:
    if value is None:
        return 0.0
    return float(value) * (MM_PER_IN if units == 1 else 1.0)


def _groups(connection) -> dict[str, str]:
    try:
        rows = connection.execute(
            "select id, parent_group_id, tool_geometry_id, name from tool_tree_entry"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    by_id = {row["id"]: row for row in rows}
    paths: dict[str, str] = {}
    for row in rows:
        if not row["tool_geometry_id"]:
            continue
        names = []
        parent = row["parent_group_id"]
        seen = set()
        while parent and parent in by_id and parent not in seen:
            seen.add(parent)
            if by_id[parent]["name"]:
                names.append(by_id[parent]["name"])
            parent = by_id[parent]["parent_group_id"]
        paths[row["tool_geometry_id"]] = " > ".join(reversed(names))
    return paths


def _tool_shape(row, units) -> tuple[str, dict, str] | None:
    kind = row["tool_type"]
    diameter = _length(row["diameter"], units)
    angle = float(row["included_angle"] or 0.0)
    flat = _length(row["flat_diameter"], units)
    tip = _length(row["tip_radius"], units)
    if kind == 0:
        return "ballnose", {"diameter": diameter}, ""
    if kind == 1:
        return "endmill", {"diameter": diameter}, ""
    if kind == 2:
        return "endmill", {"diameter": diameter}, "corner radius"
    if kind in (3, 4) and angle > 0:
        return "vbit", {"diameter": diameter, "angle": angle, "tip_diameter": flat}, ""
    if kind == 4:
        return "endmill", {"diameter": diameter}, ""
    if kind == 5:
        return "ballnose", {"diameter": tip * 2 or diameter, "angle": angle}, "taper"
    if kind == 6:
        return "drill", {"diameter": diameter}, ""
    return None


def read_vtdb(data: bytes) -> dict:
    connection = _open(data)
    tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    machines: dict[str, dict] = {}
    if "machine" in tables:
        for row in connection.execute("select * from machine"):
            scale = MM_PER_IN if row["dimensions_units"] == 1 else 1.0
            machines[row["id"]] = {
                "name": row["name"],
                "make": row["make"] or "",
                "model": row["model"] or "",
                "controller": row["controller_type"] or "",
                "width": float(row["max_width"] or 0.0) * scale,
                "height": float(row["max_height"] or 0.0) * scale,
            }
    materials = {}
    if "material" in tables:
        materials = {row["id"]: row["name"] for row in connection.execute("select id, name from material")}
    groups = _groups(connection)

    rows = connection.execute(
        """
        select e.material_id, e.machine_id, e.tool_geometry_id,
               g.name_format, g.tool_type, g.units, g.diameter, g.included_angle, g.flat_diameter,
               g.flute_length, g.tip_radius, g.notes,
               c.rate_units, c.length_units, c.feed_rate, c.plunge_rate, c.spindle_speed,
               c.stepdown, c.stepover, c.tool_number
        from tool_entity e
        join tool_geometry g on g.id = e.tool_geometry_id
        join tool_cutting_data c on c.id = e.tool_cutting_data_id
        """
    ).fetchall()
    numbers = {
        (row["tool_geometry_id"], row["machine_id"]): row["tool_number"]
        for row in rows
        if row["tool_number"] is not None
    }

    tools: list[dict] = []
    skipped_shape: set[str] = set()
    skipped_data: set[str] = set()
    plain_radius: set[str] = set()
    tapered: set[str] = set()
    units_seen: set[int] = set()
    for row in rows:
        if row["feed_rate"] is None:
            continue
        name = row["name_format"]
        shape = _tool_shape(row, row["units"])
        if shape is None:
            skipped_shape.add(name)
            continue
        rate = RATE_MM_MIN.get(row["rate_units"])
        if rate is None or not row["plunge_rate"] or not row["spindle_speed"] or not row["stepdown"]:
            skipped_data.add(name)
            continue
        units_seen.add(row["rate_units"])
        kind, values, flag = shape
        if flag == "corner radius":
            plain_radius.add(name)
        elif flag == "taper":
            tapered.add(name)
        machine = machines.get(row["machine_id"], {}).get("name", "")
        material = materials.get(row["material_id"], "")
        key = hashlib.sha1(f"{row['tool_geometry_id']}|{machine}|{material}".encode()).hexdigest()[:10]
        tools.append(
            {
                "id": f"vt-{key}",
                "name": name,
                "kind": kind,
                "feed": float(row["feed_rate"]) * rate,
                "plunge": float(row["plunge_rate"]) * rate,
                "rpm": int(row["spindle_speed"]),
                "stepdown": _length(row["stepdown"], row["length_units"]),
                "stepover": _length(row["stepover"], row["length_units"]),
                "flute_length": _length(row["flute_length"], row["units"]),
                "number": int(numbers.get((row["tool_geometry_id"], row["machine_id"])) or 0),
                "material": material,
                "machine": machine,
                "notes": groups.get(row["tool_geometry_id"], ""),
                **values,
            }
        )

    notes = []
    if plain_radius:
        notes.append(f"{len(plain_radius)} bowl or radius bits came in as end mills. Their corner radius is not modeled.")
    if tapered:
        notes.append(f"{len(tapered)} tapered ball nose bits came in as ball nose bits. Their taper is not modeled.")
    if skipped_shape:
        notes.append(f"{len(skipped_shape)} tools were skipped because Kerfline has no matching tool type.")
    if skipped_data:
        notes.append(f"{len(skipped_data)} tools were skipped because feeds or speeds are missing.")
    if units_seen - {4}:
        notes.append("Some feeds use a rate unit other than inches per minute. Check them in the tool editor.")
    return {
        "tools": tools,
        "machines": list(machines.values()),
        "materials": sorted(set(materials.values())),
        "notes": notes,
    }
