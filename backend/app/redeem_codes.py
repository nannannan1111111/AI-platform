"""Single-use balance redemption codes."""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from dataclasses import asdict, dataclass
from threading import Lock
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.credits import CreditAccounting

_metadata = MetaData()
_codes = Table(
    "redeem_codes", _metadata,
    Column("id", String(36), primary_key=True), Column("code_hash", String(64), unique=True, nullable=False),
    Column("code_hint", String(16), nullable=False), Column("credit_units", BigInteger, nullable=False),
    Column("status", String(16), nullable=False), Column("expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("created_by", String(255)),
    Column("redeemed_at", DateTime(timezone=True)), Column("redeemed_by", String(36)),
)

@dataclass(frozen=True)
class RedeemCode:
    code_id: str; code_hint: str; credits: str; status: str; expires_at: datetime | None
    created_at: datetime; redeemed_at: datetime | None = None; redeemed_by: str | None = None

class RedeemCodeError(Exception): pass
class RedeemCodeNotFound(RedeemCodeError): pass
class RedeemCodeUnavailable(RedeemCodeError): pass

def _hash(code: str) -> str: return hashlib.sha256(code.strip().upper().encode()).hexdigest()
def _new_code() -> str: return "PW-" + secrets.token_urlsafe(12).upper().replace("_", "").replace("-", "")

class InMemoryRedeemCodes:
    def __init__(self, credits: CreditAccounting, *, clock: Callable[[], datetime] | None = None) -> None:
        self.credits, self.clock = credits, clock or (lambda: datetime.now(UTC)); self._rows = {}; self._plain = {}; self._lock = Lock()
    def create(self, count: int, credits: str, *, expires_at: datetime | None = None, created_by: str | None = None) -> list[dict[str, object]]:
        from app.credits._amounts import credit_units, format_credits
        units = credit_units(credits); now = self.clock(); out = []
        with self._lock:
            for _ in range(count):
                code = _new_code(); row = RedeemCode(str(uuid4()), code[-6:], format_credits(units), "active", expires_at, now)
                self._rows[row.code_id] = row; self._plain[row.code_id] = code; out.append({**asdict(row), "code": code})
        return out
    def list(self) -> list[dict[str, object]]: return [asdict(r) for r in self._rows.values()]
    def disable(self, code_id: str) -> dict[str, object]:
        with self._lock:
            row = self._rows.get(code_id)
            if not row: raise RedeemCodeNotFound(code_id)
            self._rows[code_id] = RedeemCode(row.code_id,row.code_hint,row.credits,"disabled",row.expires_at,row.created_at,row.redeemed_at,row.redeemed_by)
            return asdict(self._rows[code_id])
    def redeem(self, code: str, account_space_id: str) -> dict[str, object]:
        with self._lock:
            row = next((r for r in self._rows.values() if _hash(self._plain[r.code_id]) == _hash(code)), None)
            if not row: raise RedeemCodeNotFound(code)
            if row.status != "active" or (row.expires_at and row.expires_at <= self.clock()): raise RedeemCodeUnavailable()
            posting = self.credits.record_admin_grant(account_space_id, row.credits, grant_reference=f"redeem-code:{row.code_id}", reason="兑换码兑换", occurred_at=self.clock())
            self._rows[row.code_id] = RedeemCode(row.code_id,row.code_hint,row.credits,"redeemed",row.expires_at,row.created_at,self.clock(),account_space_id)
            return {"code_id": row.code_id, "credits": row.credits, "posting": asdict(posting)}

class SqlAlchemyRedeemCodes(InMemoryRedeemCodes):
    def __init__(self, sessions: sessionmaker[Session], credits: CreditAccounting, *, clock=None):
        self.sessions, self.credits, self.clock = sessions, credits, clock or (lambda: datetime.now(UTC))
    @classmethod
    def for_database_url(cls, database_url: str, credits: CreditAccounting, *, clock=None):
        engine = create_engine(database_url); return cls(sessionmaker(engine, expire_on_commit=False), credits, clock=clock)
    def create(self, count, credits, *, expires_at=None, created_by=None):
        from app.credits._amounts import credit_units, format_credits
        units=credit_units(credits); now=self.clock(); out=[]
        with self.sessions.begin() as db:
            for _ in range(count):
                code=_new_code(); rid=str(uuid4()); db.execute(insert(_codes).values(id=rid,code_hash=_hash(code),code_hint=code[-6:],credit_units=units,status="active",expires_at=expires_at,created_at=now,created_by=created_by)); out.append({"code_id":rid,"code":code,"code_hint":code[-6:],"credits":format_credits(units),"status":"active","expires_at":expires_at,"created_at":now})
        return out
    def list(self):
        with self.sessions() as db: return [dict(r) for r in db.execute(select(_codes).order_by(_codes.c.created_at.desc())).mappings()]
    def disable(self, code_id):
        with self.sessions.begin() as db:
            result=db.execute(update(_codes).where(_codes.c.id==code_id,_codes.c.status=="active").values(status="disabled"))
            if result.rowcount != 1: raise RedeemCodeNotFound(code_id)
        return next(x for x in self.list() if x["id"]==code_id)
    def redeem(self, code, account_space_id):
        with self.sessions.begin() as db:
            row=db.execute(select(_codes).where(_codes.c.code_hash==_hash(code)).with_for_update()).mappings().first()
            if not row: raise RedeemCodeNotFound(code)
            if row["status"]!="active" or (row["expires_at"] and row["expires_at"]<=self.clock()): raise RedeemCodeUnavailable()
            posting=self.credits.record_admin_grant(account_space_id, f'{row["credit_units"] / 10000:.4f}', grant_reference=f'redeem-code:{row["id"]}', reason="兑换码兑换", occurred_at=self.clock())
            db.execute(update(_codes).where(_codes.c.id==row["id"]).values(status="redeemed",redeemed_at=self.clock(),redeemed_by=account_space_id))
            return {"code_id":row["id"],"credits":f'{row["credit_units"] / 10000:.4f}',"posting":asdict(posting)}
