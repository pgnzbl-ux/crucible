"""注册全部 Context ORM，供 create_all / Alembic 共用。"""


def register_models() -> None:
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.lab.models import Lab  # noqa: F401
    from app.contexts.project.models import Project, SourceArtifact  # noqa: F401
    from app.contexts.report.models import Evidence, Report  # noqa: F401
    from app.contexts.settings.models import Credential, LlmProvider, PlatformSetting  # noqa: F401
    from app.contexts.task.models import AgentEvent, NodeRun, NodeRunFailure, Task, TaskRun  # noqa: F401
