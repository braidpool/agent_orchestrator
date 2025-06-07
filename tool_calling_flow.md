# Tool Calling and Feedback Loop Documentation

## Overview

The tool calling mechanism allows agents to:
1. Request help from other agents when results are insufficient
2. Execute feedback loops to improve results iteratively
3. Dynamically adapt the pipeline based on intermediate results

## Key Components

### 1. Tool Registry
- Central registry of all agent capabilities
- Each agent registers its available tools
- Enables discovery and invocation of agent functions

### 2. Tool Calling Protocol
- Standardized way for agents to call each other
- Async execution with proper error handling
- Maintains context across tool calls

### 3. Feedback Loop Handler
- Manages iterative improvement cycles
- Configurable max iterations
- Quality evaluation at each step

## Tool Calling Flow

```
User Query
    ↓
[Initial Pipeline Execution]
    ↓
Quality Check (Summarizer)
    ├─ Sufficient → Continue
    └─ Insufficient → Request More Research
                        ↓
                    [Preparer: Generate Follow-up Searches]
                        ↓
                    [Execute Additional Research]
                        ↓
                    [Merge Results]
                        ↓
                    [Re-summarize]
    ↓
Answer Generation
    ├─ High Confidence → Return Answer
    └─ Low Confidence → Request Improvements
                        ↓
                    [Identify Missing Elements]
                        ↓
                    [Request Specific Help]
                        ↓
                    [Improve Answer]
```

## Implemented Tool Calls

### Summarizer → Preparer
**Tool**: `generate_followup_searches`
- **Trigger**: Identified information gaps
- **Parameters**: 
  - Original query
  - Current summaries
  - Identified gaps
- **Returns**: New search queries targeting gaps

### Answerer → Validator
**Tool**: `deep_validate`
- **Trigger**: Low confidence score
- **Parameters**:
  - Content to validate
  - Validation focus areas
- **Returns**: Enhanced validation results

### Any Agent → Cache
**Tool**: `semantic_search`
- **Trigger**: Need for related past information
- **Parameters**:
  - Search query
  - Relevance threshold
- **Returns**: Related cached content

## Configuration

### Enable Tool Calling
```json
{
  "tool_calling": {
    "enabled": true,
    "max_iterations": 3,
    "confidence_threshold": 0.6
  }
}
```

### Per-Agent Settings
```json
{
  "feedback_loops": {
    "summarizer": {
      "enabled": true,
      "trigger_on_gaps": true,
      "gap_threshold": 2
    },
    "answerer": {
      "enabled": true,
      "min_confidence": 0.6,
      "improvement_attempts": 2
    }
  }
}
```

## Quality Metrics

### Confidence Scoring
- Each agent evaluates its output quality
- Scores range from 0.0 to 1.0
- Triggers help requests below threshold

### Gap Detection
- Summarizer identifies missing information
- Specific gaps are catalogued
- Targeted searches fill gaps

### Iteration Tracking
- Each feedback loop is tracked
- Results improve with each iteration
- Stops at max iterations or quality threshold

## Example Scenarios

### Scenario 1: Insufficient Initial Results
1. User asks about recent scientific breakthrough
2. Initial search returns only news summaries
3. Summarizer detects lack of technical details
4. Requests searches for academic papers
5. Additional content improves answer quality

### Scenario 2: Low Confidence Answer
1. User asks complex analytical question
2. Answerer generates response but confidence is 0.4
3. Requests validation of key claims
4. Validator provides fact-checking
5. Answer improved with verified information

### Scenario 3: Evolving Query Understanding
1. Initial query is ambiguous
2. First results reveal multiple interpretations
3. Router re-evaluates query complexity
4. Pipeline adapts to use more agents
5. Final answer addresses all interpretations

## Benefits

1. **Adaptive Quality**: System improves results automatically
2. **Efficiency**: Only calls additional agents when needed
3. **Transparency**: Logs show why decisions were made
4. **Reliability**: Fallbacks prevent complete failures
5. **Scalability**: Easy to add new tool interactions

## Monitoring

Check logs for tool calling activity:
```bash
# See all tool calls
grep "Calling tool" logs/*.log

# See feedback loops
grep "feedback loop" logs/orchestrator.log

# See quality evaluations
grep "confidence" logs/answerer.log
```

## Future Enhancements

1. **Learning**: Track which tool calls improve results
2. **Optimization**: Skip tools that rarely help
3. **Parallel Execution**: Run independent tool calls concurrently
4. **Custom Workflows**: User-defined tool calling patterns
5. **Cross-Job Learning**: Use insights from past jobs