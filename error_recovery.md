# Error Recovery System Documentation

## Overview

The error recovery system ensures the pipeline continues functioning even when individual agents fail. It provides:
- Automatic retry with exponential backoff
- Fallback strategies for each agent
- Partial result handling
- Circuit breakers to prevent cascading failures
- Detailed error tracking and reporting

## Key Features

### 1. Error Classification
Errors are classified to determine the appropriate recovery strategy:

| Error Type | Description | Recovery Strategy |
|------------|-------------|-------------------|
| **Transient** | Temporary failures (network, timeout) | Retry with backoff |
| **Permanent** | Won't resolve with retry (auth, 404) | Use fallback immediately |
| **Degraded** | Partial functionality available | Continue with reduced features |
| **Unknown** | Unclassified errors | Treat as transient |

### 2. Retry Strategies
Each agent has configurable retry behavior:

```json
{
  "error_recovery": {
    "default_retry": {
      "max_attempts": 3,
      "initial_delay": 1.0,
      "max_delay": 30.0,
      "exponential_base": 2.0,
      "jitter": true
    },
    "agent_retry": {
      "summarizer": {
        "max_attempts": 3,
        "initial_delay": 2.0
      }
    }
  }
}
```

### 3. Circuit Breakers
Prevent repeated calls to failing services:

```json
{
  "circuit_breaker": {
    "threshold": 5,      // Failures before opening
    "timeout": 60        // Seconds before testing again
  }
}
```

## Agent Fallback Strategies

### Router Agent
- **Failure Impact**: Uses default routing
- **Fallback**: `["cache", "preparer", "navigator", "validator", "summarizer", "answerer"]`
- **User Experience**: Slightly less optimized pipeline

### Preparer Agent  
- **Failure Impact**: Basic search only
- **Fallback**: Uses original query as search term
- **User Experience**: Less targeted search results

### Navigator Agent
- **Failure Impact**: Basic URL selection
- **Fallback**: Extracts top URLs from search results
- **User Experience**: May fetch less relevant pages

### Validator Agent
- **Failure Impact**: Unvalidated content
- **Fallback**: Accepts all content with low confidence
- **User Experience**: May include lower quality sources

### Cache Agent
- **Failure Impact**: No caching benefits
- **Fallback**: Skip cache, fetch fresh data
- **User Experience**: Slower responses, no history

### Summarizer Agent
- **Failure Impact**: Basic summaries
- **Fallback**: Content extracts instead of summaries
- **User Experience**: Less refined information

### Answerer Agent
- **Failure Impact**: Basic answer
- **Fallback**: Concatenates available summaries
- **User Experience**: Less polished response

## Response Metadata

Responses include error recovery metadata when agents fail:

```json
{
  "answer": "...",
  "_metadata": {
    "quality_level": "partial",    // complete|partial|degraded
    "successful_agents": ["router", "preparer", "navigator"],
    "failed_agents": [
      {
        "agent": "validator",
        "error": "Timeout after 30s",
        "impact": "unvalidated_content"
      }
    ],
    "partial_result": true
  }
}
```

### Quality Levels
- **complete**: All agents succeeded
- **partial**: Non-critical agents failed
- **degraded**: Critical agents failed but fallbacks used

## Error Categories

### Network Errors
- Connection failures
- DNS resolution issues
- Timeout errors
- **Recovery**: Retry with exponential backoff

### Rate Limiting
- HTTP 429 errors
- API quota exceeded
- **Recovery**: Longer backoff delays

### Invalid Responses
- JSON parse errors
- Unexpected response format
- **Recovery**: Use fallback immediately

### Authentication
- Invalid API keys
- Expired tokens
- **Recovery**: Fail fast, no retry

## Monitoring Errors

### Check Agent Health
```bash
curl http://localhost:8000/api/monitoring | jq '.agents'
```

### View Error Logs
```bash
# All errors
grep -E "(ERROR|failed)" logs/*.log

# Retry attempts
grep "Retrying" logs/*.log

# Circuit breaker events
grep "Circuit breaker" logs/*.log

# Fallback usage
grep -i "fallback" logs/*.log
```

### Error Patterns
```bash
# Count errors by agent
grep "failed" logs/*.log | grep -oE "for \w+" | sort | uniq -c

# Find cascading failures  
grep -B2 -A2 "Circuit breaker opened" logs/*.log
```

## Configuration Examples

### High Reliability (More Retries)
```json
{
  "error_recovery": {
    "default_retry": {
      "max_attempts": 5,
      "initial_delay": 0.5,
      "max_delay": 60.0
    }
  }
}
```

### Fast Failure (Quick Fallbacks)
```json
{
  "error_recovery": {
    "default_retry": {
      "max_attempts": 1,
      "initial_delay": 0.5
    }
  }
}
```

### Agent-Specific Tuning
```json
{
  "agent_retry": {
    "cache": {
      "max_attempts": 1    // Cache not critical
    },
    "answerer": {
      "max_attempts": 5,   // Answer generation critical
      "initial_delay": 3.0
    }
  }
}
```

## Testing Error Recovery

### Simulate Network Failure
```python
# Temporarily block an agent's endpoint
iptables -A OUTPUT -p tcp --dport 11434 -j DROP
```

### Simulate Slow Response
```python
# Add latency to test timeouts
tc qdisc add dev lo root netem delay 35s
```

### Test Circuit Breaker
```python
# Send many requests to trigger circuit
for i in {1..10}; do
  curl -X POST http://localhost:8000/api/query \
    -d '{"query": "test circuit breaker"}'
done
```

## Best Practices

1. **Set Appropriate Timeouts**
   - Balance between reliability and response time
   - Consider user expectations

2. **Monitor Circuit Breakers**
   - Alert when circuits open frequently
   - Investigate root causes

3. **Log Fallback Usage**
   - Track which fallbacks activate most
   - Optimize those code paths

4. **Graceful Degradation**
   - Always provide some response
   - Inform users of degraded quality

5. **Test Failure Scenarios**
   - Regular chaos testing
   - Verify fallbacks work correctly

## Troubleshooting

### All Retries Failing
- Check network connectivity
- Verify API endpoints are correct
- Look for authentication issues

### Circuit Breaker Stuck Open
- Check timeout configuration
- Manually reset if needed
- Investigate persistent failures

### Poor Fallback Quality
- Enhance fallback strategies
- Consider caching good responses
- Add more intelligent defaults

### Cascading Failures
- Review agent dependencies
- Add more circuit breakers
- Implement bulkheads

## Benefits

1. **Resilience**: System continues despite failures
2. **User Experience**: Always get some answer
3. **Debugging**: Clear error tracking
4. **Flexibility**: Configurable per use case
5. **Recovery**: Automatic healing when issues resolve