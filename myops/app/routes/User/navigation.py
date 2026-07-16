from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.Users import DashboardPermissions
from app.utils.util import build_project_tree

router = APIRouter(tags=["USER NAVIGATION ROUTES"])

@router.get("/navigation")
async def get_navigation_tree(db: Session = Depends(get_db)):
    # Fetch all active navigations ordered by parent_id and order_num
    all_navigations = (db.query(DashboardPermissions).filter(DashboardPermissions.active == True).all())
    result = build_project_tree(all_navigations)

    return result



