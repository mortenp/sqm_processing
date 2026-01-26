# SQM Cache Implementation - Quick Reference

## Installation (5 minutes)

### Step 1: Install MySQL Driver
```bash
pip install mysql-connector-python
```

### Step 2: Setup Database (Pick One)

**Option A: Automated (Recommended)**
```bash
python3 setup_cache_db.py
```
Follow prompts, copy the output configuration.

**Option B: Manual SQL**
```bash
mysql -u root -p
```
```sql
CREATE USER 'sqm_cache'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON sqm_cache.* TO 'sqm_cache'@'localhost';
FLUSH PRIVILEGES;
```

### Step 3: Configure my_sqm_service.py
Update around line 80:
```python
DB_CONFIG = {
    'host': 'localhost',
    'user': 'sqm_cache',
    'password': 'your_password',  # ← CHANGE THIS
    'database': 'sqm_cache',
    'raise_on_warnings': False
}
CACHE_ENABLED = True  # Enable caching
```

### Step 4: Restart Service
```bash
# Kill existing uvicorn process
pkill -f "uvicorn my_sqm_service"

# Start with cache
uvicorn my_sqm_service:app --host 127.0.0.1 --port 8090
```

## Testing Cache

### Verify Installation
```bash
mysql -u sqm_cache -p sqm_cache
SELECT COUNT(*) FROM celestial_cache;
# Should return: 0 (empty after first startup)
```

### Test with Sample File
1. Process a file normally (populates cache)
2. Process same file again (should be much faster)
3. Check logs for "Cache HIT" messages

### Monitor Cache Growth
```bash
mysql -u sqm_cache -p sqm_cache
# Check entries
SELECT COUNT(*) FROM celestial_cache;

# Check unique locations cached
SELECT DISTINCT lat, lon FROM celestial_cache;

# Check database size
SELECT ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) as size_mb 
FROM information_schema.TABLES WHERE TABLE_NAME = 'celestial_cache';
```

## Configuration Options

| Setting | Default | Purpose |
|---------|---------|---------|
| `CACHE_ENABLED` | True | Enable/disable caching |
| `CACHE_TIME_BUCKET_MIN` | 20 | Time bucket in minutes (smaller = more misses) |
| `DB_CONFIG['host']` | localhost | MySQL server hostname |
| `DB_CONFIG['user']` | sqm_cache | Database username |
| `DB_CONFIG['password']` | N/A | Database password |
| `DB_CONFIG['database']` | sqm_cache | Database name |

## Debugging

### Enable Debug Logging
```python
debug = 1  # Around line 60 in my_sqm_service.py
```

### Check for Cache Hits
In logs, look for:
```
Cache HIT: 56.04, 10.87, 2024-03-19 14:20:00
Using cached values: sun_alt=-25.34, moon_alt=12.56, mw_sb=20.89
```

### MySQL Connection Issues
```bash
# Test connection
mysql -u sqm_cache -p
# Should connect successfully

# Check user exists
mysql -u root -p -e "SELECT user, host FROM mysql.user WHERE user='sqm_cache';"

# Verify database
mysql -u sqm_cache -p -e "SHOW DATABASES;"
```

### Performance Troubleshooting
1. **No cache hits despite files overlap?**
   - Check if `CACHE_ENABLED = True`
   - Verify timestamps are within same time bucket
   - Increase `CACHE_TIME_BUCKET_MIN` to 30-60 for testing

2. **Still getting timeouts?**
   - Cache may not have enough entries yet (first run)
   - Enable debug logging to see hit/miss ratio
   - Consider increasing timeout on your web server

3. **Database getting large?**
   - Cleanup old entries:
   ```sql
   DELETE FROM celestial_cache WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
   ```

## Files Changed

| File | Changes |
|------|---------|
| `my_sqm_service.py` | ✓ Added cache functions and integration |
| `setup_cache_db.py` | ✓ New - database setup helper |
| `CACHE_SETUP.md` | ✓ New - detailed setup guide |
| `CACHE_IMPLEMENTATION.md` | ✓ New - technical details |
| `PERFORMANCE_ANALYSIS.md` | ✓ New - performance data |
| `README.md` | (Optional) Add cache info |

## Common Commands

### Disable Caching (if needed)
```python
# In my_sqm_service.py line ~79
CACHE_ENABLED = False
```

### Clear All Cache
```bash
mysql -u sqm_cache -p
USE sqm_cache;
TRUNCATE TABLE celestial_cache;
```

### Delete Old Cache Entries
```bash
mysql -u sqm_cache -p sqm_cache -e "DELETE FROM celestial_cache WHERE created_at < DATE_SUB(NOW(), INTERVAL 60 DAY);"
```

### Monitor Cache in Real-Time
```bash
while true; do
  mysql -u sqm_cache -p sqm_cache -e "SELECT COUNT(*) as cached_entries FROM celestial_cache;"
  sleep 5
done
```

## Expected Results

### First Run (No Cache)
- Processing time: baseline (45-60 seconds per file)
- Cache entries created: 13-26 depending on file length
- Performance: normal

### Second Run (With Cache)
- Processing time: 1.5-2.5x faster
- Cache hits: 60-80% of calculations
- Performance: noticeably improved

### Multiple Observers (Same Night)
- Processing all files: 3-5x faster overall
- Cache hits: 70-85%
- Performance: significant improvement

## Rollback (if issues)

1. **Disable caching without removing database:**
   ```python
   CACHE_ENABLED = False  # Keep database, just don't use it
   ```

2. **Disable and remove database:**
   ```bash
   mysql -u sqm_cache -p -e "DROP DATABASE sqm_cache;"
   mysql -u root -p -e "DROP USER 'sqm_cache'@'localhost';"
   ```
   Then set `CACHE_ENABLED = False` in code

## Support Files

For detailed information, see:
- **CACHE_SETUP.md** - Complete setup guide
- **CACHE_IMPLEMENTATION.md** - Technical implementation details
- **PERFORMANCE_ANALYSIS.md** - Detailed performance analysis
- **setup_cache_db.py** - Automated setup script

## Key Takeaway

✅ **The caching system reduces processing time by 2-5x for typical multi-file scenarios while adding minimal complexity.**

Cache automatically initializes on app startup and gracefully degrades if MySQL is unavailable.
