import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.prompt import Prompt
from app.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


class PromptResponse(Prompt.__class__):
    pass


from pydantic import BaseModel


class PromptOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    prompt_text: str
    type: str
    version: int
    model: str
    is_active: bool
    use_case: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PromptOut])
async def list_prompts(
    prompt_type: str | None = Query(None, alias="type"),
    use_case: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Prompt).where(Prompt.is_active == True).order_by(Prompt.name)
    if prompt_type:
        q = q.where(Prompt.type == prompt_type)
    if use_case:
        q = q.where(Prompt.use_case == use_case)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{prompt_id}", response_model=PromptOut)
async def get_prompt(
    prompt_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return prompt
