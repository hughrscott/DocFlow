import React, { useState, useEffect } from 'react';
import { getDocument, correctPage, getPageImage } from '../services/api';

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

type ModernReviewProps = {
  documentId: string;
  onClose: () => void;
  onComplete: () => void;
  showToast: (text: string, type?: 'success' | 'error' | 'info') => void;
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

const ModernReview: React.FC<ModernReviewProps> = ({ documentId, onClose, onComplete, showToast }) => {
  const [document, setDocument] = useState<DocDetail | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [classification, setClassification] = useState('');
  const [folder, setFolder] = useState('');
  const [filename, setFilename] = useState('');
  const [isNewFolder, setIsNewFolder] = useState(false);
  const [pageImageUrl, setPageImageUrl] = useState<string | null>(null);
  const [imageLoading, setImageLoading] = useState(false);

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
          
          // Load the first page image
          if (firstPage.page_id) {
            setImageLoading(true);
            try {
              const imageBlob = await getPageImage(firstPage.page_id);
              const imageUrl = URL.createObjectURL(imageBlob);
              setPageImageUrl(imageUrl);
            } catch (imgError) {
              console.error('Error loading page image:', imgError);
            } finally {
              setImageLoading(false);
            }
          }
        }
      } catch (error) {
        console.error('Error loading document:', error);
        showToast('Failed to load document', 'error');
      } finally {
        setLoading(false);
      }
    };

    loadDocument();

    // Cleanup object URLs
    return () => {
      if (pageImageUrl) {
        URL.revokeObjectURL(pageImageUrl);
      }
    };
  }, [documentId, showToast]);

  // Navigate to next document
  const goToNext = async () => {
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
      
      // Update page image
      if (nextPage.page_id) {
        setImageLoading(true);
        try {
          const imageBlob = await getPageImage(nextPage.page_id);
          const imageUrl = URL.createObjectURL(imageBlob);
          // Revoke previous image URL
          if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
          setPageImageUrl(imageUrl);
        } catch (imgError) {
          console.error('Error loading page image:', imgError);
          // Revoke previous image URL
          if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
          setPageImageUrl(null);
        } finally {
          setImageLoading(false);
        }
      } else {
        // Revoke previous image URL
        if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
        setPageImageUrl(null);
        setImageLoading(false);
      }
    }
  };

  // Navigate to previous document
  const goToPrevious = async () => {
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
        
        // Update page image
        if (prevPage.page_id) {
          setImageLoading(true);
          try {
            const imageBlob = await getPageImage(prevPage.page_id);
            const imageUrl = URL.createObjectURL(imageBlob);
            // Revoke previous image URL
            if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
            setPageImageUrl(imageUrl);
          } catch (imgError) {
            console.error('Error loading page image:', imgError);
            // Revoke previous image URL
            if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
            setPageImageUrl(null);
          } finally {
            setImageLoading(false);
          }
        } else {
          // Revoke previous image URL
          if (pageImageUrl) URL.revokeObjectURL(pageImageUrl);
          setPageImageUrl(null);
          setImageLoading(false);
        }
      }
    }
  };

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
        await goToNext();
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

  if (loading) {
    return (
      <div className="modern-review-overlay">
        <div className="modern-review-container">
          <div className="loading-spinner">Loading document...</div>
        </div>
      </div>
    );
  }

  if (!document || !document.pages || document.pages.length === 0) {
    return (
      <div className="modern-review-overlay">
        <div className="modern-review-container">
          <div className="empty-state">No pages to review</div>
          <button onClick={onClose} className="btn-secondary">Close</button>
        </div>
      </div>
    );
  }

  const currentPage = document.pages[currentIndex];
  const totalPages = document.pages.length;
  const progress = ((currentIndex + 1) / totalPages) * 100;

  return (
    <div className="modern-review-overlay">
      <div className="modern-review-container">
        <div className="review-header">
          <div className="header-left">
            <h2>Review Document</h2>
            <p>{document.original_filename}</p>
          </div>
          <div className="header-right">
            <div className="progress-container">
              <div className="progress-text">{currentIndex + 1} of {totalPages}</div>
              <div className="progress-bar">
                <div 
                  className="progress-fill" 
                  style={{ width: `${progress}%` }}
                ></div>
              </div>
            </div>
            <button onClick={onClose} className="btn-icon">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <div className="review-content">
          <div className="document-preview">
            <div className="preview-container">
              {imageLoading ? (
                <div className="preview-loading">Loading preview...</div>
              ) : pageImageUrl ? (
                <img 
                  src={pageImageUrl} 
                  alt={`Document page ${currentPage.page_number}`}
                  className="preview-image"
                  onError={() => {
                    console.error('Failed to load page image');
                  }}
                />
              ) : (
                <div className="preview-placeholder">
                  <div>No preview available</div>
                </div>
              )}
            </div>
          </div>

          <div className="review-panel">
            <div className="classification-section">
              <h3>Document Type</h3>
              <div className="classification-grid">
                {DOCUMENT_TYPES.map(type => {
                  const displayName = type.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
                  return (
                    <button
                      key={type}
                      className={`classification-card ${classification === type ? 'selected' : ''}`}
                      onClick={() => setClassification(type)}
                    >
                      <div className="card-icon">
                        {type.startsWith('bank') && <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3" /></svg>}
                        {type.startsWith('tax') && <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>}
                        {type.startsWith('util') && <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>}
                        {type.startsWith('insur') && <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>}
                        {!['bank', 'tax', 'util', 'insur'].some(prefix => type.startsWith(prefix)) && <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>}
                      </div>
                      <div className="card-title">{displayName}</div>
                    </button>
                  );
                })}
              </div>
              <div className="confidence-indicator">
                <div className="confidence-label">AI Confidence</div>
                <div className="confidence-meter">
                  <div 
                    className="confidence-fill" 
                    style={{ width: `${currentPage.confidence_score * 100}%` }}
                  ></div>
                </div>
                <div className="confidence-value">{Math.round(currentPage.confidence_score * 100)}%</div>
              </div>
            </div>

            <div className="routing-section">
              <h3>Routing</h3>
              <div className="folder-input-group">
                <label>Folder</label>
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
              
              <div className="filename-input-group">
                <label>Filename</label>
                <input
                  type="text"
                  value={filename}
                  onChange={(e) => setFilename(e.target.value)}
                  placeholder="Enter filename..."
                  className="filename-input"
                />
              </div>
            </div>

            <div className="action-buttons">
              <button 
                onClick={goToPrevious} 
                disabled={currentIndex === 0}
                className="btn-secondary"
              >
                ← Previous
              </button>
              
              <button 
                onClick={handleSkip}
                className="btn-tertiary"
              >
                Skip
              </button>
              
              <button 
                onClick={handleSave}
                disabled={updating}
                className="btn-primary"
              >
                {updating ? 'Saving...' : 'Save & Continue'}
              </button>
              
              <button 
                onClick={goToNext} 
                disabled={currentIndex === totalPages - 1}
                className="btn-secondary"
              >
                Next →
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ModernReview;