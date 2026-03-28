"""Tests for the multi-step planner."""

import pytest

from intelligence.planner import Plan, StepStatus


class TestPlan:
    def test_basic_plan(self):
        steps = [
            {"id": 1, "description": "Build", "depends_on": [], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
            {"id": 2, "description": "Test", "depends_on": [1], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
            {"id": 3, "description": "Deploy", "depends_on": [2], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "medium", "alternative_approach": "rollback", "result": None, "attempts": 0, "error": None},
        ]
        plan = Plan(goal="Ship feature", steps=steps)

        assert plan.progress == 0.0
        assert not plan.is_complete

    def test_get_next_steps(self):
        steps = [
            {"id": 1, "description": "Build", "depends_on": [], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
            {"id": 2, "description": "Test", "depends_on": [1], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
        ]
        plan = Plan(goal="Test", steps=steps)

        # Only step 1 should be ready (no deps)
        ready = plan.get_next_steps()
        assert len(ready) == 1
        assert ready[0]["id"] == 1

        # Complete step 1 → step 2 should be ready
        plan.mark_step_completed(1, "built ok")
        ready = plan.get_next_steps()
        assert len(ready) == 1
        assert ready[0]["id"] == 2

    def test_progress(self):
        steps = [
            {"id": 1, "description": "A", "depends_on": [], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
            {"id": 2, "description": "B", "depends_on": [], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
        ]
        plan = Plan(goal="Test", steps=steps)

        assert plan.progress == 0.0
        plan.mark_step_completed(1)
        assert plan.progress == 0.5
        plan.mark_step_completed(2)
        assert plan.progress == 1.0
        assert plan.is_complete

    def test_backtrack(self):
        steps = [
            {"id": 1, "description": "Try approach A", "depends_on": [], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "Try approach B", "result": None, "attempts": 0, "error": None},
            {"id": 2, "description": "Continue", "depends_on": [1], "status": StepStatus.PENDING,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 0, "error": None},
        ]
        plan = Plan(goal="Test backtrack", steps=steps)

        # Execute and fail step 1
        plan.mark_step_failed(1, "approach A didn't work")
        assert steps[0]["status"] == StepStatus.FAILED

        # Backtrack → should use alternative
        success = plan.backtrack_to(1)
        assert success
        assert steps[0]["description"] == "Try approach B"
        assert steps[0]["status"] == StepStatus.PENDING
        assert plan.backtrack_count == 1

    def test_backtrack_resets_dependents(self):
        steps = [
            {"id": 1, "description": "Base", "depends_on": [], "status": StepStatus.COMPLETED,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "Alt base", "result": "done", "attempts": 0, "error": None},
            {"id": 2, "description": "Dependent", "depends_on": [1], "status": StepStatus.COMPLETED,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": "done", "attempts": 0, "error": None},
        ]
        plan = Plan(goal="Test", steps=steps)

        plan.backtrack_to(1)
        assert steps[1]["status"] == StepStatus.PENDING
        assert steps[1]["result"] is None

    def test_format_status(self):
        steps = [
            {"id": 1, "description": "Build", "depends_on": [], "status": StepStatus.COMPLETED,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": "ok", "attempts": 0, "error": None},
            {"id": 2, "description": "Deploy", "depends_on": [1], "status": StepStatus.FAILED,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 1, "error": "timeout"},
        ]
        plan = Plan(goal="Ship it", steps=steps)

        status = plan.format_status()
        assert "Ship it" in status
        assert "[+]" in status  # completed
        assert "[X]" in status  # failed
        assert "timeout" in status

    def test_is_stuck(self):
        steps = [
            {"id": 1, "description": "Stuck step", "depends_on": [], "status": StepStatus.FAILED,
             "tools_needed": [], "estimated_complexity": "low", "alternative_approach": "", "result": None, "attempts": 2, "error": "failed"},
        ]
        plan = Plan(goal="Test stuck", steps=steps)
        assert plan.is_stuck
