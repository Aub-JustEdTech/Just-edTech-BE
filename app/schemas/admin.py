from pydantic import BaseModel, EmailStr


class TenantCreateRequest(BaseModel):
    name: str
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


class MemberResponse(BaseModel):
    id: int
    name: str | None = None
    email: str
    role_id: int | None = None
    role_name: str | None = None
    tenant_id: int | None = None
    tenant_name: str | None = None

    model_config = {"from_attributes": True}
