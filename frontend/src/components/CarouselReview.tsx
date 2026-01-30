import React, { useState, useEffect, useCallback } from 'react';
import { getDocument, correctPage, reanalyzePage, applySequenceProposed } from '../services/api';

type PageResult = {
  page_id?: string;
  page_number: number;
  document_type: string;
  institution?: string | null;
  date?: string | null;
  confidence_score: number;
  folder: string;
  filename: string;
  proposed_folder?: string | null;
  proposed_filename?: string | null;
  provider_used?: string | null;
  model_used?: string | null;
  sequence_id?: string | null;
  success: boolean;
  error?: string | null;
};

type DocDetail = {
  document_id: string;
  original_filename: string;
  status: string;
  total_pages: number;
  pages_done: number;
  last_error?: string | null;
  last_error_at?: string | null;
  pages: PageResult[];
  doc_level?: {
    document_class?: string;
    confidence?: number;
    issuer?: string | null;
    recipient?: string | null;
    period?: string | null;
    identifiers?: Record<string, any>;
    salient_facts?: { label: string; value: any }[];
    proposed_filename?: string | null;
    rationale?: string | null;
  } | null;
};

type ToastType = 'success' | 'error' | 'info';

type CarouselReviewProps = {
  documentId: string;
  onClose: () => void;
  onComplete: () => void;
  showToast: (text: string, type?: ToastType) => void;
};

// Document type options from the backend taxonomy
const DOCUMENT_TYPES = [
  'bank_statement', 'credit_card_statement', 'mortgage_statement', 'loan_document',
  'tax_bill', 'property_tax', 'income_tax',
  'utility_bill', 'electric_bill', 'gas_bill', 'water_bill', 'internet_bill', 'mobile_bill',
  'insurance_policy', 'insurance_claim', 'auto_insurance', 'health_insurance',
  'medical_record', 'prescription', 'health_report',
  'employment_contract', 'paystub',
  'invoice', 'receipt', 'payment_record',
  'affidavit', 'legal_notice', 'court_document',
  'other', 'unknown'
];

const CarouselReview: React.FC<CarouselReviewProps> = ({ documentId, onClose, onComplete }) => {
  const [document, setDocument] = useState<DocDetail | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [classification, setClassification] = useState('');
  const [folder, setFolder] = useState('');
  const [filename, setFilename] = useState('');
  const [isNewFolder, setIsNewFolder] = useState(false);

  // Load document data
  useEffect(() => {
    const loadDocument = async () => {
      try {
        setLoading(true);
        const docData = await getDocument(documentId);
        setDocument(docData);

        // Initialize with first page's data
        if (docData.pages && docData.pages.length > 0) {
          const firstPage = docData.pages[0];
          setClassification(firstPage.document_type || '');
          setFolder(firstPage.proposed_folder || firstPage.folder || '');
          setFilename(firstPage.proposed_filename || firstPage.filename || '');

          // Determine if folder is new
          setIsNewFolder(!firstPage.folder.includes(firstPage.proposed_folder || ''));
        }
      } catch (error) {
        console.error('Error loading document:', error);
        showToast('Failed to load document', 'error');
      } finally {
        setLoading(false);
      }
    };

    loadDocument();
  }, [documentId]);

  // Navigate to next document
  const goToNext = useCallback(() => {
    if (document && currentIndex < document.pages.length - 1) {
      const nextPageIndex = currentIndex + 1;
      setCurrentIndex(nextPageIndex);
      
      // Update form fields with next page's data
      const nextPage = document.pages[nextPageIndex];
      setClassification(nextPage.document_type || '');
      setFolder(nextPage.proposed_folder || nextPage.folder || '');
      setFilename(nextPage.proposed_filename || nextPage.filename || '');
      
      // Determine if folder is new
      setIsNewFolder(!nextPage.folder.includes(nextPage.proposed_folder || ''));
    }
  }, [currentIndex, document]);

  // Navigate to previous document
  const goToPrevious = useCallback(() => {
    if (currentIndex > 0) {
      const prevPageIndex = currentIndex - 1;
      setCurrentIndex(prevPageIndex);
      
      // Update form fields with previous page's data
      const prevPage = document?.pages[prevPageIndex];
      if (prevPage) {
        setClassification(prevPage.document_type || '');
        setFolder(prevPage.proposed_folder || prevPage.folder || '');
        setFilename(prevPage.proposed_filename || prevPage.filename || '');
        
        // Determine if folder is new
        setIsNewFolder(!prevPage.folder.includes(prevPage.proposed_folder || ''));
      }
    }
  }, [currentIndex, document]);

  // Handle keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') {
        goToNext();
      } else if (e.key === 'ArrowLeft') {
        goToPrevious();
      } else if (e.key === 'Enter' || e.key === ' ') {
        handleSave();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [goToNext, goToPrevious]);

  // Save current page's classification and routing
  const handleSave = async () => {
    if (!document || !document.pages[currentIndex]) return;

    setUpdating(true);
    try {
      const currentPage = document.pages[currentIndex];
      if (!currentPage.page_id) {
        throw new Error(`Page ${currentPage.page_number} has no ID`);
      }

      await correctPage(currentPage.page_id, folder, filename);

      // Update document state
      const updatedPages = [...document.pages];
      updatedPages[currentIndex] = {
        ...currentPage,
        document_type: classification,
        folder,
        filename
      };

      setDocument({
        ...document,
        pages: updatedPages
      });

      // Move to next page if available
      if (currentIndex < document.pages.length - 1) {
        goToNext();
      } else {
        // All pages processed
        onComplete();
      }

      showToast(`Saved page ${currentPage.page_number}`, 'success');
    } catch (error) {
      console.error('Error saving page:', error);
      showToast('Failed to save page', 'error');
    } finally {
      setUpdating(false);
    }
  };

  // Skip current page and move to next
  const handleSkip = () => {
    if (currentIndex < (document?.pages.length || 0) - 1) {
      goToNext();
    } else {
      onComplete();
    }
  };

  // Approve all proposals for the current document
  const handleApproveAll = async () => {
    if (!document) return;

    setUpdating(true);
    try {
      // Apply all proposed changes
      for (const page of document.pages) {
        if (page.page_id && page.proposed_folder && page.proposed_filename) {
          await correctPage(page.page_id, page.proposed_folder, page.proposed_filename);
        } else if (!page.page_id) {
          throw new Error(`Page ${page.page_number} has no ID`);
        }
      }

      // Reload document to reflect changes
      const updatedDoc = await getDocument(documentId);
      setDocument(updatedDoc);
      onComplete();
      showToast('Approved all suggestions', 'success');
    } catch (error) {
      console.error('Error approving all:', error);
      showToast('Failed to approve all suggestions', 'error');
    } finally {
      setUpdating(false);
    }
  };

  if (loading) {
    return (
      <div className="carousel-overlay">
        <div className="carousel-container">
          <div className="carousel-loading">Loading document...</div>
        </div>
      </div>
    );
  }

  if (!document || !document.pages || document.pages.length === 0) {
    return (
      <div className="carousel-overlay">
        <div className="carousel-container">
          <div className="carousel-empty">No pages to review</div>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    );
  }

  const currentPage = document.pages[currentIndex];
  const totalPages = document.pages.length;
  const progress = ((currentIndex + 1) / totalPages) * 100;

  return (
    <div className="carousel-overlay">
      <div className="carousel-container">
        <div className="carousel-header">
          <h2>Document Review</h2>
          <div className="carousel-controls">
            <span className="carousel-progress">{currentIndex + 1} of {totalPages}</span>
            <div className="progress-bar">
              <div 
                className="progress-fill" 
                style={{ width: `${progress}%` }}
              ></div>
            </div>
            <button onClick={onClose} className="close-button">✕</button>
          </div>
        </div>

        <div className="carousel-content">
          <div className="document-preview">
            {/* Placeholder for document preview - in a real app, this would show the actual document page */}
            <div className="preview-placeholder">
              <div className="preview-label">Document Preview</div>
              <div className="preview-content">
                <div className="preview-page-number">Page {currentPage.page_number}</div>
                <div className="preview-info">
                  <div><strong>Document Type:</strong> {currentPage.document_type}</div>
                  <div><strong>Institution:</strong> {currentPage.institution || 'N/A'}</div>
                  <div><strong>Date:</strong> {currentPage.date || 'N/A'}</div>
                  <div><strong>Confidence:</strong> {(currentPage.confidence_score * 100).toFixed(0)}%</div>
                  <div><strong>Current Folder:</strong> {currentPage.folder}</div>
                  <div><strong>Current Filename:</strong> {currentPage.filename}</div>
                  {currentPage.proposed_folder && (
                    <div><strong>Proposed Folder:</strong> {currentPage.proposed_folder}</div>
                  )}
                  {currentPage.proposed_filename && (
                    <div><strong>Proposed Filename:</strong> {currentPage.proposed_filename}</div>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="review-panel">
            <div className="classification-section">
              <h3>Classification</h3>
              <select 
                value={classification} 
                onChange={(e) => setClassification(e.target.value)}
                className="classification-select"
              >
                <option value="">Select document type...</option>
                {DOCUMENT_TYPES.map(type => (
                  <option key={type} value={type}>
                    {type.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')}
                  </option>
                ))}
              </select>
              <div className="confidence-indicator">
                AI Confidence: {(currentPage.confidence_score * 100).toFixed(0)}%
              </div>
            </div>

            <div className="folder-section">
              <h3>Folder Assignment</h3>
              <input
                type="text"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                placeholder="Enter folder path..."
                className="folder-input"
              />
              <div className={`folder-status ${isNewFolder ? 'new-folder' : 'existing-folder'}`}>
                {isNewFolder ? 'New Folder' : 'Existing Folder'}
              </div>
            </div>

            <div className="filename-section">
              <h3>Filename</h3>
              <input
                type="text"
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                placeholder="Enter filename..."
                className="filename-input"
              />
            </div>

            <div className="action-buttons">
              <button 
                onClick={goToPrevious} 
                disabled={currentIndex === 0}
                className="nav-button prev-button"
              >
                ← Previous
              </button>
              
              <button 
                onClick={handleSkip}
                className="skip-button"
              >
                Skip
              </button>
              
              <button 
                onClick={handleSave}
                disabled={updating}
                className="save-button"
              >
                {updating ? 'Saving...' : 'Save & Next'}
              </button>
              
              <button 
                onClick={goToNext} 
                disabled={currentIndex === totalPages - 1}
                className="nav-button next-button"
              >
                Next →
              </button>
            </div>
            
            <div className="batch-actions">
              <button 
                onClick={handleApproveAll}
                disabled={updating}
                className="approve-all-button"
              >
                Approve All Suggestions
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CarouselReview;