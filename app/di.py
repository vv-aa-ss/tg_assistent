from typing import Optional, List
from app.db import Database

_db: Optional[Database] = None
_admin_ids: List[int] = []
_admin_usernames: List[str] = []


def set_dependencies(db: Database, admin_ids: List[int], admin_usernames: List[str] = None) -> None:
	global _db, _admin_ids, _admin_usernames
	_db = db
	_admin_ids = admin_ids
	_admin_usernames = admin_usernames or []


def get_db() -> Database:
	assert _db is not None, "Database is not initialized"
	return _db


def get_admin_ids() -> List[int]:
	return _admin_ids


def get_admin_usernames() -> List[str]:
	return _admin_usernames
