# R008 — HEIC and Input-Format Capability Detection

**Priority:** P1  
**Status:** Implemented

## Problem

The uploader advertises HEIC receipts even when the installed image stack
cannot decode HEIC. Accepted formats are currently static rather than derived
from actual runtime capabilities.

## Required outcome

The UI and upload service must advertise and accept only formats that can be
processed on the current Mac, with a clear setup path for optional formats.

## Functional requirements

1. Add and initialize a supported HEIC/HEIF decoder, or disable HEIC.
2. Verify decoder capability at application startup.
3. Expose supported receipt and statement extensions in the system-status
   payload without exposing sensitive information.
4. Build browser `accept` hints and upload validation from the same capability
   source.
5. Reject unsupported content with an actionable error before generation.
6. Validate that an uploaded image can be opened, not only that its extension
   is accepted.
7. Preserve the original uploaded file unchanged.

## Acceptance criteria

- A representative HEIC receipt can be decoded and passed to OCR/OpenAI image
  extraction, or HEIC is absent from the UI and rejected consistently.
- A renamed corrupt image fails during upload with a clear message.
- PNG, JPEG, TIFF, BMP, and PDF remain supported.

## Non-goals

- Editing or converting the original receipt in place.
