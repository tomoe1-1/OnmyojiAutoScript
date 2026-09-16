# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from module.device.human_click import TargetBox, reset_task, sample_point


class RuleClick:
    @classmethod
    def reset_task_points(cls):
        """Start a fresh click session when the scheduler starts a task."""
        reset_task()

    def __init__(self, roi_front: tuple, roi_back: tuple, name: str = None) -> None:
        """
        初始化
        :param roi_front:
        :param roi_back:
        """
        self.roi_front = roi_front
        self.roi_back = roi_back
        if name:
            self.name = name
        else:
            self.name = 'click'

    def coord(self) -> tuple:
        """
        获取坐标，在 roi_front 内按截断高斯采样。
        :return:
        """
        return self._circle_normal_coord(self.roi_front)

    def coord_more(self) -> tuple:
        """
        在 roi_back 内按截断高斯采样。
        :return:
        """
        return self._circle_normal_coord(self.roi_back)

    def _circle_normal_coord(self, roi: tuple) -> tuple:
        """Use the original ROI unchanged and sample only inside its bounds."""
        x, y, width, height = roi
        if width <= 0 or height <= 0:
            raise ValueError(f'RuleClick roi must have positive size: {roi}')
        return sample_point(TargetBox(x, y, width, height))

    @property
    def center(self) -> tuple:
        """
        返回roi_front的中心坐标
        :return:
        """
        x, y, w, h = self.roi_front
        return x + w // 2, y + h // 2

    def move(self, x: int, y: int) -> None:
        """
        移动roi_front, 需要限幅x是0-1280, y是0-720
        :param x:
        :param y:
        :return:
        """
        x, y, w, h = self.roi_front
        x += x
        y += y
        if x <= 0:
            x = 0
        elif x >= 1280:
            x = 1280

        if y <= 0:
            y = 0
        elif y >= 720:
            y = 720

        self.roi_front = x, y, w, h

    def __repr__(self):
        return self.name
