from app.agents.base import SimulatedAgent
from app.agents.build import BuildAgent
from app.agents.code_review import CodeReviewAgent
from app.agents.cost_analyzer import CostAnalyzerAgent
from app.agents.deploy import DeployAgent
from app.agents.diagnose import DiagnoseAgent
from app.agents.incident_triage import IncidentTriageAgent
from app.agents.notify import NotifyAgent
from app.agents.postmortem import PostmortemAgent
from app.agents.remediate import RemediateAgent
from app.agents.rollback import RollbackAgent
from app.agents.security_scan import SecurityScanAgent
from app.agents.terraform_apply import TerraformApplyAgent
from app.agents.terraform_plan import TerraformPlanAgent
from app.agents.test_runner import TestRunnerAgent
from app.agents.verify import VerifyAgent

_IMPLEMENTATIONS: list[type[SimulatedAgent]] = [
    CodeReviewAgent, TestRunnerAgent, BuildAgent, SecurityScanAgent, DeployAgent,
    VerifyAgent, RollbackAgent, IncidentTriageAgent, DiagnoseAgent, RemediateAgent,
    TerraformPlanAgent, TerraformApplyAgent, CostAnalyzerAgent, PostmortemAgent, NotifyAgent,
]

AGENT_REGISTRY: dict[str, SimulatedAgent] = {cls.slug: cls() for cls in _IMPLEMENTATIONS}


def get_agent_impl(slug: str) -> SimulatedAgent | None:
    return AGENT_REGISTRY.get(slug)
