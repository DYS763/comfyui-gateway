from .main import app

if __name__ == "__main__":
    import uvicorn

    from .config import settings

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)

