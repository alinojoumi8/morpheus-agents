"""
Security scanning for intelligence module content.

Reuses the same threat patterns as memory_tool.py to prevent prompt injection,
role hijacking, and secret exfiltration via stored intelligence data.

All content that gets injected into the system prompt (hot-tier memories,
strategies, personalization directives) MUST pass through scan_content()
before storage.
"""

import re
from typing import Optional

# Same threat patterns as tools/memory_tool.py — kept in sync
_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.morpheus/\.env|\~/\.morpheus/\.env', "morpheus_env"),
]

# Invisible unicode chars used for injection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def scan_content(content: str) -> Optional[str]:
    """Scan content for injection/exfiltration patterns.

    Returns error string if content is blocked, None if safe.
    """
    if not content:
        return None

    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return (
                f"Blocked: content matches threat pattern '{pid}'. "
                "Intelligence entries may be injected into the system prompt "
                "and must not contain injection or exfiltration payloads."
            )

    return None


def is_safe(content: str) -> bool:
    """Quick check: returns True if content passes security scan."""
    return scan_content(content) is None
