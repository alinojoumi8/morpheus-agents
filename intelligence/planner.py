"""
Multi-step planner with backtracking and dependency tracking.

Decomposes complex goals into sub-steps, executes them in order,
and backtracks to try alternative approaches on failure.
"""

import json
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BACKTRACKED = "backtracked"


PLANNING_PROMPT = """\
You are a task planning system. Decompose the following goal into concrete, executable steps.

Return JSON:
{
  "steps": [
    {
      "id": 1,
      "description": "what to do",
      "depends_on": [],
      "tools_needed": ["tool1", "tool2"],
      "estimated_complexity": "low|medium|high",
      "alternative_approach": "what to try if this step fails"
    }
  ],
  "parallel_groups": [[1, 2], [3]],
  "estimated_total_steps": 5,
  "risk_factors": ["potential issues"]
}

Rules:
- Steps should be concrete and actionable
- Dependencies must reference step IDs
- Parallel groups contain step IDs that can run simultaneously
- Keep steps focused (one action each)
- Respond with ONLY valid JSON

Goal: """


class Plan:
    """Represents a multi-step plan with execution tracking."""

    def __init__(self, goal: str, steps: List[Dict[str, Any]], plan_id: Optional[int] = None):
        self.goal = goal
        self.steps = steps
        self.plan_id = plan_id
        self.backtrack_count = 0
        self.status = "active"
        self._start_time = time.time()

    @classmethod
    def from_llm_response(cls, goal: str, response_data: Dict[str, Any], plan_id: Optional[int] = None) -> "Plan":
        """Create Plan from LLM planning response."""
        steps = []
        for step_data in response_data.get("steps", []):
            steps.append({
                "id": step_data.get("id", len(steps) + 1),
                "description": step_data.get("description", ""),
                "depends_on": step_data.get("depends_on", []),
                "tools_needed": step_data.get("tools_needed", []),
                "estimated_complexity": step_data.get("estimated_complexity", "medium"),
                "alternative_approach": step_data.get("alternative_approach", ""),
                "status": StepStatus.PENDING,
                "result": None,
                "attempts": 0,
                "error": None,
            })
        plan = cls(goal=goal, steps=steps, plan_id=plan_id)
        plan._parallel_groups = response_data.get("parallel_groups", [])
        plan._risk_factors = response_data.get("risk_factors", [])
        return plan

    def get_next_steps(self) -> List[Dict]:
        """Get steps ready to execute (dependencies met)."""
        completed_ids = {
            s["id"] for s in self.steps
            if s["status"] in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        }

        ready = []
        for step in self.steps:
            if step["status"] != StepStatus.PENDING:
                continue
            deps = set(step.get("depends_on", []))
            if deps.issubset(completed_ids):
                ready.append(step)

        return ready

    def mark_step_completed(self, step_id: int, result: str = ""):
        """Mark a step as completed."""
        for step in self.steps:
            if step["id"] == step_id:
                step["status"] = StepStatus.COMPLETED
                step["result"] = result
                break

    def mark_step_failed(self, step_id: int, error: str = ""):
        """Mark a step as failed."""
        for step in self.steps:
            if step["id"] == step_id:
                step["status"] = StepStatus.FAILED
                step["error"] = error
                step["attempts"] = step.get("attempts", 0) + 1
                break

    def backtrack_to(self, step_id: int) -> bool:
        """Backtrack: reset this step and all dependent steps to pending.

        Returns True if backtrack was possible.
        """
        target = None
        for step in self.steps:
            if step["id"] == step_id:
                target = step
                break

        if not target:
            return False

        # Check if alternative exists
        if not target.get("alternative_approach"):
            return False

        # Reset target to use alternative
        target["status"] = StepStatus.PENDING
        target["description"] = target["alternative_approach"]
        target["alternative_approach"] = ""  # Only one retry
        target["error"] = None

        # Reset all steps that depend on this one (transitively)
        dependent_ids = self._get_transitive_dependents(step_id)
        for step in self.steps:
            if step["id"] in dependent_ids:
                step["status"] = StepStatus.PENDING
                step["result"] = None
                step["error"] = None

        self.backtrack_count += 1
        return True

    def _get_transitive_dependents(self, step_id: int) -> set:
        """Get all steps that transitively depend on a given step."""
        dependents = set()
        queue = [step_id]
        while queue:
            current = queue.pop(0)
            for step in self.steps:
                if current in step.get("depends_on", []) and step["id"] not in dependents:
                    dependents.add(step["id"])
                    queue.append(step["id"])
        return dependents

    @property
    def is_complete(self) -> bool:
        """Check if all steps are completed or skipped."""
        return all(
            s["status"] in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in self.steps
        )

    @property
    def is_stuck(self) -> bool:
        """Check if plan is stuck (failed steps with no alternatives)."""
        for step in self.steps:
            if step["status"] == StepStatus.FAILED:
                if not step.get("alternative_approach") and step.get("attempts", 0) >= 2:
                    return True
        return False

    @property
    def progress(self) -> float:
        """Completion percentage."""
        if not self.steps:
            return 1.0
        done = sum(
            1 for s in self.steps
            if s["status"] in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return done / len(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize plan to dict."""
        return {
            "goal": self.goal,
            "steps": self.steps,
            "status": self.status,
            "backtrack_count": self.backtrack_count,
            "progress": round(self.progress, 2),
        }

    def format_status(self) -> str:
        """Format plan status for display."""
        lines = [
            f"Plan: {self.goal}",
            f"Progress: {self.progress:.0%} ({self.backtrack_count} backtracks)",
            "",
        ]
        for step in self.steps:
            icon = {
                StepStatus.PENDING: "[ ]",
                StepStatus.IN_PROGRESS: "[>]",
                StepStatus.COMPLETED: "[+]",
                StepStatus.FAILED: "[X]",
                StepStatus.SKIPPED: "[-]",
                StepStatus.BACKTRACKED: "[<]",
            }.get(step["status"], "[?]")

            line = f"  {icon} Step {step['id']}: {step['description']}"
            if step.get("error"):
                line += f"\n      Error: {step['error'][:100]}"
            if step.get("result"):
                line += f"\n      Result: {step['result'][:100]}"
            lines.append(line)

        return "\n".join(lines)


async def create_plan_async(
    goal: str,
    async_llm_call,
    intelligence_db=None,
    session_id: Optional[str] = None,
) -> Optional[Plan]:
    """Create a plan by decomposing a goal via LLM.

    Args:
        goal: The goal to decompose
        async_llm_call: Async callable(system, user) -> str
        intelligence_db: Optional for persistence
        session_id: Current session

    Returns:
        Plan object, or None on failure
    """
    try:
        response = await async_llm_call(
            "You are a task planning and decomposition system.",
            PLANNING_PROMPT + goal,
        )

        data = _parse_json(response)
        if not data or not data.get("steps"):
            return None

        plan_id = None
        if intelligence_db:
            plan_id = intelligence_db.create_plan(
                goal=goal,
                steps=data["steps"],
                session_id=session_id,
            )

        return Plan.from_llm_response(goal, data, plan_id)

    except Exception as exc:
        logger.warning("Plan creation failed: %s", exc)
        return None


def create_plan_sync(
    goal: str,
    sync_llm_call,
    intelligence_db=None,
    session_id: Optional[str] = None,
) -> Optional[Plan]:
    """Synchronous version of create_plan_async."""
    try:
        response = sync_llm_call(
            "You are a task planning and decomposition system.",
            PLANNING_PROMPT + goal,
        )

        data = _parse_json(response)
        if not data or not data.get("steps"):
            return None

        plan_id = None
        if intelligence_db:
            plan_id = intelligence_db.create_plan(
                goal=goal,
                steps=data["steps"],
                session_id=session_id,
            )

        return Plan.from_llm_response(goal, data, plan_id)

    except Exception as exc:
        logger.warning("Plan creation failed: %s", exc)
        return None


def _parse_json(response: str) -> Optional[Dict]:
    """Parse JSON from response."""
    if not response:
        return None
    import re
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = response.find('{')
    end = response.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(response[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None
