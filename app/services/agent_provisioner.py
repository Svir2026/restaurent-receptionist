from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    duplicate_template_agent,
    find_agent_by_exact_name,
    get_agent_summary,
)
from app.services.supabase_client import get_client


logger = logging.getLogger(__name__)

DUPLICATE_AGENT_STEP = "duplicate_agent"
LEASE_SECONDS = 600


class AgentProvisioningError(Exception):
    """Safe error returned by the agent provisioning service."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 502,
        failure_status: str = "failed",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.failure_status = failure_status


def _safe_log_value(
    value: object,
    max_length: int = 500,
) -> str | None:
    if value is None:
        return None

    return str(value)[:max_length]


def _extract_row(
    data: object,
    *,
    empty_code: str,
    empty_message: str,
) -> dict[str, Any]:
    if isinstance(data, list):
        row = data[0] if data else None
    elif isinstance(data, dict):
        row = data
    else:
        row = None

    if not isinstance(row, dict):
        raise AgentProvisioningError(
            code=empty_code,
            message=empty_message,
            status_code=502,
        )

    return row


def _load_provisioning_job(
    job_id: UUID,
) -> dict[str, Any]:
    """
    Load the existing provisioning job.

    This function does not modify the job.
    """

    try:
        response = (
            get_client()
            .table("provisioning_jobs")
            .select(
                (
                    "id,"
                    "restaurant_id,"
                    "idempotency_key,"
                    "status,"
                    "current_step,"
                    "requested_by"
                )
            )
            .eq("id", str(job_id))
            .limit(1)
            .execute()
        )

    except Exception as error:
        logger.error(
            "Could not load provisioning job",
            extra={
                "job_id": str(job_id),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise AgentProvisioningError(
            code="PROVISIONING_JOB_READ_FAILED",
            message=(
                "Installationsjobbet kunde inte läsas."
            ),
            status_code=502,
        ) from error

    data = response.data

    if not isinstance(data, list) or not data:
        raise AgentProvisioningError(
            code="PROVISIONING_JOB_NOT_FOUND",
            message=(
                "Installationsjobbet kunde inte hittas."
            ),
            status_code=404,
        )

    job = data[0]

    if not isinstance(job, dict):
        raise AgentProvisioningError(
            code="INVALID_PROVISIONING_JOB",
            message=(
                "Installationsjobbet har ett ogiltigt format."
            ),
            status_code=502,
        )

    required_fields = {
        "id",
        "restaurant_id",
        "idempotency_key",
        "status",
        "current_step",
        "requested_by",
    }

    if not required_fields.issubset(job):
        raise AgentProvisioningError(
            code="INCOMPLETE_PROVISIONING_JOB",
            message=(
                "Installationsjobbet saknar nödvändig information."
            ),
            status_code=502,
        )

    return job


def _claim_provisioning_job(
    job: dict[str, Any],
) -> dict[str, Any]:
    """
    Acquire the existing atomic provisioning lease.
    """

    requested_by = job.get("requested_by")

    if not requested_by:
        raise AgentProvisioningError(
            code="PROVISIONING_REQUESTED_BY_MISSING",
            message=(
                "Installationsjobbet saknar beställande "
                "administratör."
            ),
            status_code=409,
            failure_status="needs_attention",
        )

    try:
        response = get_client().rpc(
            "claim_provisioning_job",
            {
                "p_idempotency_key": str(
                    job["idempotency_key"]
                ),
                "p_requested_by": str(requested_by),
                "p_metadata": {
                    "source": "railway",
                    "operation": DUPLICATE_AGENT_STEP,
                    "job_id": str(job["id"]),
                },
                "p_lease_seconds": LEASE_SECONDS,
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Could not claim provisioning job",
            extra={
                "job_id": str(job["id"]),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise AgentProvisioningError(
            code="PROVISIONING_CLAIM_FAILED",
            message=(
                "Installationsjobbet kunde inte låsas."
            ),
            status_code=502,
        ) from error

    claim = _extract_row(
        response.data,
        empty_code="EMPTY_PROVISIONING_CLAIM",
        empty_message=(
            "Låsningen av installationsjobbet gav inget svar."
        ),
    )

    if str(claim.get("job_id")) != str(job["id"]):
        raise AgentProvisioningError(
            code="PROVISIONING_JOB_ID_MISMATCH",
            message=(
                "Fel installationsjobb returnerades vid låsning."
            ),
            status_code=409,
            failure_status="needs_attention",
        )

    if not bool(claim.get("claimed")):
        claim_status = str(claim.get("status") or "")

        if claim_status in {"completed", "cancelled"}:
            raise AgentProvisioningError(
                code="PROVISIONING_JOB_NOT_RUNNABLE",
                message=(
                    "Installationsjobbet är redan avslutat."
                ),
                status_code=409,
            )

        raise AgentProvisioningError(
            code="PROVISIONING_JOB_ALREADY_RUNNING",
            message=(
                "En annan process arbetar redan med "
                "installationsjobbet."
            ),
            status_code=409,
        )

    lease_token = claim.get("lease_token")

    if not lease_token:
        raise AgentProvisioningError(
            code="PROVISIONING_LEASE_TOKEN_MISSING",
            message=(
                "Installationslåset saknar en giltig token."
            ),
            status_code=502,
        )

    return claim


def _record_provisioning_failure(
    *,
    job_id: UUID,
    lock_token: str,
    failure_status: str,
    error_code: str,
    error_message: str,
) -> None:
    """
    Record a safe provisioning error.

    This helper never replaces the original error if recording
    the failure itself fails.
    """

    try:
        response = get_client().rpc(
            "record_provisioning_failure",
            {
                "p_job_id": str(job_id),
                "p_lock_token": lock_token,
                "p_step_key": DUPLICATE_AGENT_STEP,
                "p_failure_status": failure_status,
                "p_error_code": error_code[:100],
                "p_error_message": error_message[:500],
            },
        ).execute()

        data = response.data

        if isinstance(data, list):
            result = data[0] if data else None
        elif isinstance(data, dict):
            result = data
        else:
            result = None

        if (
            isinstance(result, dict)
            and not bool(result.get("applied"))
        ):
            logger.warning(
                "Provisioning failure was not applied",
                extra={
                    "job_id": str(job_id),
                    "result_code": _safe_log_value(
                        result.get("result_code"),
                        100,
                    ),
                },
            )

    except Exception as error:
        logger.error(
            "Could not record provisioning failure",
            extra={
                "job_id": str(job_id),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )


def _complete_duplicate_agent(
    *,
    job_id: UUID,
    lock_token: str,
    restaurant_id: UUID,
    agent_id: str,
    agent_name: str,
) -> dict[str, Any]:
    """
    Atomically save the duplicated agent and advance the job.
    """

    try:
        response = get_client().rpc(
            "complete_duplicate_agent",
            {
                "p_job_id": str(job_id),
                "p_lock_token": lock_token,
                "p_restaurant_id": str(restaurant_id),
                "p_agent_id": agent_id,
                "p_agent_name": agent_name,
                "p_template_agent_id": (
                    settings
                    .elevenlabs_template_agent_id
                    .strip()
                ),
            },
        ).execute()

    except Exception as error:
        logger.error(
            "Could not complete duplicate agent step",
            extra={
                "job_id": str(job_id),
                "restaurant_id": str(restaurant_id),
                "agent_id": agent_id,
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    getattr(error, "message", None)
                    or str(error)
                ),
            },
        )

        raise AgentProvisioningError(
            code="DUPLICATE_AGENT_SAVE_FAILED",
            message=(
                "Agenten skapades men kunde inte sparas "
                "i installationen."
            ),
            status_code=502,
        ) from error

    result = _extract_row(
        response.data,
        empty_code="EMPTY_DUPLICATE_AGENT_RESPONSE",
        empty_message=(
            "Sparningen av Agent ID gav inget giltigt svar."
        ),
    )

    applied = bool(result.get("applied"))
    idempotent_replay = bool(
        result.get("idempotent_replay")
    )

    if not applied and not idempotent_replay:
        result_code = str(
            result.get("result_code")
            or "DUPLICATE_AGENT_NOT_APPLIED"
        )

        safe_messages = {
            "JOB_NOT_FOUND": (
                "Installationsjobbet kunde inte hittas."
            ),
            "PROVISIONING_RESTAURANT_MISMATCH": (
                "Restaurangen matchar inte installationsjobbet."
            ),
            "PROVISIONING_LEASE_LOST": (
                "Installationslåset hann gå ut."
            ),
            "DUPLICATE_AGENT_STEP_NOT_FOUND": (
                "Steget duplicate_agent kunde inte hittas."
            ),
            "PROVISIONING_STEP_MISMATCH": (
                "Installationen är inte redo för agentsteget."
            ),
            "ELEVENLABS_AGENT_ID_CONFLICT": (
                "Restaurangen har redan ett annat Agent ID."
            ),
            "DUPLICATE_AGENT_REFERENCE_CONFLICT": (
                "Agentsteget innehåller redan ett annat Agent ID."
            ),
        }

        raise AgentProvisioningError(
            code=result_code,
            message=safe_messages.get(
                result_code,
                "Agent ID kunde inte sparas.",
            ),
            status_code=409,
            failure_status="needs_attention",
        )

    return result


def _build_provisioning_agent_name(
    job_id: UUID,
) -> str:
    """
    Build a deterministic name used only during provisioning.
    """

    return f"SVIR-PROVISION-{job_id}"


def provision_duplicate_agent(
    *,
    job_id: UUID,
    restaurant_id: UUID,
) -> dict[str, Any]:
    """
    Safely duplicate or recover an ElevenLabs agent.

    Flow:
    1. Read and claim the provisioning job.
    2. Confirm duplicate_agent is the current step.
    3. Search for a previously created exact-name agent.
    4. Duplicate the template only when no match exists.
    5. Confirm the agent has no assigned phone number.
    6. Save the Agent ID atomically in Supabase.
    """

    lock_token: str | None = None

    try:
        job = _load_provisioning_job(job_id)

        if (
            str(job.get("restaurant_id"))
            != str(restaurant_id)
        ):
            raise AgentProvisioningError(
                code="PROVISIONING_RESTAURANT_MISMATCH",
                message=(
                    "Restaurangen matchar inte "
                    "installationsjobbet."
                ),
                status_code=409,
                failure_status="needs_attention",
            )

        if job.get("current_step") != DUPLICATE_AGENT_STEP:
            raise AgentProvisioningError(
                code="PROVISIONING_STEP_MISMATCH",
                message=(
                    "Installationen är inte redo för "
                    "agentduplicering."
                ),
                status_code=409,
            )

        claim = _claim_provisioning_job(job)

        lock_token = str(claim["lease_token"])

        if (
            str(claim.get("restaurant_id"))
            != str(restaurant_id)
        ):
            raise AgentProvisioningError(
                code="CLAIMED_RESTAURANT_MISMATCH",
                message=(
                    "Fel restaurang returnerades vid låsning."
                ),
                status_code=409,
                failure_status="needs_attention",
            )

        if (
            claim.get("current_step")
            != DUPLICATE_AGENT_STEP
        ):
            raise AgentProvisioningError(
                code="CLAIMED_STEP_MISMATCH",
                message=(
                    "Installationssteget ändrades under "
                    "låsningen."
                ),
                status_code=409,
            )

        provisioning_name = (
            _build_provisioning_agent_name(job_id)
        )

        try:
            existing_agent = find_agent_by_exact_name(
                provisioning_name
            )

        except ElevenLabsClientError as error:
            if "Multiple ElevenLabs agents" in str(error):
                raise AgentProvisioningError(
                    code="ELEVENLABS_AGENT_NAME_CONFLICT",
                    message=(
                        "Flera ElevenLabs-agenter har samma "
                        "installationsnamn. Manuell kontroll krävs."
                    ),
                    status_code=409,
                    failure_status="needs_attention",
                ) from error

            raise

        created_new_agent = False
        recovered_existing_agent = False

        if existing_agent is not None:
            agent_id = str(existing_agent["agent_id"])
            recovered_existing_agent = True

        else:
            duplicated_agent = duplicate_template_agent(
                provisioning_name
            )

            agent_id = str(
                duplicated_agent["agent_id"]
            )

            created_new_agent = True

        agent_summary = get_agent_summary(agent_id)

        returned_name = str(
            agent_summary.get("name") or ""
        ).strip()

        if returned_name != provisioning_name:
            raise AgentProvisioningError(
                code="ELEVENLABS_AGENT_NAME_MISMATCH",
                message=(
                    "Den skapade agentens namn matchar inte "
                    "installationsjobbet."
                ),
                status_code=409,
                failure_status="needs_attention",
            )

        phone_number_count = int(
            agent_summary.get("phone_number_count") or 0
        )

        if phone_number_count != 0:
            raise AgentProvisioningError(
                code="ELEVENLABS_AGENT_HAS_PHONE",
                message=(
                    "Den nya agenten har oväntat ett "
                    "telefonnummer kopplat. Manuell kontroll krävs."
                ),
                status_code=409,
                failure_status="needs_attention",
            )

        completion = _complete_duplicate_agent(
            job_id=job_id,
            lock_token=lock_token,
            restaurant_id=restaurant_id,
            agent_id=agent_id,
            agent_name=provisioning_name,
        )

        return {
            "success": True,
            "restaurant_id": str(restaurant_id),
            "provisioning_job_id": str(job_id),
            "agent_id": agent_id,
            "agent_name": provisioning_name,
            "created_new_agent": created_new_agent,
            "recovered_existing_agent": (
                recovered_existing_agent
            ),
            "phone_number_count": phone_number_count,
            "idempotent_replay": bool(
                completion.get("idempotent_replay")
            ),
            "next_step": str(
                completion.get("next_step")
                or "update_agent"
            ),
        }

    except AgentProvisioningError as error:
        if lock_token is not None:
            _record_provisioning_failure(
                job_id=job_id,
                lock_token=lock_token,
                failure_status=error.failure_status,
                error_code=error.code,
                error_message=error.message,
            )

        raise

    except ElevenLabsClientError as error:
        logger.error(
            "ElevenLabs duplicate agent operation failed",
            extra={
                "job_id": str(job_id),
                "restaurant_id": str(restaurant_id),
                "error_type": type(error).__name__,
                "error_message": _safe_log_value(
                    str(error),
                    500,
                ),
            },
        )

        if lock_token is not None:
            _record_provisioning_failure(
                job_id=job_id,
                lock_token=lock_token,
                failure_status="failed",
                error_code=(
                    "ELEVENLABS_DUPLICATE_AGENT_FAILED"
                ),
                error_message=(
                    "ElevenLabs-agenten kunde inte skapas "
                    "eller kontrolleras."
                ),
            )

        raise AgentProvisioningError(
            code="ELEVENLABS_DUPLICATE_AGENT_FAILED",
            message=(
                "ElevenLabs-agenten kunde inte skapas "
                "eller kontrolleras."
            ),
            status_code=502,
        ) from error

    except Exception as error:
        logger.exception(
            "Unexpected duplicate agent provisioning failure",
            extra={
                "job_id": str(job_id),
                "restaurant_id": str(restaurant_id),
            },
        )

        if lock_token is not None:
            _record_provisioning_failure(
                job_id=job_id,
                lock_token=lock_token,
                failure_status="failed",
                error_code="AGENT_PROVISIONING_FAILED",
                error_message=(
                    "Agentsteget kunde inte slutföras."
                ),
            )

        raise AgentProvisioningError(
            code="AGENT_PROVISIONING_FAILED",
            message=(
                "Agentsteget kunde inte slutföras."
            ),
            status_code=502,
        ) from error
