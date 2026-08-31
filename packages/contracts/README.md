# Contracts

This directory stores generated and reviewed API/domain contract artifacts.

`openapi.json` is generated from the FastAPI application with:

```powershell
python scripts/export_openapi.py
```

The contract is versioned from Milestone 0-1 so frontend and worker changes can be checked against a stable API surface before real AI or video processing is introduced.
