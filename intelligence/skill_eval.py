"""
Skill evaluation and scoring — track skill quality over time.

After each skill invocation, evaluates outcome and stores score.
Aggregates scores to auto-flag underperforming skills.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Score thresholds
FLAG_THRESHOLD = 0.4       # Average score below this → flag skill
DISABLE_THRESHOLD = 0.2    # Average score below this → suggest disable
MIN_INVOCATIONS = 3        # Minimum invocations before scoring matters


def evaluate_skill_invocation(
    skill_name: str,
    session_id: str,
    tool_calls_after: List[Dict],
    final_response: Optional[str],
    error_occurred: bool,
    user_feedback_positive: Optional[bool] = None,
    intelligence_db=None,
) -> Dict[str, Any]:
    """Evaluate a skill invocation and return score.

    Scoring heuristics:
    - Base score: 0.5 (neutral)
    - +0.3 if no errors occurred
    - -0.3 if errors occurred
    - +0.2 if user gave positive feedback
    - -0.2 if user gave negative feedback
    - -0.1 if too many tool calls (>20, suggests confusion)

    Args:
        skill_name: Name of the invoked skill
        session_id: Current session
        tool_calls_after: Tool calls made after skill activation
        final_response: Agent's final response text
        error_occurred: Whether any tool errors happened
        user_feedback_positive: Explicit user feedback (None if no feedback)
        intelligence_db: Optional IntelligenceDB for storage

    Returns:
        Dict with outcome, score, and context
    """
    score = 0.5  # Base

    # Error check
    if error_occurred:
        score -= 0.3
    else:
        score += 0.3

    # User feedback
    if user_feedback_positive is True:
        score += 0.2
    elif user_feedback_positive is False:
        score -= 0.2

    # Efficiency check
    num_calls = len(tool_calls_after)
    if num_calls > 20:
        score -= 0.1  # Too many calls suggests confusion

    # Clamp
    score = max(0.0, min(1.0, score))

    # Determine outcome
    if score >= 0.7:
        outcome = "success"
    elif score >= 0.4:
        outcome = "partial"
    else:
        outcome = "failure"

    result = {
        "skill_name": skill_name,
        "outcome": outcome,
        "score": round(score, 2),
        "context": {
            "tool_calls": num_calls,
            "error_occurred": error_occurred,
            "user_feedback": user_feedback_positive,
        },
    }

    # Store in database
    if intelligence_db:
        try:
            intelligence_db.log_skill_score(
                skill_name=skill_name,
                outcome=outcome,
                score=score,
                session_id=session_id,
                context=result["context"],
            )
        except Exception as exc:
            logger.warning("Failed to log skill score: %s", exc)

    return result


def get_skill_health_report(intelligence_db) -> Dict[str, Dict[str, Any]]:
    """Get health report for all scored skills.

    Returns dict of skill_name → {
        avg_score, total, successes, failures, status, recommendation
    }
    """
    scores = intelligence_db.get_skill_aggregate_scores()
    report = {}

    for skill_name, data in scores.items():
        avg = data["avg_score"] or 0.5
        total = data["total"] or 0

        if total < MIN_INVOCATIONS:
            status = "insufficient_data"
            recommendation = None
        elif avg < DISABLE_THRESHOLD:
            status = "critical"
            recommendation = f"Consider disabling '{skill_name}' — avg score {avg:.2f}"
        elif avg < FLAG_THRESHOLD:
            status = "flagged"
            recommendation = f"Skill '{skill_name}' underperforming — avg score {avg:.2f}, review needed"
        elif avg >= 0.7:
            status = "healthy"
            recommendation = None
        else:
            status = "acceptable"
            recommendation = None

        report[skill_name] = {
            "avg_score": round(avg, 2),
            "total_invocations": total,
            "successes": data["successes"],
            "failures": data["failures"],
            "status": status,
            "recommendation": recommendation,
        }

    return report


def format_skill_scores_for_display(report: Dict[str, Dict]) -> str:
    """Format skill health report for CLI/chat display."""
    if not report:
        return "No skill scores recorded yet."

    lines = ["Skill Health Report:", ""]
    for name, data in sorted(report.items(), key=lambda x: x[1]["avg_score"]):
        status_icon = {
            "healthy": "+",
            "acceptable": "~",
            "flagged": "!",
            "critical": "X",
            "insufficient_data": "?",
        }.get(data["status"], "?")

        line = (
            f"  [{status_icon}] {name}: "
            f"score={data['avg_score']:.2f} "
            f"({data['successes']}/{data['total_invocations']} success)"
        )
        lines.append(line)

        if data["recommendation"]:
            lines.append(f"      ^ {data['recommendation']}")

    return "\n".join(lines)
