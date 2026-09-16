"""Standard-library click sampling and timing helpers.

These helpers deliberately model only a tap.  They do not emit touch moves:
on ADB-style backends a move requires a pressed contact and is therefore a
swipe, which is intentionally kept outside this module.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from contextvars import ContextVar


_last_sample = ContextVar('human_click_sample', default=None)
_task_generation = ContextVar('human_click_task', default=0)


def reset_task():
    _last_sample.set(None)
    _task_generation.set(_task_generation.get() + 1)


def task_generation():
    return _task_generation.get()


def sampled_width(point):
    """Consume ROI metadata once; direct coordinate clicks keep their exact point."""
    sample = _last_sample.get()
    _last_sample.set(None)
    return sample[1] if sample is not None and sample[0] == tuple(point) else 1.0


@dataclass(frozen=True)
class TargetBox:
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.x, self.y, self.w, self.h)) or self.w <= 0 or self.h <= 0:
            raise ValueError('TargetBox must have finite coordinates and positive size')

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def min_side(self) -> float:
        return max(1.0, min(self.w, self.h))


def sample_point(box: TargetBox, sigma_frac: float = 0.28,
                 rng: random.Random | None = None) -> tuple[int, int]:
    """Draw a truncated Gaussian point without ever leaving *box*."""
    rng = rng or random
    if not math.isfinite(sigma_frac) or sigma_frac <= 0:
        raise ValueError('sigma_frac must be finite and positive')
    left, right = math.ceil(box.x), math.ceil(box.x + box.w) - 1
    top, bottom = math.ceil(box.y), math.ceil(box.y + box.h) - 1
    if left > right or top > bottom:
        raise ValueError(f'TargetBox contains no integer pixel: {box}')
    sx = max(1.0, box.w * sigma_frac)
    sy = max(1.0, box.h * sigma_frac)
    for _ in range(96):
        x, y = round(rng.gauss(box.cx, sx)), round(rng.gauss(box.cy, sy))
        if left <= x <= right and top <= y <= bottom:
            _last_sample.set(((x, y), box.min_side))
            return x, y
    point = min(right, max(left, round(box.cx))), min(bottom, max(top, round(box.cy)))
    _last_sample.set((point, box.min_side))
    return point


def min_jerk(t: float) -> float:
    return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5


def bezier_path(p0: tuple[float, float], p1: tuple[float, float], n: int = 28,
                bow: float = 0.18, rng: random.Random | None = None) -> list[tuple[float, float]]:
    """Return a minimum-jerk cubic-Bezier path for backends that support hover."""
    rng = rng or random
    if n < 1:
        raise ValueError('n must be positive')
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / distance, dx / distance
    amplitude = distance * bow * rng.uniform(0.5, 1.5) * rng.choice((-1.0, 1.0))
    c1 = (x0 + dx * .30 + nx * amplitude, y0 + dy * .30 + ny * amplitude)
    c2 = (x0 + dx * .72 + nx * amplitude * .6, y0 + dy * .72 + ny * amplitude * .6)
    points = []
    for index in range(n + 1):
        s = min_jerk(index / n)
        u = 1 - s
        points.append((u ** 3 * x0 + 3 * u ** 2 * s * c1[0] + 3 * u * s ** 2 * c2[0] + s ** 3 * x1,
                       u ** 3 * y0 + 3 * u ** 2 * s * c1[1] + 3 * u * s ** 2 * c2[1] + s ** 3 * y1))
        if 0 < index < n:
            px, py = points[-1]
            points[-1] = px + rng.gauss(0, .6), py + rng.gauss(0, .6)
    return points


def fitts_time(distance: float, width: float, a: float = .09, b: float = .14) -> float:
    return a + b * math.log2(1 + 2 * distance / max(1.0, width))


def plan_move(start: tuple[float, float], target: tuple[float, float], width: float,
              rng: random.Random | None = None) -> list[tuple[tuple[float, float], float]]:
    """Plan, but do not inject, a hover trajectory (tap backends cannot hover)."""
    rng = rng or random
    distance = math.dist(start, target)
    if distance < 3:
        return [(target, rng.uniform(.01, .03))]
    total = fitts_time(distance, width)
    overshoot_ratio = rng.uniform(.03, .09)
    overshoot = tuple(start[i] + (target[i] - start[i]) * (1 + overshoot_ratio) for i in range(2))
    main = bezier_path(start, overshoot, n=26, rng=rng)
    correction = bezier_path(overshoot, target, n=rng.randint(2, 3), bow=.05, rng=rng)[1:]
    return ([(point, total * .82 / len(main)) for point in main] +
            [(point, total * .18 / len(correction)) for point in correction])


def sample_interval(median: float = .50, sigma: float = .35, floor: float = .10,
                    ceil: float = 6.0, rng: random.Random | None = None) -> float:
    rng = rng or random
    return min(ceil, max(floor, rng.lognormvariate(math.log(median), sigma)))


def sample_dwell(median: float = .075, sigma: float = .30,
                 rng: random.Random | None = None) -> float:
    rng = rng or random
    return max(.025, rng.lognormvariate(math.log(median), sigma))


@dataclass
class Session:
    """A session-level cadence that does not replace task-defined click intervals."""
    rounds_per_burst: tuple[int, int] = (18, 45)
    micro_pause: tuple[float, float] = (1.2, 4.0)
    long_rest: tuple[float, float] = (12.0, 90.0)
    fatigue_gain: float = .35
    rng: random.Random = field(default_factory=random.Random)
    _left: int = 0
    _count: int = 0
    _last_click: float | None = None

    def before_click(self) -> None:
        """Wait only for an additional sampled gap; caller-defined waits remain intact."""
        now = time.monotonic()
        median = .50 * (1 + self.fatigue_gain * min(self._count, 200) / 200)
        gap = sample_interval(median, rng=self.rng)
        if self._last_click is not None:
            time.sleep(max(0.0, gap - (now - self._last_click)))

    def after_click(self) -> float | None:
        """Record a successful click and return a rest for the controller to log."""
        self._count += 1
        self._last_click = time.monotonic()
        if not self._left:
            self._left = self.rng.randint(*self.rounds_per_burst)
        self._left -= 1
        if self._left <= 0:
            self._left = self.rng.randint(*self.rounds_per_burst)
            pause = self.rng.uniform(*(self.long_rest if self.rng.random() < .25 else self.micro_pause))
            return pause
        return None
