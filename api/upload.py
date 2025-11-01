"""
Quick fix: Just copy this corrected correct_decision function
and replace it in your upload.py file
"""

# Replace the correct_decision function with this:

@router.post("/decisions/correct", response_model=CorrectionResponse)
async def correct_decision(
    correction: CorrectionRequest,
    db=Depends(get_db)
):
    """
    Correct a document routing decision.
    
    When user places document in different folder, this records the correction
    and the learning engine learns from it.
    
    Args:
        correction: Correction with decision_id and correct folder/filename
        
    Returns:
        CorrectionResponse with success status
    """
    try:
        logger.info(f"Recording correction for decision: {correction.decision_id}")
        
        # Get the decision
        decision = db.query(ProcessingDecision).filter(
            ProcessingDecision.id == correction.decision_id
        ).first()
        
        if not decision:
            raise HTTPException(status_code=404, detail="Decision not found")
        
        # Get the page
        page = db.query(Page).filter(Page.id == decision.page_id).first()
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        
        # Update page with actual folder
        page.assigned_folder = correction.actual_folder
        page.output_filename = correction.actual_filename
        page.processing_status = "organized"
        db.commit()
        
        # Record in learning engine
        learning_engine = LearningEngine(db)
        result = learning_engine.record_correction(
            decision_id=correction.decision_id,
            actual_folder=correction.actual_folder,
            actual_filename=correction.actual_filename
        )
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result['message'])
        
        logger.info(f"Correction recorded and file organized")
        
        return CorrectionResponse(
            success=True,
            message=f"File organized to: {correction.actual_folder}/{correction.actual_filename}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording correction: {e}")
        raise HTTPException(status_code=500, detail=f"Error recording correction: {str(e)}")
