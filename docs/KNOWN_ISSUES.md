# Known Issues - Logging v3.3

## 1. Exception Tracebacks Show in Plain Text

**Issue**: When an exception occurs in the application, the traceback is displayed in plain text format even when `LOG_FORMAT=json`.

**Example**:
```
Exception in ASGI application
Traceback (most recent call last):
  File "/path/to/file.py", line 190, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
...
```

**Why**: Python's built-in exception handler outputs directly to stderr, bypassing our logging formatters.

**Workaround**: Catch exceptions and log them using `logger.exception()` which will format them as JSON.

**Status**: To be fixed in future version by implementing custom exception handler.

---

## 2. Uvicorn Startup Messages May Vary

**Issue**: Some uvicorn startup messages ("Started server process", "Waiting for application startup") may not use custom formatters in all execution contexts.

**Why**: These messages are emitted before our logging configuration is fully applied.

**Workaround**: These are informational only and don't affect runtime logging.

**Status**: Low priority - does not impact production Loki/Grafana ingestion.

---

## 3. Backwards Compatibility

**Breaking Change**: v3.3 changes log format from plain text to structured JSON/human.

**Impact**: 
- Old log parsers expecting plain text will break
- `analyze_logs.py` now only parses JSON format

**Migration**:
- Update log parsers to handle JSON
- Use `LOG_FORMAT=human` for legacy text-based tools
- Tests must set `LOG_FORMAT=json` before running

---

## Planned Improvements

### v3.4 (Future)
- [ ] Custom exception handler for structured tracebacks
- [ ] Suppress all non-structured logs
- [ ] Log rotation support
- [ ] Performance metrics (request duration distribution)
- [ ] Grafana dashboard templates

