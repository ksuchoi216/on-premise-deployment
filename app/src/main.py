from fastapi import FastAPI
from app.infrastructure.database import Base, engine
from app.interface.api import router

app = FastAPI()

@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)

app.include_router(router)
