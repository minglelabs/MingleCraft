"""Uniform, map-wide hierarchical coordinate selection for Jev."""

from dataclasses import dataclass

from jevcraft.models import ChoiceQuestion, Observation

DEFAULT_GRID_SIZE = 8
DEFAULT_PRECISION_PX = 8


@dataclass(frozen=True)
class SpatialGridSpec:
    map_width: int
    map_height: int
    region_divisions: int = 8
    cell_divisions: int = 8
    precision_px: int = DEFAULT_PRECISION_PX

    def __post_init__(self):
        if self.map_width < 1 or self.map_height < 1 or self.precision_px < 1:
            raise ValueError("Map dimensions and precision must be positive")

    def region_bounds(self, rx: int, ry: int) -> tuple[int, int, int, int]:
        min_x = self.map_width * rx // self.region_divisions
        min_y = self.map_height * ry // self.region_divisions
        max_x = self.map_width * (rx + 1) // self.region_divisions
        max_y = self.map_height * (ry + 1) // self.region_divisions
        return min_x, min_y, max_x, max_y


def get_spatial_grid_spec(
    obs: Observation, precision_px: int = DEFAULT_PRECISION_PX
) -> SpatialGridSpec:
    return SpatialGridSpec(
        map_width=obs.map_width,
        map_height=obs.map_height,
        region_divisions=DEFAULT_GRID_SIZE,
        cell_divisions=DEFAULT_GRID_SIZE,
        precision_px=precision_px,
    )


def build_refinement_question(
    actor_label: str,
    action_kind: str,
    bounds: tuple[int, int, int, int],
    level: int,
    divisions: int = 8,
) -> ChoiceQuestion:
    min_x, min_y, max_x, max_y = bounds
    criteria = {}
    for y in range(divisions):
        for x in range(divisions):
            left = min_x + (max_x - min_x) * x // divisions
            top = min_y + (max_y - min_y) * y // divisions
            right = min_x + (max_x - min_x) * (x + 1) // divisions
            bottom = min_y + (max_y - min_y) * (y + 1) // divisions
            if right > left and bottom > top:
                criteria[f"refine_{x}_{y}"] = (
                    f"Uniform cell ({x},{y}) pixels x=[{left},{right}), y=[{top},{bottom})"
                )
    return ChoiceQuestion(
        instructions=(
            f"Select the uniform map cell for {actor_label} to {action_kind}. "
            f"This is refinement level {level} within the previously selected region. "
            "Bounds are half-open; x increases rightward and y downward."
        ),
        criteria=criteria,
    )


def parse_refinement_key(key: str) -> tuple[int, int]:
    parts = key.split("_")
    if len(parts) != 3 or parts[0] != "refine":
        raise ValueError(f"Invalid refinement choice key: {key}")
    return int(parts[1]), int(parts[2])


def child_bounds(
    bounds: tuple[int, int, int, int], x: int, y: int, divisions: int = 8
) -> tuple[int, int, int, int]:
    min_x, min_y, max_x, max_y = bounds
    return (
        min_x + (max_x - min_x) * x // divisions,
        min_y + (max_y - min_y) * y // divisions,
        min_x + (max_x - min_x) * (x + 1) // divisions,
        min_y + (max_y - min_y) * (y + 1) // divisions,
    )


def build_region_question(
    actor_label: str, action_kind: str, spec: SpatialGridSpec
) -> ChoiceQuestion:
    criteria = {}
    for ry in range(spec.region_divisions):
        for rx in range(spec.region_divisions):
            key = f"region_{rx}_{ry}"
            min_x, min_y, max_x, max_y = spec.region_bounds(rx, ry)
            if max_x <= min_x or max_y <= min_y:
                continue
            criteria[key] = (
                f"Map Region ({rx},{ry}) covering pixels x=[{min_x},{max_x}), y=[{min_y},{max_y})"
            )
    return ChoiceQuestion(
        instructions=(
            f"Select the target macro map region for {actor_label} to {action_kind}. "
            "Uniform map-wide coverage without distance or visibility filtering."
        ),
        criteria=criteria,
    )


def parse_region_key(region_key: str) -> tuple[int, int]:
    parts = region_key.split("_")
    if len(parts) != 3 or parts[0] != "region":
        raise ValueError(f"Invalid region choice key: {region_key}")
    return int(parts[1]), int(parts[2])
