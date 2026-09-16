"""Dismiss the Duel talisman gift without clicking through its modal."""
from module.atom.image import RuleImage
from module.exception import GameStuckError
from module.logger import logger


GIFT_ACCEPT = RuleImage(
    roi_front=(725, 496, 220, 51), roi_back=(695, 471, 280, 101),
    threshold=0.90, method='Template matching',
    file='./tasks/Component/gift_popup/gift_accept.png',
)
GIFT_TALISMAN = RuleImage(
    roi_front=(810, 216, 150, 48), roi_back=(780, 191, 210, 98),
    threshold=0.90, method='Template matching',
    file='./tasks/Component/gift_popup/gift_talisman.png',
)


def _gift_visible(task):
    # Require both the labelled button and the specific reward panel.  A lone
    # generic gold button must never authorize a click on another screen.
    if not task.appear(GIFT_ACCEPT) or not task.appear(GIFT_TALISMAN):
        return False
    bx, by = GIFT_ACCEPT.roi_front[:2]
    tx, ty = GIFT_TALISMAN.roi_front[:2]
    return abs((tx - bx) - 85) <= 12 and abs((ty - by) + 280) <= 12


def claim_gift_popup(task):
    """Claim once, refresh the frame, and retry only while the modal is visible.

    Called by BaseTask.screenshot so it also handles a late popup during
    matchmaking.  Raw device screenshots avoid recursive popup handling.
    """
    handled = False
    for _ in range(3):
        if not _gift_visible(task):
            return handled
        logger.info('Talisman gift popup detected: click 开心收下')
        task.click(GIFT_ACCEPT)
        handled = True
        task.device.sleep(0.6)
        task.device.screenshot()
    if _gift_visible(task):
        raise GameStuckError('Talisman gift popup remained after 3 claim attempts')
    return handled
