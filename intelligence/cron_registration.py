"""
Auto-register intelligence system cron jobs.

Called during agent initialization when intelligence is enabled.
Creates system cron jobs for:
1. Memory consolidation (daily at 3 AM)
2. Daily digest (weekdays at 8 AM)
3. Prompt optimization (Sunday at 2 AM)

Jobs are idempotent — re-running this won't create duplicates.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# Job IDs used for idempotent registration
CONSOLIDATION_JOB_ID_PREFIX = "intel-consolidate"
DIGEST_JOB_ID_PREFIX = "intel-digest"
OPTIMIZATION_JOB_ID_PREFIX = "intel-optimize"

# Job definitions
SYSTEM_JOBS = [
    {
        "name": "Memory Consolidation",
        "id_prefix": CONSOLIDATION_JOB_ID_PREFIX,
        "prompt": (
            "Run intelligence memory consolidation: decay inactive entries, "
            "promote frequently-accessed memories to hot tier, demote low-relevance "
            "entries to cold tier, deduplicate near-identical entries, and distill "
            "cold memory clusters into summaries. Report stats when done."
        ),
        "schedule": "0 3 * * *",  # Daily at 3 AM
        "deliver": "local",  # Silent, no delivery
    },
    {
        "name": "Daily Briefing",
        "id_prefix": DIGEST_JOB_ID_PREFIX,
        "prompt": (
            "Generate today's intelligence briefing: summarize recent sessions, "
            "list pending follow-ups, report sentiment trends, flag any "
            "underperforming skills, and suggest priorities for the day."
        ),
        "schedule": "0 8 * * 1-5",  # Weekdays at 8 AM
        "deliver": "origin",  # Deliver to home channel
    },
    {
        "name": "Prompt Optimization",
        "id_prefix": OPTIMIZATION_JOB_ID_PREFIX,
        "prompt": (
            "Run weekly prompt optimization analysis: review reflections, "
            "sentiment trends, strategy success rates, failure patterns, and "
            "user preferences from the past week. Generate specific suggestions "
            "for improving agent behavior and system prompt."
        ),
        "schedule": "0 2 * * 0",  # Sunday at 2 AM
        "deliver": "local",  # Silent
    },
]


def register_intelligence_cron_jobs(config: Dict = None) -> List[str]:
    """Register intelligence system cron jobs if not already present.

    Args:
        config: Intelligence config dict (optional)

    Returns:
        List of created job IDs (empty if all already exist)
    """
    try:
        from cron.jobs import load_jobs, create_job
    except ImportError:
        logger.debug("Cron module not available, skipping job registration")
        return []

    # Check which jobs already exist
    try:
        existing_jobs = load_jobs()
    except Exception:
        existing_jobs = []

    existing_names = {j.get("name", "") for j in existing_jobs}
    existing_ids = {j.get("id", "") for j in existing_jobs}

    created = []

    for job_def in SYSTEM_JOBS:
        # Skip if already exists (check by name prefix match)
        if job_def["name"] in existing_names:
            continue

        # Also check by ID prefix
        if any(jid.startswith(job_def["id_prefix"]) for jid in existing_ids):
            continue

        # Check if this job type is enabled in config
        if config:
            if job_def["id_prefix"] == DIGEST_JOB_ID_PREFIX:
                if not config.get("monitors", {}).get("enabled", False):
                    continue
            # Consolidation and optimization always registered if intelligence is on

        try:
            job = create_job(
                prompt=job_def["prompt"],
                schedule=job_def["schedule"],
                name=job_def["name"],
                deliver=job_def["deliver"],
            )
            created.append(job["id"])
            logger.info("Registered intelligence cron job: %s (id=%s)",
                        job_def["name"], job["id"])
        except Exception as exc:
            logger.warning("Failed to register cron job '%s': %s",
                           job_def["name"], exc)

    return created


def unregister_intelligence_cron_jobs() -> List[str]:
    """Remove all intelligence system cron jobs.

    Returns:
        List of removed job IDs
    """
    try:
        from cron.jobs import load_jobs, delete_job
    except ImportError:
        return []

    removed = []
    try:
        jobs = load_jobs()
        for job in jobs:
            name = job.get("name", "")
            if name in {jd["name"] for jd in SYSTEM_JOBS}:
                try:
                    delete_job(job["id"])
                    removed.append(job["id"])
                except Exception:
                    pass
    except Exception:
        pass

    return removed
