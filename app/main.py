from fastapi import FastAPI

from app.api import admin, auth, query

app = FastAPI(title="ADV RAG", version="0.1.0-lesson-9")
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(query.router)

