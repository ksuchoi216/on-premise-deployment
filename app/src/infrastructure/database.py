import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

class DatabaseManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        database_url = os.environ["DATABASE_URL"]
        # connection pool 4개로 제한 (pool_size=4)
        # max_overflow=0 으로 설정하여 최대 연결 수를 초과하지 않도록 함
        self.engine = create_engine(database_url, pool_size=4, max_overflow=0)
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

db_manager = DatabaseManager()
engine = db_manager.engine
Base = declarative_base()

def get_db():
    db = db_manager.SessionLocal()
    try:
        yield db
    finally:
        db.close()
