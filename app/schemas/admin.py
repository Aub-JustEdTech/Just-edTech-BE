from pydantic import BaseModel, EmailStr


class TenantCreateRequest(BaseModel):
    name: str
    domain: str | None = None
    logo_url: str | None = None


class TenantResponse(BaseModel):
    id: int
    name: str
    domain: str
    logo_url: str | None = None

    model_config = {"from_attributes": True}


class InviteAdminRequest(BaseModel):
    email: EmailStr


class InviteAdminResponse(BaseModel):
    email: str
    status: str  # "sent" | "already_member" | "cooldown"
