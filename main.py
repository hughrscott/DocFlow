"""
DocFlow FastAPI Application.

Main entry point for the DocFlow backend API.
Combines all services into a unified REST API.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

# Import routers
# from api.upload import router as upload_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for app startup and shutdown.
    """
    # Startup
    logger.info("DocFlow API starting up...")
    yield
    # Shutdown
    logger.info("DocFlow API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="DocFlow API",
    description="Intelligent document organization and routing system",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# app.include_router(upload_router)


@app.get("/")
async def root():
    """Root endpoint - API info"""
    return {
        "name": "DocFlow API",
        "version": "1.0.0",
        "description": "Intelligent document organization and routing",
        "endpoints": {
            "upload": "POST /api/v1/documents/upload",
            "correct": "POST /api/v1/decisions/correct",
            "accuracy": "GET /api/v1/accuracy",
            "learning": "GET /api/v1/learning",
            "recommendations": "GET /api/v1/recommendations",
            "health": "GET /api/v1/health",
            "document": "GET /api/v1/documents/{document_id}",
            "metrics": "POST /api/v1/metrics/record"
        }
    }


@app.get("/api/v1/status")
async def api_status():
    """Get API status"""
    return {
        "status": "operational",
        "version": "1.0.0",
        "timestamp": __import__('datetime').datetime.utcnow().isoformat()
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle uncaught exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting DocFlow API server...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
