"""
Database initialization and session management for DocFlow.

Handles SQLAlchemy setup, session creation, and database initialization.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from database.models import Base
from config.settings import settings
import logging

logger = logging.getLogger(__name__)


# Create database engine
if settings.database_url.startswith("sqlite"):
    # SQLite specific configuration for better concurrency
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=settings.debug,
    )
    
    # Enable foreign keys for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # PostgreSQL or other database
    engine = create_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """
    Initialize database by creating all tables.
    
    Should be called on application startup.
    Creates all tables defined in Base.metadata if they don't exist.
    """
    try:
        logger.info(f"Initializing database at {settings.database_url}")
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialization complete")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def get_db() -> Session:
    """
    Get a database session.
    
    Used as a FastAPI dependency for endpoints that need database access.
    
    Returns:
        SQLAlchemy Session instance
        
    Example:
        @app.get("/documents")
        async def list_documents(db: Session = Depends(get_db)):
            return db.query(Document).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def async_get_db():
    """
    Async version of get_db for async endpoints.
    
    Yields:
        SQLAlchemy Session instance
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def close_db() -> None:
    """
    Close all database connections.
    
    Should be called on application shutdown.
    """
    try:
        engine.dispose()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database: {e}")
