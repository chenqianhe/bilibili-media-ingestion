from sqlalchemy import event
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlmodel import Session, create_engine, select

from app import crud
from app.core.config import settings
from app.models import User, UserCreate
from app.services.text_sanitization import strip_nul_bytes_from_model

engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI), pool_pre_ping=True)


# Make sure all SQLModel models are imported through `app.models` before
# initializing the database so relationship metadata is registered.


@event.listens_for(SQLAlchemySession, "before_flush")
def strip_postgres_forbidden_nul_bytes(
    session: SQLAlchemySession,
    flush_context: object,
    instances: object,
) -> None:
    del flush_context, instances
    for item in list(session.new) + list(session.dirty):
        strip_nul_bytes_from_model(item)


def init_db(session: Session) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        user = crud.create_user(session=session, user_create=user_in)
