# Changelog

## [Unreleased]

### Added
- **YAML Support**: Now accepts OpenAPI specifications in YAML format (`.yaml`, `.yml`)
  - Automatic format detection based on file extension
  - Fallback auto-detection for files without standard extensions
  - Graceful error handling when PyYAML is not installed
- **Hash-based Content Security Policy**: Static HTML files now have XSS protection
  - SHA-256 hash computed at generation time for inline scripts
  - Blocks unauthorized script execution even if XSS vulnerabilities exist
  - Added `base-uri 'none'` directive to prevent base tag injection
- **Security Documentation**: Added comments explaining CSP implementation

### Changed
- Updated CLI help text to mention YAML support
- Updated README with YAML examples and PyYAML requirement
- Improved error messages for file format and dependency issues

### Security
- Implemented hash-based CSP for defense-in-depth XSS protection
- All user-controlled data properly escaped using DOM methods
- Removed inline event handlers in favor of addEventListener

### Technical
- Script hash computation accounts for exact browser parsing behavior
- Placeholder system prevents f-string interference with hash substitution
- Auto-detection attempts JSON parsing first, then falls back to YAML

## Dependencies
- **Core**: Python 3.7+ (no external dependencies for JSON files)
- **Optional**: PyYAML for YAML file support (`pip install pyyaml`)


## [Latest Update]

### Changed - Universal Color Scheme
- **Redesigned color rules** to be universally applicable to any OpenAPI specification
- Colors now based on **structural characteristics** rather than domain-specific field names:
  - **Purple**: API Root entities (appears at API surface)
  - **Blue**: Large complex entities (8+ fields)
  - **Coral**: Medium entities (6-7 fields)
  - **Teal**: Small entities (3-5 fields)
  - **Amber**: Minimal entities (1-2 fields)
  - **Green**: Leaf entities (no outgoing relationships)
  - **Red**: Error/Response wrapper types
  - **Gray**: Uncategorized/default

### Added - Enhanced Navigation
- **Automatic node selection** when clicking schema chips in API Surface tab
  - Clicking a request/response schema now switches to Entity Graph tab
  - The clicked schema is automatically selected and highlighted in the diagram
  - Connected paths are highlighted to show relationships
  - Detail panel opens automatically for the selected schema

### Technical Details
- Color assignment now considers:
  - Schema size (field count)
  - API surface presence (root vs nested)
  - Relationship count (outgoing references)
  - Error/wrapper detection patterns
- Universal patterns work across all API domains (not just specific use cases)
- Legend updates dynamically based on colors actually used in the spec

