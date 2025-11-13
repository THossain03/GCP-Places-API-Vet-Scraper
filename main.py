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
import csv
import shutil
import re

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


def nearby_search(lat: float, lng: float, radius: int, api_key: str, queries: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
	"""Perform multiple Nearby Search queries (type/keyword combos) and return deduplicated result items.

	`queries` should be a list of dicts with either 'type' or 'keyword' (or both). Example:
	  [{'type': 'veterinary_care'}, {'keyword': 'veterinary'}, {'keyword': 'vet'}]

	We follow pagination for each query and dedupe by place_id.
	"""
	base = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
	results_by_id: Dict[str, Dict[str, Any]] = {}

	for q in queries:
		params = {
			"location": f"{lat},{lng}",
			"radius": radius,
			"key": api_key,
		}
		if q.get("type"):
			params["type"] = q["type"]
		if q.get("keyword"):
			params["keyword"] = q["keyword"]

		page = 0
		while True:
			page += 1
			resp = requests.get(base, params=params, timeout=10)
			resp.raise_for_status()
			data = resp.json()
			for item in data.get("results", []):
				pid = item.get("place_id")
				if not pid:
					continue
				# Keep first-seen item for each place_id
				if pid not in results_by_id:
					results_by_id[pid] = item

			next_token = data.get("next_page_token")
			if not next_token or page >= 3:
				break

			# Per Google docs, next_page_token may take a short time to become valid.
			time.sleep(2)
			params = {
				"pagetoken": next_token,
				"key": api_key,
			}

	return list(results_by_id.values())


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


TIER1_TYPES = {
	"veterinary_care",
	"veterinarian",
	"veterinary_pharmacy",
	"animal_hospital",
	"emergency_veterinarian_service",
}

# Secondary types that are pet-related; require stronger textual evidence to classify as vet
TIER2_TYPES = {
	"pet_store",
	"pet_groomer",
	"pet_care_service",
	"pet_boarding_service",
	"pet_trainer",
	"animal_shelter",
}

# Wider sweep (optional) — farm and hospital variants
TIER3_TYPES = {
	"farm",
	"animal_husbandry",
}

KEYWORD_STRONG = [
	"vet",
	"veterinary",
	"veterinarian",
	"animal",
	"pet",
	"clinic",
	"animal hospital",
	"spay",
	"neuter",
	"canine",
	"feline",
]


def text_has_keyword(text: Optional[str]) -> bool:
	if not text:
		return False
	t = text.lower()
	for kw in KEYWORD_STRONG:
		if kw in t:
			return True
	return False


def classify_place(details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
	"""Classify a place details response into Tier 1 (highly likely), Tier 2 (probable), or None.

	Returns an analysis dict when the place should be kept, or None to discard.
	"""
	res = details.get("result", {})
	types = set(res.get("types", []))
	name = res.get("name", "")
	address = res.get("formatted_address", "")
	website = res.get("website", "")

	# Tier 1: explicit type match
	matched_t1 = list(TIER1_TYPES & types)
	if matched_t1:
		return {
			"tier": 1,
			"label": "highly likely vet clinic/hospital with BMS",
			"match": "type",
			"matched_types": matched_t1,
		}

	# Tier 2: secondary types but require textual evidence
	matched_t2 = list(TIER2_TYPES & types)
	if matched_t2:
		# Strong if name/address/website contains vet-related keywords
		if text_has_keyword(name) or text_has_keyword(address) or text_has_keyword(website):
			return {
				"tier": 2,
				"label": "probable",
				"match": "type+keyword",
				"matched_types": matched_t2,
			}

	# Tier 3: optional wider-sweep types — treat similarly to Tier2 but weaker
	matched_t3 = list(TIER3_TYPES & types)
	if matched_t3:
		if text_has_keyword(name) or text_has_keyword(address) or text_has_keyword(website):
			return {
				"tier": 3,
				"label": "possible (wider sweep)",
				"match": "type+keyword",
				"matched_types": matched_t3,
			}

	# Also consider places that don't have vet types but whose text strongly indicates veterinary
	if text_has_keyword(name) or text_has_keyword(address) or text_has_keyword(website):
		# Without vet types this is weaker but still useful — mark as probable (tier 2)
		return {
			"tier": 2,
			"label": "probable (text-match)",
			"match": "keyword",
		}

	return None


def score_place(details: Dict[str, Any]) -> Dict[str, Any]:
	"""Compute a heuristic score (0-100) estimating likelihood the place exposes appointment/slot-level BMS.

	Scoring is conservative and local-only. It combines:
	  - type-based signals (strong if 'veterinary_care')
	  - textual keyword matches in name/address/website
	  - presence of booking-related words on the website URL or in known booking providers
	  - small boosts for contact presence and ratings

	Returns a dict: {score: float, reasons: {name:weight,...}}
	"""
	res = details.get("result", {})
	types = set(res.get("types", []))
	name = (res.get("name") or "").lower()
	address = (res.get("formatted_address") or "").lower()
	website = (res.get("website") or "").lower()
	phone = bool(res.get("formatted_phone_number"))

	reasons: Dict[str, float] = {}
	score = 0.0

	# Type signals
	if "veterinary_care" in types:
		reasons["type:veterinary_care"] = 60.0
		score += 60.0
	else:
		inter = TIER2_TYPES & types
		if inter:
			# weaker signal for secondary types
			reasons[f"type:{','.join(sorted(inter))}"] = 20.0
			score += 20.0

	# Keyword matches in name/address/website
	kw_hits = 0
	for kw in KEYWORD_STRONG:
		if kw in name:
			kw_hits += 1
			reasons[f"name_contains:{kw}"] = reasons.get(f"name_contains:{kw}", 0) + 6.0
			score += 6.0
		if kw in address:
			kw_hits += 1
			reasons[f"address_contains:{kw}"] = reasons.get(f"address_contains:{kw}", 0) + 3.0
			score += 3.0
		if kw in website:
			kw_hits += 1
			reasons[f"website_contains:{kw}"] = reasons.get(f"website_contains:{kw}", 0) + 8.0
			score += 8.0

	# Booking/appointment signals on website URL itself (cheap heuristic)
	booking_tokens = ["book", "appointment", "schedule", "online-booking", "reserve", "booking", "calendly", "setmore", "patient portal", "portal", "appointments"]
	for bt in booking_tokens:
		if bt in website:
			reasons[f"website_book_token:{bt}"] = 15.0
			score += 15.0
			break

	# Presence of phone is a small positive signal
	if phone:
		reasons["has_phone"] = 5.0
		score += 5.0

	# Ratings / popularity: normalize user_ratings_total into a small boost (capped)
	urt = res.get("user_ratings_total") or 0
	if urt > 0:
		boost = min(5.0, urt / 1000.0 * 5.0)  # up to +5 points for many ratings
		reasons["user_ratings_total"] = round(boost, 2)
		score += boost

	# Cap score to 100
	final_score = max(0.0, min(100.0, score))
	return {"score": round(final_score, 2), "reasons": reasons}

def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description="Places API test script: Nearby Search + Details")
	parser.add_argument("--radius", type=int, default=25000, help="search radius in meters (default 25000 = 25 km)")
	parser.add_argument("--type", default="restaurant", help="place type to search for (default 'restaurant')")
	parser.add_argument("--lat", type=float, help="latitude (skip IP lookup)")
	parser.add_argument("--lng", type=float, help="longitude (skip IP lookup)")
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
		print(f"Running targeted Nearby Searches for veterinary-related places within radius={args.radius}m")

		# Build queries from tiered types and a few keyword fallbacks
		type_queries: List[Dict[str, Optional[str]]] = []
		for t in sorted(list(TIER1_TYPES | TIER2_TYPES | TIER3_TYPES)):
			type_queries.append({"type": t})
		# Add some keyword fallbacks to catch variations
		keyword_queries = [{"keyword": k} for k in ["veterinary", "vet", "animal hospital", "pet clinic"]]
		queries = type_queries + keyword_queries

		# Single-radius search only (minimize API calls) — default radius is 25km
		raw_results = nearby_search(lat, lng, args.radius, API_KEY, queries)
		print(f"Nearby Search returned {len(raw_results)} unique raw results (deduped)")

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

			# Require website presence (we need a website to query for appointment widget etc.)
			res_quick = details.get("result", {})
			website = res_quick.get("website")
			if not website:
				print(f"  Discarding {place_id}: no website present")
				continue

			# Compute a heuristic AI-style score (local rules) to rank BMS likelihood
			score_info = score_place(details)
			heuristic_score = score_info.get("score", 0.0)

			# Decision rules for inclusion using the heuristic score:
			# - If heuristic > 30: include immediately (strong signal)
			# - If heuristic < 10: discard immediately (too weak)
			# - If heuristic in [10,30]: treat as borderline and use the heuristic value
			final_score = heuristic_score

			if heuristic_score > 30.0:
				print(f"  Heuristic strong: {place_id} score={heuristic_score} (>30) -> include")
			elif heuristic_score < 10.0:
				print(f"  Heuristic weak: {place_id} score={heuristic_score} (<10) -> discard")
				continue
			else:
				# borderline case: 10-30 -> use heuristic
				print(f"  Borderline {place_id} (heuristic={heuristic_score}) -> using heuristic")
				final_score = heuristic_score

			# Require final score > 20 to include
			if final_score <= 20.0:
				print(f"  Discarding {place_id}: final_score={final_score} <= 20.0")
				continue

			# Optionally also derive a tiered label from classify_place (keeps previous behavior)
			tier_info = classify_place(details) or {}

			# Attach analysis and scoring to the top-level details object
			details_out = dict(details)  # shallow copy
			details_out["analysis"] = tier_info
			details_out["heuristic_score"] = heuristic_score
			details_out["final_score"] = final_score
			# Script stores heuristic analysis only
			details_out["score_reasons"] = score_info.get("reasons", {})

			# Provide a concise candidate summary for downstream heuristics
			res = details_out.get("result", {})
			candidate_summary = {
				"place_id": res.get("place_id"),
				"name": res.get("name"),
				"formatted_address": res.get("formatted_address"),
				"types": res.get("types", []),
				"website": res.get("website"),
				"formatted_phone_number": res.get("formatted_phone_number"),
				"rating": res.get("rating"),
				"user_ratings_total": res.get("user_ratings_total"),
			}
			details_out["candidate_summary"] = candidate_summary

			detailed_places.append(details_out)

		# Prepare output directory and filename with timestamp
		output_dir = "outputs"
		os.makedirs(output_dir, exist_ok=True)
		timestamp = time.strftime("%Y%m%dT%H%M%S")

		# Archive any existing top-level outputs matching the pattern
		archives_base = os.path.join(output_dir, "archives")
		jsons_dir = os.path.join(archives_base, "jsons")
		csvs_dir = os.path.join(archives_base, "csvs")
		os.makedirs(jsons_dir, exist_ok=True)
		os.makedirs(csvs_dir, exist_ok=True)

		# Only match files directly under outputs/ with the expected naming pattern
		pattern = re.compile(r"^places_full_\d{8}T\d{6}\.(json|csv)$")
		for fname in os.listdir(output_dir):
			# Skip archives directory and subdirectories
			fpath = os.path.join(output_dir, fname)
			if not os.path.isfile(fpath):
				continue
			if not pattern.match(fname):
				continue
			lower = fname.lower()
			if lower.endswith('.json'):
				dest_dir = jsons_dir
			elif lower.endswith('.csv'):
				dest_dir = csvs_dir
			else:
				dest_dir = archives_base

			dest = os.path.join(dest_dir, fname)
			try:
				shutil.move(fpath, dest)
				print(f"Archived existing output {fpath} -> {dest}")
			except Exception as e:
				print(f"Failed to archive {fpath}: {e}")

		# Use a fixed base name so outputs are predictable
		base_name = "places_full"
		out_filename = f"{base_name}_{timestamp}.json"
		out_path = os.path.join(output_dir, out_filename)

		# Build a simplified, flat JSON that's CSV-friendly and contains the most useful fields per place
		from urllib.parse import urlparse, parse_qs

		def extract_component(ac_list, ac_type, short=False):
			if not ac_list:
				return ""
			for ac in ac_list:
				types = ac.get("types", [])
				if ac_type in types:
					return ac.get("short_name") if short else ac.get("long_name")
			return ""

		simplified: List[Dict[str, Any]] = []
		for d in detailed_places:
			res = d.get("result") or {}
			# Basic fields
			name = res.get("name", "")
			address = res.get("formatted_address", "")
			types = res.get("types", []) or []
			primary_category = types[0] if types else ""
			phone = res.get("formatted_phone_number", "")

			# Address components
			ac = res.get("address_components", []) or []
			zip_code = extract_component(ac, "postal_code")
			city = extract_component(ac, "locality") or extract_component(ac, "postal_town") or extract_component(ac, "administrative_area_level_2")
			state = extract_component(ac, "administrative_area_level_1")
			state_code = extract_component(ac, "administrative_area_level_1", short=True)

			# Plus code
			plus = res.get("plus_code") or {}
			plus_code = plus.get("global_code") or plus.get("compound_code") or ""

			website = res.get("website", "")

			# CID (try to parse from maps URL if present)
			cid = ""
			maps_url = res.get("url") or res.get("canonical_url") or ""
			if maps_url:
				try:
					q = parse_qs(urlparse(maps_url).query)
					cid_vals = q.get("cid")
					if cid_vals:
						cid = cid_vals[0]
				except Exception:
					cid = ""

			# Location
			lat_val = None
			lng_val = None
			geom = res.get("geometry") or {}
			loc = geom.get("location") if geom else None
			if loc:
				lat_val = loc.get("lat")
				lng_val = loc.get("lng")

			# Ratings
			total_reviews = res.get("user_ratings_total") or 0
			avg_rating = res.get("rating") or 0

			# Star breakdown from sampled reviews (may be partial)
			star_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
			for rv in res.get("reviews", []) or []:
				rscore = rv.get("rating")
				try:
					ir = int(round(float(rscore)))
				except Exception:
					continue
				if ir < 1:
					ir = 1
				if ir > 5:
					ir = 5
				star_counts[ir] = star_counts.get(ir, 0) + 1

			# Opening hours (weekday_text is usually ['Monday: ...', ...])
			oh = res.get("opening_hours") or {}
			weekday_text = oh.get("weekday_text") or []
			# Map defaults
			hours_map = {"Monday": "", "Tuesday": "", "Wednesday": "", "Thursday": "", "Friday": "", "Saturday": "", "Sunday": ""}
			for line in weekday_text:
				if not isinstance(line, str):
					continue
				if ":" in line:
					try:
						day, times = line.split(":", 1)
						day = day.strip()
						hours_map[day] = times.strip()
					except Exception:
						continue

			simplified.append({
				"business_name": name,
				"address": address,
				"primary_category": primary_category,
				"phone": phone,
				"zip_code": zip_code,
				"city": city,
				"state": state,
				"state_code": state_code,
				"plus_code": plus_code,
				"website": website,
				"cid": cid,
				"latitude": lat_val,
				"longitude": lng_val,
				"total_reviews": total_reviews,
				"average_rating": avg_rating,
				"1_star_reviews": star_counts[1],
				"2_star_reviews": star_counts[2],
				"3_star_reviews": star_counts[3],
				"4_star_reviews": star_counts[4],
				"5_star_reviews": star_counts[5],
				"monday_hours": hours_map.get("Monday", ""),
				"tuesday_hours": hours_map.get("Tuesday", ""),
				"wednesday_hours": hours_map.get("Wednesday", ""),
				"thursday_hours": hours_map.get("Thursday", ""),
				"friday_hours": hours_map.get("Friday", ""),
				"saturday_hours": hours_map.get("Saturday", ""),
				"sunday_hours": hours_map.get("Sunday", ""),
			})

		# Write simplified JSON (pretty-printed for readability)
		with open(out_path, "w", encoding="utf-8") as f:
			json.dump({"search_center": {"lat": lat, "lng": lng}, "places": simplified}, f, ensure_ascii=False, indent=2)

		# Also write a CSV alongside the JSON for easy analysis / spreadsheet import.
		csv_filename = f"{base_name}_{timestamp}.csv"
		csv_path = os.path.join(output_dir, csv_filename)

		# Define CSV columns in a stable order to match the JSON fields.
		fieldnames = [
			"business_name",
			"address",
			"primary_category",
			"phone",
			"zip_code",
			"city",
			"state",
			"state_code",
			"plus_code",
			"website",
			"cid",
			"latitude",
			"longitude",
			"total_reviews",
			"average_rating",
			"1_star_reviews",
			"2_star_reviews",
			"3_star_reviews",
			"4_star_reviews",
			"5_star_reviews",
			"monday_hours",
			"tuesday_hours",
			"wednesday_hours",
			"thursday_hours",
			"friday_hours",
			"saturday_hours",
			"sunday_hours",
		]

		try:
			# Use newline='' for correct newline handling on Windows
			with open(csv_path, "w", encoding="utf-8", newline='') as csvf:
				writer = csv.DictWriter(csvf, fieldnames=fieldnames)
				writer.writeheader()
				for row in simplified:
					# Ensure all keys exist and None -> '' for CSV cleanliness
					out_row = {k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames}
					writer.writerow(out_row)
			print(f"Wrote JSON to {out_path} and CSV to {csv_path}")
		except Exception as e:
			print(f"Wrote JSON to {out_path} but failed to write CSV: {e}")

		return 0

	except requests.HTTPError as e:
		print("HTTP error during API call:", e)
		return 3
	except Exception as e:
		print("Unexpected error:", e)
		return 4


if __name__ == "__main__":
	sys.exit(main())
