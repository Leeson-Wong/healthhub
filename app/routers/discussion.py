"""病情讨论区 API。家属手机直接发帖（无 token 门槛，与录入页一致）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import DiscussionPost, Person
from app.schemas import DiscussionIn, DiscussionOut

router = APIRouter(prefix="/discussion", tags=["discussion"])

CATEGORIES = {"观察", "问题", "医生反馈", "决定", "其他"}


@router.post("", response_model=DiscussionOut, status_code=201)
def create_post(body: DiscussionIn, db: Session = Depends(get_db)):
    if body.category not in CATEGORIES:
        raise HTTPException(422, f"category 必须是 {sorted(CATEGORIES)} 之一")
    pid = body.person_id
    if pid is None:
        if not body.person_ref:
            raise HTTPException(422, "person_id or person_ref required")
        p = db.query(Person).filter_by(external_ref=body.person_ref).first()
        if p is None:
            raise HTTPException(404, f"person_ref '{body.person_ref}' not found")
        pid = p.id
    post = DiscussionPost(person_id=pid, author=body.author.strip() or "家属",
                          category=body.category, content=body.content.strip())
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@router.get("", response_model=list[DiscussionOut])
def list_posts(person_id: int, limit: int = 100, db: Session = Depends(get_db)):
    return (db.query(DiscussionPost).filter_by(person_id=person_id)
            .order_by(DiscussionPost.created_at.desc(), DiscussionPost.id.desc())
            .limit(min(limit, 500)).all())
