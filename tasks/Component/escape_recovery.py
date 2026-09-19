"""Refresh page identity after ESC, without replaying a completed task."""
from module.device.escape_popup import battle_exit_confirm_point
from module.logger import logger


def handle_escape_recovery(task):
    device = task.device
    generation = getattr(device, '_escape_generation', 0)
    if (not generation or generation == getattr(task, '_seen_escape_generation', 0)
            or getattr(device, '_recovery_handling', False)):
        return
    from tasks.GameUi.game_ui import GameUi

    device._recovery_handling = True
    try:
        task.interval_timer.clear()
        task.animates.clear()
        navigator = task if isinstance(task, GameUi) else GameUi(task.config, device)
        navigator.navigator.current_page = None
        navigator.navigator.last_enter_success_page_key = None
        navigator.navigator.edge_penalties.clear()
        confirmed_generation = None
        waited_generation = None
        while True:
            # The task loop resumes only with a fresh, recognized page.
            learning = getattr(device, '_error_learning', None)
            plan = learning.plan if learning is not None else None
            delay = plan['settle'] if plan and waited_generation != device._escape_generation else 0.8
            waited_generation = device._escape_generation
            device.sleep(delay)
            device.screenshot()
            # screenshot() may have started another recovery generation.
            learning = getattr(device, '_error_learning', None)
            plan = learning.plan if learning is not None else None
            task._seen_escape_generation = device._escape_generation
            point = battle_exit_confirm_point(device.image)
            if point is not None:
                if confirmed_generation != device._escape_generation:
                    logger.info('ESC recovery: confirm exit battle dialog')
                    if device.click(*point, control_name='ESC_EXIT_BATTLE_CONFIRM') is not False:
                        confirmed_generation = device._escape_generation
                        if learning is not None:
                            learning.confirmed_exit()
                continue
            before_detection = device._escape_generation
            page = None
            if plan:
                preferred = navigator.navigator.pages.get(plan['page'])
                if preferred is not None:
                    page = navigator._detect_pages([preferred], skip_first_screenshot=True)
            if page is None:
                page = navigator._detect_current_page(skip_first_screenshot=True)
            if device._escape_generation != before_detection:
                continue
            if page is None:
                continue
            navigator.navigator.current_page = page
            if learning is not None:
                learning.page_observed(page.key)
            logger.info(f'ESC recovery: current page reidentified as {page.key}; resume target matching')
            device.stuck_record_clear()
            device.click_record_clear()
            return
    finally:
        device._recovery_handling = False
