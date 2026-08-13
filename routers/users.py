from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models
from schemas import UserCreate, UserOut
from auth_utils import hash_password
import string
import random

def _uid(prefix: str = "") -> str:
    chars = string.ascii_uppercase + string.digits
    return prefix + "".join(random.choices(chars, k=6))

router = APIRouter()

@router.get("/list")
def list_users(db: Session = Depends(get_db)):
    users = db.query(
        models.User, 
        models.Employee.employee_code, 
        models.Employee.dept_code,
        models.Department.name.label("dept_name")
    ).outerjoin(
        models.Employee, models.User.id == models.Employee.user_id
    ).outerjoin(
        models.Department, models.Employee.dept_code == models.Department.dept_code
    ).all()

    return [{
        "userId": u.User.id,
        "email": u.User.email,
        "firstName": u.User.firstName,
        "lastName": u.User.lastName,
        "phoneNumber": u.User.phoneNumber,
        "role": u.User.role.value if u.User.role else "ADMIN",
        "employeeCode": u.employee_code,
        "deptCode": u.dept_code,
        "deptName": u.dept_name
    } for u in users]

@router.post("/create")
def create_user(payload: dict, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.get("email")).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # map frontend roles to models.UserRole if possible
    role_str = payload.get("role", "ADMIN")
    if role_str == "SUPER_ADMIN":
        role_val = models.UserRole.SUPER_ADMIN
    elif role_str == "ADMIN":
        role_val = models.UserRole.admin
    elif role_str == "PROCUREMENT":
        role_val = models.UserRole.approver
    elif role_str == "QUALITY_AUDITOR":
        role_val = models.UserRole.approver
    elif role_str == "EMPLOYEE":
        role_val = models.UserRole.EMPLOYEE
    elif role_str == "PURCHASE_DEPT":
        role_val = models.UserRole.PURCHASE_DEPT
    else:
        role_val = models.UserRole.submitter

    pwd = payload.get("password", "User@123")
    dept_code = payload.get("deptCode", "UNKNOWN")
    
    # Generate deterministic employee code
    count = db.query(models.Employee).filter(models.Employee.dept_code == dept_code).count() + 1
    emp_code = f"EMP-{dept_code}-{count:04d}"
    
    user = models.User(
        firstName=payload.get("firstName", ""),
        lastName=payload.get("lastName", ""),
        email=payload.get("email", ""),
        password=hash_password(pwd),
        role=role_val,
        phoneNumber=payload.get("phoneNumber", ""),
        is_active=True,
        super_admin_id=1,
        dept_code=dept_code
        # employee_code will be set after creating employee
    )
    db.add(user)
    db.flush() # get user.id
    
    # Create employee linked to user, without storing password!
    emp = models.Employee(
        employee_code=emp_code,
        name=f"{payload.get('firstName', '')} {payload.get('lastName', '')}".strip(),
        email=payload.get("email", ""),
        user_id=user.id,
        dept_code=dept_code
    )
    db.add(emp)
    db.flush()
    
    # Now that employee is created, update the user with the employee_code
    user.employee_code = emp_code
    
    db.commit()
    db.refresh(user)
    return {"userId": user.id, "email": user.email, "message": "User created successfully"}

@router.post("/{user_id}/deactivate/")
def deactivate_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"message": "User deactivated"}
