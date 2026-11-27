import unittest

from cortai.templates import (
    DEFAULT_TEMPLATE_CONFIG,
    render_kwargs_from_template,
    validate_template_config,
)


class TestValidateTemplateConfig(unittest.TestCase):

    def test_empty_config_fills_all_defaults(self):
        result = validate_template_config({})
        self.assertEqual(result, DEFAULT_TEMPLATE_CONFIG)

    def test_partial_override_keeps_other_defaults(self):
        result = validate_template_config({"caption_preset": "cyan-shadow"})
        self.assertEqual(result["caption_preset"], "cyan-shadow")
        self.assertEqual(result["layout"], "auto")
        self.assertEqual(result["watermark"], DEFAULT_TEMPLATE_CONFIG["watermark"])

    def test_partial_nested_override_merges_not_replaces(self):
        result = validate_template_config({"watermark": {"opacity": 0.9}})
        self.assertEqual(result["watermark"]["opacity"], 0.9)
        self.assertEqual(result["watermark"]["position"], "bottom-right")  # untouched default

    def test_unknown_top_level_field_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"not_a_real_field": 1})

    def test_unknown_nested_field_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"watermark": {"not_a_real_field": 1}})

    def test_invalid_layout_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"layout": "zoom"})

    def test_blurred_background_layout_accepted(self):
        result = validate_template_config({"layout": "blurred_background"})
        self.assertEqual(result["layout"], "blurred_background")

    def test_all_faces_layout_accepted(self):
        result = validate_template_config({"layout": "all_faces"})
        self.assertEqual(result["layout"], "all_faces")

    def test_invalid_caption_preset_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"caption_preset": "does-not-exist"})

    def test_invalid_watermark_position_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"watermark": {"position": "middle-ish"}})

    def test_opacity_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"watermark": {"opacity": 1.5}})
        with self.assertRaises(ValueError):
            validate_template_config({"watermark": {"opacity": -0.1}})

    def test_non_positive_watermark_width_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"watermark": {"width": 0}})

    def test_non_positive_export_dimensions_raise(self):
        with self.assertRaises(ValueError):
            validate_template_config({"export": {"target_width": 0}})

    def test_crf_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"export": {"crf": 100}})

    def test_non_dict_config_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config("not a dict")

    def test_vocabulary_defaults_to_empty_list(self):
        result = validate_template_config({})
        self.assertEqual(result["vocabulary"], [])

    def test_vocabulary_list_of_strings_accepted(self):
        result = validate_template_config({"vocabulary": ["ANAC", "VOEPASS"]})
        self.assertEqual(result["vocabulary"], ["ANAC", "VOEPASS"])

    def test_vocabulary_non_list_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"vocabulary": "ANAC"})

    def test_vocabulary_with_non_string_items_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"vocabulary": ["ANAC", 123]})


    def test_export_custom_vcodec_and_preset_accepted(self):
        result = validate_template_config({"export": {"vcodec": "h264_amf", "preset": "ultrafast", "crf": 18}})
        self.assertEqual(result["export"]["vcodec"], "h264_amf")
        self.assertEqual(result["export"]["preset"], "ultrafast")
        self.assertEqual(result["export"]["crf"], 18)

    def test_invalid_export_vcodec_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"export": {"vcodec": "invalid_codec"}})

    def test_invalid_export_preset_raises(self):
        with self.assertRaises(ValueError):
            validate_template_config({"export": {"preset": "lightning"}})


class TestRenderKwargsFromTemplate(unittest.TestCase):

    def test_default_config_maps_to_expected_kwargs(self):
        config = validate_template_config({})
        kwargs = render_kwargs_from_template(config)
        self.assertEqual(kwargs["caption_preset"], "white-bold")
        self.assertEqual(kwargs["layout"], "auto")
        self.assertEqual(kwargs["watermark_position"], "bottom-right")
        self.assertEqual(kwargs["watermark_opacity"], 0.5)
        self.assertIsNone(kwargs["watermark_path"])  # enabled + no custom path -> use default
        self.assertEqual(kwargs["vcodec"], "libx264")
        self.assertEqual(kwargs["preset"], "medium")
        self.assertEqual(kwargs["crf"], 23)
        self.assertIsNone(kwargs["title"])
        self.assertIsNone(kwargs["cta"])

    def test_disabled_watermark_forces_empty_path(self):
        config = validate_template_config({"watermark": {"enabled": False, "path": "/some/custom.png"}})
        kwargs = render_kwargs_from_template(config)
        self.assertEqual(kwargs["watermark_path"], "")

    def test_enabled_watermark_with_custom_path_is_preserved(self):
        config = validate_template_config({"watermark": {"path": "/some/custom.png"}})
        kwargs = render_kwargs_from_template(config)
        self.assertEqual(kwargs["watermark_path"], "/some/custom.png")

    def test_title_and_cta_pass_through(self):
        config = validate_template_config({"title": "Meu Canal", "cta": "Siga para mais!"})
        kwargs = render_kwargs_from_template(config)
        self.assertEqual(kwargs["title"], "Meu Canal")
        self.assertEqual(kwargs["cta"], "Siga para mais!")


if __name__ == "__main__":
    unittest.main()
