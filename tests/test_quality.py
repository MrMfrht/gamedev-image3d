import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cloud'))
from quality import resolve_quality
from worker import build_prompt

class QualityTests(unittest.TestCase):
    def test_low_poly_preview_only_changes_decimation(self):
        expected = build_prompt('trellis2', 42, 'preview', 'front.png')
        expected['186']['inputs']['target_face_count'] = 5000
        actual = build_prompt('trellis2', 42, 'preview', 'front.png', {'target_faces': 5000})
        self.assertEqual(actual, expected)
        with self.assertRaises(ValueError):
            resolve_quality('preview', {'target_faces': 4999})

    def test_bounded_controls_reject_expensive_or_unknown_inputs(self):
        for settings in [{'texture_size':8192},{'shape_steps':10000},
                         {'target_faces':True},{'custom_graph':'anything'}]:
            with self.assertRaises(ValueError): resolve_quality('detail',settings)

    def test_detail_does_not_silently_raise_geometry_resolution(self):
        graph=build_prompt('trellis2',42,'detail','input.png')
        self.assertEqual(graph['94']['inputs']['target_resolution'],1024)
        self.assertEqual(graph['288']['inputs']['value'],4096)
        self.assertEqual(graph['save']['class_type'],'SaveGLB')

    def test_independent_calls_do_not_mutate_workflow_defaults(self):
        first=build_prompt('trellis2',42,'detail','a.png',{'texture_size':2048})
        second=build_prompt('trellis2',42,'detail','b.png')
        self.assertEqual(first['288']['inputs']['value'],2048)
        self.assertEqual(second['288']['inputs']['value'],4096)
