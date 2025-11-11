# GCP-Places-API-Vet-Scraper

Internal research project: a small script to experiment with Google Maps Platform Places API (Nearby Search + Place Details) for discovering businesses (e.g., veterinary clinics) around a geographic point / current IP location.

## Quickstart

1. Create a Python virtual environment (recommended) and activate it.

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create a `.env` file in the repository root with your API key (or set the environment variable `GCP_PLACES_API_KEY`). Example `.env` (copy `.env.example`):

```
GCP_PLACES_API_KEY=YOUR_API_KEY_HERE
```

4. Run the script. By default it attempts to locate your public IP and uses a 10 km radius searching for `restaurant`:

```powershell
python .\main.py
```

5. To limit results during testing or to target a specific coordinate, provide `--lat`, `--lng`, and `--radius`:

```powershell
python .\main.py --lat 37.4219 --lng -122.0840 --radius 1000 --type restaurant --out sample_places.json
```

6. Output: the script writes a JSON file (default `places_full.json`) containing place details for inspection.

## Files

- `main.py` — the Nearby Search + Place Details test script.
- `.env` / `.env.example` — environment keys.
- `requirements.txt` — Python dependencies.

## Next steps

- Swap default `--type` to search for veterinary-specific results (use `keyword=veterinary` or type `veterinary_care`) for production testing.
- Add retries/backoff and rate-limiting if you plan to run larger searches.
