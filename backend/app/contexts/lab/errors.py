class LabBusyError(Exception):
    """Lab 正被 live 任务占用。"""

    def __init__(self, task_ids: list[str]) -> None:
        self.task_ids = task_ids
        super().__init__("靶场正被运行中的任务占用")


class LabNotFoundError(Exception):
    """Lab 不存在或不属于当前用户。"""
