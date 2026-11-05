from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

MM_PER_IN = 25.4


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Vector:
    points: list[list[float]]
    closed: bool
    layer: str = "Layer 1"
    id: str = field(default_factory=new_id)
    group: str = ""
    anchors: list[list[float]] | None = None


@dataclass
class Tool:
    id: str
    name: str
    kind: str
    diameter: float
    feed: float
    plunge: float
    rpm: int
    stepdown: float
    stepover: float = 0.0
    angle: float = 0.0
    flute_length: float = 0.0
    tip_diameter: float = 0.0
    number: int = 0
    material: str = ""
    machine: str = ""
    notes: str = ""

    @property
    def radius(self) -> float:
        return self.diameter / 2

    @property
    def tan_half(self) -> float:
        return math.tan(math.radians(self.angle / 2))

    @property
    def tip_radius(self) -> float:
        return self.tip_diameter / 2

    @property
    def max_vee_depth(self) -> float:
        return max(self.radius - self.tip_radius, 0.0) / self.tan_half


@dataclass
class Material:
    width: float
    height: float
    thickness: float


@dataclass
class Operation:
    kind: str
    tool_id: str
    vector_ids: list[str]
    params: dict = field(default_factory=dict)
    name: str = ""
    id: str = field(default_factory=new_id)
    enabled: bool = True


def new_layer(name: str = "Layer 1", keepout: bool = False) -> dict:
    return {"name": name, "visible": True, "locked": False, "keepout": keepout}


@dataclass
class Job:
    material: Material
    units: str = "in"
    z_zero: str = "top"
    xy_origin: str = "lower_left"
    safe_z: float = 5.08
    spoilboard_allowance: float = 0.5
    vectors: list[Vector] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    layers: list[dict] = field(default_factory=lambda: [new_layer()])
    guides: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    texts: dict = field(default_factory=dict)
    custom_posts: list[dict] = field(default_factory=list)
    arc_output: bool = False
    tiling: dict = field(default_factory=dict)
    machine: dict = field(default_factory=dict)

    def tool(self, tool_id: str) -> Tool:
        for tool in self.tools:
            if tool.id == tool_id:
                return tool
        raise KeyError(f"Unknown tool: {tool_id}")

    def vectors_for(self, ids: list[str]) -> list[Vector]:
        wanted = set(ids)
        return [v for v in self.vectors if v.id in wanted]

    def keepout_vectors(self) -> list[Vector]:
        names = {layer["name"] for layer in self.layers if layer.get("keepout")}
        return [v for v in self.vectors if v.closed and v.layer in names]


def job_from_dict(data: dict) -> Job:
    return Job(
        material=Material(**data["material"]),
        units=data.get("units", "in"),
        z_zero=data.get("z_zero", "top"),
        xy_origin=data.get("xy_origin", "lower_left"),
        safe_z=data.get("safe_z", 5.08),
        spoilboard_allowance=data.get("spoilboard_allowance", 0.5),
        vectors=[Vector(**v) for v in data.get("vectors", [])],
        tools=[Tool(**t) for t in data.get("tools", [])],
        operations=[Operation(**o) for o in data.get("operations", [])],
        layers=data.get("layers") or [new_layer()],
        guides=data.get("guides", []),
        dimensions=data.get("dimensions", []),
        texts=data.get("texts", {}),
        custom_posts=data.get("custom_posts", []),
        arc_output=data.get("arc_output", False),
        tiling=data.get("tiling", {}),
        machine=data.get("machine", {}),
    )


def job_to_dict(job: Job) -> dict:
    return asdict(job)


def default_tools() -> list[Tool]:
    path = Path(__file__).parent / "tooldb.json"
    return [Tool(**row) for row in json.loads(path.read_text())]
