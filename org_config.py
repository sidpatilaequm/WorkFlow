from sqlalchemy.orm import Session

from models import OrgConfig


def is_enabled(db: Session, flag: str) -> bool:
    """Reads the single org_config row (id=1) written by backend_java's admin-gated endpoint.
    Fails open (True) if the row is somehow missing, matching that table's own "everything on
    by default" seed — a missing row should never be the reason a real action gets blocked."""
    cfg = db.query(OrgConfig).filter(OrgConfig.id == 1).first()
    if cfg is None:
        return True
    return bool(getattr(cfg, flag, True))
