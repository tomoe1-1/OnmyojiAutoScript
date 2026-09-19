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
        while True:
            # The task loop resumes only with a fresh, recognized page.
            device.sleep(0.8)
            device.screenshot()
            task._seen_escape_generation = device._escape_generation
            point = battle_exit_confirm_point(device.image)
            if point is not None:
                if confirmed_generation != device._escape_generation:
                    logger.info('ESC recovery: confirm exit battle dialog')
                    device.click(*point, control_name='ESC_EXIT_BATTLE_CONFIRM')
                    confirmed_generation = device._escape_generation
                continue
            before_detection = device._escape_generation
            page = navigator._detect_current_page(skip_first_screenshot=True)
            if device._escape_generation != before_detection:
                continue
            if page is None:
                continue
            navigator.navigator.current_page = page
            logger.info(f'ESC recovery: current page reidentified as {page.key}; resume target matching')
            device.stuck_record_clear()
            device.click_record_clear()
            return
    finally:
        device._recovery_handling = False
