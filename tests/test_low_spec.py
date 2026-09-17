"""Offline response-budget and background-process regressions."""
import ast
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from module.base.timer import Timer
from module.device.performance import PerformanceProfile
from module.exception import GameStuckError, GameNotRunningError, GameTooManyClickError

ROOT = Path(__file__).resolve().parents[1]


def load_method(path, cls, name, ns):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), path, 'exec'), ns)
    return ns[name]


def device(low=False):
    ns = dict(deque=deque, Timer=Timer, PerformanceProfile=PerformanceProfile,
              contextmanager=contextmanager, time=time, logger=Mock(), IS_WINDOWS=False,
              GameStuckError=GameStuckError, GameNotRunningError=GameNotRunningError,
              GameTooManyClickError=GameTooManyClickError)
    class Parent:
        def __init__(self, config):
            self.config = config
        def screenshot_interval_set(self):
            pass
    for name in ('Screenshot', 'Control', 'AppControl'):
        ns[name] = type(name, (), {})
    ns['Platform'] = Parent
    tree = ast.parse((ROOT / 'module/device/device.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Device')
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'device.py', 'exec'), ns)
    d = ns['Device'](SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
        low_spec_mode=low, screenshot_method='ADB'))))
    d.app_is_running = lambda: True
    return d


class LowSpecTests(unittest.TestCase):
    def test_profiles_default_and_low(self):
        normal, low = PerformanceProfile(), PerformanceProfile(True)
        self.assertEqual(normal.timeout(60), 60)
        self.assertEqual(low.timeout(60), 180)
        self.assertEqual(low.timeout(300), 900)
        self.assertEqual(low.timeout(15), 45)
        self.assertEqual(low.screenshot_interval(.1), .8)
        self.assertEqual(low.screenshot_interval(1, True), 1.5)
        self.assertEqual(low.screenshot_interval(3), 3)

    def test_slow_idle_still_eventually_fails(self):
        for low, budget in [(False, 60), (True, 180)]:
            d = device(low)
            with patch('time.time', return_value=1000):
                d.stuck_record_clear()
            d.stuck_timer._reach_count = 1000
            d.stuck_timer_long._reach_count = 1000
            with patch('time.time', return_value=1000 + budget - 1):
                self.assertFalse(d.stuck_record_check())
            with patch('time.time', return_value=1000 + budget + 1):
                with self.assertRaises(GameStuckError):
                    d.stuck_record_check()

    def test_device_state_is_independent(self):
        a, b = device(True), device(False)
        a.click_record_add('A')
        a.stuck_record_add('BATTLE_STATUS_S')
        self.assertEqual(list(b.click_record), [])
        self.assertEqual(b.detect_record, set())
        self.assertIsNot(a.stuck_timer, b.stuck_timer)
        self.assertIsNot(a._screenshot_interval, b._screenshot_interval)

    def test_login_marker_survives_click_and_deadline_does_not_reset(self):
        d = device(True)
        with patch('time.monotonic', return_value=100), patch('time.sleep'):
            with d.login_wait():
                deadline = d._login_deadline
                for _ in range(50):
                    d.handle_control_check('Skip animation')
                self.assertIn('LOGIN_CHECK', d.detect_record)
                self.assertEqual(d._login_deadline, deadline)
                with patch('time.monotonic', return_value=deadline):
                    with self.assertRaises(GameStuckError):
                        d.stuck_record_check()
                    with self.assertRaises(GameStuckError):
                        d.handle_control_check('Skip animation')
        self.assertIsNone(d._login_deadline)
        self.assertFalse(d.detect_record)
        self.assertFalse(d.click_record)

    def test_login_exception_cleans_up(self):
        d = device(True)
        with self.assertRaises(ValueError):
            with d.login_wait():
                raise ValueError('recognition failed')
        self.assertIsNone(d._login_deadline)
        self.assertFalse(d.detect_record)

    def test_repeat_click_protection_stays_enabled(self):
        for low, limit in [(False, 10), (True, 30)]:
            d = device(low)
            for _ in range(limit - 1):
                d.click_record_add('A')
                d.click_record_check()
            d.click_record_add('A')
            with self.assertRaises(GameTooManyClickError):
                d.click_record_check()

    def test_alternating_click_protection(self):
        d = device(True)
        for _ in range(17):
            d.click_record_add('A')
            d.click_record_add('B')
        d.click_record_check()
        d.click_record_add('A')
        d.click_record_add('B')
        with self.assertRaises(GameTooManyClickError):
            d.click_record_check()

    def test_action_pacing_only_low_spec(self):
        for low in (False, True):
            d = device(low)
            with patch('time.monotonic', return_value=100), patch('time.sleep') as sleep:
                d.handle_control_check('A')
                d.handle_control_check('B')
                if low:
                    sleep.assert_called_once_with(1.2)
                else:
                    sleep.assert_not_called()

    def test_screenshot_backend_overrides_and_config_preserved(self):
        fn = load_method('module/device/screenshot.py', 'Screenshot', 'screenshot_interval_set',
                         dict(logger=Mock(), limit_in=lambda v, lo, hi: max(lo, min(hi, v))))
        for method in ('ADB', 'scrcpy', 'nemu_ipc'):
            for low in (False, True):
                opt = SimpleNamespace(screenshot_interval=.3, combat_screenshot_interval=1.)
                d = SimpleNamespace(performance=PerformanceProfile(low), _screenshot_interval=Timer(.1),
                    config=SimpleNamespace(Emulator_ScreenshotMethod=method, script=SimpleNamespace(
                        optimization=opt, device=SimpleNamespace(screenshot_method=method))))
                fn(d)
                self.assertEqual(d._screenshot_interval.limit, .8 if low else
                                 (.1 if method == 'scrcpy' else .2 if method == 'nemu_ipc' else .3))
                fn(d, 'combat')
                self.assertEqual(d._screenshot_interval.limit, 1.5 if low else .1 if method == 'scrcpy' else 1.)
                self.assertEqual(opt.screenshot_interval, .3)
                self.assertEqual(opt.combat_screenshot_interval, 1.)

    def test_background_adb_no_console(self):
        fn = load_method('module/device/connection.py', 'Connection', 'adb_command',
                         dict(subprocess=subprocess, logger=Mock()))
        process = Mock()
        process.communicate.return_value = (b'ok', None)
        with patch.object(subprocess, 'Popen', return_value=process) as popen:
            self.assertEqual(fn(SimpleNamespace(adb_binary='adb.exe', serial='test'), ['devices']), b'ok')
            self.assertEqual(popen.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
            self.assertFalse(popen.call_args.kwargs['shell'])

    def test_wait_accepts_visible_frame_even_when_capture_was_slow(self):
        class RuleImage:
            name = 'ready'
        fn = load_method('tasks/base_task.py', 'BaseTask', 'wait_until_appear',
                         dict(Timer=Timer, RuleImage=RuleImage, RuleOcr=type('RuleOcr', (), {}), logger=Mock()))
        clock = [1000]
        def screenshot():
            clock[0] += 20
        for visible in (True, False):
            task = SimpleNamespace(device=device(True), screenshot=screenshot, appear=lambda target: visible)
            with patch('time.time', side_effect=lambda: clock[0]):
                self.assertEqual(fn(task, RuleImage(), wait_time=5), visible)

    def test_wait_budget_scales_and_caller_passes_keyword(self):
        class RuleImage:
            name = 'ready'
        made = []
        def timer(seconds):
            made.append(seconds)
            return Mock(reached=lambda: True)
        fn = load_method('tasks/base_task.py', 'BaseTask', 'wait_until_appear',
                         dict(Timer=timer, RuleImage=RuleImage, RuleOcr=type('RuleOcr', (), {}), logger=Mock()))
        task = SimpleNamespace(device=device(True), screenshot=Mock(), appear=lambda target: False)
        self.assertFalse(fn(task, RuleImage(), wait_time=5))
        self.assertEqual(made, [15])
        from typing import Union
        click = load_method('tasks/base_task.py', 'BaseTask', 'wait_until_appear_then_click',
                            dict(RuleImage=RuleImage, RuleClick=RuleImage, RuleLongClick=RuleImage, Union=Union))
        task.wait_until_appear = Mock(return_value=False)
        target = RuleImage()
        self.assertFalse(click(task, target, wait_time=7))
        task.wait_until_appear.assert_called_once_with(target, wait_time=7)

    def test_startup_probe_uses_extended_budget(self):
        d = device(True)
        d.screenshot_adb = lambda: 'black'
        d.screenshot_methods = {'ADB': d.screenshot_adb}
        clock = [1000]
        d.wait_app_start_ready.__func__.__globals__['get_color'] = lambda image, area: (0, 0, 0)
        with patch('time.monotonic', side_effect=lambda: clock[0]), patch('time.sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            d.wait_app_start_ready()
        self.assertEqual(clock[0], 1045)

    def test_background_git_no_console(self):
        fn = load_method('module/server/updater.py', 'Updater', 'execute_command',
                         dict(subprocess=subprocess, logger=Mock(), DEFAULT_GIT_TIMEOUT=5))
        with patch.object(subprocess, 'run') as run:
            fn(None, 'git status')
            self.assertEqual(run.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
            self.assertTrue(run.call_args.kwargs['capture_output'])


if __name__ == '__main__':
    unittest.main()
