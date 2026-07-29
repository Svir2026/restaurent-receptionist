from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.internal_auth import require_svir_internal_secret
from app.schemas.agent_provisioning import (
    DuplicateAgentRequest,
    DuplicateAgentResponse,
)
from app.schemas.menu_import import (
    ImportMenuResponse,
    ValidateMenuImportRequest,
    ValidateMenuImportResponse,
)
from app.services.agent_provisioner import (
    AgentProvisioningError,
    provision_duplicate_agent,
)
from app.services.elevenlabs_client import (
    ElevenLabsClientError,
    find_tool_by_exact_name,
    get_agent_configuration_snapshot,
    get_template_agent_summary,
    get_tool_configuration,
    list_workspace_tools,
)
from app.services.elevenlabs_resource_auditor import (
    get_agent_resource_audit,
)
from app.services.elevenlabs_tool_provisioner import (
    ElevenLabsToolProvisioningError,
    ensure_testkok2_calculate_order_total_v2_tool,
    ensure_yz_calculate_order_total_v2_tool,
)
from app.services.menu_importer import (
    MenuImportError,
    import_structured_menu,
)
from app.services.restaurant_tool_token_provider import (
    RestaurantToolTokenProviderError,
    get_restaurant_tool_token_from_vault,
)
from app.services.menu_validator import validate_menu_import


router = APIRouter(
    prefix="/internal/provisioning",
    tags=["internal-provisioning"],
)


TESTKOK2_CALCULATE_TOOL_CONFIRMATION = (
    "CREATE_TESTKOK2_CALCULATE_TOOL"
)


YZ_RESTAURANT_ID = UUID(
    "fc032c24-1dd6-4f94-9a4e-872a50c2487a"
)
YZ_CALCULATE_TOOL_CONFIRMATION = (
    "CREATE_YZ_CALCULATE_TOOL"
)


@router.post(
    "/menu/validate",
    response_model=ValidateMenuImportResponse,
)
def validate_structured_menu(
    payload: ValidateMenuImportRequest,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> ValidateMenuImportResponse | JSONResponse:
    """
    Validate a structured restaurant menu.

    This endpoint never writes to Supabase.
    """

    result = validate_menu_import(payload)

    if not result.valid:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(result),
        )

    return result


@router.post(
    "/menu/import",
    response_model=ImportMenuResponse,
)
def import_validated_menu(
    payload: ValidateMenuImportRequest,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> ImportMenuResponse | JSONResponse:
    """
    Validate and import a structured restaurant menu.

    The Supabase RPC performs the complete import atomically
    and advances the provisioning job to duplicate_agent.
    """

    validation = validate_menu_import(payload)

    if not validation.valid:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(validation),
        )

    try:
        result = import_structured_menu(payload)

    except MenuImportError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return ImportMenuResponse(
        success=True,
        restaurant_id=payload.restaurant_id,
        provisioning_job_id=payload.provisioning_job_id,
        import_id=result["import_id"],
        idempotent_replay=bool(
            result["idempotent_replay"]
        ),
        category_count=int(result["category_count"]),
        item_count=int(result["item_count"]),
        alias_count=int(result["alias_count"]),
        option_group_count=int(
            result["option_group_count"]
        ),
        option_count=int(result["option_count"]),
        ingredient_count=int(
            result["ingredient_count"]
        ),
        allergen_count=int(
            result["allergen_count"]
        ),
        next_step=str(result["next_step"]),
        warnings=validation.warnings,
    )


@router.get("/elevenlabs/template/check")
def check_elevenlabs_template(
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    Check that Railway can read the ElevenLabs template agent.

    This endpoint is read-only. It does not create, duplicate,
    update, publish, or delete any ElevenLabs agent.
    """

    try:
        template_agent = get_template_agent_summary()

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "elevenlabs_template_check_failed",
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "template_agent": template_agent,
    }


@router.get(
    "/elevenlabs/agents/{agent_id}/configuration"
)
def read_elevenlabs_agent_configuration(
    agent_id: str,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    Read the current ElevenLabs agent configuration.

    This endpoint is read-only. It does not update the agent,
    connect a phone number, or write anything to Supabase.
    """

    try:
        configuration = get_agent_configuration_snapshot(
            agent_id
        )

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": (
                    "elevenlabs_agent_configuration_"
                    "read_failed"
                ),
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "configuration": configuration,
    }


@router.get(
    "/elevenlabs/agents/{agent_id}/resources"
)
def read_elevenlabs_agent_resources(
    agent_id: str,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    Read a safe audit of the tools and knowledge-base
    documents connected to an ElevenLabs agent.

    This endpoint is read-only. Secret header values,
    authentication values, and URL query values are excluded.
    """

    try:
        resources = get_agent_resource_audit(agent_id)

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": (
                    "elevenlabs_agent_resource_audit_failed"
                ),
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "resources": resources,
    }


@router.get("/elevenlabs/tools")
def read_elevenlabs_workspace_tools(
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    List safe summaries of owned ElevenLabs webhook tools.

    This endpoint is read-only. It never returns request-header
    values and does not create, update, connect, or delete tools.
    """

    try:
        tools = list_workspace_tools()

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": (
                    "elevenlabs_workspace_tools_"
                    "read_failed"
                ),
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "tool_count": len(tools),
        "tools": tools,
    }


@router.get("/elevenlabs/tools/by-name")
def read_elevenlabs_tool_by_exact_name(
    tool_name: str,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    Search for one owned ElevenLabs webhook tool by exact name.

    This endpoint is read-only. It returns found=false when no
    exact match exists and fails safely if the name is ambiguous.
    """

    try:
        tool = find_tool_by_exact_name(tool_name)

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": (
                    "elevenlabs_tool_exact_name_"
                    "read_failed"
                ),
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "requested_name": tool_name.strip(),
        "found": tool is not None,
        "tool": tool,
    }


@router.post(
    "/elevenlabs/tools/testkok2/"
    "calculate-order-total-v2/ensure"
)
def ensure_testkok2_calculate_order_total_tool(
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
    confirmation: Annotated[
        str | None,
        Header(alias="X-Svir-Confirmation"),
    ] = None,
    tool_token: Annotated[
        str | None,
        Header(alias="X-Svir-Tool-Token"),
    ] = None,
) -> dict[str, object]:
    """
    Reuse or create only testkok2's secure v2
    calculate-order-total workspace tool.

    Deployment alone does not execute this endpoint.

    The endpoint requires:
    - the existing X-Svir-Internal-Secret
    - an explicit X-Svir-Confirmation header

    X-Svir-Tool-Token is required only when the tool
    does not already exist and must be created.

    This endpoint does not connect the tool to an agent,
    update an agent, change a phone number, update
    Supabase, or advance a provisioning step.
    """

    if (
        not isinstance(confirmation, str)
        or confirmation.strip()
        != TESTKOK2_CALCULATE_TOOL_CONFIRMATION
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "explicit_confirmation_required",
                "message": (
                    "The exact X-Svir-Confirmation header "
                    "is required."
                ),
            },
        )

    try:
        result = (
            ensure_testkok2_calculate_order_total_v2_tool(
                tool_token=tool_token,
            )
        )

    except ElevenLabsToolProvisioningError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "elevenlabs_tool_provisioning_blocked",
                "message": str(error),
            },
        ) from error

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "elevenlabs_tool_request_failed",
                "message": str(error),
            },
        ) from error

    return result


@router.post(
    "/elevenlabs/tools/yz-thai-wok-sushi/"
    "calculate-order-total-v2/ensure"
)
def ensure_yz_calculate_order_total_tool(
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
    confirmation: Annotated[
        str | None,
        Header(alias="X-Svir-Confirmation"),
    ] = None,
) -> dict[str, object]:
    """
    Reuse or create only YZ Thai Wok & Sushi's secure
    v2 calculate-order-total workspace tool.

    Deployment alone does not execute this endpoint.

    The endpoint requires:
    - the existing X-Svir-Internal-Secret
    - the exact X-Svir-Confirmation header

    The restaurant tool token is loaded server-side from
    Supabase Vault only if the tool does not already exist.

    This endpoint does not accept a tool token from the caller.
    It does not connect the tool to an agent, update an agent,
    change a phone number, update the menu, activate the
    restaurant, or advance a provisioning step.
    """

    if (
        not isinstance(confirmation, str)
        or confirmation.strip()
        != YZ_CALCULATE_TOOL_CONFIRMATION
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "explicit_confirmation_required",
                "message": (
                    "The exact X-Svir-Confirmation header "
                    "is required."
                ),
            },
        )

    def load_yz_tool_token() -> str:
        return get_restaurant_tool_token_from_vault(
            YZ_RESTAURANT_ID
        )

    try:
        result = ensure_yz_calculate_order_total_v2_tool(
            tool_token_provider=load_yz_tool_token,
        )

    except RestaurantToolTokenProviderError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    except ElevenLabsToolProvisioningError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "elevenlabs_tool_provisioning_blocked",
                "message": str(error),
            },
        ) from error

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "elevenlabs_tool_request_failed",
                "message": str(error),
            },
        ) from error

    return result


@router.get(
    "/elevenlabs/tools/{tool_id}/configuration"
)
def read_elevenlabs_tool_configuration(
    tool_id: str,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> dict[str, object]:
    """
    Read a safe configuration snapshot for one workspace tool.

    This endpoint is read-only. Request-header names may be shown,
    but secret request-header values are never returned.
    """

    try:
        configuration = get_tool_configuration(tool_id)

    except ElevenLabsClientError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": (
                    "elevenlabs_tool_configuration_"
                    "read_failed"
                ),
                "message": str(error),
            },
        ) from error

    return {
        "success": True,
        "read_only": True,
        "configuration": configuration,
    }


@router.post(
    "/elevenlabs/duplicate",
    response_model=DuplicateAgentResponse,
)
def duplicate_elevenlabs_agent(
    payload: DuplicateAgentRequest,
    _: Annotated[
        None,
        Depends(require_svir_internal_secret),
    ],
) -> DuplicateAgentResponse:
    """
    Duplicate or recover the ElevenLabs agent for one
    provisioning job.

    A valid internal secret and the explicit confirmation
    CREATE_TEST_AGENT are required.
    """

    try:
        result = provision_duplicate_agent(
            job_id=payload.provisioning_job_id,
            restaurant_id=payload.restaurant_id,
        )

    except AgentProvisioningError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    return DuplicateAgentResponse(
        success=bool(result["success"]),
        restaurant_id=result["restaurant_id"],
        provisioning_job_id=(
            result["provisioning_job_id"]
        ),
        agent_id=str(result["agent_id"]),
        agent_name=str(result["agent_name"]),
        created_new_agent=bool(
            result["created_new_agent"]
        ),
        recovered_existing_agent=bool(
            result["recovered_existing_agent"]
        ),
        phone_number_count=int(
            result["phone_number_count"]
        ),
        idempotent_replay=bool(
            result["idempotent_replay"]
        ),
        next_step="update_agent",
    )
