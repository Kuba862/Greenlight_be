from sqlalchemy import URL, create_engine
from .config import get_settings

settings = get_settings()

database_url = URL.create(
    drivername="postgresql+psycopg",
    username=settings.db_user,
    password=settings.db_password.get_secret_value(),
    host=settings.db_host,
    port=settings.db_port,
    database=settings.db_name,
)


engine = create_engine(
    database_url,
    pool_size=1,
    max_overflow=1,
    pool_timeout=5,
    pool_pre_ping=True,
    hide_parameters=True,
    connect_args={
        "sslmode": "require",
        "connect_timeout": 5,
        "prepare_threshold": None,
    },
)