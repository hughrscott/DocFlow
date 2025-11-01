"""
SQLAlchemy database models for DocFlow.

Defines the data schema for storing documents, processing decisions,
folder mappings, and learning data.
"""

from sqlalchemy import Column, String, Integer, DateTime, Float, JSON, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()


class Document(Base):
    """
    Represents an uploaded PDF document.
    
    Stores metadata about the original document before processing.
    """
    __tablename__ = "documents"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    original_filename = Column(String, nullable=False)
    upload_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    total_pages = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="processing")  # processing, completed, failed
    error_message = Column(String, nullable=True)
    file_size_bytes = Column(Integer, nullable=False)
    
    def __repr__(self) -> str:
        return f"<Document(id={self.id}, filename={self.original_filename}, pages={self.total_pages})>"


class Page(Base):
    """
    Represents a single page from a document.
    
    Stores the results of AI analysis for each page including
    extracted metadata and routing decisions.
    """
    __tablename__ = "pages"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    
    # AI Analysis Results
    document_type = Column(String, nullable=False)  # e.g., "bank_statement", "tax_bill"
    institution = Column(String, nullable=True)      # e.g., "PNC", "Revolut"
    analysis_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    confidence_score = Column(Float, nullable=False)  # 0.0 to 1.0
    
    # Extracted Metadata (stored as JSON)
    extracted_metadata = Column(JSON, nullable=False, default={})
    """
    Contains extracted data like:
    {
        "date": "2025-01",
        "account_type": "checking",
        "account_holder": "John Doe",
        "account_number_masked": "****1234",
        ...
    }
    """
    
    # Routing Information
    assigned_folder = Column(String, nullable=False)
    output_filename = Column(String, nullable=False)
    processing_status = Column(String, nullable=False, default="completed")  # completed, failed, pending
    processing_error = Column(String, nullable=True)
    
    # Provider Information
    llm_provider_used = Column(String, nullable=False)  # "claude", "ollama", etc.
    llm_model_used = Column(String, nullable=False)
    
    def __repr__(self) -> str:
        return f"<Page(id={self.id}, doc_id={self.document_id}, type={self.document_type})>"


class FolderMapping(Base):
    """
    Stores learned mappings between folder characteristics and document types.
    
    Used by the learning engine to improve categorization over time.
    """
    __tablename__ = "folder_mappings"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_path = Column(String, nullable=False, unique=True)
    
    # Folder Characteristics (learned from content)
    institution_patterns = Column(JSON, nullable=False, default=[])
    document_type_patterns = Column(JSON, nullable=False, default=[])
    keywords = Column(JSON, nullable=False, default=[])
    
    # Learning Metrics
    confidence_score = Column(Float, nullable=False, default=0.0)
    times_used = Column(Integer, nullable=False, default=0)
    times_correct = Column(Integer, nullable=False, default=0)
    last_updated = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Metadata
    created_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_auto_created = Column(Boolean, nullable=False, default=False)
    
    def __repr__(self) -> str:
        return f"<FolderMapping(path={self.folder_path}, confidence={self.confidence_score})>"


class ProcessingDecision(Base):
    """
    Tracks all categorization decisions made by the system.
    
    Used for learning and improving accuracy over time.
    Records both proposed and actual destinations for each page.
    """
    __tablename__ = "processing_decisions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    page_id = Column(String, ForeignKey("pages.id"), nullable=False)
    
    # Proposed Decision
    proposed_folder = Column(String, nullable=False)
    proposed_filename = Column(String, nullable=False)
    proposed_confidence = Column(Float, nullable=False)
    
    # Actual Outcome
    actual_folder = Column(String, nullable=True)  # None if proposal was correct
    actual_filename = Column(String, nullable=True)
    user_corrected = Column(Boolean, nullable=False, default=False)
    
    # Metrics
    decision_correct = Column(Boolean, nullable=True)  # None if not yet verified
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        correct_str = "✓" if self.decision_correct else "✗" if self.decision_correct is False else "?"
        return f"<ProcessingDecision(id={self.id}, correct={correct_str})>"


class LearningMetrics(Base):
    """
    Stores aggregated learning metrics for system performance tracking.
    
    Updated periodically to track accuracy trends over time.
    """
    __tablename__ = "learning_metrics"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Time Period
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Accuracy Metrics
    total_decisions = Column(Integer, nullable=False, default=0)
    correct_decisions = Column(Integer, nullable=False, default=0)
    accuracy_percentage = Column(Float, nullable=False, default=0.0)
    
    # Provider Performance
    claude_accuracy = Column(Float, nullable=True)
    ollama_accuracy = Column(Float, nullable=True)
    
    # Learning Progress
    new_folders_created = Column(Integer, nullable=False, default=0)
    folder_mappings_improved = Column(Integer, nullable=False, default=0)
    
    # Metadata
    recorded_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f"<LearningMetrics(accuracy={self.accuracy_percentage}%, period={self.period_start})>"
