"""注册全部 Context ORM，供 create_all / Alembic 共用。"""


def register_models() -> None:
    from app.contexts.discovery.models import ScanRun  # noqa: F401
    from app.contexts.finding.models import (  # noqa: F401
        Adjudication,
        AlertGroup,
        LeadNodeRun,
        LeadRun,
        RawFinding,
        ReviewAction,
    )
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.lab.models import Lab  # noqa: F401
    from app.contexts.project.models import Project, SourceArtifact  # noqa: F401
    from app.contexts.report.models import Evidence, Report  # noqa: F401
    from app.contexts.settings.models import Credential, LlmProvider, PlatformSetting  # noqa: F401
    from app.contexts.task.models import (  # noqa: F401
        AgentEvent, AgentUsage, NodeRun, NodeRunFailure, Task, TaskRun,
    )
