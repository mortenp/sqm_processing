# SQM Processing - Celestial Calculations Cache Setup

## Overview

The SQM processing service now includes a MySQL-backed caching system that dramatically improves performance by avoiding redundant astronomical calculations (sun altitude, moon altitude, Milky Way brightness) for similar timestamps and locations.

### Performance Improvement

- **Time Buckets**: Calculations are cached at 20-minute intervals
- **When Caching Helps Most**:
  - Processing multiple files from the same location with overlapping timestamps
  - Reprocessing the same data
  - Processing data within the same night from the same observer

- **Expected Speedup**: 2-10x faster processing for repeated locations/times, depending on file overlap

## Installation & Configuration

### 1. Install MySQL Python Driver

If not already installed:
```bash
pip install mysql-connector-python
```

### 2. Create MySQL Database User

Connect to your MySQL server:
```bash
mysql -u root -p
```

Then run:
```sql
CREATE USER 'sqm_cache'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON sqm_cache.* TO 'sqm_cache'@'localhost';
FLUSH PRIVILEGES;
```

### 3. Configure Credentials in my_sqm_service.py

Update the `DB_CONFIG` dictionary with your MySQL credentials (around line 80):

```python
DB_CONFIG = {
    'host': 'localhost',      # Your MySQL host
    'user': 'sqm_cache',      # Database user
    'password': 'your_secure_password',  # User password
    'database': 'sqm_cache',   # Database name
    'raise_on_warnings': False
}
```

### 4. Enable/Disable Caching

Toggle caching in the configuration section (around line 79):

```python
CACHE_ENABLED = True  # Set to False to disable caching
```

You can also adjust the cache time bucket granularity:
```python
CACHE_TIME_BUCKET_MIN = 20  # Cache granularity: 20 minutes (adjust as needed)
```

## Database Schema

The system automatically creates the required table on startup:

```sql
CREATE TABLE celestial_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lat DECIMAL(10, 6) NOT NULL,
    lon DECIMAL(10, 6) NOT NULL,
    time_bucket DATETIME NOT NULL,
    sun_alt FLOAT,
    moon_alt FLOAT,
    mw_brightness FLOAT,
    milky_way_visible BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_calc (lat, lon, time_bucket)
);
```

## How It Works

### Caching Logic

For each location and time:
1. **Time Bucketing**: The timestamp is rounded to the nearest N minutes (default: 20)
2. **Cache Lookup**: Check if `(latitude, longitude, time_bucket)` exists in the database
3. **Cache Hit**: If found, use stored values instantly
4. **Cache Miss**: Calculate values, store them, return calculated values
5. **Updates**: Duplicate entries are automatically updated with new values

### What Gets Cached

- **Sun Altitude** (degrees above horizon)
- **Moon Altitude** (degrees above horizon)
- **Milky Way Surface Brightness** (mag/arcsec²)
- **Milky Way Visibility** (boolean: visible or not)

### What Doesn't Get Cached

The rolling MPSAS statistics (standard deviation, averages) are still calculated per-line as they depend on the specific data content, not just timestamps.

## Monitoring & Debugging

### Enable Debug Logging

Set `debug = 1` in the configuration to see:
- "Cache HIT" and "Cache MISS" messages
- Cached vs. calculated values
- Zenith angle and Milky Way parameters

Example log output:
```
Cache HIT: 56.04, 10.87, 2025-01-26 14:20:00
Using cached values: sun_alt=-25.34, moon_alt=15.62, mw_sb=20.89
```

### Check Cache Size

View your cache database:
```sql
USE sqm_cache;
SELECT COUNT(*) as total_cached FROM celestial_cache;
SELECT DISTINCT lat, lon FROM celestial_cache;
SELECT * FROM celestial_cache WHERE lat = 56.04 LIMIT 5;
```

### Clear Cache (if needed)

```sql
DELETE FROM sqm_cache.celestial_cache WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
```

## Fallback Behavior

If MySQL is unavailable or caching fails:
1. The service logs a warning but continues normally
2. All calculations are performed in real-time
3. No timeout occurs - the service degrades gracefully

To disable caching completely and avoid connection attempts:
```python
CACHE_ENABLED = False
```

## Performance Tips

1. **Adjust Time Bucket**: For more frequent calculations, reduce `CACHE_TIME_BUCKET_MIN` (e.g., to 10)
   - Smaller bucket = more cache misses but more accurate per-timestamp
   - Larger bucket = fewer cache entries, more cache hits

2. **Cache Maintenance**: Periodically clean old entries:
   ```sql
   DELETE FROM celestial_cache WHERE created_at < DATE_SUB(NOW(), INTERVAL 60 DAY);
   ```

3. **Monitor Database Size**: Cache grows with unique (lat, lon, time_bucket) combinations
   - Typical: ~1-10 MB per observer location per month

## Troubleshooting

### "Cache database initialization failed"

**Problem**: Can't connect to MySQL
- Check MySQL is running: `mysql -u root -p`
- Verify credentials in `DB_CONFIG`
- Ensure MySQL user exists with proper permissions
- Set `CACHE_ENABLED = False` to run without caching

### Cache not being used

**Check**: Look for "Cache MISS" messages in logs
- Might indicate new location/time combinations
- Time buckets might not be aligning (check CACHE_TIME_BUCKET_MIN)
- Verify database is populated: `SELECT COUNT(*) FROM celestial_cache;`

### Database running out of disk space

Clean old entries:
```sql
DELETE FROM celestial_cache WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
OPTIMIZE TABLE celestial_cache;
```

## Integration with Existing Setup

The caching is fully backward compatible:
- Existing code continues to work unchanged
- Database initialization happens automatically on app startup
- If caching fails, the service continues processing normally
- No changes needed to the `/process` endpoint

## Future Enhancements

Possible improvements:
- Redis backend for faster in-memory caching
- Cache statistics endpoint (hit rate, size)
- Configurable cleanup policies
- Support for multiple observer locations with automatic routing
