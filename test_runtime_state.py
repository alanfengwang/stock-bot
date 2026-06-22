from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import runtime_state


class RuntimeStateTests(unittest.TestCase):
    def test_default_load_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'runtime.json')
            with patch.object(runtime_state, 'RUNTIME_STATE_FILE', path):
                state = runtime_state.load_runtime_state()
                self.assertEqual(state['regime'], 'UNKNOWN')
                self.assertEqual(state['worker_beats'], {})

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'runtime.json')
            with patch.object(runtime_state, 'RUNTIME_STATE_FILE', path):
                runtime_state.save_runtime_state({
                    'regime': 'NEUTRAL',
                    'worker_beats': {'执行': {'ts': 1.0, 'detail': 'ok'}},
                })
                state = runtime_state.load_runtime_state()
                self.assertEqual(state['regime'], 'NEUTRAL')
                self.assertIn('执行', state['worker_beats'])


if __name__ == '__main__':
    unittest.main()
