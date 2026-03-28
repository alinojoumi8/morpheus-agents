"""
Personalization engine — adapt agent behavior to user preferences.

Tracks and infers:
- Communication style (verbosity, formality)
- Workflow patterns (repeated tool sequences)
- Priority inference (speed vs accuracy vs cost)
- Time-of-day behavioral adjustments
- Multi-persona support
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Communication Style Analysis
# ══════════════════════════════════════════════════════════════════

def analyze_user_style(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze user messages to infer communication style preferences.

    Returns dict with style observations and confidence scores.
    """
    user_msgs = [m for m in messages if m.get("role") == "user" and m.get("content")]
    if not user_msgs:
        return {}

    contents = [m["content"] for m in user_msgs]

    # Verbosity analysis
    avg_length = sum(len(c) for c in contents) / len(contents)
    if avg_length < 50:
        verbosity = "terse"
        verbosity_confidence = min(0.9, 0.5 + (50 - avg_length) / 100)
    elif avg_length < 200:
        verbosity = "moderate"
        verbosity_confidence = 0.4
    else:
        verbosity = "verbose"
        verbosity_confidence = min(0.9, 0.5 + (avg_length - 200) / 500)

    # Formality analysis
    informal_markers = sum(
        1 for c in contents
        for pattern in [r'\blol\b', r'\bbtw\b', r'\bidk\b', r'\bimo\b', r'\bpls\b',
                        r'\bthx\b', r'\bya\b', r'\byeah\b', r'\bnah\b', r'!{2,}',
                        r'\bhaha\b', r'\blmao\b', r'\bomg\b']
        if re.search(pattern, c, re.IGNORECASE)
    )
    formal_markers = sum(
        1 for c in contents
        for pattern in [r'\bplease\b', r'\bkindly\b', r'\bregards\b',
                        r'\bthank you\b', r'\bwould you\b', r'\bcould you\b',
                        r'\bI would appreciate\b']
        if re.search(pattern, c, re.IGNORECASE)
    )

    total_markers = informal_markers + formal_markers
    if total_markers == 0:
        formality = "neutral"
        formality_confidence = 0.3
    elif informal_markers > formal_markers:
        formality = "casual"
        formality_confidence = min(0.8, informal_markers / max(1, len(contents)))
    else:
        formality = "formal"
        formality_confidence = min(0.8, formal_markers / max(1, len(contents)))

    # Emoji usage
    emoji_count = sum(
        len(re.findall(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF]', c))
        for c in contents
    )
    uses_emoji = emoji_count > len(contents) * 0.3

    # Direct vs indirect
    question_count = sum(1 for c in contents if c.strip().endswith('?'))
    command_count = sum(
        1 for c in contents
        if re.match(r'^(do|make|create|fix|add|remove|change|update|run|build|deploy)\b', c.strip(), re.IGNORECASE)
    )
    if command_count > question_count:
        feedback_style = "direct"
    elif question_count > command_count:
        feedback_style = "indirect"
    else:
        feedback_style = "mixed"

    return {
        "verbosity": verbosity,
        "verbosity_confidence": round(verbosity_confidence, 2),
        "formality": formality,
        "formality_confidence": round(formality_confidence, 2),
        "uses_emoji": uses_emoji,
        "feedback_style": feedback_style,
        "avg_message_length": round(avg_length, 1),
        "message_count": len(user_msgs),
    }


def generate_style_directive(preferences: Dict[str, Dict[str, Any]]) -> str:
    """Generate a system prompt directive from accumulated preferences.

    Only includes preferences with sufficient confidence.
    """
    parts = []
    min_confidence = 0.5

    verbosity = preferences.get("user_preferred_verbosity", {})
    if verbosity.get("confidence", 0) >= min_confidence:
        val = verbosity["value"]
        if val == "terse":
            parts.append("Keep responses concise and brief. User prefers short, direct answers.")
        elif val == "verbose":
            parts.append("Provide detailed explanations. User appreciates thorough responses.")

    formality = preferences.get("user_preferred_formality", {})
    if formality.get("confidence", 0) >= min_confidence:
        val = formality["value"]
        if val == "casual":
            parts.append("Use a casual, conversational tone.")
        elif val == "formal":
            parts.append("Maintain a professional, formal tone.")

    feedback = preferences.get("user_feedback_style", {})
    if feedback.get("confidence", 0) >= min_confidence:
        val = feedback["value"]
        if val == "direct":
            parts.append("User gives direct commands. Respond with action, not questions.")

    if not parts:
        return ""

    return "Communication preferences: " + " ".join(parts)


# ══════════════════════════════════════════════════════════════════
# Workflow Pattern Detection
# ══════════════════════════════════════════════════════════════════

class WorkflowTracker:
    """Tracks tool call sequences to detect recurring patterns."""

    def __init__(self, min_pattern_length: int = 2, max_pattern_length: int = 8):
        self.min_length = min_pattern_length
        self.max_length = max_pattern_length
        self._current_sequence: List[str] = []

    def record_tool_call(self, tool_name: str):
        """Record a tool call in the current sequence."""
        self._current_sequence.append(tool_name)
        # Cap sequence length to prevent memory issues
        if len(self._current_sequence) > 200:
            self._current_sequence = self._current_sequence[-100:]

    def detect_patterns(self) -> List[Tuple[List[str], int]]:
        """Detect recurring patterns in the current sequence.

        Returns list of (pattern, frequency) tuples.
        """
        if len(self._current_sequence) < self.min_length * 2:
            return []

        patterns = {}
        seq = self._current_sequence

        for length in range(self.min_length, min(self.max_length + 1, len(seq) // 2 + 1)):
            for start in range(len(seq) - length + 1):
                pattern = tuple(seq[start:start + length])
                pattern_key = "|".join(pattern)

                if pattern_key not in patterns:
                    # Count occurrences
                    count = 0
                    for i in range(len(seq) - length + 1):
                        if tuple(seq[i:i + length]) == pattern:
                            count += 1
                    if count >= 2:
                        patterns[pattern_key] = (list(pattern), count)

        # Sort by frequency * length (longer frequent patterns are more interesting)
        result = sorted(
            patterns.values(),
            key=lambda x: x[1] * len(x[0]),
            reverse=True,
        )
        return result[:10]  # Top 10 patterns

    def store_patterns(self, intelligence_db, trigger: str = "session"):
        """Store detected patterns in the database."""
        patterns = self.detect_patterns()
        for pattern, freq in patterns:
            if freq >= 3:  # Only store if seen 3+ times
                intelligence_db.record_workflow_pattern(
                    trigger=trigger,
                    tool_sequence=pattern,
                    pattern_name=f"{'→'.join(pattern[:3])}{'...' if len(pattern) > 3 else ''}",
                )

    def reset(self):
        """Reset current sequence."""
        self._current_sequence.clear()


# ══════════════════════════════════════════════════════════════════
# Priority Inference
# ══════════════════════════════════════════════════════════════════

def infer_priorities(
    messages: List[Dict[str, Any]],
    session_duration_s: float = 0,
) -> Dict[str, float]:
    """Infer user's priorities from conversation patterns.

    Returns dict of priority → confidence (0-1):
    - speed: user wants fast results
    - accuracy: user wants correct results
    - cost: user is cost-conscious
    - learning: user wants to understand
    """
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    all_text = " ".join(user_msgs).lower()

    priorities = {
        "speed": 0.0,
        "accuracy": 0.0,
        "cost": 0.0,
        "learning": 0.0,
    }

    # Speed signals
    speed_patterns = [r'\bquick\b', r'\bfast\b', r'\bjust do it\b', r'\basap\b',
                      r'\bhurry\b', r'\bdon\'t explain\b', r'\bskip\b']
    for p in speed_patterns:
        if re.search(p, all_text):
            priorities["speed"] += 0.15

    # Short messages = speed preference
    avg_len = sum(len(m) for m in user_msgs) / max(1, len(user_msgs))
    if avg_len < 30:
        priorities["speed"] += 0.1

    # Accuracy signals
    accuracy_patterns = [r'\bmake sure\b', r'\bverify\b', r'\bcheck\b', r'\btest\b',
                         r'\bcorrect\b', r'\baccurate\b', r'\bcareful\b', r'\bprecise\b']
    for p in accuracy_patterns:
        if re.search(p, all_text):
            priorities["accuracy"] += 0.15

    # Cost signals
    cost_patterns = [r'\bcheap\b', r'\bcost\b', r'\bpric\b', r'\bbudget\b',
                     r'\bexpensive\b', r'\bfree\b', r'\bsave money\b']
    for p in cost_patterns:
        if re.search(p, all_text):
            priorities["cost"] += 0.2

    # Learning signals
    learn_patterns = [r'\bwhy\b', r'\bhow does\b', r'\bexplain\b', r'\bunderstand\b',
                      r'\bwhat is\b', r'\bteach\b', r'\blearn\b']
    for p in learn_patterns:
        if re.search(p, all_text):
            priorities["learning"] += 0.1

    # Clamp all to [0, 1]
    priorities = {k: min(1.0, v) for k, v in priorities.items()}

    return priorities


# ══════════════════════════════════════════════════════════════════
# Time-of-Day Awareness
# ══════════════════════════════════════════════════════════════════

def get_time_context(hour: Optional[int] = None) -> Dict[str, Any]:
    """Get time-of-day context for behavioral adjustments.

    Returns context dict with mode, suggestions, and style hints.
    """
    if hour is None:
        hour = datetime.now().hour

    if 6 <= hour < 11:
        return {
            "period": "morning",
            "mode": "planning",
            "style_hint": "Proactive, suggest priorities and briefing",
            "suggest_briefing": True,
            "suggest_followups": True,
        }
    elif 11 <= hour < 14:
        return {
            "period": "midday",
            "mode": "focused",
            "style_hint": "Efficient, execution-oriented",
            "suggest_briefing": False,
            "suggest_followups": False,
        }
    elif 14 <= hour < 18:
        return {
            "period": "afternoon",
            "mode": "execution",
            "style_hint": "Focused on completing tasks, suggest wrap-up",
            "suggest_briefing": False,
            "suggest_followups": True,
        }
    elif 18 <= hour < 22:
        return {
            "period": "evening",
            "mode": "review",
            "style_hint": "Relaxed pace, review and planning for tomorrow",
            "suggest_briefing": False,
            "suggest_followups": True,
        }
    else:
        return {
            "period": "night",
            "mode": "minimal",
            "style_hint": "Brief, essential only, don't overwhelm",
            "suggest_briefing": False,
            "suggest_followups": False,
        }


# ══════════════════════════════════════════════════════════════════
# Multi-Persona Support
# ══════════════════════════════════════════════════════════════════

class PersonaManager:
    """Manages multiple persona profiles for different contexts."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._personas = config or {}
        self._active_persona = "default"

    @property
    def active(self) -> str:
        return self._active_persona

    def switch(self, persona_name: str) -> bool:
        """Switch to a different persona."""
        if persona_name in self._personas or persona_name == "default":
            self._active_persona = persona_name
            logger.info("Switched to persona: %s", persona_name)
            return True
        return False

    def get_style_directive(self) -> str:
        """Get style directive for the active persona."""
        if self._active_persona == "default":
            return ""
        persona = self._personas.get(self._active_persona, {})
        style = persona.get("style", "")
        return f"Persona mode: {self._active_persona}. Style: {style}" if style else ""

    def get_active_config(self) -> Dict[str, Any]:
        """Get config for the active persona."""
        if self._active_persona == "default":
            return {}
        return self._personas.get(self._active_persona, {})

    def auto_select(self, hour: Optional[int] = None) -> str:
        """Auto-select persona based on time of day."""
        if hour is None:
            hour = datetime.now().hour

        for name, config in self._personas.items():
            active_hours = config.get("active_hours", "")
            if active_hours and "-" in active_hours:
                try:
                    start, end = active_hours.split("-")
                    start_h, end_h = int(start), int(end)
                    if start_h <= hour < end_h:
                        self._active_persona = name
                        return name
                except (ValueError, TypeError):
                    pass

        return self._active_persona

    def list_personas(self) -> List[Dict[str, Any]]:
        """List all configured personas."""
        result = [{"name": "default", "style": "standard", "active": self._active_persona == "default"}]
        for name, config in self._personas.items():
            result.append({
                "name": name,
                "style": config.get("style", ""),
                "active_hours": config.get("active_hours", ""),
                "active": self._active_persona == name,
            })
        return result


def update_preferences_from_session(
    intelligence_db,
    messages: List[Dict[str, Any]],
    session_id: str,
):
    """Analyze session messages and update user preferences."""
    style = analyze_user_style(messages)
    if not style:
        return

    # Update verbosity preference
    if style.get("verbosity_confidence", 0) > 0.4:
        db_pref = intelligence_db.get_preferences().get("user_preferred_verbosity", {})
        old_confidence = db_pref.get("confidence", 0)
        # Increase confidence gradually
        new_confidence = min(0.95, max(old_confidence, style["verbosity_confidence"]))
        intelligence_db.set_preference(
            key="user_preferred_verbosity",
            value=style["verbosity"],
            confidence=new_confidence,
            evidence=[f"session:{session_id}", f"avg_len:{style['avg_message_length']}"],
        )

    # Update formality preference
    if style.get("formality_confidence", 0) > 0.4:
        db_pref = intelligence_db.get_preferences().get("user_preferred_formality", {})
        old_confidence = db_pref.get("confidence", 0)
        new_confidence = min(0.95, max(old_confidence, style["formality_confidence"]))
        intelligence_db.set_preference(
            key="user_preferred_formality",
            value=style["formality"],
            confidence=new_confidence,
            evidence=[f"session:{session_id}"],
        )

    # Update feedback style
    intelligence_db.set_preference(
        key="user_feedback_style",
        value=style["feedback_style"],
        confidence=0.4,
        evidence=[f"session:{session_id}"],
    )

    # Update priorities
    priorities = infer_priorities(messages)
    for priority, confidence in priorities.items():
        if confidence > 0.2:
            intelligence_db.set_preference(
                key=f"priority_{priority}",
                value=str(round(confidence, 2)),
                confidence=confidence,
                evidence=[f"session:{session_id}"],
            )
