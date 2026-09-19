"""Local, bounded recovery experience. Never stores executable actions or coordinates."""
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


def frame_signature(image):
    if image is None or not hasattr(image, 'shape') or len(image.shape) != 3:
        return None
    import cv2
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (16, 9), interpolation=cv2.INTER_AREA)
    return json.dumps([int(image.shape[1]), int(image.shape[0]), (small // 16).flatten().tolist()])


def similar(left, right):
    try:
        a, b = json.loads(left), json.loads(right)
        if a[:2] != b[:2] or len(a[2]) != 144 or len(b[2]) != 144:
            return False
        delta = [abs(x - y) for x, y in zip(a[2], b[2])]
        return sum(delta) <= 72 and sum(v > 1 for v in delta) <= 7
    except (ValueError, TypeError, IndexError):
        return False


class ExperienceStore:
    MAX_AGE = 30 * 86400

    def __init__(self, path, warn=lambda message: None):
        self.path = Path(path)
        self.warn = warn

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=0.2)
        try:
            connection.execute('''CREATE TABLE IF NOT EXISTS experience (
                id INTEGER PRIMARY KEY, context TEXT, reason TEXT, signature TEXT,
                run_id TEXT, success INTEGER, page TEXT, action TEXT, settle REAL, created REAL,
                UNIQUE(context, reason, signature, run_id))''')
            connection.execute('''CREATE TABLE IF NOT EXISTS errors (
                context TEXT, kind TEXT, occurrences INTEGER, last_seen REAL,
                PRIMARY KEY(context, kind))''')
            connection.execute('CREATE INDEX IF NOT EXISTS experience_lookup ON experience(context, reason, created)')
            return connection
        except Exception:
            connection.close()
            raise

    def record(self, context, episode, run_id, success):
        if episode.get('signature') is None:
            return
        connection = None
        try:
            connection = self._connect()
            with connection:
                connection.execute('''INSERT INTO experience
                    (context,reason,signature,run_id,success,page,action,settle,created)
                    VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(context,reason,signature,run_id)
                    DO UPDATE SET success=MIN(experience.success,excluded.success), created=excluded.created''',
                    (context, episode['reason'], episode['signature'], run_id, int(success),
                     episode.get('page', ''), episode.get('action', 'esc'),
                     max(.8, min(5., episode.get('settle', .8))), time.time()))
                connection.execute('DELETE FROM experience WHERE id NOT IN (SELECT id FROM experience ORDER BY id DESC LIMIT 5000)')
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self.warn(f'Error learning unavailable; original recovery retained: {exc}')
        finally:
            if connection is not None:
                connection.close()

    def lookup(self, context, reason, signature):
        if signature is None or not self.path.exists():
            return None
        connection = None
        try:
            connection = self._connect()
            rows = connection.execute('''SELECT signature,run_id,success,page,action,settle,created
                FROM experience WHERE context=? AND reason=? AND created>=?
                ORDER BY id DESC LIMIT 100''', (context, reason, time.time()-self.MAX_AGE)).fetchall()
            candidates, runs = [], set()
            for stored, run_id, success, page, action, settle, created in rows:
                if not similar(signature, stored):
                    continue
                # Any recent matching failure invalidates the prior streak.
                if not success:
                    return None
                if run_id in runs:
                    continue
                runs.add(run_id)
                if action not in ('esc', 'esc_confirm_exit') or not isinstance(page, str) or not page:
                    return None
                if not isinstance(settle, (float, int)) or not math.isfinite(settle):
                    return None
                candidates.append(dict(page=page, action=action, settle=max(.8, min(5., settle))))
                if len(candidates) == 2:
                    if candidates[0]['page'] != candidates[1]['page'] or candidates[0]['action'] != candidates[1]['action']:
                        return None
                    return dict(page=page, action=action, settle=max(p['settle'] for p in candidates))
            return None
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self.warn(f'Error learning read failed; original recovery retained: {exc}')
            return None
        finally:
            if connection is not None:
                connection.close()

    def record_error(self, context, error):
        connection = None
        try:
            connection = self._connect()
            with connection:
                connection.execute('''INSERT INTO errors VALUES (?,?,1,?)
                    ON CONFLICT(context,kind) DO UPDATE SET occurrences=occurrences+1,last_seen=excluded.last_seen''',
                    (context, type(error).__name__, time.time()))
                connection.execute('DELETE FROM errors WHERE rowid NOT IN (SELECT rowid FROM errors ORDER BY last_seen DESC LIMIT 1000)')
        except (OSError, sqlite3.Error) as exc:
            self.warn(f'Error learning diagnostic record failed: {exc}')
        finally:
            if connection is not None:
                connection.close()


class LearningSession:
    def __init__(self, store, context):
        self.store = store
        self.context = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)
        self.run_id = uuid.uuid4().hex
        self.current = None
        self.plan = None
        self.finished = False

    def begin(self, reason, image):
        # Another recovery before task completion is not proven success.
        if self.current is not None:
            self.store.record(self.context, self.current, self.run_id, False)
        self.current = dict(reason=reason, signature=frame_signature(image), action='esc', started=time.monotonic())
        self.plan = self.store.lookup(self.context, reason, self.current['signature'])
        return self.plan

    def confirmed_exit(self):
        if self.current is not None:
            self.current['action'] = 'esc_confirm_exit'

    def page_observed(self, page):
        if self.current is not None:
            self.current['page'] = page
            self.current['settle'] = max(.8, min(5., time.monotonic()-self.current['started']))

    def finish(self, success, error=None):
        if self.finished:
            return
        self.finished = True
        if error is not None:
            self.store.record_error(self.context, error)
        if self.current is not None:
            verified = success and bool(self.current.get('page'))
            self.store.record(self.context, self.current, self.run_id, verified)


def create_session(config, command, warn, device=None):
    settings = config.script.device
    if not getattr(settings, 'error_learning_enabled', True):
        return None
    # Scope prevents transferring behavior between accounts/tasks/backends.
    context = dict(version=1, config=getattr(config, 'config_name', ''), task=command,
                   serial=str(getattr(device, 'serial', getattr(settings, 'serial', ''))),
                   package=str(getattr(device, 'package', getattr(settings, 'package_name', ''))),
                   backend=str(getattr(settings, 'control_method', '')), low_spec=bool(getattr(settings, 'low_spec_mode', False)))
    path = Path(__file__).resolve().parents[2] / 'config' / 'error_learning.sqlite3'
    return LearningSession(ExperienceStore(path, warn), context)
