"""
Learning Engine Service for DocFlow.

Tracks document routing decisions, learns from corrections, and improves
categorization accuracy over time. This is what makes DocFlow smarter.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from database.models import (
    ProcessingDecision,
    LearningMetrics,
    FolderMapping
)
from database.database import get_db
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LearningEngine:
    """
    Learns from document routing decisions to improve accuracy.
    
    Responsibilities:
    - Track routing decisions (proposed vs actual)
    - Learn from user corrections
    - Update folder mapping confidence scores
    - Generate accuracy metrics
    - Improve future predictions
    """
    
    def __init__(self, db: Session):
        """
        Initialize learning engine with database connection.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        logger.info("LearningEngine initialized")
    
    def record_decision(
        self,
        page_id: str,
        proposed_folder: str,
        proposed_filename: str,
        proposed_confidence: float,
        actual_folder: Optional[str] = None,
        actual_filename: Optional[str] = None,
        user_corrected: bool = False
    ) -> Dict[str, Any]:
        """
        Record a document routing decision.
        
        Called when a document is analyzed and routed. Can be updated later
        if user corrects the decision.
        
        Args:
            page_id: Unique ID of the page being routed
            proposed_folder: Folder proposed by AI
            proposed_filename: Filename proposed by AI
            proposed_confidence: Confidence score (0-1)
            actual_folder: Actual folder if user corrected (optional)
            actual_filename: Actual filename if user corrected (optional)
            user_corrected: Whether user corrected the decision
            
        Returns:
            Dictionary with decision record info
        """
        try:
            logger.info(
                f"Recording decision for page {page_id}: "
                f"proposed={proposed_folder}, user_corrected={user_corrected}"
            )
            
            # Create decision record
            decision = ProcessingDecision(
                page_id=page_id,
                proposed_folder=proposed_folder,
                proposed_filename=proposed_filename,
                proposed_confidence=proposed_confidence,
                actual_folder=actual_folder,
                actual_filename=actual_filename,
                user_corrected=user_corrected,
                decision_correct=(actual_folder is None or proposed_folder == actual_folder),
                timestamp=datetime.utcnow()
            )
            
            self.db.add(decision)
            self.db.commit()
            
            logger.info(f"Decision recorded with ID: {decision.id}")
            
            return {
                "success": True,
                "decision_id": decision.id,
                "message": "Decision recorded successfully"
            }
        
        except Exception as e:
            logger.error(f"Error recording decision: {e}")
            self.db.rollback()
            return {
                "success": False,
                "decision_id": None,
                "message": f"Error recording decision: {str(e)}"
            }
    
    def record_correction(
        self,
        decision_id: str,
        actual_folder: str,
        actual_filename: str
    ) -> Dict[str, Any]:
        """
        Record a user correction to a previous decision.
        
        Called when user corrects where a document was routed.
        Updates the decision and triggers learning.
        
        Args:
            decision_id: ID of decision to correct
            actual_folder: Correct folder
            actual_filename: Correct filename
            
        Returns:
            Dictionary with update info
        """
        try:
            logger.info(f"Recording correction for decision {decision_id}")
            
            # Find decision
            decision = self.db.query(ProcessingDecision).filter(
                ProcessingDecision.id == decision_id
            ).first()
            
            if not decision:
                error_msg = f"Decision not found: {decision_id}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "message": error_msg
                }
            
            # Update decision
            decision.actual_folder = actual_folder
            decision.actual_filename = actual_filename
            decision.user_corrected = True
            decision.decision_correct = (decision.proposed_folder == actual_folder)
            
            self.db.commit()
            
            logger.info(
                f"Correction recorded: {decision.proposed_folder} → {actual_folder}"
            )
            
            # Learn from this correction
            self._learn_from_correction(decision)
            
            return {
                "success": True,
                "message": "Correction recorded and learned from"
            }
        
        except Exception as e:
            logger.error(f"Error recording correction: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": f"Error recording correction: {str(e)}"
            }
    
    def _learn_from_correction(self, decision: ProcessingDecision) -> None:
        """
        Learn from a correction to improve future decisions.
        
        Args:
            decision: The corrected decision
        """
        try:
            # If decision was wrong, update folder mapping confidence
            if not decision.decision_correct:
                logger.debug(f"Learning from incorrect decision: {decision.id}")
                
                # Find related folder mapping
                folder_mapping = self.db.query(FolderMapping).filter(
                    FolderMapping.folder_path == decision.proposed_folder
                ).first()
                
                if folder_mapping:
                    # Decrease confidence for this folder
                    folder_mapping.times_used += 1
                    # Don't increment times_correct since this was wrong
                    new_confidence = folder_mapping.times_correct / folder_mapping.times_used
                    folder_mapping.confidence_score = max(0.0, new_confidence)
                    folder_mapping.last_updated = datetime.utcnow()
                    
                    self.db.commit()
                    logger.info(
                        f"Updated folder mapping for {decision.proposed_folder}: "
                        f"confidence={new_confidence:.2f}"
                    )
            else:
                # Decision was correct, increase confidence
                logger.debug(f"Learning from correct decision: {decision.id}")
                
                folder_mapping = self.db.query(FolderMapping).filter(
                    FolderMapping.folder_path == decision.proposed_folder
                ).first()
                
                if folder_mapping:
                    folder_mapping.times_used += 1
                    folder_mapping.times_correct += 1
                    new_confidence = folder_mapping.times_correct / folder_mapping.times_used
                    folder_mapping.confidence_score = min(1.0, new_confidence)
                    folder_mapping.last_updated = datetime.utcnow()
                    
                    self.db.commit()
                    logger.info(
                        f"Updated folder mapping for {decision.proposed_folder}: "
                        f"confidence={new_confidence:.2f}"
                    )
        
        except Exception as e:
            logger.error(f"Error learning from correction: {e}")
    
    def calculate_accuracy(
        self,
        period_days: int = 7
    ) -> Dict[str, Any]:
        """
        Calculate accuracy metrics for a time period.
        
        Args:
            period_days: Number of days to analyze (default: last 7 days)
            
        Returns:
            Dictionary with accuracy metrics
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=period_days)
            
            logger.info(f"Calculating accuracy for last {period_days} days")
            
            # Query decisions in period
            decisions = self.db.query(ProcessingDecision).filter(
                ProcessingDecision.timestamp >= cutoff_date
            ).all()
            
            if not decisions:
                logger.warning(f"No decisions found in last {period_days} days")
                return {
                    "period_days": period_days,
                    "total_decisions": 0,
                    "correct_decisions": 0,
                    "accuracy_percentage": 0.0,
                    "message": "No data available for this period"
                }
            
            # Count correct decisions
            correct = sum(1 for d in decisions if d.decision_correct)
            total = len(decisions)
            accuracy = (correct / total * 100) if total > 0 else 0.0
            
            logger.info(
                f"Accuracy: {correct}/{total} = {accuracy:.1f}%"
            )
            
            return {
                "period_days": period_days,
                "total_decisions": total,
                "correct_decisions": correct,
                "accuracy_percentage": round(accuracy, 2),
                "decisions_analyzed": total,
                "start_date": cutoff_date.isoformat(),
                "end_date": datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Error calculating accuracy: {e}")
            return {
                "period_days": period_days,
                "total_decisions": 0,
                "correct_decisions": 0,
                "accuracy_percentage": 0.0,
                "error": str(e)
            }
    
    def record_metrics(
        self,
        period_days: int = 7
    ) -> Dict[str, Any]:
        """
        Record learning metrics for a period into the database.
        
        Creates a LearningMetrics record with accuracy, new folders created, etc.
        
        Args:
            period_days: Number of days for this metrics period
            
        Returns:
            Dictionary with recorded metrics
        """
        try:
            logger.info(f"Recording metrics for {period_days} day period")
            
            # Calculate accuracy
            accuracy_data = self.calculate_accuracy(period_days)
            
            if accuracy_data.get('total_decisions', 0) == 0:
                logger.warning("No decisions to record metrics for")
                return {
                    "success": False,
                    "message": "No decisions in period"
                }
            
            # Count new folders created
            cutoff_date = datetime.utcnow() - timedelta(days=period_days)
            new_folders = self.db.query(FolderMapping).filter(
                FolderMapping.created_date >= cutoff_date,
                FolderMapping.is_auto_created == True
            ).count()
            
            # Count improved folders (increased confidence)
            improved_folders = self.db.query(FolderMapping).filter(
                FolderMapping.last_updated >= cutoff_date,
                FolderMapping.confidence_score >= 0.8
            ).count()
            
            # Create metrics record
            metrics = LearningMetrics(
                period_start=cutoff_date,
                period_end=datetime.utcnow(),
                total_decisions=accuracy_data['total_decisions'],
                correct_decisions=accuracy_data['correct_decisions'],
                accuracy_percentage=accuracy_data['accuracy_percentage'],
                new_folders_created=new_folders,
                folder_mappings_improved=improved_folders,
                recorded_date=datetime.utcnow()
            )
            
            self.db.add(metrics)
            self.db.commit()
            
            logger.info(
                f"Metrics recorded: accuracy={accuracy_data['accuracy_percentage']:.1f}%, "
                f"new_folders={new_folders}, improved={improved_folders}"
            )
            
            return {
                "success": True,
                "metrics_id": metrics.id,
                "accuracy_percentage": accuracy_data['accuracy_percentage'],
                "total_decisions": accuracy_data['total_decisions'],
                "new_folders_created": new_folders,
                "folder_mappings_improved": improved_folders
            }
        
        except Exception as e:
            logger.error(f"Error recording metrics: {e}")
            self.db.rollback()
            return {
                "success": False,
                "message": f"Error recording metrics: {str(e)}"
            }
    
    def get_folder_recommendations(
        self,
        min_confidence: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Get high-confidence folder mappings as recommendations.
        
        Useful for suggesting folders based on what works well.
        
        Args:
            min_confidence: Minimum confidence threshold (0-1)
            
        Returns:
            List of recommended folder mappings
        """
        try:
            logger.info(f"Getting folder recommendations (min_confidence={min_confidence})")
            
            mappings = self.db.query(FolderMapping).filter(
                FolderMapping.confidence_score >= min_confidence,
                FolderMapping.times_used >= 2  # At least 2 uses
            ).order_by(
                FolderMapping.confidence_score.desc()
            ).all()
            
            recommendations = []
            for mapping in mappings:
                recommendations.append({
                    "folder_path": mapping.folder_path,
                    "confidence_score": mapping.confidence_score,
                    "times_used": mapping.times_used,
                    "times_correct": mapping.times_correct,
                    "accuracy_rate": mapping.times_correct / mapping.times_used if mapping.times_used > 0 else 0,
                    "last_updated": mapping.last_updated.isoformat() if mapping.last_updated else None
                })
            
            logger.info(f"Found {len(recommendations)} high-confidence recommendations")
            return recommendations
        
        except Exception as e:
            logger.error(f"Error getting folder recommendations: {e}")
            return []
    
    def get_learning_summary(self) -> Dict[str, Any]:
        """
        Get a summary of learning progress.
        
        Returns:
            Dictionary with learning statistics
        """
        try:
            logger.info("Getting learning summary")
            
            # Total decisions
            total_decisions = self.db.query(ProcessingDecision).count()
            
            # Recent accuracy (last 7 days)
            recent_accuracy = self.calculate_accuracy(period_days=7)
            
            # Overall accuracy
            all_decisions = self.db.query(ProcessingDecision).all()
            if all_decisions:
                overall_correct = sum(1 for d in all_decisions if d.decision_correct)
                overall_accuracy = (overall_correct / len(all_decisions) * 100)
            else:
                overall_accuracy = 0.0
            
            # High confidence folders
            high_confidence = self.db.query(FolderMapping).filter(
                FolderMapping.confidence_score >= 0.9
            ).count()
            
            # Total folders learned
            total_folders = self.db.query(FolderMapping).count()
            
            summary = {
                "total_decisions_processed": total_decisions,
                "overall_accuracy_percentage": round(overall_accuracy, 2),
                "recent_accuracy_percentage": recent_accuracy['accuracy_percentage'],
                "recent_period_days": 7,
                "total_learned_folders": total_folders,
                "high_confidence_folders": high_confidence,
                "learning_confidence": round(high_confidence / total_folders * 100, 2) if total_folders > 0 else 0
            }
            
            logger.info(f"Learning summary: {summary['overall_accuracy_percentage']}% accuracy")
            return summary
        
        except Exception as e:
            logger.error(f"Error getting learning summary: {e}")
            return {
                "error": str(e),
                "total_decisions_processed": 0,
                "overall_accuracy_percentage": 0.0
            }
    
    def export_learned_patterns(self) -> Dict[str, Any]:
        """
        Export learned patterns for analysis or sharing.
        
        Returns:
            Dictionary with all learned folder mappings and patterns
        """
        try:
            logger.info("Exporting learned patterns")
            
            folders = self.db.query(FolderMapping).all()
            
            patterns = {
                "exported_date": datetime.utcnow().isoformat(),
                "total_folders": len(folders),
                "folders": []
            }
            
            for folder in folders:
                patterns["folders"].append({
                    "folder_path": folder.folder_path,
                    "confidence_score": folder.confidence_score,
                    "times_used": folder.times_used,
                    "times_correct": folder.times_correct,
                    "accuracy_rate": folder.times_correct / folder.times_used if folder.times_used > 0 else 0,
                    "institution_patterns": folder.institution_patterns,
                    "document_type_patterns": folder.document_type_patterns,
                    "keywords": folder.keywords
                })
            
            logger.info(f"Exported {len(folders)} folder patterns")
            return patterns
        
        except Exception as e:
            logger.error(f"Error exporting patterns: {e}")
            return {
                "error": str(e),
                "total_folders": 0,
                "folders": []
            }
