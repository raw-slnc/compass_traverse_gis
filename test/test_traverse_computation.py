# coding=utf-8
"""Tests for survey notebook domain models."""

import os
import sys
import math
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from survey_model import (
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
            path="/tmp/compass_projects.sqlite",
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


if __name__ == "__main__":
    unittest.main()
