# Cache Prepopulation - Quick Reference

## What It Does

Pre-calculates and caches astronomical values (sun altitude, moon altitude, Milky Way brightness) for an entire year or date range at a specific location. This guarantees instant cache hits when processing files from that location.

## Installation

Make sure dependencies are installed:

```bash
pip install mysql-connector-python astropy
```

## Quick Examples

### Prepopulate entire 2025 for your site

```bash
python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025
```

Expected time: ~40-60 minutes
Expected entries: ~26,280 (one per 20-minute time bucket)

### Prepopulate just March 2025

```bash
python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025 --month 3
```

Expected time: ~3-5 minutes
Expected entries: ~2,160

### Prepopulate custom date range

```bash
python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --start 2025-03-01 --end 2025-10-31
```

## What Happens

1. **Before prepopulation**: First file from location takes 45 seconds (calculations)
2. **After prepopulation**: All files from that location take 3-5 seconds (cache hits)

## Example Workflow

```bash
# 1. Prepopulate cache for 2025
python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025

# Wait 40-60 minutes...

# 2. Now process your files - they'll be instant!
# Upload files from location 56.04, 10.87
# Processing will be 10-20x faster

# 3. Check cache entries
mysql -u sqm_cache -p sqm_cache
SELECT COUNT(*) FROM celestial_cache WHERE lat = 56.0 AND lon = 11.0;
```

## For Multiple Observers

```bash
# Prepopulate for observer 1
python3 prepopulate_cache.py --lat 56.041 --lon 10.874 --year 2025

# Prepopulate for observer 2 (nearby)
python3 prepopulate_cache.py --lat 56.049 --lon 10.872 --year 2025

# Both will map to (56.0, 11.0) due to location rounding
# Same cache is used - both files are fast!
```

## Monitoring

```bash
# Watch progress in real-time
tail -f sqm_service.log | grep -E "Progress|complete"

# Check what's cached
mysql -u sqm_cache -p sqm_cache
SELECT COUNT(*) as total FROM celestial_cache;
SELECT DISTINCT lat, lon, COUNT(*) as entries FROM celestial_cache GROUP BY lat, lon;
```

## Performance by Scope

| Scope | Time | Entries |
|-------|------|---------|
| 1 day | 10 sec | 72 |
| 1 week | 1 min | 504 |
| 1 month | 3-5 min | ~2,160 |
| 3 months | 10-15 min | ~6,500 |
| 6 months | 20-30 min | ~13,000 |
| **1 year** | **40-60 min** | **~26,280** |

## Recommended Strategy

1. **First time**: Prepopulate 1 month or less to test
   ```bash
   python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025 --month 3
   ```

2. **Observation season**: Prepopulate 6 months before starting
   ```bash
   python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --start 2025-03-01 --end 2025-09-30
   ```

3. **Full year**: Prepopulate at start of year
   ```bash
   python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025
   ```

## Troubleshooting

### "Can't connect to MySQL"
- Ensure MySQL is running: `mysql -u sqm_cache -p`
- Check DB_CONFIG in my_sqm_service.py

### Script is very slow
- Normal for full year (~1 minute per 1000 entries)
- For faster testing, do one month instead: `--month 3`

### Want to re-prepopulate
- Script automatically updates existing entries (no duplicates)
- Just run it again with the same parameters

## Check Cache Contents

```sql
-- Count all cached entries
mysql -u sqm_cache -p sqm_cache
SELECT COUNT(*) FROM celestial_cache;

-- View by location
SELECT lat, lon, COUNT(*) FROM celestial_cache GROUP BY lat, lon;

-- See date range for a location
SELECT MIN(time_bucket), MAX(time_bucket) 
FROM celestial_cache WHERE lat = 56.0;

-- Clear old entries if needed
DELETE FROM celestial_cache WHERE lat = 56.0 AND lon = 11.0;
```

## Next Steps

1. Install dependencies: `pip install mysql-connector-python astropy`
2. Run prepopulation: `python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025`
3. Wait for completion (40-60 minutes for full year)
4. Process your files - they'll be 10x faster!
