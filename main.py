"""Simple test script to query Google Places (Places API Web Service).

This script:
- resolves an approximate lat/lon via public IP geolocation
- runs a Nearby Search for `type=restaurant` (default) within a radius (default 10000 m)
- follows pagination (up to 3 pages) and fetches Place Details for each result
- writes a JSON file `places_full.json` containing the detailed place objects

Notes:
- Set your API key in the environment variable GCP_PLACES_API_KEY or replace the placeholder below.
- This script uses free IP geolocation services (may be rate-limited). You can pass explicit --lat/--lng to skip IP lookup.
"""

import os
import sys
import time
import json
import argparse
from typing import Tuple, List, Dict, Any, Optional

import requests

# Auto-load environment variables from a local .env file when python-dotenv is installed.
try:
	from dotenv import load_dotenv
	load_dotenv()
except Exception:
	# If python-dotenv isn't installed, we'll fall back to reading real environment variables.
	pass

# Use an environment variable when possible; otherwise a visible placeholder is used.
API_KEY = os.getenv("GCP_PLACES_API_KEY", "YOUR_API_KEY_HERE")

IP_GEO_SERVICES = [
	"https://ipapi.co/json/",
	"https://ipinfo.io/json",
]


def get_ip_location() -> Optional[Tuple[float, float]]:
	"""Attempt to get approximate lat/lon for the current machine's public IP.

	Returns (lat, lon) or None if lookup fails.
	"""
	for url in IP_GEO_SERVICES:
		try:
			r = requests.get(url, timeout=5)
			r.raise_for_status()
			data = r.json()
			# ipapi.co returns 'latitude' and 'longitude'
			if "latitude" in data and "longitude" in data:
				return float(data["latitude"]), float(data["longitude"])
			# ipinfo.io returns 'loc' as "lat,lon"
			if "loc" in data:
				loc = data["loc"].split(",")
				return float(loc[0]), float(loc[1])
		except Exception:
			# Try next service on any failure
			continue
	return None


def nearby_search(lat: float, lng: float, radius: int, place_type: str, api_key: str) -> List[Dict[str, Any]]:
	"""Perform a Nearby Search and return aggregated result items (raw API results).

	This follows pagination using next_page_token (up to 3 pages as supported by the API).
	"""
	base = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
	results: List[Dict[str, Any]] = []
	params = {
		"location": f"{lat},{lng}",
		"radius": radius,
		"type": place_type,
		"key": api_key,
	}

	page = 0
	while True:
		page += 1
		resp = requests.get(base, params=params, timeout=10)
		resp.raise_for_status()
		data = resp.json()
		results.extend(data.get("results", []))

		next_token = data.get("next_page_token")
		if not next_token or page >= 3:
			break

		# Per Google docs, next_page_token may take a short time to become valid.
		time.sleep(2)
		params = {
			"pagetoken": next_token,
			"key": api_key,
		}

	return results


def place_details(place_id: str, api_key: str) -> Dict[str, Any]:
	"""Fetch Place Details for a place_id.

	We request a broad set of fields to capture the "place context" for testing.
	"""
	base = "https://maps.googleapis.com/maps/api/place/details/json"
	# A comprehensive set of fields that are commonly useful.
	fields = [
		"place_id",
		"name",
		"formatted_address",
		"address_components",
		"geometry",
		"plus_code",
		"types",
		"rating",
		"user_ratings_total",
		"price_level",
		"opening_hours",
		"permanently_closed",
		"photos",
		"website",
		"formatted_phone_number",
		"utc_offset",
		"reviews",
	]
	params = {
		"place_id": place_id,
		"fields": ",".join(fields),
		"key": api_key,
	}
	r = requests.get(base, params=params, timeout=10)
	r.raise_for_status()
	return r.json()


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description="Places API test script: Nearby Search + Details")
	parser.add_argument("--radius", type=int, default=10000, help="search radius in meters (default 10000 = 10 km)")
	parser.add_argument("--type", default="restaurant", help="place type to search for (default 'restaurant')")
	parser.add_argument("--lat", type=float, help="latitude (skip IP lookup)")
	parser.add_argument("--lng", type=float, help="longitude (skip IP lookup)")
	parser.add_argument("--out", default="places_full.json", help="output JSON filename")
	args = parser.parse_args(argv)

	if API_KEY == "YOUR_API_KEY_HERE":
		print("WARNING: Using placeholder API key. Set GCP_PLACES_API_KEY environment variable to a valid key.")

	if args.lat is None or args.lng is None:
		print("Attempting to determine approximate location from public IP...")
		loc = get_ip_location()
		if not loc:
			print("Failed to determine location from IP. Provide --lat and --lng to continue.")
			return 2
		lat, lng = loc
		print(f"Using approximate location lat={lat}, lng={lng}")
	else:
		lat, lng = args.lat, args.lng

	try:
		print(f"Running Nearby Search: type={args.type}, radius={args.radius}m")
		raw_results = nearby_search(lat, lng, args.radius, args.type, API_KEY)
		print(f"Nearby Search returned {len(raw_results)} raw results")

		detailed_places: List[Dict[str, Any]] = []
		for idx, item in enumerate(raw_results, start=1):
			place_id = item.get("place_id")
			name = item.get("name")
			print(f"[{idx}/{len(raw_results)}] Fetching details for: {name} ({place_id})")
			try:
				details = place_details(place_id, API_KEY)
			except Exception as e:
				print(f"  ERROR fetching details for {place_id}: {e}")
				# keep the raw item as fallback
				details = {"error": str(e), "raw": item}
			detailed_places.append(details)

		# Prepare output directory and filename with timestamp
		output_dir = "outputs"
		os.makedirs(output_dir, exist_ok=True)
		timestamp = time.strftime("%Y%m%dT%H%M%S")
		# Use the provided --out as a base name (without directory or extension)
		base_name = os.path.splitext(os.path.basename(args.out))[0]
		out_filename = f"{base_name}_{timestamp}.json"
		out_path = os.path.join(output_dir, out_filename)

		# Write full detailed results to JSON for inspection
		with open(out_path, "w", encoding="utf-8") as f:
			json.dump({"search_center": {"lat": lat, "lng": lng}, "places": detailed_places}, f, ensure_ascii=False, indent=2)

		print(f"Wrote detailed places to {out_path}")
		return 0

	except requests.HTTPError as e:
		print("HTTP error during API call:", e)
		return 3
	except Exception as e:
		print("Unexpected error:", e)
		return 4


if __name__ == "__main__":
	sys.exit(main())
