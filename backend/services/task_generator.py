"""
Task Generator Service - Auto-generate evidence collection tasks based on frequency.

This service should be run as a daily cron job to generate upcoming evidence collection tasks.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import date, timedelta
from typing import List
import logging

from models import EvidenceTracking, EvidenceCollectionTask, User
from database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# Frequency to days mapping (case-insensitive)
FREQUENCY_DAYS = {
    'daily': 1,
    'weekly': 7,
    'biweekly': 14,
    'bi-weekly': 14,
    'monthly': 30,
    'quarterly': 90,
    'semi-annually': 180,
    'semi-annual': 180,
    'annually': 365,
    'annual': 365,
    'yearly': 365
}

# Skip these frequency values (not time-based)
SKIP_FREQUENCIES = ['as required', 'as needed', 'continuous', 'ongoing', 'ad hoc', 'on demand']


async def generate_evidence_tasks():
    """
    Generate evidence collection tasks for all tracked evidence based on frequency.

    Logic:
    - For each evidence tracking record with a frequency
    - Calculate next due date based on last_collection_date or today
    - If no task exists for that due date, create one
    - Assign to owner_user_id or assigned_user_id
    - Create task 7 days before due date
    """
    logger.info("Starting evidence task generation...")

    async with AsyncSessionLocal() as db:
        # Get all evidence tracking records with frequency set
        result = await db.execute(
            select(EvidenceTracking).where(
                and_(
                    EvidenceTracking.is_tracked == True,
                    EvidenceTracking.frequency.isnot(None),
                    EvidenceTracking.frequency != ''
                )
            )
        )
        evidence_records = result.scalars().all()

        logger.info(f"Found {len(evidence_records)} evidence records with frequency")

        tasks_created = 0
        tasks_skipped = 0

        for evidence in evidence_records:
            try:
                # Parse frequency (case-insensitive, strip punctuation)
                if not evidence.frequency:
                    tasks_skipped += 1
                    continue

                frequency = evidence.frequency.lower().strip().rstrip('.')

                # Skip non-time-based frequencies
                if frequency in SKIP_FREQUENCIES:
                    logger.debug(f"Skipping non-time-based frequency '{frequency}' for evidence {evidence.evidence_id}")
                    tasks_skipped += 1
                    continue

                if frequency not in FREQUENCY_DAYS:
                    logger.warning(f"Invalid frequency '{frequency}' for evidence {evidence.evidence_id}. Expected: {', '.join(FREQUENCY_DAYS.keys())}")
                    tasks_skipped += 1
                    continue

                days_interval = FREQUENCY_DAYS[frequency]

                # Calculate next due date
                if evidence.last_collection_date:
                    next_due = evidence.last_collection_date + timedelta(days=days_interval)
                else:
                    # No previous collection - for first task, set reasonable due dates:
                    # - Annual/Semi-annual: 30 days from now (give time to prepare)
                    # - Quarterly: 30 days from now
                    # - Monthly: 30 days from now (aligns with first month)
                    # - Weekly/Daily: use actual interval
                    if days_interval >= 90:  # Quarterly or longer
                        next_due = date.today() + timedelta(days=30)
                    elif days_interval >= 30:  # Monthly
                        next_due = date.today() + timedelta(days=30)
                    else:
                        next_due = date.today() + timedelta(days=days_interval)

                # Check if task already exists for this due date (or within 3 days)
                result = await db.execute(
                    select(EvidenceCollectionTask).where(
                        and_(
                            EvidenceCollectionTask.evidence_tracking_id == evidence.id,
                            EvidenceCollectionTask.due_date >= next_due - timedelta(days=3),
                            EvidenceCollectionTask.due_date <= next_due + timedelta(days=3),
                            EvidenceCollectionTask.status != 'completed'
                        )
                    )
                )
                existing_task = result.scalar_one_or_none()

                if existing_task:
                    logger.debug(f"Task already exists for evidence {evidence.evidence_id} due {next_due}")
                    tasks_skipped += 1
                    continue

                # Determine assigned user (prefer assigned_user, fallback to owner)
                assigned_user_id = evidence.assigned_user_id or evidence.owner_user_id

                # Create task with enhanced fields
                task = EvidenceCollectionTask(
                    evidence_tracking_id=evidence.id,
                    due_date=next_due,
                    status='not_started',
                    assigned_user_id=assigned_user_id,
                    auto_generated=True,
                    task_type='collection',
                    title=f'Collect Evidence: {evidence.evidence_id}',
                    description=f'Scheduled {frequency} collection of evidence {evidence.evidence_id}.',
                    priority='medium'
                )
                db.add(task)

                # Update evidence next_collection_date
                evidence.next_collection_date = next_due

                logger.info(f"Created task for evidence {evidence.evidence_id} due {next_due}")
                tasks_created += 1

            except Exception as e:
                logger.error(f"Error generating task for evidence {evidence.evidence_id}: {e}")
                continue

        # Commit all changes
        try:
            await db.commit()
            logger.info(f"Task generation complete: {tasks_created} created, {tasks_skipped} skipped")
        except Exception as e:
            logger.error(f"Failed to commit tasks: {e}")
            await db.rollback()

    return {
        "tasks_created": tasks_created,
        "tasks_skipped": tasks_skipped
    }


if __name__ == "__main__":
    """
    Run this script as a cron job:
    0 0 * * * cd /app && python -m services.task_generator
    """
    import asyncio
    logging.basicConfig(level=logging.INFO)

    result = asyncio.run(generate_evidence_tasks())
    print(f"Task generation complete: {result}")
