from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.device.error_learning import ExperienceStore, LearningSession, frame_signature, create_session
from module.exception import TaskEnd
from test_low_spec import load_method, ROOT, device


class ErrorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'learning.sqlite3'
        self.store = ExperienceStore(self.path)
        self.image = np.full((720, 1280, 3), 96, np.uint8)

    def record_run(self, success=True, page='main', context=None, confirmed=False):
        session = LearningSession(self.store, context or {'task': 'Orochi'})
        session.begin('wait:target', self.image)
        if confirmed:
            session.confirmed_exit()
        if page:
            session.page_observed(page)
        session.finish(success)
        return session

    def test_requires_two_distinct_completed_successes(self):
        first = self.record_run()
        first.finish(True)
        candidate = LearningSession(self.store, {'task': 'Orochi'})
        self.assertIsNone(candidate.begin('wait:target', self.image))
        self.record_run()
        plan = LearningSession(self.store, {'task': 'Orochi'}).begin('wait:target', self.image)
        self.assertEqual(plan['page'], 'main')
        self.assertEqual(plan['action'], 'esc')
        self.assertNotIn('coordinates', plan)

    def test_task_failure_or_missing_page_never_promotes(self):
        for success, page in ((False, 'main'), (True, None)):
            self.record_run(success, page)
            self.record_run(success, page)
            s = LearningSession(self.store, {'task': 'Orochi'})
            self.assertIsNone(s.begin('wait:target', self.image))

    def test_later_failure_disables_previous_success(self):
        self.record_run()
        self.record_run()
        failed = self.record_run(False)
        self.assertIsNotNone(failed.plan)
        s = LearningSession(self.store, {'task': 'Orochi'})
        self.assertIsNone(s.begin('wait:target', self.image))
        self.record_run()
        self.record_run()
        self.assertIsNotNone(LearningSession(self.store, {'task': 'Orochi'}).begin('wait:target', self.image))

    def test_context_error_and_frame_must_match(self):
        self.record_run(context={'task': 'Orochi', 'serial': 'A'})
        self.record_run(context={'task': 'Orochi', 'serial': 'A'})
        for ctx in ({'task': 'Other', 'serial': 'A'}, {'task': 'Orochi', 'serial': 'B'}):
            self.assertIsNone(LearningSession(self.store, ctx).begin('wait:target', self.image))
        s = LearningSession(self.store, {'task': 'Orochi', 'serial': 'A'})
        self.assertIsNone(s.begin('different error', self.image))
        self.assertIsNone(LearningSession(self.store, {'task': 'Orochi', 'serial': 'A'}).begin('wait:target', self.image + 96))

    def test_conflicting_page_outcomes_not_reused(self):
        self.record_run(page='main')
        self.record_run(page='battle')
        self.assertIsNone(LearningSession(self.store, {'task': 'Orochi'}).begin('wait:target', self.image))

    def test_expired_experience_and_invalid_actions_not_reused(self):
        self.record_run()
        self.record_run()
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE experience SET created=?', (time.time()-31*86400,))
        s = LearningSession(self.store, {'task': 'Orochi'})
        self.assertIsNone(s.begin('wait:target', self.image))
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE experience SET created=?,action=?', (time.time(), 'run arbitrary code'))
        self.assertIsNone(s.store.lookup(s.context, 'wait:target', frame_signature(self.image)))

    def test_second_failure_same_run_cannot_become_success(self):
        s = LearningSession(self.store, {'task': 'Orochi'})
        s.begin('wait:target', self.image)
        s.page_observed('main')
        s.begin('wait:target', self.image)
        s.page_observed('main')
        s.finish(True)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT success FROM experience').fetchall(), [(0,)])

    def test_corrupt_or_locked_database_is_nonfatal(self):
        warnings = []
        store = ExperienceStore(self.path, warnings.append)
        self.path.write_bytes(b'not sqlite')
        s = LearningSession(store, {'task': 'Orochi'})
        self.assertIsNone(s.begin('wait', self.image))
        s.finish(False, RuntimeError('failed'))
        self.assertTrue(warnings)
        self.path.unlink()
        self.record_run()
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN EXCLUSIVE')
            store.record_error('{}', RuntimeError('busy'))
            c.rollback()

    def test_disabled_learning_never_creates_database(self):
        cfg = types.SimpleNamespace(script=types.SimpleNamespace(device=types.SimpleNamespace(error_learning_enabled=False)))
        self.assertIsNone(create_session(cfg, 'Orochi', Mock()))
        self.assertFalse(self.path.exists())

    def test_error_diagnostics_separate_from_successful_solutions(self):
        s = LearningSession(self.store, {'task': 'Orochi'})
        s.finish(False, ValueError('private text not stored'))
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT kind,occurrences FROM errors').fetchall(), [('ValueError', 1)])
            self.assertEqual(c.execute('SELECT count(*) FROM experience').fetchone()[0], 0)

    def test_failure_flag_cannot_be_overwritten_by_success(self):
        fn = load_method('module/config/config.py', 'Config', 'task_delay', dict(
            datetime=datetime, timedelta=timedelta, logger=Mock(), convert_to_underscore=lambda s: s.lower(),
            dict_to_kv=lambda *a, **k: '', ScriptError=RuntimeError))
        scheduler = types.SimpleNamespace(success_interval=timedelta(hours=1), failure_interval=timedelta(hours=1))
        cfg = types.SimpleNamespace(reload=Mock(), model=types.SimpleNamespace(orochi=types.SimpleNamespace(scheduler=scheduler)),
                                    _learning_task_outcomes={}, lock_config=Mock(), save=Mock())
        fn(cfg, 'Orochi', success=False, server=False)
        fn(cfg, 'Orochi', success=True, server=False)
        self.assertIs(cfg._learning_task_outcomes['orochi'], False)

    def test_learned_hint_is_verified_then_falls_back(self):
        d = device()
        d._escape_generation = 1
        d.sleep = Mock()
        d.screenshot = Mock()
        d.image = self.image
        d._error_learning = types.SimpleNamespace(plan=dict(page='main', action='esc', settle=3.), page_observed=Mock())
        class FakeGameUi:
            pass
        task = FakeGameUi()
        task.device = d
        task.interval_timer = {}
        task.animates = {}
        preferred = object()
        task.navigator = types.SimpleNamespace(current_page=None, last_enter_success_page_key=None, edge_penalties={}, pages={'main': preferred})
        task._detect_pages = Mock(return_value=None)
        task._detect_current_page = Mock(return_value=types.SimpleNamespace(key='other'))
        fake = types.ModuleType('tasks.GameUi.game_ui')
        fake.GameUi = FakeGameUi
        log = types.ModuleType('module.logger')
        log.logger = Mock()
        with patch.dict(sys.modules, {'tasks.GameUi.game_ui': fake, 'module.logger': log}):
            spec = importlib.util.spec_from_file_location('learning_recovery_subject', ROOT / 'tasks/Component/escape_recovery.py')
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with patch.object(mod, 'battle_exit_confirm_point', return_value=None):
                mod.handle_escape_recovery(task)
        d.sleep.assert_called_once_with(3.)
        task._detect_pages.assert_called_once_with([preferred], skip_first_screenshot=True)
        task._detect_current_page.assert_called_once()
        self.assertEqual(task.navigator.current_page.key, 'other')

    def test_learning_does_not_raise_escape_limit(self):
        d = device()
        d._error_learning = LearningSession(self.store, {'task': 'Orochi'})
        d.image = self.image
        from module.exception import TaskRecoveryFailed
        for _ in range(3):
            d.recover_by_escape('same error')
        with self.assertRaises(TaskRecoveryFailed):
            d.recover_by_escape('same error')
        self.assertEqual(d.press_escape.call_count, 3)

    def test_script_end_requires_explicit_success_and_not_delay(self):
        fake_click = types.ModuleType('module.atom.click')
        fake_click.RuleClick = types.SimpleNamespace(reset_task_points=Mock())
        for declared, status, expected in ((True, None, True), (False, None, False),
                                            (None, None, False), (True, 'server_update_delayed', False),
                                            (None, 'recovered', True)):
            with self.subTest(declared=declared, status=status):
                session = Mock()
                cfg = types.SimpleNamespace(global_game=types.SimpleNamespace(ocr=types.SimpleNamespace(save_ocr_log=False)))
                def execute():
                    if declared is not None:
                        cfg._learning_task_outcomes['orochi'] = declared
                    raise TaskEnd()
                task_module = types.SimpleNamespace(ScriptTask=lambda **kw: types.SimpleNamespace(run=execute))
                fn = load_method('script.py', 'Script', 'run', dict(
                    TaskEnd=TaskEnd, Path=Path, logger=Mock(), set_ocr_logging_enabled=Mock(),
                    load_module=lambda *args: task_module, convert_to_underscore=lambda s: s.lower()))
                script = types.SimpleNamespace(config=cfg, device=types.SimpleNamespace(reset_task_recovery=Mock(), screenshot=Mock()),
                    _reset_task_runtime_outcome=Mock(), _handle_task_exception=lambda e, c: True,
                    last_task_runtime_outcome={'status': status}, _finish_error_learning=session.finish)
                with patch.dict(sys.modules, {'module.atom.click': fake_click}), patch('module.device.error_learning.create_session', return_value=session):
                    self.assertTrue(fn(script, 'Orochi'))
                session.finish.assert_called_once_with(expected)

    def test_small_render_variation_matches_but_geometry_does_not(self):
        self.record_run()
        self.record_run()
        slight = self.image.copy()
        slight[0:50, 0:50] += 16
        self.assertIsNotNone(LearningSession(self.store, {'task': 'Orochi'}).begin('wait:target', slight))
        resized = np.full((360, 640, 3), 96, np.uint8)
        self.assertIsNone(LearningSession(self.store, {'task': 'Orochi'}).begin('wait:target', resized))

    def test_cli_export_and_explicit_clear(self):
        self.record_run()
        tool = ROOT / 'dev_tools/error_learning.py'
        result = subprocess.run([sys.executable, str(tool), '--db', str(self.path), 'clear'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        output = Path(self.temp.name) / 'report.json'
        subprocess.run([sys.executable, str(tool), '--db', str(self.path), 'export', '--output', str(output)], check=True, capture_output=True)
        self.assertEqual(len(json.loads(output.read_text(encoding='utf-8'))['experiences']), 1)
        subprocess.run([sys.executable, str(tool), '--db', str(self.path), 'clear', '--all'], check=True, capture_output=True)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM experience').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
