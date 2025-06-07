# Graceful Shutdown Documentation

## Overview

The system now supports graceful shutdown to ensure:
- All pending database writes complete
- Active jobs have time to finish
- No data loss during shutdown
- Clean resource cleanup

## Key Components

### 1. StateManager Shutdown
- **Write Queue Draining**: Processes all queued writes before stopping
- **Timeout Protection**: 30-second timeout prevents hanging
- **Final Flush**: Ensures last-minute writes are saved
- **Status Tracking**: Monitors pending writes and queue size

### 2. Signal Handling
- **SIGTERM/SIGINT**: Graceful shutdown on Ctrl+C or kill
- **Platform Support**: Works on Linux/Mac (limited on Windows)
- **Task Cancellation**: Cleanly cancels all running asyncio tasks
- **Event Loop Cleanup**: Proper cleanup of asyncio resources

### 3. Orchestrator Shutdown
- **Active Job Tracking**: Waits for running jobs to complete
- **Configurable Timeout**: 30-second timeout for active jobs
- **Cascade Shutdown**: Stops all components in order

## Shutdown Process

```
1. Signal Received (SIGTERM/SIGINT)
   ↓
2. Orchestrator.stop() called
   ├─ Wait for active jobs (30s timeout)
   └─ Log remaining active jobs
   ↓
3. StateManager.stop() called
   ├─ Set shutdown event
   ├─ Drain write queue
   ├─ Complete pending writes
   └─ Cancel writer task
   ↓
4. Final cleanup
   ├─ Close database connections
   ├─ Cancel remaining tasks
   └─ Close event loop
```

## Configuration

### Timeouts
```python
# In StateManager
self._shutdown_timeout = 30  # seconds for queue drain

# In Orchestrator  
timeout = 30  # seconds for active jobs
```

### Monitoring Endpoints

**Health Check with Queue Status**:
```bash
curl http://localhost:8000/api/health
```

Response:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-20T10:30:00",
  "state_manager": {
    "queue_size": 5,
    "pending_writes": 2,
    "is_shutting_down": false,
    "writer_running": true
  },
  "active_jobs": 3
}
```

**Detailed Monitoring**:
```bash
curl http://localhost:8000/api/monitoring
```

## Usage

### Starting the Server
```bash
python main.py
# Output: Press Ctrl+C to stop the server gracefully
```

### Graceful Shutdown
```bash
# Method 1: Keyboard Interrupt
Ctrl+C

# Method 2: Kill Signal
kill -TERM <pid>

# Method 3: Systemd
systemctl stop agent-orchestrator
```

### Docker Support
```dockerfile
# In Dockerfile
STOPSIGNAL SIGTERM

# docker-compose.yml
services:
  orchestrator:
    stop_grace_period: 60s
```

### Systemd Service
```ini
[Unit]
Description=LLM Agent Orchestrator
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python /path/to/main.py
Restart=on-failure
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

## Monitoring During Shutdown

### Watch Queue Status
```bash
while true; do
  curl -s http://localhost:8000/api/monitoring | jq .state_manager
  sleep 1
done
```

### Check Logs
```bash
# Watch shutdown progress
tail -f logs/orchestrator.log | grep -E "(shutdown|stopping|stopped)"

# Check for write errors
tail -f logs/state_manager.log | grep -E "(error|flush)"
```

## Testing Graceful Shutdown

### Test Script
```python
import asyncio
import aiohttp

async def flood_test():
    """Send many requests then shutdown"""
    async with aiohttp.ClientSession() as session:
        # Send 50 requests
        tasks = []
        for i in range(50):
            task = session.post(
                'http://localhost:8000/api/query',
                json={"query": f"Test query {i}"}
            )
            tasks.append(task)
        
        # Wait for all to start
        await asyncio.gather(*tasks, return_exceptions=True)
        
        print("Requests sent. Now shutdown the server with Ctrl+C")
        print("Watch the logs to see graceful shutdown in action")

asyncio.run(flood_test())
```

## Troubleshooting

### Queue Not Draining
- Check `pending_writes` in monitoring
- Look for database lock errors
- Increase `_shutdown_timeout` if needed

### Jobs Not Completing
- Check individual agent logs
- Look for stuck LLM calls
- Consider reducing job timeout

### Data Loss Prevention
- Always use graceful shutdown
- Monitor queue size regularly
- Set up alerts for queue > 100

## Best Practices

1. **Regular Monitoring**: Check queue status periodically
2. **Graceful Restarts**: Always use signals, not kill -9
3. **Timeout Tuning**: Adjust timeouts based on workload
4. **Load Shedding**: Reject new requests during shutdown
5. **Health Checks**: Use /api/health in load balancers

## Benefits

- **Zero Data Loss**: All writes complete before shutdown
- **Clean Restarts**: No corrupted state on restart
- **Debugging**: Clear logs show shutdown progress
- **Production Ready**: Handles real-world scenarios
- **Monitoring**: Full visibility into system state