import React, { useState, useEffect } from 'react';
import {
  getProposals, acceptProposal, modifyProposal, rejectProposal,
  acceptAllProposals, getSubDocPreview,
} from '../services/api';

type SubDocument = {
  id: string;
  document_id: string;
  start_page: number;
  end_page: number;
  page_count: number;
  document_type: string | null;
  document_category: string | null;
  institution: string | null;
  classification_metadata: Record<string, any> | null;
  confidence_score: number | null;
  split_rationale: string | null;
  proposed_folder: string | null;
  proposed_filename: string | null;
  filing_rationale: string | null;
  final_folder: string | null;
  final_filename: string | null;
  status: string;
  llm_provider_used: string | null;
};

type FilingProposal = {
  id: string;
  sub_document_id: string;
  proposed_folder: string;
  proposed_filename: string;
  is_new_folder: boolean;
  rationale: string | null;
  alternatives_considered: any[] | null;
  confidence: number | null;
  decision: string | null;
  user_folder: string | null;
  user_filename: string | null;
};

type ProposalsData = {
  document_id: string;
  status: string;
  sub_documents: SubDocument[];
  proposals: FilingProposal[];
};

type ToastFn = (text: string, type?: 'success' | 'error' | 'info') => void;

type Props = {
  documentId: string;
  onClose: () => void;
  onComplete: () => void;
  showToast: ToastFn;
};

const GROUP_COLORS = ['#7aa2f7', '#9ece6a', '#bb9af7', '#e0af68', '#f7768e', '#73daca'];

const TYPE_FIELDS: Record<string, string[]> = {
  bank_statement: ['account_type', 'account_last4', 'statement_period'],
  letter: ['sender', 'subject', 'reason'],
  invoice: ['vendor', 'invoice_number', 'total', 'due_date'],
  tax_form: ['form_type', 'tax_year', 'employer'],
  utility_bill: ['utility_type', 'provider', 'amount_due'],
  legal_document: ['case_number', 'court', 'document_subtype'],
  insurance_document: ['policy_number', 'insurer', 'coverage_type'],
  medical_document: ['provider', 'patient', 'visit_date'],
};

function getSubDocColor(index: number): string {
  return GROUP_COLORS[index % GROUP_COLORS.length];
}

function getKeyMetadata(sd: SubDocument): { label: string; value: string }[] {
  const meta = sd.classification_metadata || {};
  const fields: { label: string; value: string }[] = [];
  if (sd.institution) fields.push({ label: 'Institution', value: sd.institution });
  const relevantKeys = TYPE_FIELDS[sd.document_type || ''] || Object.keys(meta).slice(0, 4);
  for (const key of relevantKeys) {
    if (meta[key] != null && meta[key] !== '') {
      fields.push({ label: key.replace(/_/g, ' '), value: String(meta[key]) });
    }
  }
  return fields.slice(0, 4);
}

const ProposalReview: React.FC<Props> = ({ documentId, onClose, onComplete, showToast }) => {
  const [data, setData] = useState<ProposalsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [editFolder, setEditFolder] = useState('');
  const [editFilename, setEditFilename] = useState('');
  const [editRationale, setEditRationale] = useState('');
  const [rejectMode, setRejectMode] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [showMeta, setShowMeta] = useState(false);

  const selectedSubDoc = data?.sub_documents[selectedIdx] ?? null;
  const selectedProposal = data?.proposals.find(
    (p) => p.sub_document_id === selectedSubDoc?.id
  ) ?? null;

  // Load proposals
  useEffect(() => {
    loadProposals();
  }, [documentId]);

  async function loadProposals() {
    setLoading(true);
    try {
      const result = await getProposals(documentId);
      setData(result);
      if (result.sub_documents.length > 0) {
        setSelectedIdx(0);
        setPreviewPage(1);
      }
    } catch (e: any) {
      showToast(e?.message ?? 'Failed to load proposals', 'error');
    } finally {
      setLoading(false);
    }
  }

  // Load preview when sub-doc or page changes
  useEffect(() => {
    if (!selectedSubDoc) return;
    let cancelled = false;
    setPreviewLoading(true);

    getSubDocPreview(selectedSubDoc.id, previewPage, 150)
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        setPreviewUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      })
      .catch(() => {
        if (cancelled) return;
        setPreviewUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return null;
        });
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => { cancelled = true; };
  }, [selectedSubDoc?.id, previewPage]);

  // Cleanup preview URL on unmount
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, []);

  // Keyboard navigation
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (editMode || rejectMode) return;
      if (e.key === 'ArrowLeft' && previewPage > 1) {
        setPreviewPage((p) => p - 1);
      } else if (e.key === 'ArrowRight' && selectedSubDoc && previewPage < selectedSubDoc.page_count) {
        setPreviewPage((p) => p + 1);
      } else if (e.key === 'Escape') {
        onClose();
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [editMode, rejectMode, previewPage, selectedSubDoc]);

  function selectSubDoc(index: number) {
    setSelectedIdx(index);
    setPreviewPage(1);
    setEditMode(false);
    setRejectMode(false);
    setShowMeta(false);
  }

  function startEdit() {
    if (selectedProposal) {
      setEditFolder(selectedProposal.proposed_folder);
      setEditFilename(selectedProposal.proposed_filename);
      setEditRationale('');
    }
    setEditMode(true);
    setRejectMode(false);
  }

  function startReject() {
    setRejectMode(true);
    setEditMode(false);
    setRejectReason('');
  }

  function moveToNextPending() {
    if (!data) return;
    // Find next pending after current index
    const nextIdx = data.sub_documents.findIndex(
      (sd, i) => i > selectedIdx && sd.status === 'proposed'
    );
    if (nextIdx >= 0) {
      selectSubDoc(nextIdx);
    } else {
      // Check if any pending remain
      const anyPending = data.sub_documents.some((sd) => sd.status === 'proposed');
      if (!anyPending) {
        onComplete();
      }
    }
  }

  async function handleAccept() {
    if (!selectedSubDoc) return;
    setBusy(true);
    try {
      await acceptProposal(selectedSubDoc.id);
      showToast('Proposal accepted', 'success');
      await loadProposals();
      moveToNextPending();
    } catch (e: any) {
      showToast(e?.message ?? 'Accept failed', 'error');
    } finally {
      setBusy(false);
    }
  }

  async function handleModify() {
    if (!selectedSubDoc) return;
    setBusy(true);
    try {
      await modifyProposal(selectedSubDoc.id, {
        folder: editFolder || undefined,
        filename: editFilename || undefined,
        rationale: editRationale || undefined,
      });
      showToast("Got it \u2014 I'll remember this for next time.", 'success');
      setEditMode(false);
      await loadProposals();
      moveToNextPending();
    } catch (e: any) {
      showToast(e?.message ?? 'Modify failed', 'error');
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (!selectedSubDoc || !rejectReason.trim()) return;
    setBusy(true);
    try {
      await rejectProposal(selectedSubDoc.id, rejectReason);
      showToast("Got it \u2014 I'll remember this for next time.", 'success');
      setRejectMode(false);
      setRejectReason('');
      await loadProposals();
      moveToNextPending();
    } catch (e: any) {
      showToast(e?.message ?? 'Reject failed', 'error');
    } finally {
      setBusy(false);
    }
  }

  async function handleAcceptAll() {
    setBusy(true);
    try {
      const result = await acceptAllProposals(documentId);
      showToast(`Accepted ${result.accepted} proposals`, 'success');
      if (result.errors?.length) {
        showToast(`${result.errors.length} errors occurred`, 'error');
      }
      await loadProposals();
      onComplete();
    } catch (e: any) {
      showToast(e?.message ?? 'Accept all failed', 'error');
    } finally {
      setBusy(false);
    }
  }

  // ── Loading / Empty states ──────────────────────────────────────────

  if (loading) {
    return (
      <div className="pr-overlay">
        <div className="pr-container">
          <div className="pr-loading">Loading proposals...</div>
        </div>
      </div>
    );
  }

  if (!data || data.sub_documents.length === 0) {
    return (
      <div className="pr-overlay">
        <div className="pr-container">
          <div className="pr-empty">
            <div>No sub-documents found</div>
            <button onClick={onClose} style={{ marginTop: 16 }}>Close</button>
          </div>
        </div>
      </div>
    );
  }

  // ── Derived state ───────────────────────────────────────────────────

  const pendingCount = data.sub_documents.filter((sd) => sd.status === 'proposed').length;
  const totalCount = data.sub_documents.length;
  const reviewedCount = totalCount - pendingCount;
  const progress = totalCount > 0 ? (reviewedCount / totalCount) * 100 : 0;

  // Build page → sub-doc mapping for thumbnail strip
  const pageGroups: { pageNum: number; subDocIndex: number; color: string }[] = [];
  data.sub_documents.forEach((sd, sdIdx) => {
    for (let p = sd.start_page; p <= sd.end_page; p++) {
      pageGroups.push({ pageNum: p, subDocIndex: sdIdx, color: getSubDocColor(sdIdx) });
    }
  });

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="pr-overlay">
      <div className="pr-container">
        {/* Header */}
        <div className="pr-header">
          <div>
            <h2 style={{ margin: 0 }}>Review Proposals</h2>
            <span className="pr-header-subtitle">
              {reviewedCount} of {totalCount} reviewed
              {pendingCount > 0 && ` \u00b7 ${pendingCount} pending`}
            </span>
          </div>
          <div className="pr-header-controls">
            <div className="progress-bar" style={{ width: 160 }}>
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            {pendingCount > 0 && (
              <button
                className="pr-accept-all-btn"
                onClick={handleAcceptAll}
                disabled={busy}
              >
                Accept All ({pendingCount})
              </button>
            )}
            <button onClick={onClose} className="close-button">
              &#x2715;
            </button>
          </div>
        </div>

        {/* Main split pane */}
        <div className="pr-content">
          {/* Left: Page Preview */}
          <div className="pr-preview-pane">
            {previewLoading ? (
              <div className="pr-preview-loading">Loading preview...</div>
            ) : previewUrl ? (
              <img src={previewUrl} alt="Page preview" className="pr-preview-image" />
            ) : (
              <div className="pr-preview-placeholder">No preview available</div>
            )}
            {selectedSubDoc && selectedSubDoc.page_count > 1 && (
              <div className="pr-page-nav">
                <button
                  onClick={() => setPreviewPage((p) => Math.max(1, p - 1))}
                  disabled={previewPage <= 1}
                  className="pr-page-nav-btn"
                >
                  &larr;
                </button>
                <span className="pr-page-indicator">
                  Page {previewPage} of {selectedSubDoc.page_count}
                </span>
                <button
                  onClick={() =>
                    setPreviewPage((p) => Math.min(selectedSubDoc!.page_count, p + 1))
                  }
                  disabled={previewPage >= selectedSubDoc.page_count}
                  className="pr-page-nav-btn"
                >
                  &rarr;
                </button>
              </div>
            )}
          </div>

          {/* Right: Proposal Card */}
          <div className="pr-detail-pane">
            {selectedSubDoc && (
              <>
                {/* Type badge + confidence + status */}
                <div className="pr-type-header">
                  <span
                    className="pr-type-badge"
                    style={{ borderColor: getSubDocColor(selectedIdx) }}
                  >
                    {(selectedSubDoc.document_type || 'unknown').replace(/_/g, ' ')}
                  </span>
                  <span className="pr-confidence">
                    {selectedSubDoc.confidence_score != null
                      ? `${Math.round(selectedSubDoc.confidence_score * 100)}%`
                      : '\u2014'}
                  </span>
                  <span className={`pr-status-pill pr-status-${selectedSubDoc.status}`}>
                    {selectedSubDoc.status}
                  </span>
                </div>

                {/* Page range */}
                <div className="pr-pages-range">
                  Pages {selectedSubDoc.start_page}
                  {selectedSubDoc.end_page !== selectedSubDoc.start_page &&
                    `\u2013${selectedSubDoc.end_page}`}
                  {' '}({selectedSubDoc.page_count} pg)
                  {selectedSubDoc.split_rationale && (
                    <span className="pr-split-rationale">
                      {' '}&middot; {selectedSubDoc.split_rationale}
                    </span>
                  )}
                </div>

                {/* Key metadata */}
                <div className="pr-metadata-section">
                  {getKeyMetadata(selectedSubDoc).map(({ label, value }) => (
                    <div key={label} className="pr-metadata-row">
                      <span className="pr-metadata-label">{label}</span>
                      <span className="pr-metadata-value">{value}</span>
                    </div>
                  ))}
                  {selectedSubDoc.classification_metadata &&
                    Object.keys(selectedSubDoc.classification_metadata).length > 0 && (
                      <button
                        className="pr-expand-btn"
                        onClick={() => setShowMeta(!showMeta)}
                      >
                        {showMeta ? 'Hide details' : 'Show all metadata'}
                      </button>
                    )}
                  {showMeta && selectedSubDoc.classification_metadata && (
                    <pre className="pr-metadata-full">
                      {JSON.stringify(selectedSubDoc.classification_metadata, null, 2)}
                    </pre>
                  )}
                </div>

                {/* Filing proposal — only for proposed items */}
                {selectedProposal && selectedSubDoc.status === 'proposed' && (
                  <div className="pr-filing-section">
                    <div className="pr-filing-label">Proposed Filing Path</div>
                    <div className="pr-filing-path">
                      {selectedProposal.proposed_folder}/{selectedProposal.proposed_filename}
                    </div>
                    {selectedProposal.is_new_folder && (
                      <span className="pr-new-folder-badge">New Folder</span>
                    )}
                    {selectedProposal.rationale && (
                      <div className="pr-rationale">{selectedProposal.rationale}</div>
                    )}
                    {selectedProposal.confidence != null && (
                      <div className="pr-filing-confidence">
                        Filing confidence: {Math.round(selectedProposal.confidence * 100)}%
                      </div>
                    )}
                  </div>
                )}

                {/* Filed info */}
                {selectedSubDoc.status === 'filed' && (
                  <div className="pr-filed-section">
                    <div className="pr-filed-label">Filed to</div>
                    <div className="pr-filing-path">
                      {selectedSubDoc.final_folder}/{selectedSubDoc.final_filename}
                    </div>
                  </div>
                )}

                {/* Rejected info */}
                {selectedSubDoc.status === 'rejected' && (
                  <div className="pr-rejected-section">
                    <div className="pr-rejected-label">Rejected</div>
                  </div>
                )}

                {/* Action buttons — only for proposed */}
                {selectedSubDoc.status === 'proposed' && !editMode && !rejectMode && (
                  <div className="pr-actions">
                    <button
                      className="pr-accept-btn"
                      onClick={handleAccept}
                      disabled={busy}
                    >
                      {busy ? 'Processing...' : 'Accept'}
                    </button>
                    <button className="pr-edit-btn" onClick={startEdit} disabled={busy}>
                      Edit
                    </button>
                    <button className="pr-reject-btn" onClick={startReject} disabled={busy}>
                      Reject
                    </button>
                  </div>
                )}

                {/* Edit form */}
                {editMode && (
                  <div className="pr-edit-form">
                    <label className="pr-edit-label">
                      Folder
                      <input
                        type="text"
                        value={editFolder}
                        onChange={(e) => setEditFolder(e.target.value)}
                        className="pr-edit-input"
                      />
                    </label>
                    <label className="pr-edit-label">
                      Filename
                      <input
                        type="text"
                        value={editFilename}
                        onChange={(e) => setEditFilename(e.target.value)}
                        className="pr-edit-input"
                      />
                    </label>
                    <label className="pr-edit-label">
                      Reason (helps me learn)
                      <input
                        type="text"
                        value={editRationale}
                        onChange={(e) => setEditRationale(e.target.value)}
                        placeholder="e.g., separate checking and savings"
                        className="pr-edit-input"
                      />
                    </label>
                    <div className="pr-edit-actions">
                      <button
                        className="pr-save-btn"
                        onClick={handleModify}
                        disabled={busy}
                      >
                        {busy ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        className="pr-cancel-btn"
                        onClick={() => setEditMode(false)}
                        disabled={busy}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {/* Reject form */}
                {rejectMode && (
                  <div className="pr-reject-form">
                    <label className="pr-edit-label">
                      Reason for rejection
                      <input
                        type="text"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        placeholder="e.g., wrong document, need to rescan"
                        className="pr-edit-input"
                        autoFocus
                      />
                    </label>
                    <div className="pr-edit-actions">
                      <button
                        className="pr-reject-confirm-btn"
                        onClick={handleReject}
                        disabled={busy || !rejectReason.trim()}
                      >
                        {busy ? 'Rejecting...' : 'Confirm Reject'}
                      </button>
                      <button
                        className="pr-cancel-btn"
                        onClick={() => setRejectMode(false)}
                        disabled={busy}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Thumbnail strip with colored sub-document groupings */}
        <div className="pr-thumbnail-strip">
          {pageGroups.map(({ pageNum, subDocIndex, color }) => (
            <div
              key={pageNum}
              className={`pr-thumbnail ${subDocIndex === selectedIdx ? 'active' : ''}`}
              style={{
                borderBottomColor: color,
                borderBottomWidth: 3,
                borderBottomStyle: 'solid',
              }}
              onClick={() => {
                selectSubDoc(subDocIndex);
                const sd = data.sub_documents[subDocIndex];
                setPreviewPage(pageNum - sd.start_page + 1);
              }}
            >
              <div className="pr-thumb-num">{pageNum}</div>
              <div className="pr-thumb-type" style={{ color }}>
                {(data.sub_documents[subDocIndex].document_type || '?').slice(0, 8)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ProposalReview;
