from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.middleware.validator import get_current_user
from app.models import Users, CrmNotes
from app.schemas import CrmNoteCreate, CrmNoteUpdate, CrmNoteOut
from app.utils.pagination import paginate


router = APIRouter(tags=["AGENT NOTES"])

@router.get("/agent-notes")
def list_agent_notes(
	agent_id: Optional[str] = Query(...),
	source_id: Optional[str] = Query(None),
	type: Optional[str] = Query(None),
	sub_type: Optional[str] = Query(None),
	is_private: Optional[bool] = Query(None),
	sortColumn: Optional[str] = Query("time_stamp"),
	sortOrder: Optional[str] = Query("desc"),
	page: int = 1,
	page_size: int = 50,
	db: Session = Depends(get_db),
):
	query = (
		db.query(
			CrmNotes,
			Users.f_name,
			Users.l_name,
			Users.login
		)
		.outerjoin(Users, Users.user_id == CrmNotes.user_id)
	)
	if agent_id:
		query = query.filter(CrmNotes.agent_id == agent_id)
	if source_id:
		query = query.filter(CrmNotes.source_id == source_id)
	if type:
		query = query.filter(CrmNotes.type == type)
	if sub_type:
		query = query.filter(CrmNotes.sub_type == sub_type)
	if is_private is not None:
		query = query.filter(CrmNotes.is_private == is_private)


	paginated = paginate(
		query,
		db,
		model=CrmNotes,
		page=page,
		page_size=page_size,
		sort_column=sortColumn,
		sort_order=sortOrder,
	)

	items = []
	for row in paginated["items"]:
		note_obj, f_name, l_name, login = row
		item_dict = dict(note_obj.__dict__)
		item_dict.pop("_sa_instance_state", None)
		item_dict["owner_full_name"] = f"{f_name or ''} {l_name or ''}".strip()
		item_dict["login"] = login
		item_dict["is_private"] = item_dict.get("is_private")
		item_dict["source_id"] = item_dict.get("source_id")
		item_dict["sub_type"] = item_dict.get("sub_type")
		items.append(item_dict)

	paginated["items"] = items
	return paginated


@router.post("/agent-notes", response_model=CrmNoteOut)
def create_agent_note(
	payload: CrmNoteCreate,
	db: Session = Depends(get_db),
	current_user: dict = Depends(get_current_user),
):
	new_note = CrmNotes(
		type=payload.type,
		description=payload.description,
		agent_id=str(payload.agent_id) if payload.agent_id else None,
		is_private=payload.is_private if payload.is_private is not None else False,
		source_id=str(payload.source_id) if payload.source_id else None,
		user_id=current_user["user_id"],
		agent_npn=payload.agent_npn if payload.agent_npn else None,
		time_stamp=datetime.now(timezone.utc),
	)
	db.add(new_note)
	db.flush()
	db.refresh(new_note)
	return new_note


@router.patch("/agent-notes", response_model=CrmNoteOut)
def update_agent_note(
	payload: CrmNoteUpdate,
	db: Session = Depends(get_db),
	current_user: dict = Depends(get_current_user),
):
	try:
		current = db.query(CrmNotes).filter(CrmNotes.pk_id == str(payload.pk_id)).first()
	except SQLAlchemyError:
		raise HTTPException(status_code=400, detail="Invalid pk_id format")
	if not current:
		raise HTTPException(status_code=404, detail="Note not found")
	if str(current.user_id) != str(current_user.get("user_id")) and current_user.get("role") != "admin":
		raise HTTPException(status_code=403, detail="Not authorized to update this note")

	if payload.type is not None:
		current.type = payload.type
	if payload.description is not None:
		current.description = payload.description
	if payload.is_private is not None:
		current.is_private = payload.is_private
	if payload.sub_type is not None:
		current.sub_type = payload.sub_type
	current.time_stamp = datetime.now(timezone.utc)

	db.flush()
	db.refresh(current)
	return current


@router.delete("/agent-notes/{pk_id}")
def delete_agent_note(
	pk_id: str,
	db: Session = Depends(get_db),
	current_user: dict = Depends(get_current_user),
):
	note = db.query(CrmNotes).filter(CrmNotes.pk_id == pk_id).first()
	if not note:
		raise HTTPException(status_code=404, detail="Note not found")

	if str(note.user_id) != str(current_user.get("user_id")) and current_user.get("role") != "admin":
		raise HTTPException(status_code=403, detail="Not authorized to delete this note")

	db.delete(note)
	db.flush()
	return {"detail": "Note deleted successfully"}

