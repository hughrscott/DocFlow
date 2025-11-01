"""
Proper test for Learning Engine with database setup
"""
import uuid
from datetime import datetime
from services.learning_engine import LearningEngine
from database.database import SessionLocal
from database.models import Document, Page

# Get database session
db = SessionLocal()

try:
    # Create a test document first
    doc_id = str(uuid.uuid4())
    test_doc = Document(
        id=doc_id,
        original_filename="test_statement.pdf",
        upload_date=datetime.utcnow(),
        total_pages=2,
        status="processed",
        file_size_bytes=1024
    )
    db.add(test_doc)
    db.commit()
    print(f"✓ Created test document: {doc_id}")
    
    # Create test pages
    page_ids = []
    for i in range(1, 3):
        page_id = str(uuid.uuid4())
        page_ids.append(page_id)
        
        test_page = Page(
            id=page_id,
            document_id=doc_id,
            page_number=i,
            document_type="bank_statement",
            institution="PNC Bank",
            analysis_date=datetime.utcnow(),
            confidence_score=0.85,
            extracted_metadata={"test": "data"},
            assigned_folder="Banking/Personal/PNC",
            output_filename=f"PNC-Stmt-2025-01-Page{i}.pdf",
            processing_status="completed",
            llm_provider_used="ollama",
            llm_model_used="llava:latest"
        )
        db.add(test_page)
    
    db.commit()
    print(f"✓ Created test pages: {page_ids}")
    
    # Now test the learning engine
    engine = LearningEngine(db)
    print("✓ LearningEngine initialized\n")
    
    # Test 1: Record decisions
    print("TEST 1: Record Decisions")
    decisions = [
        {
            "page_id": page_ids[0],
            "proposed_folder": "Banking/Personal/PNC",
            "proposed_filename": "PNC-Stmt-2025-01.pdf",
            "proposed_confidence": 0.85,
            "user_corrected": False
        },
        {
            "page_id": page_ids[1],
            "proposed_folder": "Banking/Personal/Chase",
            "proposed_filename": "Chase-Stmt-2025-01.pdf",
            "proposed_confidence": 0.90,
            "user_corrected": False
        }
    ]
    
    for decision in decisions:
        result = engine.record_decision(**decision)
        print(f"  ✓ {decision['page_id'][:8]}...: {result['message']}")
    
    # Test 2: Calculate accuracy
    print("\nTEST 2: Calculate Accuracy")
    accuracy = engine.calculate_accuracy(period_days=7)
    print(f"  Total decisions: {accuracy['total_decisions']}")
    print(f"  Correct decisions: {accuracy['correct_decisions']}")
    print(f"  Accuracy: {accuracy['accuracy_percentage']}%")
    
    # Test 3: Get learning summary
    print("\nTEST 3: Learning Summary")
    summary = engine.get_learning_summary()
    print(f"  Total processed: {summary['total_decisions_processed']}")
    print(f"  Overall accuracy: {summary['overall_accuracy_percentage']}%")
    print(f"  Recent accuracy: {summary['recent_accuracy_percentage']}%")
    print(f"  Learned folders: {summary['total_learned_folders']}")
    
    print("\n✓ All tests passed!")

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    db.close()
