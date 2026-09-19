import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from test_low_spec import device, load_method, ROOT
from test_human_click import control
from module.device.escape_popup import battle_exit_confirm_point
from module.exception import TaskRecoveryFailed, GameNotRunningError, TaskEnd


class EscapeRecoveryTests(unittest.TestCase):
    def test_exactly_three_esc_then_fail_without_fourth(self):
        d = device()
        for attempt in range(3):
            d.recover_by_escape('missing target')
            self.assertEqual(d._escape_attempts, attempt + 1)
            d.stuck_record_clear()
            d.click_record_clear()
        with self.assertRaises(TaskRecoveryFailed):
            d.recover_by_escape('still missing')
        self.assertEqual(d.press_escape.call_count, 3)
        d.reset_task_recovery()
        self.assertEqual(d._escape_attempts, 0)

    def test_repeat_clicks_pause_until_timeout_and_dont_dispatch(self):
        for low, budget in ((False, 60), (True, 180)):
            d = device(low)
            with patch('time.monotonic', return_value=100), patch('time.sleep'):
                for _ in range(9):
                    self.assertTrue(d.handle_control_check('target'))
                self.assertFalse(d.handle_control_check('target'))
                self.assertFalse(d.handle_control_check('target'))
            with patch('time.monotonic', return_value=100 + budget - .1):
                d.stuck_record_check()
                d.press_escape.assert_not_called()
            with patch('time.monotonic', return_value=100 + budget):
                d.stuck_record_check()
                d.press_escape.assert_called_once()
                self.assertFalse(d.handle_control_check('old frame target'))

    def test_different_target_cancels_pending_repeat_timeout(self):
        d = device()
        with patch('time.monotonic', return_value=100):
            for _ in range(10):
                d.handle_control_check('old')
            self.assertTrue(d.handle_control_check('new'))
        self.assertIsNone(d._click_recovery_at)
        d.press_escape.assert_not_called()

    def test_stopped_game_and_failed_esc_do_not_loop(self):
        d = device()
        d.app_is_running = lambda: False
        with self.assertRaises(GameNotRunningError):
            d.recover_by_escape('missing')
        d.press_escape.assert_not_called()
        d.app_is_running = lambda: True
        d.press_escape.side_effect = RuntimeError('bad handle')
        with self.assertRaises(TaskRecoveryFailed):
            d.recover_by_escape('missing')

    def test_control_does_not_send_blocked_click_or_swipe(self):
        d = control('ADB')
        d.handle_control_check.return_value = False
        d.swipe_adb = Mock()
        self.assertFalse(d.click(10, 20))
        self.assertFalse(d.long_click(10, 20))
        self.assertFalse(d.swipe((10, 20), (200, 220)))
        d.long_click_adb.assert_not_called()
        d.swipe_adb.assert_not_called()

    def test_escape_backend_routes_to_device(self):
        for backend in ('window_message', 'minitouch', 'adb', 'uiautomator2', 'scrcpy'):
            with self.subTest(backend=backend):
                d = control(backend)
                d.press_escape_window_message = Mock()
                d.adb_shell = Mock(return_value='')
                d.press_escape()
                d.press_escape_window_message.assert_not_called()
                d.adb_shell.assert_called_once_with(['input', 'keyevent', 'KEYCODE_BACK'])

    def test_escape_input_error_is_not_silently_accepted_or_retried(self):
        d = control('minitouch')
        for failure in ('Error: Invalid keycode', RuntimeError('device offline')):
            d.adb_shell = Mock(return_value=failure if isinstance(failure, str) else '')
            if isinstance(failure, Exception):
                d.adb_shell.side_effect = failure
            with self.assertRaises(RuntimeError):
                d.press_escape()
            self.assertEqual(d.adb_shell.call_count, 1)

    def test_timeout_reaches_real_back_transport_and_stops_after_three(self):
        d = device()
        transport = control('minitouch')
        transport.adb_shell = Mock(return_value='')
        d.press_escape = transport.press_escape
        d.stuck_timer.reached = Mock(return_value=True)
        d.stuck_timer_long.reached = Mock(return_value=True)
        for _ in range(3):
            d.stuck_record_check()
        with self.assertRaises(TaskRecoveryFailed):
            d.stuck_record_check()
        self.assertEqual(transport.adb_shell.call_count, 3)
        for call in transport.adb_shell.call_args_list:
            self.assertEqual(call.args, (['input', 'keyevent', 'KEYCODE_BACK'],))

    def test_windows_escape_targets_only_configured_window(self):
        post = Mock()
        fn = load_method('module/device/method/windows_impl.py', 'Window', 'press_escape_window_message',
                         dict(IsWindow=lambda hwnd: hwnd == 123, PostMessage=post))
        fn(SimpleNamespace(root_node=SimpleNamespace(num=123)))
        self.assertEqual(post.call_count, 2)
        self.assertEqual([c.args[:3] for c in post.call_args_list], [(123, 256, 27), (123, 257, 27)])

    def test_navigation_drops_pre_escape_action(self):
        fn = load_method('tasks/GameUi/navigator.py', 'GameUi', '_execute_action', {})
        d = SimpleNamespace(_escape_generation=0)
        task = SimpleNamespace(device=d, maybe_screenshot=lambda skip: setattr(d, '_escape_generation', 1))
        self.assertFalse(fn(task, object(), skip_first_screenshot=False))

    def test_task_failure_does_not_restart_or_mark_success(self):
        fn = load_method('script.py', 'Script', '_handle_task_exception',
                         dict(TaskRecoveryFailed=TaskRecoveryFailed, logger=Mock()))
        script = SimpleNamespace(save_error_log=Mock(), config=Mock(), _set_task_runtime_outcome=Mock())
        self.assertFalse(fn(script, TaskRecoveryFailed('3 ESC'), 'Orochi'))
        script.config.task_call.assert_not_called()
        script.config.task_delay.assert_called_once_with(task='Orochi', success=False)

    def test_dialog_match_requires_title_and_both_buttons(self):
        asset = ROOT / 'module/device/recovery_assets/exit_battle.png'
        original = cv2.imdecode(np.frombuffer(asset.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        original = cv2.cvtColor(original, cv2.COLOR_BGR2RGB)
        for scale in (1., 1.5, 2.):
            dialog = cv2.resize(original, None, fx=scale, fy=scale)
            h, w = dialog.shape[:2]
            frame = np.zeros((720, 1280, 3), np.uint8)
            frame[200:200+h, 400:400+w] = dialog
            point = battle_exit_confirm_point(frame)
            self.assertIsNotNone(point, scale)
            self.assertLess(abs(point[0] - (400 + 190*scale)), 4)
            self.assertLess(abs(point[1] - (200 + 126*scale)), 4)
        self.assertIsNone(battle_exit_confirm_point(np.zeros((720, 1280, 3), np.uint8)))
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[200:361, 400:651] = original
        frame[240:282, 440:612] = 180
        self.assertIsNone(battle_exit_confirm_point(frame))
        frame[200:361, 400:651] = original
        frame[300:350, 414:637] = 180
        self.assertIsNone(battle_exit_confirm_point(frame))

    def test_popup_confirm_precedes_page_matching_and_no_task_restart(self):
        log = []
        d = device()
        d._escape_generation = 1
        d.sleep = Mock()
        d.image = object()
        d.screenshot = Mock(side_effect=lambda: log.append('screenshot'))
        d.click = Mock(side_effect=lambda *a, **k: log.append('confirm'))
        class FakeGameUi:
            pass
        task = FakeGameUi()
        task.device = d
        task.interval_timer = {'old': 1}
        task.animates = {'old': 1}
        task.navigator = SimpleNamespace(current_page='old', last_enter_success_page_key='old', edge_penalties={'old': 1})
        task._detect_current_page = Mock(side_effect=lambda **kw: (log.append('match'), SimpleNamespace(key='main'))[1])
        fake_module = ModuleType('tasks.GameUi.game_ui')
        fake_module.GameUi = FakeGameUi
        fake_logger = ModuleType('module.logger')
        fake_logger.logger = Mock()
        with patch.dict(sys.modules, {'module.logger': fake_logger, 'tasks.GameUi.game_ui': fake_module}):
            spec = importlib.util.spec_from_file_location('recovery_subject', ROOT / 'tasks/Component/escape_recovery.py')
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with patch.object(mod, 'battle_exit_confirm_point', side_effect=[(190, 126), None]):
                mod.handle_escape_recovery(task)
            self.assertEqual(log, ['screenshot', 'confirm', 'screenshot', 'match'])
            self.assertEqual(task.navigator.current_page.key, 'main')
            self.assertFalse(task.interval_timer)
            self.assertFalse(task.animates)
            self.assertFalse(d._recovery_handling)
            mod.handle_escape_recovery(task)
            self.assertEqual(d.click.call_count, 1)

            # A later ESC without this modal must only refresh page detection.
            d._escape_generation = 2
            d.click.reset_mock()
            with patch.object(mod, 'battle_exit_confirm_point', return_value=None):
                mod.handle_escape_recovery(task)
            d.click.assert_not_called()
            self.assertEqual(task.navigator.current_page.key, 'main')

            # Failure while refreshing must release the reentrancy guard.
            d._escape_generation = 3
            d.screenshot.side_effect = TaskRecoveryFailed('exhausted')
            with self.assertRaises(TaskRecoveryFailed):
                mod.handle_escape_recovery(task)
            self.assertFalse(d._recovery_handling)


if __name__ == '__main__':
    unittest.main()
