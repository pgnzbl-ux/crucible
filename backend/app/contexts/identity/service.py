from app.core.security import create_access_token, hash_password, verify_password
from .models import User
from .repository import IdentityRepository
from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse


class IdentityService:
    def __init__(self, repo: IdentityRepository):
        self.repo = repo

    async def register(self, request: RegisterRequest) -> UserResponse:
        existing = await self.repo.get_by_email(request.email)
        if existing:
            raise ValueError("邮箱已被注册")

        user = User(
            email=request.email,
            password_hash=hash_password(request.password),
            display_name=request.display_name,
        )
        user = await self.repo.create(user)
        return UserResponse.model_validate(user)

    async def login(self, request: LoginRequest) -> TokenResponse:
        user = await self.repo.get_by_email(request.email)
        if not user or not user.is_active:
            raise ValueError("邮箱或密码错误")
        if not verify_password(request.password, user.password_hash):
            raise ValueError("邮箱或密码错误")

        token = create_access_token(user.id, user.email)
        return TokenResponse(
            access_token=token,
            user=UserResponse.model_validate(user),
        )

    async def get_current_user(self, token: str) -> UserResponse | None:
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        if not payload:
            return None
        user = await self.repo.get_by_id(payload["sub"])
        if not user or not user.is_active:
            return None
        return UserResponse.model_validate(user)
