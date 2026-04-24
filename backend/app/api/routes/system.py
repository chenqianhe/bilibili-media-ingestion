from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import SessionDep, get_current_active_superuser
from app.models import (
    BilibiliAccessStatusPublic,
    BilibiliAccessUpdate,
    User,
)
from app.services.bilibili_access import (
    SecretStoreError,
    clear_database_bilibili_access,
    get_bilibili_access_status,
    set_database_bilibili_access,
)

router = APIRouter(prefix="/system", tags=["system"])

AdminUserDep = Annotated[User, Depends(get_current_active_superuser)]


@router.get(
    "/bilibili-access",
    response_model=BilibiliAccessStatusPublic,
)
def read_bilibili_access_status(
    *,
    session: SessionDep,
    current_user: AdminUserDep,
) -> Any:
    del current_user
    return get_bilibili_access_status(session)


@router.put(
    "/bilibili-access",
    response_model=BilibiliAccessStatusPublic,
)
def update_bilibili_access_status(
    *,
    session: SessionDep,
    current_user: AdminUserDep,
    payload: BilibiliAccessUpdate,
) -> Any:
    try:
        return set_database_bilibili_access(
            session,
            actor=current_user.email,
            netscape_cookies=payload.netscape_cookies,
            download_user_agent=payload.download_user_agent,
        )
    except SecretStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete(
    "/bilibili-access",
    response_model=BilibiliAccessStatusPublic,
)
def clear_bilibili_access_status(
    *,
    session: SessionDep,
    current_user: AdminUserDep,
) -> Any:
    return clear_database_bilibili_access(
        session,
        actor=current_user.email,
    )
