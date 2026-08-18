"""Runtime settings, editable from the dashboard.

Anything an operator should be able to change without a deploy lives here
(auto-reply text, default links). Anything that changes what the app *is*
(credentials, pricing, brand) belongs in .env — those should require a deploy
and leave a trail.

Note: the reference system let an unauthenticated visitor rewrite the auto-reply
and the default outbound link. Whatever you expose here is editable by anyone
holding a session, so keep secrets out of it.
"""

from sqlalchemy import Column, Integer, String, Text
from app.core.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    description = Column(String(500), nullable=True)


AUTO_REPLY_KEY = "auto_reply_message"


def get_setting(db, key: str, default: str = None) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row and row.value else default


def set_setting(db, key: str, value: str, description: str = None) -> AppSetting:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        row = AppSetting(key=key, value=value, description=description)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row
