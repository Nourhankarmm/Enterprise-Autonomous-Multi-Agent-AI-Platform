"""Local durable provider simulator. Never connects to SMTP or external mail APIs."""
import uuid
from sqlalchemy import select,insert
from sqlalchemy.exc import IntegrityError
from ..database import sandbox_messages
from .schemas import WorkflowError

class PreSubmissionFailure(Exception):pass
class UnknownOutcome(Exception):pass

class SandboxMailAdapter:
    supports_idempotency=True
    def __init__(self,engine):self.engine=engine

    def lookup(self,key):
        with self.engine.connect() as db:
            row=db.execute(select(sandbox_messages).where(sandbox_messages.c.idempotency_key==key)).mappings().first()
            return dict(row) if row else None

    def send(self,key,payload,digest):
        existing=self.lookup(key)
        if existing:
            if existing['action_hash']!=digest:raise WorkflowError(409,'Provider key payload mismatch')
            return existing['receipt']
        receipt={'provider':'sandbox','message_id':'sandbox-'+str(uuid.uuid4()),'accepted':True}
        try:
            with self.engine.begin() as db:
                db.execute(insert(sandbox_messages).values(idempotency_key=key,tenant_id=payload['tenant_id'],
                    action_hash=digest,receipt=receipt))
        except IntegrityError:
            existing=self.lookup(key)
            if not existing or existing['action_hash']!=digest:raise WorkflowError(409,'Provider key payload mismatch')
            return existing['receipt']
        return receipt
