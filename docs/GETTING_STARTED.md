# Getting started on a Mac

NLP Expenses is a local application. Its browser interface runs only on your
Mac, and trip files remain local unless you choose **Best quality**, which sends
receipt content to the OpenAI API.

## Install

1. Install Python 3.11 or newer and Tesseract OCR. With Homebrew:

   ```bash
   brew install python@3.12 tesseract
   ```

2. Get the application source:

   - Developers can clone `https://github.com/omegabonobo/nlp-expense-trips.git`.
   - Alternatively, open the latest GitHub release, choose **Source code (zip)**,
     and unzip it somewhere permanent.

3. In Finder, double-click **NLP Expenses.command**. If macOS blocks the first
   launch, Control-click the file, choose **Open**, and confirm.

The first launch creates a private Python environment and installs the verified
runtime dependencies. Later launches reuse it and open the app in your browser.
Keep the launcher Terminal window open while using the app; closing it stops the
local server.

## Private data

A fresh clone or source download stores settings, receipts, statements, and
generated reports under:

```text
~/Documents/NLP Expenses Data/
```

This directory is separate from the application source, so updating or replacing
the source does not remove trip data. Existing checkouts that already contain a
`.env` or `trips/` directory continue using that checkout for compatibility.

Never commit receipts, statements, `.env`, generated workbooks, or exported trip
packages. They may contain personal, financial, or credential data.

## Receipt extraction

Basic/offline mode uses local OCR and does not require an account. Best quality
requires each user to enter their own OpenAI API key through **Add OpenAI key**.
The key is saved only in the private data directory's `.env` file.

## Update

For a Git clone, stop the app and run:

```bash
git pull --ff-only
```

Then double-click the launcher again. It refreshes the installed project when
needed. Developers should create their own branch before making changes.

For a ZIP installation, download and unzip the newer release, then launch it.
The separate data directory is discovered automatically.

## Troubleshooting

- **Python is missing or too old:** install Python 3.11+ and launch again.
- **Scans have poor Basic/offline results:** verify `tesseract --version` works,
  or use Best quality after configuring an individual API key.
- **The browser did not open:** keep the Terminal window open and use the local
  address printed there, normally beginning with `http://127.0.0.1:`.
- **Setup failed after an update:** delete only the source folder's `.venv`
  directory and launch again. Do not delete the separate data directory.

For development commands and release checks, see [DEVELOPMENT.md](DEVELOPMENT.md).
