# DocFlow UI Specification: Carousel-Based Document Review

Audience: developers and designers
Owner: maintainers
Last Updated: 2026-01-29

## Overview

This document specifies the carousel-based document review interface for DocFlow. The UI allows users to visually review each document page, confirm classifications and folder assignments, and approve or modify the AI's suggestions before final filing.

## Purpose

The carousel UI addresses the need for user verification of AI-generated document classifications and routing decisions. Rather than processing documents without review, users can visually inspect each page, confirm the AI's work, and make corrections before documents are permanently filed.

## Components

### 1. Document Carousel
- Horizontal scrollable gallery displaying document pages as visual thumbnails
- Each page shows a preview image and associated metadata
- Navigation controls (previous/next buttons, thumbnail navigation dots)
- Keyboard navigation support (arrow keys, spacebar, enter)

### 2. Classification Panel
- Shows AI-determined document type (e.g., "Bank Statement", "Tax Bill", "Invoice")
- Allows user to override classification if incorrect
- Dropdown with predefined document types from the taxonomy
- Option to add custom document type

### 3. Folder Assignment Panel
- Shows proposed folder destination (e.g., "Banking/Personal/PNC", "Taxes/Property")
- Visual indicator if folder is new or existing
- Allows user to select different folder from suggestions or create new one
- Breadcrumb navigation for folder selection

### 4. Filename Preview
- Shows proposed filename based on document metadata
- Allows user to modify filename if needed
- Real-time preview of how the file will be named

### 5. Action Controls
- "Approve" button to accept all AI suggestions
- "Reject" button to mark document for manual review
- "Save" button to apply changes and move to next document
- "Skip" button to defer decision on current document

## User Flow

1. User uploads documents via the upload interface
2. AI processes documents and generates classifications/folder suggestions
3. User enters carousel review mode
4. For each document page:
   - Reviews visual preview
   - Confirms or modifies classification
   - Confirms or modifies folder assignment
   - Confirms or modifies filename
   - Approves the changes
5. System files documents according to user-approved decisions
6. Learning engine updates based on user corrections

## Visual Design

### Layout
- Main carousel area taking 60% of screen width
- Right sidebar with classification/folder panels taking 40% of screen width
- Bottom navigation controls
- Responsive design for different screen sizes

### Visual Elements
- Document thumbnails with border highlighting
- Color-coded document types (blue for banking, green for taxes, etc.)
- Icons for different document types
- Visual distinction between new and existing folders
- Progress indicator showing position in document set

### Interactions
- Click on thumbnail to select document
- Drag to navigate between documents
- Hover effects on interactive elements
- Smooth transitions between documents
- Loading states during AI processing

## API Integration Points

### Data Sources
- `/api/v1/documents/{document_id}` - Get document details and page results
- `/api/v1/documents/folder_suggestions` - Get suggested folder destinations
- `/api/v1/providers/readiness` - Check AI provider status

### Actions
- `POST /api/v1/documents/pages/{page_id}/correct` - Apply user corrections
- `POST /api/v1/documents/pages/{page_id}/reanalyze` - Re-analyze specific page
- `POST /api/v1/documents/{document_id}/reanalyze` - Re-analyze entire document
- `POST /api/v1/documents/{document_id}/confirm_class` - Confirm document class

## Accessibility

- Keyboard navigation support
- Screen reader compatibility
- High contrast mode option
- Zoom support for document previews
- Alternative text for document images

## Performance Considerations

- Lazy loading of document previews
- Caching of processed document images
- Progressive loading for large document sets
- Optimized image formats for thumbnails

## Error Handling

- Graceful degradation when AI services unavailable
- Clear error messages for failed classifications
- Ability to retry failed operations
- Offline mode for reviewing already-processed documents

## Future Enhancements

- Batch operations for similar documents
- Machine learning feedback loop based on user corrections
- Custom document type creation
- Integration with external document sources
- Mobile-responsive design