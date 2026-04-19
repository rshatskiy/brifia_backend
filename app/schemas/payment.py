from pydantic import BaseModel
from uuid import UUID


class CreatePaymentRequest(BaseModel):
    plan_id: UUID
    return_url: str | None = None


class CreatePaymentResponse(BaseModel):
    confirmation_url: str
    payment_id: str
