from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi import Request, HTTPException
import jwt
from app.core.config import settings
from app.models import ClientAccessToken
from app.db.session import SessionLocal

headers={
    "Access-Control-Allow-Origin": "*",  # Allow all origins
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}
class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, exclude_paths: list[str] = None):
        super().__init__(app)
        self.exclude_paths = exclude_paths or []

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if request.method == "OPTIONS":
            return await call_next(request)

        if any(excluded in path for excluded in self.exclude_paths):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        x_user_id = request.headers.get("x-user-id")

        if auth_header:
            if auth_header.startswith("API_KEY ") and x_user_id:
                if "/service/" not in path:
                    return JSONResponse(content={"detail": "Invalid path for API Key authentication"}, status_code=401, headers=headers)
                
                db = SessionLocal()
                client = db.query(ClientAccessToken).filter_by(id=x_user_id.strip()).first()
                if not client or client.token != auth_header.split("API_KEY ")[-1].strip():
                    return JSONResponse(content={"detail": "Invalid API Key or User ID"}, status_code=401, headers=headers)
                return await call_next(request)
            elif not auth_header.startswith("Bearer "):
                return JSONResponse(content={"detail": "Missing or invalid Authorization header"}, status_code=401, headers=headers)
        else:
            return JSONResponse(content={"detail": "Missing Authorization header"}, status_code=401, headers=headers)

        token = auth_header.split("Bearer ")[-1].strip()
        # TODO: validate the user in database
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                audience=settings.JWT_ISSUER,
            )
            # Store claims (user_id, roles, etc.) in request.state
            request.state.user = payload
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token expired"}, headers=headers)
        except jwt.InvalidTokenError:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"}, headers=headers)

        return await call_next(request)
    
def get_current_user(request: Request):
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="User not authenticated")
    return request.state.user

