# Changelog

All notable changes to this project will be documented in this file.

## [0.1] - 2026-10-12

### Added
- Added 2 more sheet in excel in component/export.py to show balance in and out. Then added a timer indicator in app.py that gets updated every 0.2 second to show progress.

### Fixed
- FIxed to sort by date after detection, put a priority in detected cr/db before deskripsi to decide money in or out. need to update to support multiple files at the same time and split by bank account.
- Fixed description extraction from splitting text into other improperly. Current problem: if uploaded document isn't already sorted, balance will be calculated out of order.
- fixed the modularization. need to debug layout.py to understand why table reconstruction sometime fails
- Fixed date parsing, and numbering for saldo, kredit and debit.

### Changed
- Edit README.md
- Modularization of all functions from initially just parser. Adjusted based on possible templates, but it was educated guess for future templates. Future work: Test and adjust for more templates, Support for multiple documents handling at once, Adjust based on CA needs.
- Testing out table finder from Fitz and adjusting parser.py (still work in progress)
- Removed redundant files
- Initial commit