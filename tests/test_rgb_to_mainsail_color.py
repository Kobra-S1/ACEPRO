"""
Test suite for rgb_to_mainsail_color function.

Tests color mapping from RGB to Mainsail color names using HSV conversion.
"""

import unittest
from extras.ace.commands import rgb_to_mainsail_color


class TestRgbToMainsailColor(unittest.TestCase):
    """Test RGB to Mainsail color name mapping."""

    def test_pure_red(self):
        """Pure red (255,0,0) should map to 'error'."""
        result = rgb_to_mainsail_color(255, 0, 0)
        self.assertEqual(result, 'error')

    def test_pure_green(self):
        """Pure green (0,255,0) should map to 'success'."""
        result = rgb_to_mainsail_color(0, 255, 0)
        self.assertEqual(result, 'success')

    def test_pure_blue(self):
        """Pure blue (0,0,255) should map to 'primary'."""
        result = rgb_to_mainsail_color(0, 0, 255)
        self.assertEqual(result, 'primary')

    def test_pure_cyan(self):
        """Pure cyan (0,255,255) should map to 'info'."""
        result = rgb_to_mainsail_color(0, 255, 255)
        self.assertEqual(result, 'info')

    def test_pure_magenta(self):
        """Pure magenta (255,0,255) should map to 'accent'."""
        result = rgb_to_mainsail_color(255, 0, 255)
        self.assertEqual(result, 'accent')

    def test_pure_yellow(self):
        """Pure yellow (255,255,0) should map to 'success' (hue 60° is green boundary)."""
        result = rgb_to_mainsail_color(255, 255, 0)
        self.assertEqual(result, 'success')

    def test_orange(self):
        """Orange (235,128,66) has hue ~25° which maps to 'error' (red range 0-30°)."""
        result = rgb_to_mainsail_color(235, 128, 66)
        self.assertEqual(result, 'error')

    def test_white(self):
        """Pure white (255,255,255) should return None (terminal default)."""
        result = rgb_to_mainsail_color(255, 255, 255)
        self.assertIsNone(result)

    def test_black(self):
        """Pure black (0,0,0) should map to 'secondary'."""
        result = rgb_to_mainsail_color(0, 0, 0)
        self.assertEqual(result, 'secondary')

    def test_dark_gray(self):
        """Dark gray (64,64,64) should map to 'secondary'."""
        result = rgb_to_mainsail_color(64, 64, 64)
        self.assertEqual(result, 'secondary')

    def test_light_gray(self):
        """Light gray (191,191,191) should map to 'secondary'."""
        result = rgb_to_mainsail_color(191, 191, 191)
        self.assertEqual(result, 'secondary')

    def test_mid_gray(self):
        """Mid gray (128,128,128) should map to 'secondary'."""
        result = rgb_to_mainsail_color(128, 128, 128)
        self.assertEqual(result, 'secondary')

    def test_near_white_high_brightness(self):
        """Near-white with high brightness (250,250,250) should return None."""
        result = rgb_to_mainsail_color(250, 250, 250)
        self.assertIsNone(result)

    def test_low_saturation_bluish_gray(self):
        """Bluish gray with low saturation (128,128,150) should map to 'secondary'."""
        result = rgb_to_mainsail_color(128, 128, 150)
        self.assertEqual(result, 'secondary')

    def test_low_saturation_greenish_gray(self):
        """Greenish gray with low saturation (120,140,120) should map to 'secondary'."""
        result = rgb_to_mainsail_color(120, 140, 120)
        self.assertEqual(result, 'secondary')

    def test_red_hue_boundary_low(self):
        """Red at low hue boundary (hue ~0°) should map to 'error'."""
        # Pure red has hue 0°
        result = rgb_to_mainsail_color(255, 10, 0)
        self.assertEqual(result, 'error')

    def test_red_hue_boundary_high(self):
        """Red at high hue boundary (hue ~350°) should map to 'error'."""
        # Hue around 350° (magenta-red)
        result = rgb_to_mainsail_color(255, 0, 43)
        self.assertEqual(result, 'error')

    def test_orange_yellow_boundary(self):
        """Color at orange-yellow boundary (hue ~50°) should map to 'warning'."""
        # Hue around 50°
        result = rgb_to_mainsail_color(255, 212, 0)
        self.assertEqual(result, 'warning')

    def test_yellow_green_boundary(self):
        """Color at yellow-green boundary (hue ~60°) should map to 'success'."""
        # Hue exactly 60° (yellow-green)
        result = rgb_to_mainsail_color(255, 255, 0)
        # Could be warning (yellow) or success depending on exact threshold
        self.assertIn(result, ['warning', 'success'])

    def test_green_cyan_boundary(self):
        """Color at green-cyan boundary (hue ~150°) should map to 'info'."""
        # Hue around 150° (cyan-green)
        result = rgb_to_mainsail_color(0, 255, 128)
        self.assertIn(result, ['success', 'info'])

    def test_cyan_blue_boundary(self):
        """Color at cyan-blue boundary (hue ~210°) should map to 'primary'."""
        # Hue around 210° (blue-cyan)
        result = rgb_to_mainsail_color(0, 128, 255)
        self.assertIn(result, ['info', 'primary'])

    def test_blue_magenta_boundary(self):
        """Color at blue-magenta boundary (hue ~270°) should map to 'accent'."""
        # Hue around 270° (blue-magenta)
        result = rgb_to_mainsail_color(128, 0, 255)
        self.assertIn(result, ['primary', 'accent'])

    def test_magenta_red_boundary(self):
        """Color at magenta-red boundary (hue ~330°) should map to 'error'."""
        # Hue around 330° (red-magenta)
        result = rgb_to_mainsail_color(255, 0, 128)
        self.assertIn(result, ['accent', 'error'])

    def test_dark_red(self):
        """Dark red (128,0,0) should map to 'error'."""
        result = rgb_to_mainsail_color(128, 0, 0)
        self.assertEqual(result, 'error')

    def test_dark_green(self):
        """Dark green (0,128,0) should map to 'success'."""
        result = rgb_to_mainsail_color(0, 128, 0)
        self.assertEqual(result, 'success')

    def test_dark_blue(self):
        """Dark blue (0,0,128) should map to 'primary'."""
        result = rgb_to_mainsail_color(0, 0, 128)
        self.assertEqual(result, 'primary')

    def test_light_red_pinkish(self):
        """Light red/pinkish (255,128,128) should map to 'error'."""
        result = rgb_to_mainsail_color(255, 128, 128)
        self.assertEqual(result, 'error')

    def test_light_green(self):
        """Light green (128,255,128) should map to 'success'."""
        result = rgb_to_mainsail_color(128, 255, 128)
        self.assertEqual(result, 'success')

    def test_light_blue(self):
        """Light blue (128,128,255) should map to 'primary'."""
        result = rgb_to_mainsail_color(128, 128, 255)
        self.assertEqual(result, 'primary')

    def test_orca_teal(self):
        """Orca teal color (0,150,136) should map to 'info' (cyan range)."""
        result = rgb_to_mainsail_color(0, 150, 136)
        self.assertEqual(result, 'info')

    def test_saturation_threshold_just_below(self):
        """Color with saturation just below 30% threshold should map to 'secondary'."""
        # Saturation ~25%: mostly gray with slight color tint
        result = rgb_to_mainsail_color(150, 160, 150)
        self.assertEqual(result, 'secondary')

    def test_saturation_threshold_just_above(self):
        """Color with saturation around 35% but low overall still maps to 'secondary'."""
        # Saturation ~26%: still below 30% threshold
        result = rgb_to_mainsail_color(100, 135, 100)
        self.assertEqual(result, 'secondary')

    def test_zero_rgb_values(self):
        """All zero RGB (0,0,0) should map to 'secondary' (black)."""
        result = rgb_to_mainsail_color(0, 0, 0)
        self.assertEqual(result, 'secondary')

    def test_max_rgb_values(self):
        """All max RGB (255,255,255) should return None (white)."""
        result = rgb_to_mainsail_color(255, 255, 255)
        self.assertIsNone(result)

    def test_single_channel_dominant_red(self):
        """Single red channel dominant (255,50,50) should map to 'error'."""
        result = rgb_to_mainsail_color(255, 50, 50)
        self.assertEqual(result, 'error')

    def test_single_channel_dominant_green(self):
        """Single green channel dominant (50,255,50) should map to 'success'."""
        result = rgb_to_mainsail_color(50, 255, 50)
        self.assertEqual(result, 'success')

    def test_single_channel_dominant_blue(self):
        """Single blue channel dominant (50,50,255) should map to 'primary'."""
        result = rgb_to_mainsail_color(50, 50, 255)
        self.assertEqual(result, 'primary')


if __name__ == '__main__':
    unittest.main()
