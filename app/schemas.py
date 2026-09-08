from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
    new_password_confirm: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    avatar_url: str | None = None
    created_at: str | None = None


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1)


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PageSelection(BaseModel):
    mode: str = "all"
    first_count: int = 1
    last_count: int = 1
    specific_pages: list[int] = []


class Position(BaseModel):
    x: float = 0
    y: float = 0
    width: float = 100
    height: float = 100
    page_width: float = 595
    page_height: float = 842


class LogoPosition(BaseModel):
    position: Position = Position()
    position_rest: Position | None = None


class JobConfig(BaseModel):
    page_selection: PageSelection = PageSelection()
    position: Position = Position()
    position_rest: Position | None = None
    position_mode: str = "single"
    mode: str = "multiple_pdfs"
    pos_strategy: str = "shared"
    logo_positions: dict[str, LogoPosition] = {}


class JobCreateRequest(BaseModel):
    config: JobConfig


class JobResponse(BaseModel):
    id: str
    status: str
    total_files: int
    processed_files: int
    config: dict
    error_message: str | None = None
    created_at: str | None = None


class JobStatusResponse(BaseModel):
    id: str
    status: str
    total_files: int
    processed_files: int
    error_message: str | None = None


class ProcessingListItem(BaseModel):
    id: str
    title: str | None
    display_label: str
    status: str
    mode: str
    total_files: int
    processed_files: int
    created_at: str | None
    is_expired: bool


class ProcessingListResponse(BaseModel):
    items: list[ProcessingListItem]
    total: int


class PresetResponse(BaseModel):
    id: str
    name: str
    mode: str = "multiple_pdfs"
    pos_strategy: str = "shared"
    page_selection: dict
    position: dict
    position_rest: dict | None
    position_mode: str
    created_at: str | None


class PresetCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    mode: str = "multiple_pdfs"
    pos_strategy: str = "shared"
    page_selection: PageSelection
    position: Position
    position_rest: Position | None = None
    position_mode: str = "single"


class LogoResponse(BaseModel):
    id: str
    name: str
    aspect_ratio: float | None
    created_at: str | None


class LogoRenameRequest(BaseModel):
    name: str = Field(min_length=1)
