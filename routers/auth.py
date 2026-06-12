from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from schemas import UserCreate, UserOut, Token, TokenResponse, LoginRequest, RefreshRequest
from auth_utils import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    get_current_user,
)
from jose import JWTError, jwt
import os
import models

router = APIRouter()

SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = os.getenv("ALGORITHM", "HS256")


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = models.User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        department=payload.department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Account is inactive")
    token = create_access_token(data={"sub": user.id})
    return Token(access_token=token, user=user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    try:
        data = jwt.decode(payload.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if data.get("type") != "refresh":
            raise cred_exc
        user_id = int(data.get("sub", 0))
    except (JWTError, ValueError):
        raise cred_exc

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return TokenResponse(
        access_token=create_access_token(data={"sub": user.id}),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user
