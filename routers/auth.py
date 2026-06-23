from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from schemas import UserCreate, UserOut, Token, TokenResponse, LoginRequest, RefreshRequest, OutOfOfficeUpdate
from auth_utils import (
    verify_password, hash_password,
    create_access_token, create_refresh_token,
    get_current_user,
)
from jose import JWTError, jwt
import os
import models

router = APIRouter()

SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = os.getenv("ALGORITHM", "HS256")


@router.post("/register", response_model=UserOut)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user = models.User(
        firstName=payload.firstName,
        lastName=payload.lastName,
        email=payload.email,
        password=hash_password(payload.password),
        role=payload.role,
        phoneNumber=payload.phoneNumber,
        designation=payload.designation,
        company_id=payload.company_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password):
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
def me(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/me/out-of-office", response_model=UserOut)
def set_out_of_office(
    payload: OutOfOfficeUpdate,
    user_id: int = 0,
    db: Session = Depends(get_db),
):
    """
    Mark yourself out-of-office until a given time and name a delegate.
    While ooo_until is in the future, any approver-group notification meant
    for you is sent to your delegate instead, and your delegate may act on
    your behalf — the action is still recorded against your approver slot.
    Pass ooo_until: null to clear OOO status.
    """
    current_user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.delegate_id is not None:
        if payload.delegate_id == current_user.id:
            raise HTTPException(400, "Cannot delegate to yourself")
        delegate = db.query(models.User).filter(models.User.id == payload.delegate_id).first()
        if not delegate:
            raise HTTPException(404, "Delegate user not found")
    current_user.ooo_until = payload.ooo_until
    current_user.delegate_id = payload.delegate_id
    db.commit()
    db.refresh(current_user)
    return current_user
