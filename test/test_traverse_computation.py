# coding=utf-8
"""Tests for survey notebook domain models."""

import os
import sys
import math
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from survey_model import (  # noqa: E402
    Coordinate,
    DEFAULT_NOTEBOOK_COLUMNS,
    IMPORTABLE_NOTEBOOK_COLUMNS,
    BlockKind,
    DistanceSource,
    DistanceUnit,
    InclinationUnit,
    ProjectRecord,
    ProjectWorkspace,
    StorageBackend,
    SurveyBlock,
    SurveyObservation,
    TraverseComputation,
    UnitProfile,
    azimuth_to_deltas,
    alpha_index_label,
    compute_traverse,
    compute_horizontal_distance,
    normalize_station_label,
    observation_distance_source,
    observation_horizontal_distance,
    detect_blocks,
    rows_to_project,
    validate_observation_inputs,
)


class SurveyModelTest(unittest.TestCase):
    """Test notebook normalization and unit conversions."""

    def test_station_labels_are_not_forced_to_numeric(self):
        self.assertEqual(normalize_station_label(" 43+ "), "43+")
        self.assertEqual(normalize_station_label("bp-1"), "BP-1")
        self.assertEqual(normalize_station_label(" 0 - 1 "), "0-1")

    def test_default_columns_match_single_grid_layout(self):
        self.assertEqual(
            DEFAULT_NOTEBOOK_COLUMNS,
            (
                "from_station",
                "target_station",
                "connect_to",
                "close_to",
                "exclude_marker",
                "slope_distance",
                "inclination",
                "azimuth",
                "horizontal_distance",
                "geo_point",
                "delta_x",
                "delta_y",
            ),
        )
        self.assertEqual(
            IMPORTABLE_NOTEBOOK_COLUMNS,
            (
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
            ),
        )

    def test_alpha_index_label_supports_extended_sequences(self):
        self.assertEqual(alpha_index_label(0), "A")
        self.assertEqual(alpha_index_label(25), "Z")
        self.assertEqual(alpha_index_label(26), "AA")

    def test_horizontal_distance_from_degree_input(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.METERS,
            inclination_unit=InclinationUnit.DEGREES,
        )
        result = compute_horizontal_distance(10.0, 60.0, units)
        self.assertTrue(math.isclose(result, 5.0, rel_tol=1e-9))

    def test_horizontal_distance_from_percent_input(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.FEET,
            inclination_unit=InclinationUnit.PERCENT,
        )
        result = compute_horizontal_distance(100.0, 100.0, units)
        self.assertTrue(math.isclose(result, 21.55261469056597, rel_tol=1e-9))

    def test_azimuth_to_deltas_uses_north_clockwise_convention(self):
        delta_x, delta_y = azimuth_to_deltas(10.0, 90.0)
        self.assertTrue(math.isclose(delta_x, 10.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(delta_y, 0.0, abs_tol=1e-9))

    def test_sd_and_inc_take_priority_over_hd(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.METERS,
            inclination_unit=InclinationUnit.DEGREES,
        )
        row = SurveyObservation(
            from_station="BP",
            target_station="1",
            slope_distance=10.0,
            inclination=60.0,
            horizontal_distance=8.0,
        )
        self.assertTrue(
            math.isclose(observation_horizontal_distance(row, units), 5.0, rel_tol=1e-9)
        )
        self.assertEqual(observation_distance_source(row), DistanceSource.SLOPE_DISTANCE)

    def test_hd_is_used_when_sd_inc_are_absent(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.METERS,
            inclination_unit=InclinationUnit.DEGREES,
        )
        row = SurveyObservation(
            from_station="BP",
            target_station="1",
            horizontal_distance=8.0,
        )
        self.assertEqual(observation_horizontal_distance(row, units), 8.0)
        self.assertEqual(observation_distance_source(row), DistanceSource.HORIZONTAL_DISTANCE)

    def test_validation_requires_distance_pair_or_hd(self):
        row = SurveyObservation(
            from_station="BP",
            target_station="1",
            slope_distance=10.0,
        )
        self.assertEqual(
            validate_observation_inputs(row),
            ["AZ is required.", "INC is required when SD is provided."],
        )

    def test_compute_traverse_expands_simple_hd_path(self):
        result = compute_traverse(
            [
                SurveyObservation(
                    from_station="BP",
                    target_station="1",
                    azimuth=90.0,
                    horizontal_distance=10.0,
                ),
                SurveyObservation(
                    from_station="1",
                    target_station="2",
                    azimuth=0.0,
                    horizontal_distance=5.0,
                ),
            ],
            start_coordinate=Coordinate(100.0, 200.0),
        )

        self.assertIsInstance(result, TraverseComputation)
        self.assertEqual(len(result.leg_results), 2)
        coordinates = result.station_coordinates()
        self.assertTrue(math.isclose(coordinates["1"].x, 110.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(coordinates["1"].y, 200.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(coordinates["2"].x, 110.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(coordinates["2"].y, 205.0, rel_tol=1e-9))

    def test_compute_traverse_uses_sd_inc_as_primary_distance(self):
        result = compute_traverse(
            [
                SurveyObservation(
                    from_station="BP",
                    target_station="49",
                    azimuth=20.0,
                    inclination=30.0,
                    slope_distance=11.7,
                    horizontal_distance=999.0,
                ),
            ],
            start_coordinate=Coordinate(0.0, 0.0),
        )

        leg = result.leg_results[0]
        expected_hd = 11.7 * math.cos(math.radians(30.0))
        self.assertTrue(math.isclose(leg.horizontal_distance, expected_hd, rel_tol=1e-9))

    def test_compute_traverse_requires_known_first_station(self):
        with self.assertRaises(ValueError):
            compute_traverse(
                [
                    SurveyObservation(
                        from_station="BP",
                        target_station="1",
                        azimuth=90.0,
                        horizontal_distance=10.0,
                    ),
                ],
                start_coordinate=Coordinate(0.0, 0.0),
                start_station="EP",
            )

    def test_project_collects_station_keys(self):
        project = rows_to_project(
            [
                SurveyObservation(
                    from_station="BP",
                    target_station="1",
                    connect_to="43+",
                ),
                SurveyObservation(
                    from_station="1",
                    target_station="2",
                    close_to="BP-1",
                ),
            ]
        )
        self.assertEqual(
            project.station_keys(),
            ["BP", "1", "43+", "1", "2", "BP-1"],
        )

    def test_blocks_support_auto_and_manual_names(self):
        project = rows_to_project(
            [
                SurveyObservation(
                    from_station="BP",
                    target_station="1",
                    block_id="area_a",
                ),
                SurveyObservation(
                    from_station="1",
                    target_station="2",
                    block_id="route_main",
                ),
            ],
            blocks=[
                SurveyBlock(
                    block_id="area_a",
                    kind=BlockKind.AREA,
                    sequence_index=0,
                ),
                SurveyBlock(
                    block_id="route_main",
                    kind=BlockKind.ROUTE,
                    sequence_index=0,
                    manual_name="林班西側",
                ),
            ],
            project_id="worksite_a",
            project_name="事業作業地A",
        )
        self.assertEqual(project.block_name("area_a"), "Area A")
        self.assertEqual(project.block_name("route_main"), "林班西側")
        self.assertEqual(project.block_names(), ["Area A", "林班西側"])
        self.assertEqual(project.project_id, "worksite_a")
        self.assertEqual(project.project_name, "事業作業地A")

    def test_workspace_can_switch_projects(self):
        workspace = ProjectWorkspace(
            backend=StorageBackend.SQLITE,
            path=os.path.join(tempfile.gettempdir(), "compass_projects.sqlite"),
        )
        workspace.add_project(
            ProjectRecord(
                project_id="worksite_a",
                project_name="事業作業地A",
                is_active=True,
            )
        )
        workspace.add_project(
            ProjectRecord(
                project_id="worksite_b",
                project_name="事業作業地B",
            )
        )

        self.assertEqual(workspace.project_names(), ["事業作業地A", "事業作業地B"])
        self.assertEqual(workspace.active_project().project_name, "事業作業地A")

        workspace.set_active_project("worksite_b")
        self.assertEqual(workspace.active_project().project_name, "事業作業地B")

    def test_detect_blocks_keeps_main_line_continuation_after_connect_to(self):
        observations = [
            SurveyObservation(from_station="1", target_station="2"),
            SurveyObservation(from_station="2", target_station="3", connect_to="3"),
            SurveyObservation(from_station="3", target_station="4"),
            SurveyObservation(from_station="4", target_station="5"),
            SurveyObservation(from_station="5", target_station="6", exclude_marker="開始"),
            SurveyObservation(from_station="6", target_station="7"),
            SurveyObservation(from_station="7", target_station="8", exclude_marker="終了"),
            SurveyObservation(from_station="8", target_station="9"),
            SurveyObservation(from_station="3", target_station="10", exclude_marker="単独"),
            SurveyObservation(from_station="10", target_station="11"),
            SurveyObservation(from_station="11", target_station="12"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[:8], [block_ids[0]] * 8)
        self.assertNotEqual(block_ids[7], block_ids[8])
        self.assertEqual(block_ids[8:], [block_ids[8]] * 3)
        block_kind_by_id = {block.block_id: block.kind for block in blocks}
        self.assertEqual(block_kind_by_id[block_ids[0]], BlockKind.ROUTE)
        self.assertEqual(block_kind_by_id[block_ids[8]], BlockKind.BRANCH)

    def test_detect_blocks_allows_shifted_station_labels_to_stay_on_main_line(self):
        observations = [
            SurveyObservation(from_station="1", target_station="2"),
            SurveyObservation(from_station="2", target_station="3", connect_to="3"),
            SurveyObservation(from_station="3", target_station="4"),
            SurveyObservation(from_station="5", target_station="6", exclude_marker="開始"),
            SurveyObservation(from_station="6", target_station="7"),
            SurveyObservation(from_station="7", target_station="8", exclude_marker="終了"),
            SurveyObservation(from_station="8", target_station="9"),
            SurveyObservation(from_station="9", target_station="10"),
            SurveyObservation(from_station="3", target_station="11", exclude_marker="単独"),
            SurveyObservation(from_station="11", target_station="12"),
            SurveyObservation(from_station="12", target_station="13"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[:8], [block_ids[0]] * 8)
        self.assertNotEqual(block_ids[7], block_ids[8])
        self.assertEqual(block_ids[8:], [block_ids[8]] * 3)

    def test_detect_blocks_handles_multiple_closures_with_pending_junction(self):
        # connect_to="A2" is self-referential (it names this row's own target),
        # so it never resolves as a real departure anywhere later in this data
        # and junction_pending stays armed for the rest of the rows. The
        # second closure (B loop) must still start its own block.
        observations = [
            SurveyObservation(from_station="A1", target_station="A2", connect_to="A2"),
            SurveyObservation(from_station="A2", target_station="A3"),
            SurveyObservation(from_station="A3", target_station="A4"),
            SurveyObservation(from_station="A4", target_station="A1", close_to="A1"),
            SurveyObservation(from_station="B1", target_station="B2"),
            SurveyObservation(from_station="B2", target_station="B3"),
            SurveyObservation(from_station="B3", target_station="B1", close_to="B1"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[:4], [block_ids[0]] * 4)
        self.assertEqual(block_ids[4:], [block_ids[4]] * 3)
        self.assertNotEqual(block_ids[3], block_ids[4])
        block_kind_by_id = {block.block_id: block.kind for block in blocks}
        self.assertEqual(block_kind_by_id[block_ids[0]], BlockKind.AREA)
        self.assertEqual(block_kind_by_id[block_ids[4]], BlockKind.AREA)

    def test_detect_blocks_forward_reference_connect_to_suspends_immediately(self):
        # connect_to="65" names a different (not yet reached) station, unlike
        # the self-referential case above. The line up to 48 must suspend
        # right away, the 48->49 leg is a standalone connecting branch, 49..65
        # is its own closed area, and 65..85 resumes the ORIGINAL line
        # (48 and 65 are the same physical point) and closes back to BP.
        observations = [
            SurveyObservation(from_station="BP", target_station="1"),
            SurveyObservation(from_station="1", target_station="47"),
            SurveyObservation(from_station="47", target_station="48", connect_to="65"),
            SurveyObservation(from_station="48", target_station="49"),
            SurveyObservation(from_station="49", target_station="64"),
            SurveyObservation(from_station="64", target_station="65", close_to="49"),
            SurveyObservation(from_station="65", target_station="84"),
            SurveyObservation(from_station="84", target_station="85", close_to="BP"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[0], block_ids[1])
        self.assertEqual(block_ids[1], block_ids[2])
        self.assertNotEqual(block_ids[2], block_ids[3])
        self.assertNotEqual(block_ids[3], block_ids[4])
        self.assertEqual(block_ids[4], block_ids[5])
        self.assertEqual(block_ids[6], block_ids[0])
        self.assertEqual(block_ids[7], block_ids[0])

        block_kind_by_id = {block.block_id: block.kind for block in blocks}
        self.assertEqual(block_kind_by_id[block_ids[0]], BlockKind.AREA)
        self.assertEqual(block_kind_by_id[block_ids[3]], BlockKind.BRANCH)
        self.assertEqual(block_kind_by_id[block_ids[4]], BlockKind.AREA)

    def test_detect_blocks_self_referential_connect_to_resumes_after_intervening_area(self):
        # Same physical layout as the forward-reference test above, but the
        # field crew re-used the label "48" itself for the resume point
        # instead of introducing a new label "65" (connect_to="48" equals
        # this row's own target, so it looks self-referential like the
        # "keeps_main_line_continuation" test). The close_to at 64->65 in
        # between is what proves this "48" later on is a genuine resumption
        # of the original line, not a brand new branch — the result must be
        # identical in shape to the forward-reference version.
        observations = [
            SurveyObservation(from_station="BP", target_station="1"),
            SurveyObservation(from_station="1", target_station="47"),
            SurveyObservation(from_station="47", target_station="48", connect_to="48"),
            SurveyObservation(from_station="48", target_station="49"),
            SurveyObservation(from_station="49", target_station="64"),
            SurveyObservation(from_station="64", target_station="65", close_to="49"),
            SurveyObservation(from_station="48", target_station="84"),
            SurveyObservation(from_station="84", target_station="85", close_to="BP"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[0], block_ids[1])
        self.assertEqual(block_ids[1], block_ids[2])
        self.assertNotEqual(block_ids[2], block_ids[3])
        self.assertNotEqual(block_ids[3], block_ids[4])
        self.assertEqual(block_ids[4], block_ids[5])
        self.assertEqual(block_ids[6], block_ids[0])
        self.assertEqual(block_ids[7], block_ids[0])

        block_kind_by_id = {block.block_id: block.kind for block in blocks}
        self.assertEqual(block_kind_by_id[block_ids[0]], BlockKind.AREA)
        self.assertEqual(block_kind_by_id[block_ids[3]], BlockKind.BRANCH)
        self.assertEqual(block_kind_by_id[block_ids[4]], BlockKind.AREA)

    def test_detect_blocks_splits_trailing_area_when_data_ends_mid_branch(self):
        # A branch that departs from the main line (row "3->10") and never
        # resumes it before the data ends. Its close_to on the very last row
        # (14->15, closing back to 11) must still split the branch into a
        # connecting line (3->10->11) and a trailing area (11..15) instead of
        # being absorbed into one large block.
        observations = [
            SurveyObservation(from_station="1", target_station="2"),
            SurveyObservation(from_station="2", target_station="3", connect_to="3"),
            SurveyObservation(from_station="3", target_station="4"),
            SurveyObservation(from_station="4", target_station="5", exclude_marker="開始"),
            SurveyObservation(from_station="5", target_station="6"),
            SurveyObservation(from_station="6", target_station="7", exclude_marker="終了"),
            SurveyObservation(from_station="7", target_station="8"),
            SurveyObservation(from_station="8", target_station="9"),
            SurveyObservation(from_station="3", target_station="10", exclude_marker="単独"),
            SurveyObservation(from_station="10", target_station="11"),
            SurveyObservation(from_station="11", target_station="12"),
            SurveyObservation(from_station="12", target_station="13"),
            SurveyObservation(from_station="13", target_station="14"),
            SurveyObservation(from_station="14", target_station="15", close_to="11"),
        ]

        blocks, block_ids = detect_blocks(observations)

        self.assertEqual(block_ids[:8], [block_ids[0]] * 8)
        self.assertEqual(block_ids[8], block_ids[9])
        self.assertNotEqual(block_ids[9], block_ids[10])
        self.assertEqual(block_ids[10:], [block_ids[10]] * 4)
        block_kind_by_id = {block.block_id: block.kind for block in blocks}
        self.assertEqual(block_kind_by_id[block_ids[8]], BlockKind.BRANCH)
        self.assertEqual(block_kind_by_id[block_ids[10]], BlockKind.AREA)

    def test_compute_traverse_known_coordinates_resolve_cross_block_closure(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.METERS,
            inclination_unit=InclinationUnit.DEGREES,
        )
        # Station "1" is only known because an earlier block computed it;
        # this block never visits "1" itself except via close_to.
        known_coordinates = {"1": Coordinate(0.0, 0.0)}
        observations = [
            SurveyObservation(
                from_station="5",
                target_station="6",
                slope_distance=10.0,
                inclination=0.0,
                azimuth=90.0,
                close_to="1",
            ),
        ]

        with self.assertRaises(ValueError):
            compute_traverse(
                observations,
                start_coordinate=Coordinate(20.0, 0.0),
                units=units,
                start_station="5",
            )

        computation = compute_traverse(
            observations,
            start_coordinate=Coordinate(20.0, 0.0),
            units=units,
            start_station="5",
            known_coordinates=known_coordinates,
        )
        self.assertEqual(len(computation.closures), 1)
        self.assertTrue(
            math.isclose(computation.closures[0].reference_coordinate.x, 0.0, abs_tol=1e-9)
        )

    def test_close_to_records_error_without_replacing_target_coordinate(self):
        units = UnitProfile(
            distance_unit=DistanceUnit.METERS,
            inclination_unit=InclinationUnit.DEGREES,
        )
        observations = [
            SurveyObservation(
                from_station="BP",
                target_station="1",
                slope_distance=10.0,
                inclination=0.0,
                azimuth=90.0,
            ),
            SurveyObservation(
                from_station="1",
                target_station="2",
                slope_distance=10.0,
                inclination=0.0,
                azimuth=90.0,
            ),
            SurveyObservation(
                from_station="2",
                target_station="3",
                slope_distance=10.0,
                inclination=0.0,
                azimuth=270.0,
                close_to="1",
            ),
            SurveyObservation(
                from_station="3",
                target_station="4",
                slope_distance=10.0,
                inclination=0.0,
                azimuth=90.0,
            ),
        ]

        computation = compute_traverse(
            observations,
            start_coordinate=Coordinate(0.0, 0.0),
            units=units,
            start_station="BP",
        )

        self.assertEqual(len(computation.closures), 1)
        self.assertTrue(
            math.isclose(computation.closures[0].error_distance, 0.0, abs_tol=1e-9)
        )
        self.assertTrue(
            math.isclose(computation.leg_results[2].target_coordinate.x, 10.0, rel_tol=1e-9)
        )


if __name__ == "__main__":
    unittest.main()
