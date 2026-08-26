"""Tests for the plotting primitives."""

import unittest

from nodder import viz


class ResampleTest(unittest.TestCase):
    def test_returns_exactly_the_requested_count(self):
        for source in (3, 10, 240):
            for want in (1, 7, 40):
                with self.subTest(source=source, want=want):
                    self.assertEqual(
                        len(viz.resample(list(range(source)), want)), want
                    )

    def test_an_empty_series_becomes_zeroes(self):
        self.assertEqual(viz.resample([], 4), [0.0, 0.0, 0.0, 0.0])

    def test_a_matching_length_is_returned_unchanged(self):
        self.assertEqual(viz.resample([1, 2, 3], 3), [1.0, 2.0, 3.0])

    def test_downsampling_keeps_a_spike_rather_than_averaging_it_away(self):
        # A burst of activity is the interesting part of an activity chart.
        series = [0] * 100
        series[42] = 9
        self.assertEqual(max(viz.resample(series, 10)), 9.0)

    def test_a_zero_count_is_empty_not_an_error(self):
        self.assertEqual(viz.resample([1, 2, 3], 0), [])


class AreaTest(unittest.TestCase):
    def test_has_the_requested_shape(self):
        lines = viz.area([1, 5, 3], width=20, height=4)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(len(line) == 20 for line in lines))

    def test_every_character_is_braille(self):
        for line in viz.area([1, 5, 3, 8], 12, 3):
            for char in line:
                self.assertTrue(0x2800 <= ord(char) <= 0x28FF, repr(char))

    def test_a_flat_zero_series_draws_blank(self):
        lines = viz.area([0, 0, 0], 10, 2)
        self.assertEqual(lines, [chr(0x2800) * 10] * 2)

    def test_a_taller_value_fills_more_of_the_column(self):
        # Against a shared ceiling. Without `peak` each chart scales to its
        # own maximum, so two flat series would fill identically -- which is
        # the correct behaviour, just not what this is asserting.
        def ink(values):
            return sum(ord(c) != 0x2800
                       for line in viz.area(values, 8, 4, peak=10)
                       for c in line)
        self.assertGreater(ink([10, 10, 10, 10]), ink([1, 1, 1, 1]))

    def test_without_a_peak_each_chart_scales_to_its_own_maximum(self):
        self.assertEqual(viz.area([1, 1], 6, 2), viz.area([9, 9], 6, 2))

    def test_a_small_non_zero_value_still_shows(self):
        # Otherwise a quiet period is indistinguishable from a dead one.
        lines = viz.area([100, 1], 4, 3)
        self.assertTrue(any(ord(c) != 0x2800 for c in lines[-1]))

    def test_the_peak_can_be_pinned_so_charts_share_a_scale(self):
        small = viz.area([1], 4, 2, peak=100)
        large = viz.area([100], 4, 2, peak=100)
        self.assertNotEqual(small, large)

    def test_a_zero_size_chart_is_empty_not_an_error(self):
        self.assertEqual(viz.area([1, 2], 0, 3), [])
        self.assertEqual(viz.area([1, 2], 10, 0), [])

    def test_no_values_draws_blank_rather_than_raising(self):
        self.assertEqual(viz.area([], 6, 2), [chr(0x2800) * 6] * 2)

    def test_negative_values_do_not_escape_the_canvas(self):
        lines = viz.area([-5, 3, -1], 8, 2)
        self.assertEqual(len(lines), 2)


class SparklineTest(unittest.TestCase):
    def test_is_exactly_the_requested_width(self):
        self.assertEqual(len(viz.sparkline([1, 2, 3], 12)), 12)

    def test_a_flat_zero_series_reads_as_empty(self):
        self.assertEqual(viz.sparkline([0, 0], 4), "····")

    def test_no_values_reads_as_empty(self):
        self.assertEqual(viz.sparkline([], 3), "···")

    def test_the_largest_value_reaches_the_top_block(self):
        self.assertIn("█", viz.sparkline([0, 1, 9], 3))

    def test_a_zero_inside_a_busy_series_is_a_gap_not_a_block(self):
        self.assertEqual(viz.sparkline([5, 0, 5], 3)[1], "·")

    def test_a_zero_width_is_empty_not_an_error(self):
        self.assertEqual(viz.sparkline([1, 2], 0), "")


class BarTest(unittest.TestCase):
    def test_is_exactly_the_requested_width(self):
        self.assertEqual(len(viz.bar(3, 10, 20)), 20)

    def test_a_full_value_fills_the_bar(self):
        self.assertEqual(viz.bar(10, 10, 5), "█████")

    def test_zero_draws_nothing(self):
        self.assertEqual(viz.bar(0, 10, 4).strip(), "")

    def test_overflow_is_clamped_rather_than_overrunning(self):
        self.assertEqual(viz.bar(999, 10, 6), "██████")

    def test_a_zero_ceiling_does_not_divide_by_zero(self):
        self.assertEqual(viz.bar(5, 0, 4), "    ")


if __name__ == "__main__":
    unittest.main()
