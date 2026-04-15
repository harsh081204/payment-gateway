from fastapi import FastAPI
from app.routers import charges

app = FastAPI()

app.include_router(charges.router, prefix="/charges", tags=["charges"])