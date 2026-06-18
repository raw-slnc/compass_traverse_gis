# -*- coding: utf-8 -*-
"""Domain models for compass survey notebook handling.

The first implementation goal is to preserve field notation as entered while
also providing normalized values for traverse calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
import math
import re
from typing import Iterable


DEFAULT_NOTEBOOK_COLUMNS = (
    "from_station",
    "target_station",
    "connect_to",
    "close_to",
    "slope_distance",
    "inclination",
    "azimuth",
    "horizontal_distance",
    "geo_point",
    "delta_x",
    "delta_y",
)

IMPORTABLE_NOTEBOOK_COLUMNS = (
    "from_station",
    "target_station",
    "connect_to",
    "close_to",
    "slope_distance",
    "inclination",
    "azimuth",
    "horizontal_distance",
    "latitude_dms",
    "longitude_dms",
)


class DistanceUnit(str, Enum):
    """Distance units accepted by the notebook."""

    METERS = "m"
    FEET = "ft"


class InclinationUnit(str, Enum):
    """Inclination representation used in field notes."""

    DEGREES = "deg"
    PERCENT = "pct"


class BlockKind(str, Enum):
    """Semantic block categories shown in output and preview."""

    AREA = "area"
    ROUTE = "route"
    BRANCH = "branch"
    GENERIC = "generic"


class StorageBackend(str, Enum):
    """Persistent container formats for multiple notebook projects."""

    GEOPACKAGE = "gpkg"
    SQLITE = "sqlite"


class DistanceSource(str, Enum):
    """How horizontal distance is resolved for one observation."""

    SLOPE_DISTANCE = "sd"
    HORIZONTAL_DISTANCE = "hd"


def normalize_station_label(label: str) -> str:
    """Keep field notation but remove accidental spacing noise.

    Labels are intentionally treated as strings because field crews may use
    values such as "BP", "0-1", "BP-1", or "43+".
    """

    cleaned = re.sub(r"\s+", "", (label or "").strip())
    return cleaned.upper()


def alpha_index_label(index: int) -> str:
    """Convert a zero-based index to A, B, ..., Z, AA style labels."""

    if index < 0:
        raise ValueError("index must be non-negative")

    chars: list[str] = []
    value = index
    while True:
        value, remainder = divmod(value, 26)
        chars.append(chr(ord("A") + remainder))
        if value == 0:
            break
        value -= 1
    return "".join(reversed(chars))


@dataclass(frozen=True)
class UnitProfile:
    """Project-level measurement defaults."""

    distance_unit: DistanceUnit = DistanceUnit.METERS
    inclination_unit: InclinationUnit = InclinationUnit.DEGREES


@dataclass(frozen=True)
class StationRef:
    """A station identifier with both raw and normalized forms."""

    raw: str
    key: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalize_station_label(self.raw))


@dataclass(frozen=True)
class Coordinate:
    """Planar coordinate in meters."""

    x: float
    y: float


@dataclass
class SurveyBlock:
    """Named block metadata managed outside the notebook grid.

    Blank rows in the notebook may visually suggest boundaries, but block names
    and output labels are maintained explicitly here.
    """

    block_id: str
    kind: BlockKind = BlockKind.GENERIC
    sequence_index: int = 0
    manual_name: str = ""
    start_row: int | None = None
    end_row: int | None = None

    def auto_name(self) -> str:
        if self.kind == BlockKind.AREA:
            return f"Area {alpha_index_label(self.sequence_index)}"
        if self.kind == BlockKind.ROUTE:
            return f"Line {alpha_index_label(self.sequence_index).lower()}"
        if self.kind == BlockKind.BRANCH:
            return f"Branch {alpha_index_label(self.sequence_index).lower()}'"
        return f"Block {self.sequence_index + 1}"

    def display_name(self) -> str:
        return self.manual_name.strip() or self.auto_name()


@dataclass
class ProjectRecord:
    """A saved project entry inside one GPKG/SQLite workspace."""

    project_id: str
    project_name: str
    business_name: str = ""
    year_reference_date: str = ""
    fiscal_year_start: str = "4/1"
    surveyor: str = ""
    operation_type: str = ""
    year_carryover: int = 0
    is_complete: bool = False
    description: str = ""
    is_active: bool = False

    def display_year(self) -> int | None:
        reference = parse_iso_like_date(self.year_reference_date)
        fiscal_start = parse_month_day(self.fiscal_year_start)
        if reference is None or fiscal_start is None:
            return None
        fiscal_start_month, fiscal_start_day = fiscal_start
        fiscal_boundary = date(reference.year, fiscal_start_month, fiscal_start_day)
        if reference < fiscal_boundary:
            return reference.year - 1
        return reference.year

    def display_year_western(self) -> str:
        year_value = self.display_year()
        return "" if year_value is None else str(year_value)

    def display_year_japanese(self) -> str:
        year_value = self.display_year()
        return western_year_to_japanese_era(year_value)


@dataclass
class SurveyObservation:
    """One notebook row.

    Rows are mode-free: branch, closure, split, and ordinary traverse legs are
    represented by optional columns instead of a separate entry mode.
    """

    from_station: str
    target_station: str
    connect_to: str = ""
    close_to: str = ""
    slope_distance: float | None = None
    inclination: float | None = None
    azimuth: float | None = None
    horizontal_distance: float | None = None
    latitude_dms: str = ""
    longitude_dms: str = ""
    block_id: str = ""
    row_kind: str = ""
    note: str = ""
    source_line: int | None = None

    def from_ref(self) -> StationRef:
        return StationRef(self.from_station)

    def target_ref(self) -> StationRef:
        return StationRef(self.target_station)

    def connect_to_ref(self) -> StationRef | None:
        if not self.connect_to:
            return None
        return StationRef(self.connect_to)

    def close_to_ref(self) -> StationRef | None:
        if not self.close_to:
            return None
        return StationRef(self.close_to)


@dataclass
class NotebookProject:
    """In-memory notebook project."""

    units: UnitProfile = field(default_factory=UnitProfile)
    blocks: dict[str, SurveyBlock] = field(default_factory=dict)
    observations: list[SurveyObservation] = field(default_factory=list)
    project_id: str = ""
    project_name: str = ""

    def add_row(self, row: SurveyObservation) -> None:
        self.observations.append(row)

    def add_block(self, block: SurveyBlock) -> None:
        self.blocks[block.block_id] = block

    def block_name(self, block_id: str) -> str:
        block = self.blocks.get(block_id)
        if block is None:
            return ""
        return block.display_name()

    def block_names(self) -> list[str]:
        return [block.display_name() for block in self.blocks.values()]

    def station_keys(self) -> list[str]:
        keys: list[str] = []
        for row in self.observations:
            keys.extend(
                key
                for key in (
                    row.from_ref().key,
                    row.target_ref().key,
                    row.connect_to_ref().key if row.connect_to_ref() else "",
                    row.close_to_ref().key if row.close_to_ref() else "",
                )
                if key
            )
        return keys


@dataclass
class ProjectWorkspace:
    """One persistent file containing multiple switchable projects."""

    backend: StorageBackend = StorageBackend.SQLITE
    path: str = ""
    projects: list[ProjectRecord] = field(default_factory=list)

    def add_project(self, project: ProjectRecord) -> None:
        self.projects.append(project)

    def project_names(self) -> list[str]:
        return [project.project_name for project in self.projects]

    def active_project(self) -> ProjectRecord | None:
        for project in self.projects:
            if project.is_active:
                return project
        return self.projects[0] if self.projects else None

    def set_active_project(self, project_id: str) -> None:
        found = False
        for project in self.projects:
            is_target = project.project_id == project_id
            project.is_active = is_target
            found = found or is_target
        if not found:
            raise KeyError(f"Unknown project_id: {project_id}")


@dataclass(frozen=True)
class TraverseLegResult:
    """Calculated result for one traverse observation."""

    from_station: str
    target_station: str
    horizontal_distance: float
    azimuth_degrees: float
    delta_x: float
    delta_y: float
    from_coordinate: Coordinate
    target_coordinate: Coordinate
    correction_x: float = 0.0
    correction_y: float = 0.0
    corrected_delta_x: float | None = None
    corrected_delta_y: float | None = None
    corrected_target_coordinate: Coordinate | None = None
    closure_station: str = ""
    closure_error_x: float | None = None
    closure_error_y: float | None = None
    closure_error_distance: float | None = None


@dataclass(frozen=True)
class TraverseClosure:
    """Closure information for one leg that closes to an existing station."""

    from_station: str
    target_station: str
    reference_station: str
    leg_index: int
    reference_coordinate: Coordinate
    computed_coordinate: Coordinate
    error_x: float
    error_y: float
    error_distance: float


@dataclass(frozen=True)
class LabelMismatch:
    """A row where from_station label differs from the previous row's target_station."""

    source_line: int
    from_station: str
    previous_target: str


@dataclass
class TraverseComputation:
    """Sequential coordinate expansion for a simple traverse."""

    start_station: str
    start_coordinate: Coordinate
    leg_results: list[TraverseLegResult] = field(default_factory=list)
    closures: list[TraverseClosure] = field(default_factory=list)

    def station_coordinates(self) -> dict[str, Coordinate]:
        coordinates: dict[str, Coordinate] = {
            normalize_station_label(self.start_station): self.start_coordinate,
        }
        for leg in self.leg_results:
            coordinates[normalize_station_label(leg.target_station)] = leg.target_coordinate
        return coordinates

    def total_horizontal_distance(self) -> float:
        return sum(leg.horizontal_distance for leg in self.leg_results)

    def latest_closure(self) -> TraverseClosure | None:
        if not self.closures:
            return None
        return self.closures[-1]

    def closure_ratio(self) -> float | None:
        closure = self.latest_closure()
        if closure is None or math.isclose(closure.error_distance, 0.0, abs_tol=1e-12):
            return None if closure is None else math.inf
        total_distance = self.closure_span_distance(closure)
        if math.isclose(total_distance, 0.0, abs_tol=1e-12):
            return None
        return total_distance / closure.error_distance

    def closure_span_distance(self, closure: TraverseClosure | None = None) -> float:
        selected = closure or self.latest_closure()
        if selected is None:
            return 0.0
        return sum(
            leg.horizontal_distance
            for leg in self.leg_results[: selected.leg_index + 1]
        )

    def corrected_leg_results(self) -> list[TraverseLegResult]:
        return [leg for leg in self.leg_results if leg.corrected_target_coordinate is not None]

    def corrected_area(self) -> float | None:
        closure = self.latest_closure()
        if closure is None:
            return None
        points = [self.start_coordinate]
        for leg in self.leg_results[: closure.leg_index + 1]:
            coordinate = leg.corrected_target_coordinate or leg.target_coordinate
            points.append(coordinate)
        if len(points) < 4:
            return None
        if not _coordinates_match(points[0], points[-1]):
            points.append(points[0])
        area = 0.0
        for first, second in zip(points, points[1:]):
            area += first.x * second.y - second.x * first.y
        return abs(area) / 2.0

    def corrected_perimeter(self) -> float | None:
        closure = self.latest_closure()
        if closure is None:
            return None
        return self.closure_span_distance(closure)


def _coordinates_match(first: Coordinate, second: Coordinate, tolerance: float = 1e-9) -> bool:
    return (
        math.isclose(first.x, second.x, abs_tol=tolerance)
        and math.isclose(first.y, second.y, abs_tol=tolerance)
    )


def convert_distance(value: float, unit: DistanceUnit) -> float:
    """Convert a distance to meters."""

    if unit == DistanceUnit.METERS:
        return value
    if unit == DistanceUnit.FEET:
        return value * 0.3048
    raise ValueError(f"Unsupported distance unit: {unit}")


def parse_month_day(text: str) -> tuple[int, int] | None:
    raw = str(text or "").strip()
    match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", raw)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def parse_iso_like_date(text: str) -> date | None:
    raw = str(text or "").strip()
    match = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})", raw)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def western_year_to_japanese_era(year_value: int | None) -> str:
    if year_value is None:
        return ""
    if year_value >= 2019:
        era_year = year_value - 2018
        year_text = "元" if era_year == 1 else str(era_year)
        return f"令和{year_text}年"
    if year_value >= 1989:
        return f"平成{year_value - 1988}年"
    if year_value >= 1926:
        return f"昭和{year_value - 1925}年"
    return str(year_value)


def convert_distance_from_meters(value: float, unit: DistanceUnit) -> float:
    """Convert a distance in meters to the selected display unit."""

    if unit == DistanceUnit.METERS:
        return value
    if unit == DistanceUnit.FEET:
        return value / 0.3048
    raise ValueError(f"Unsupported distance unit: {unit}")


def inclination_to_degrees(value: float, unit: InclinationUnit) -> float:
    """Convert inclination to degrees.

    Percent means grade percent, e.g. 100 = 45 degrees.
    """

    if unit == InclinationUnit.DEGREES:
        return value
    if unit == InclinationUnit.PERCENT:
        return math.degrees(math.atan(value / 100.0))
    raise ValueError(f"Unsupported inclination unit: {unit}")


def compute_horizontal_distance(
    slope_distance: float,
    inclination: float,
    units: UnitProfile,
) -> float:
    """Compute horizontal distance in meters from slope distance and inclination."""

    slope_distance_m = convert_distance(slope_distance, units.distance_unit)
    inclination_deg = inclination_to_degrees(
        inclination,
        units.inclination_unit,
    )
    return slope_distance_m * math.cos(math.radians(inclination_deg))


def observation_horizontal_distance(
    observation: SurveyObservation,
    units: UnitProfile,
) -> float | None:
    """Resolve horizontal distance in meters.

    Prefer SD+INC when available because the field workflow treats them as the
    primary observation pair. Fall back to explicit HD when SD+INC is absent.
    """

    if observation.slope_distance is not None and observation.inclination is not None:
        return compute_horizontal_distance(
            observation.slope_distance,
            observation.inclination,
            units,
        )
    if observation.horizontal_distance is not None:
        return convert_distance(
            observation.horizontal_distance,
            units.distance_unit,
        )
    return None


def observation_distance_source(observation: SurveyObservation) -> DistanceSource | None:
    """Return which distance input drives the calculation."""

    if observation.slope_distance is not None and observation.inclination is not None:
        return DistanceSource.SLOPE_DISTANCE
    if observation.horizontal_distance is not None:
        return DistanceSource.HORIZONTAL_DISTANCE
    return None


def validate_observation_inputs(observation: SurveyObservation) -> list[str]:
    """Validate minimum traverse inputs for one notebook row."""

    errors: list[str] = []

    if observation.azimuth is None:
        errors.append("AZ is required.")

    if observation.slope_distance is not None and observation.inclination is None:
        errors.append("INC is required when SD is provided.")
    if observation.inclination is not None and observation.slope_distance is None:
        errors.append("SD is required when INC is provided.")

    if (
        observation.horizontal_distance is None
        and observation.slope_distance is None
        and observation.inclination is None
    ):
        errors.append("Either SD+INC or HD is required.")

    return errors


def azimuth_to_deltas(horizontal_distance: float, azimuth_degrees: float) -> tuple[float, float]:
    """Convert azimuth and horizontal distance to XY deltas in meters.

    This assumes surveying azimuth measured clockwise from north.
    X corresponds to easting and Y corresponds to northing.
    """

    radians = math.radians(azimuth_degrees)
    delta_x = horizontal_distance * math.sin(radians)
    delta_y = horizontal_distance * math.cos(radians)
    return delta_x, delta_y


def detect_blocks(
    observations: list[SurveyObservation],
    row_kinds: list[str] | None = None,
) -> tuple[list[SurveyBlock], list[str]]:
    """Detect block boundaries from an observation list.

    Returns (blocks, block_ids) where block_ids[i] is the block_id for
    observations[i].  row_kinds[i] carries manual overrides: "branch" forces
    the row into a BRANCH block; "route_override" promotes a BRANCH candidate
    to ROUTE.  An empty string means auto-detect.

    Detection rules
    ---------------
    1. The first observation always starts a new block.
    2. A row with connect_to set declares a junction at that station.
       The immediately following row starts a new sub-block (the branch).
    3. While a junction is suspended, any row whose from_station matches the
       junction station key resumes the main block — regardless of whether the
       branch sub-block had a close_to row.
    4. A row that follows a closure row (and is not a junction resume) starts
       a new block.  If the block has close_to it is AREA; otherwise BRANCH.
    5. Manual row_kind "branch" / "route_override" override the auto kind.
    Rows are processed in table order regardless of station-label continuity.
    """

    if not observations:
        return [], []

    overrides: list[str] = list(row_kinds) if row_kinds else [""] * len(observations)
    while len(overrides) < len(observations):
        overrides.append("")

    blocks: list[SurveyBlock] = []
    block_row_lists: list[list[int]] = []

    current_block_rows: list[int] = []
    current_block_seq: int = 0
    current_block_is_branch: bool = False
    block_seq_counter: int = 0

    junction_pending: bool = False
    junction_station_key: str | None = None
    suspended_block_rows: list[int] | None = None
    suspended_block_seq: int | None = None
    suspended_block_is_branch: bool = False

    post_closure_flag: bool = False

    def _finish_block_entry(row_indices: list[int], seq: int,
                            force_branch: bool = False) -> SurveyBlock:
        has_close = any(observations[r].close_to for r in row_indices)
        any_branch_ov = any(overrides[r] == "branch" for r in row_indices)
        any_route_ov  = any(overrides[r] == "route_override" for r in row_indices)
        if any_route_ov:
            kind = BlockKind.ROUTE
        elif any_branch_ov:
            kind = BlockKind.BRANCH
        elif has_close:
            kind = BlockKind.AREA
        elif force_branch:
            kind = BlockKind.BRANCH
        else:
            kind = BlockKind.ROUTE
        return SurveyBlock(
            block_id=f"blk_{seq}",
            kind=kind,
            sequence_index=seq,
            start_row=row_indices[0] if row_indices else None,
            end_row=row_indices[-1] if row_indices else None,
        )

    def _commit_block(rows: list[int], seq: int, force_branch: bool) -> None:
        nonlocal block_seq_counter
        if not rows:
            return

        if not force_branch or not any(overrides[r] == "route_override" for r in rows):
            blocks.append(_finish_block_entry(rows, seq, force_branch))
            block_row_lists.append(list(rows))
            return

        # Branch block containing route_override: split at state transitions.
        # Default state in a forced-branch block is Branch.
        # route_override switches to Route; any non-route_override row switches back to Branch.
        segments: list[tuple[list[int], bool]] = []  # (row_indices, in_route)
        buf: list[int] = []
        in_route = False

        for r in rows:
            ov = overrides[r]
            if ov == "route_override" and not in_route:
                if buf:
                    segments.append((list(buf), False))
                buf = [r]
                in_route = True
            elif ov != "route_override" and in_route:
                if buf:
                    segments.append((list(buf), True))
                buf = [r]
                in_route = False
            else:
                buf.append(r)

        if buf:
            segments.append((list(buf), in_route))

        for idx, (seg_rows, seg_in_route) in enumerate(segments):
            use_seq = seq if idx == 0 else block_seq_counter
            if idx > 0:
                block_seq_counter += 1
            blocks.append(_finish_block_entry(seg_rows, use_seq, not seg_in_route))
            block_row_lists.append(seg_rows)

    def _split_or_commit_branch() -> bool:
        """Commit current_block_rows, splitting into connecting+area if applicable.

        Returns True if a split was performed (area portion committed separately),
        False if committed as a single block.
        Modifies nonlocal block_seq_counter via the outer scope.
        """
        nonlocal block_seq_counter
        if post_closure_flag and i > 0 and current_block_rows:
            prev_close = normalize_station_label(
                observations[i - 1].close_to or "")
            if prev_close and prev_close != junction_station_key:
                split_at = next(
                    (k for k, r in enumerate(current_block_rows)
                     if normalize_station_label(
                         observations[r].from_station) == prev_close),
                    None)
                if split_at is not None and split_at > 0:
                    # Connecting lines → BRANCH (force_branch=True)
                    _commit_block(current_block_rows[:split_at],
                                  current_block_seq, True)
                    # Area portion → AREA (has close_to, force_branch=False)
                    area_seq = block_seq_counter
                    block_seq_counter += 1
                    _commit_block(current_block_rows[split_at:], area_seq, False)
                    return True
        _commit_block(current_block_rows, current_block_seq,
                      current_block_is_branch)
        return False

    for i, obs in enumerate(observations):
        start_new = False
        is_post_closure_start = False

        if i == 0:
            start_new = True

        elif junction_pending:
            # Row immediately after a connect_to row → start branch sub-block.
            suspended_block_rows = list(current_block_rows)
            suspended_block_seq = current_block_seq
            suspended_block_is_branch = current_block_is_branch
            current_block_rows = []
            current_block_seq = block_seq_counter
            block_seq_counter += 1
            current_block_is_branch = True  # junction-started sub-blocks are BRANCH by default
            junction_pending = False

        elif suspended_block_rows is not None:
            from_key = normalize_station_label(obs.from_station)
            if junction_station_key is not None and from_key == junction_station_key:
                # Main block resumes. Check for connecting+area split within branch.
                _split_or_commit_branch()
                current_block_rows = list(suspended_block_rows)
                current_block_seq = suspended_block_seq
                current_block_is_branch = suspended_block_is_branch
                suspended_block_rows = None
                suspended_block_seq = None
                junction_station_key = None
            elif post_closure_flag and overrides[i] != "route_override":
                # Intermediate closure within branch (main not resuming yet).
                if _split_or_commit_branch():
                    current_block_rows = []
                    current_block_seq = block_seq_counter
                    block_seq_counter += 1
                    current_block_is_branch = True
                else:
                    start_new = True
                    is_post_closure_start = True

        elif post_closure_flag and overrides[i] != "route_override":
            start_new = True
            is_post_closure_start = True

        if start_new and current_block_rows:
            _commit_block(current_block_rows, current_block_seq,
                          current_block_is_branch)
            current_block_rows = []
            current_block_seq = block_seq_counter
            block_seq_counter += 1
            current_block_is_branch = is_post_closure_start
        elif start_new:
            current_block_seq = block_seq_counter
            block_seq_counter += 1

        current_block_rows.append(i)

        if obs.connect_to and not junction_pending and suspended_block_rows is None:
            junction_station_key = normalize_station_label(obs.connect_to)
            junction_pending = True

        post_closure_flag = bool(obs.close_to)

    _commit_block(current_block_rows, current_block_seq, current_block_is_branch)

    if suspended_block_rows:
        _commit_block(suspended_block_rows, suspended_block_seq,
                      suspended_block_is_branch)

    # Re-assign sequence_index per kind for display naming.
    # Use the earliest observed row so the mainline that starts first
    # becomes Area A / Line a more naturally for field users.
    by_kind: dict[BlockKind, list[SurveyBlock]] = {}
    for blk in blocks:
        by_kind.setdefault(blk.kind, []).append(blk)
    for _kind, kind_blocks in by_kind.items():
        ordered = sorted(
            kind_blocks,
            key=lambda blk: (
                blk.start_row if blk.start_row is not None else 10**9,
                blk.end_row if blk.end_row is not None else 10**9,
                blk.block_id,
            ),
        )
        for seq, blk in enumerate(ordered):
            blk.sequence_index = seq

    block_id_per_row: list[str] = [""] * len(observations)
    for blk, row_list in zip(blocks, block_row_lists):
        for r in row_list:
            if 0 <= r < len(block_id_per_row):
                block_id_per_row[r] = blk.block_id

    return blocks, block_id_per_row


def seq_index_map(
    observations: list[SurveyObservation],
    blocks: list[SurveyBlock],
    block_ids: list[str],
) -> dict[str, int]:
    """Return {station_key: seq_index} using per-block sequential numbering.

    seq_index = 0 is the from_station of the first row in each block.
    Subsequent target_stations increment within the block.
    Stations shared across blocks take the value from the first occurrence.
    Uses block_ids to support non-contiguous blocks (junction splits).
    """

    block_row_map: dict[str, list[int]] = {}
    for i, bid in enumerate(block_ids):
        if bid:
            block_row_map.setdefault(bid, []).append(i)

    result: dict[str, int] = {}
    for blk in blocks:
        seq = 0
        for r in sorted(block_row_map.get(blk.block_id, [])):
            if r >= len(observations):
                break
            obs = observations[r]
            from_key = normalize_station_label(obs.from_station)
            tgt_key  = normalize_station_label(obs.target_station)
            if from_key not in result:
                result[from_key] = seq
                seq += 1
            if tgt_key not in result:
                result[tgt_key] = seq
                seq += 1
    return result


def compute_traverse(
    observations: Iterable[SurveyObservation],
    start_coordinate: Coordinate,
    units: UnitProfile | None = None,
    start_station: str | None = None,
) -> TraverseComputation:
    """Expand a simple ordered traverse from a known start coordinate."""

    observation_list = list(observations)
    if not observation_list:
        raise ValueError("At least one observation is required.")

    selected_units = units or UnitProfile()
    initial_station = start_station or observation_list[0].from_station
    normalized_start = normalize_station_label(initial_station)
    first_from = normalize_station_label(observation_list[0].from_station)
    if first_from != normalized_start:
        raise ValueError("start_station must match the first observation from_station.")

    station_coordinates: dict[str, Coordinate] = {normalized_start: start_coordinate}
    leg_results: list[TraverseLegResult] = []
    closures: list[TraverseClosure] = []

    for observation in observation_list:
        errors = validate_observation_inputs(observation)
        if errors:
            joined = " ".join(errors)
            raise ValueError(
                f"Invalid observation {observation.from_station}->{observation.target_station}: {joined}"
            )

        from_key = observation.from_ref().key
        if from_key not in station_coordinates:
            # Station-label gap: the field crew's numbering skipped a station.
            # Inherit the most recently computed position so the traverse
            # continues row-sequentially without requiring a measured leg.
            station_coordinates[from_key] = list(station_coordinates.values())[-1]

        horizontal_distance = observation_horizontal_distance(observation, selected_units)
        if horizontal_distance is None:
            raise ValueError(
                f"Could not resolve horizontal distance for {observation.from_station}->{observation.target_station}."
            )

        delta_x, delta_y = azimuth_to_deltas(horizontal_distance, observation.azimuth)
        from_coordinate = station_coordinates[from_key]
        target_coordinate = Coordinate(
            x=from_coordinate.x + delta_x,
            y=from_coordinate.y + delta_y,
        )

        closure_reference_key = ""
        closure_reference_label = ""
        close_to_ref = observation.close_to_ref()
        target_key = observation.target_ref().key
        if close_to_ref is not None:
            closure_reference_key = close_to_ref.key
            closure_reference_label = close_to_ref.raw

        closure_error_x = None
        closure_error_y = None
        closure_error_distance = None
        if closure_reference_key:
            reference_coordinate = station_coordinates.get(closure_reference_key)
            if reference_coordinate is None:
                raise ValueError(
                    f"Missing closure reference for {observation.from_station}->{observation.target_station}: "
                    f"{closure_reference_label}"
                )
            closure_error_x = target_coordinate.x - reference_coordinate.x
            closure_error_y = target_coordinate.y - reference_coordinate.y
            closure_error_distance = math.hypot(closure_error_x, closure_error_y)
            closures.append(
                TraverseClosure(
                    from_station=observation.from_station,
                    target_station=observation.target_station,
                    reference_station=closure_reference_label,
                    leg_index=len(leg_results),
                    reference_coordinate=reference_coordinate,
                    computed_coordinate=target_coordinate,
                    error_x=closure_error_x,
                    error_y=closure_error_y,
                    error_distance=closure_error_distance,
                )
            )

        leg_results.append(
            TraverseLegResult(
                from_station=observation.from_station,
                target_station=observation.target_station,
                horizontal_distance=horizontal_distance,
                azimuth_degrees=observation.azimuth,
                delta_x=delta_x,
                delta_y=delta_y,
                from_coordinate=from_coordinate,
                target_coordinate=target_coordinate,
                closure_station=closure_reference_label,
                closure_error_x=closure_error_x,
                closure_error_y=closure_error_y,
                closure_error_distance=closure_error_distance,
            )
        )
        station_coordinates[observation.target_ref().key] = target_coordinate

    computation = TraverseComputation(
        start_station=initial_station,
        start_coordinate=start_coordinate,
        leg_results=leg_results,
        closures=closures,
    )
    return apply_bowditch_correction(computation)


def apply_bowditch_correction(computation: TraverseComputation) -> TraverseComputation:
    """Apply Bowditch correction to the latest closed traverse span."""

    closure = computation.latest_closure()
    if closure is None:
        return computation

    span_legs = computation.leg_results[: closure.leg_index + 1]
    span_distance = computation.closure_span_distance(closure)
    if math.isclose(span_distance, 0.0, abs_tol=1e-12):
        return computation

    corrected_legs: list[TraverseLegResult] = []
    corrected_from_coordinate = computation.start_coordinate

    for index, leg in enumerate(computation.leg_results):
        if index <= closure.leg_index:
            ratio = leg.horizontal_distance / span_distance
            correction_x = closure.error_x * ratio
            correction_y = closure.error_y * ratio
            corrected_delta_x = leg.delta_x - correction_x
            corrected_delta_y = leg.delta_y - correction_y
            corrected_target_coordinate = Coordinate(
                x=corrected_from_coordinate.x + corrected_delta_x,
                y=corrected_from_coordinate.y + corrected_delta_y,
            )
            corrected_leg = replace(
                leg,
                correction_x=correction_x,
                correction_y=correction_y,
                corrected_delta_x=corrected_delta_x,
                corrected_delta_y=corrected_delta_y,
                corrected_target_coordinate=corrected_target_coordinate,
            )
            corrected_from_coordinate = corrected_target_coordinate
        else:
            corrected_target_coordinate = Coordinate(
                x=corrected_from_coordinate.x + leg.delta_x,
                y=corrected_from_coordinate.y + leg.delta_y,
            )
            corrected_leg = replace(
                leg,
                corrected_delta_x=leg.delta_x,
                corrected_delta_y=leg.delta_y,
                corrected_target_coordinate=corrected_target_coordinate,
            )
            corrected_from_coordinate = corrected_target_coordinate
        corrected_legs.append(corrected_leg)

    return TraverseComputation(
        start_station=computation.start_station,
        start_coordinate=computation.start_coordinate,
        leg_results=corrected_legs,
        closures=computation.closures,
    )


def detect_label_mismatches(
    observations: list[SurveyObservation],
) -> list[LabelMismatch]:
    """Return rows where from_station does not match the previous row's target_station.

    Cross-block boundaries are excluded; a block change is an intentional break.
    Both intentional gaps (40→41) and typos (u→i) are flagged — the user decides.
    """
    mismatches: list[LabelMismatch] = []
    for i in range(1, len(observations)):
        if observations[i].block_id != observations[i - 1].block_id:
            continue
        prev_target_key = normalize_station_label(observations[i - 1].target_station)
        curr_from_key = normalize_station_label(observations[i].from_station)
        if curr_from_key != prev_target_key:
            mismatches.append(
                LabelMismatch(
                    source_line=observations[i].source_line or (i + 1),
                    from_station=observations[i].from_station,
                    previous_target=observations[i - 1].target_station,
                )
            )
    return mismatches


def rows_to_project(
    rows: Iterable[SurveyObservation],
    units: UnitProfile | None = None,
    blocks: Iterable[SurveyBlock] | None = None,
    project_id: str = "",
    project_name: str = "",
) -> NotebookProject:
    """Build a project from observation rows."""

    project = NotebookProject(
        units=units or UnitProfile(),
        project_id=project_id,
        project_name=project_name,
    )
    for block in blocks or []:
        project.add_block(block)
    for row in rows:
        project.add_row(row)
    return project
