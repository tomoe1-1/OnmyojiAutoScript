"""Recognize only the supplied '确认退出战斗吗？' dialog, including its buttons."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _templates():
    path = Path(__file__).parent / 'recovery_assets' / 'exit_battle.png'
    original = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if original is None:
        raise RuntimeError('Unable to load ESC battle exit dialog template')
    templates = []
    for scale in np.arange(0.75, 2.26, 0.05):
        image = cv2.resize(original, None, fx=float(scale), fy=float(scale), interpolation=cv2.INTER_LINEAR)
        h, w = image.shape
        title_box = (int(w * 40 / 251), int(h * 40 / 161), int(w * 212 / 251), int(h * 82 / 161))
        button_box = (int(w * 14 / 251), int(h * 100 / 161), int(w * 237 / 251), int(h * 150 / 161))
        x1, y1, x2, y2 = title_box
        templates.append((image[y1:y2, x1:x2], image, title_box, button_box))
    return templates


def battle_exit_confirm_point(frame):
    if frame is None or frame.ndim != 3:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    for title, dialog, title_box, button_box in _templates():
        if title.shape[0] > gray.shape[0] or title.shape[1] > gray.shape[1]:
            continue
        result = cv2.matchTemplate(gray, title, cv2.TM_CCOEFF_NORMED)
        _, score, _, position = cv2.minMaxLoc(result)
        if score < 0.90:
            continue
        left, top = position[0] - title_box[0], position[1] - title_box[1]
        height, width = dialog.shape
        if left < 0 or top < 0 or left + width > gray.shape[1] or top + height > gray.shape[0]:
            continue
        x1, y1, x2, y2 = button_box
        candidate = gray[top+y1:top+y2, left+x1:left+x2]
        buttons = dialog[y1:y2, x1:x2]
        if cv2.matchTemplate(candidate, buttons, cv2.TM_CCOEFF_NORMED)[0, 0] >= 0.87:
            return left + round(width * 190 / 251), top + round(height * 126 / 161)
    return None
