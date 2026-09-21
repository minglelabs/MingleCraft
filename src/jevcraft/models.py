"""Versioned wire contract shared with the BWAPI bridge."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TrainType = str
BuildType = str
Category = str


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Position(Model):
    x: int = Field(ge=0, le=8192)
    y: int = Field(ge=0, le=8192)

    def distance_squared(self, other: "Position") -> int:
        return (self.x - other.x) ** 2 + (self.y - other.y) ** 2


class BuildSite(Model):
    unit_type: BuildType
    tile: Position


class Unit(Model):
    id: int = Field(ge=0)
    type: str = Field(max_length=80)
    position: Position
    hit_points: int = Field(ge=0)
    completed: bool = True
    idle: bool = False
    training: bool = False
    constructing: bool = False
    can_move: bool = False
    can_attack: bool = False
    can_train: tuple[TrainType, ...] = ()
    can_gather: tuple[int, ...] = ()
    build_sites: tuple[BuildSite, ...] = ()


class Enemy(Model):
    id: int = Field(ge=0)
    type: str = Field(max_length=80)
    position: Position
    hit_points: int = Field(ge=0)
    visible: bool


class Mineral(Model):
    id: int = Field(ge=0)
    position: Position


class Location(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    position: Position
    kind: Literal["home", "start"]
    explored: bool = False


class Counters(Model):
    gathered_minerals: int = Field(default=0, ge=0)
    gathered_gas: int = Field(default=0, ge=0)
    spent_minerals: int = Field(default=0, ge=0)
    spent_gas: int = Field(default=0, ge=0)
    units_killed: int = Field(default=0, ge=0)
    units_lost: int = Field(default=0, ge=0)
    commands_attempted: int = Field(default=0, ge=0)
    commands_accepted: int = Field(default=0, ge=0)
    commands_effective: int = Field(default=0, ge=0)


class Receipt(Model):
    decision_id: str
    frame: int = Field(ge=0)
    attempted: int = Field(ge=0)
    accepted: int = Field(ge=0)
    effective: int = Field(ge=0)
    reason: str = Field(max_length=256)


class Observation(Model):
    protocol_version: Literal[1] = 1
    match_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    frame: int = Field(ge=0)
    map_name: str = Field(min_length=1, max_length=256)
    map_hash: str = Field(min_length=1, max_length=128)
    self_race: str = "Terran"
    enemy_race: str = "Terran"
    complete_map_information: Literal[False] = False
    minerals: int = Field(ge=0)
    gas: int = Field(ge=0)
    # Human supply, not BWAPI's doubled supply units.
    supply_used: int = Field(ge=0, le=400)
    supply_total: int = Field(ge=0, le=400)
    home: Position
    units: tuple[Unit, ...] = Field(max_length=1024)
    enemies: tuple[Enemy, ...] = Field(default=(), max_length=1024)
    mineral_patches: tuple[Mineral, ...] = Field(default=(), max_length=512)
    locations: tuple[Location, ...] = Field(default=(), max_length=16)
    destroyed_enemy_ids: tuple[int, ...] = ()
    counters: Counters = Counters()
    receipts: tuple[Receipt, ...] = Field(default=(), max_length=64)
    ended: bool = False
    result: Literal["win", "loss", "draw", "unknown"] = "unknown"

    @model_validator(mode="after")
    def unique_ids(self):
        for collection in (self.units, self.enemies, self.mineral_patches, self.locations):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("Duplicate entity IDs in observation")
        return self


class Command(Model):
    kind: str
    unit_ids: tuple[int, ...] = Field(min_length=1, max_length=200)
    unit_type: str | None = None
    target_id: int | None = None
    position: Position | None = None
    tile: Position | None = None


class Action(Model):
    id: str
    category: Category
    label: str
    group: str
    priority: float = 0
    commands: tuple[Command, ...] = ()


class ChoiceQuestion(Model):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]


class ChoiceAnswer(Model):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class NoulQuestion(Model):
    type: Literal["noul"] = "noul"
    instructions: str


class NoulAnswer(Model):
    type: Literal["noul"] = "noul"
    noul: float = Field(ge=0, le=1)


class ProviderResult(Model):
    model: str
    answers: dict[str, ChoiceAnswer | NoulAnswer]
    usage: dict[str, int] = {}


class DecisionRequest(Model):
    state: dict
    questions: dict[str, ChoiceQuestion | NoulQuestion]
    choice_tree: dict[str, dict[str, str]] | None = None
    # Baselines only; deliberately excluded from remote model payloads.
    priorities: dict[str, dict[str, float]]


class DecisionEnvelope(Model):
    protocol_version: Literal[1] = 1
    match_id: str
    decision_id: str
    observed_frame: int
    expires_frame: int
    action_id: str
    commands: tuple[Command, ...]
    fallback_reason: str | None = None
