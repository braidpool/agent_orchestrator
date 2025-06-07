import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List, Type
from enum import Enum
from datetime import datetime
import aiohttp
import json

logger = logging.getLogger("ErrorHandler")

class ErrorType(Enum):
    """Classification of error types"""
    TRANSIENT = "transient"  # Retry might help
    PERMANENT = "permanent"  # Retry won't help
    DEGRADED = "degraded"   # Can continue with reduced functionality
    UNKNOWN = "unknown"     # Not sure, treat as transient

class ErrorCategory(Enum):
    """Categories of errors for handling strategies"""
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_RESPONSE = "invalid_response"
    AUTHENTICATION = "authentication"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    INTERNAL = "internal"
    UNKNOWN = "unknown"

class ErrorClassifier:
    """Classifies errors to determine handling strategy"""
    
    @staticmethod
    def classify(error: Exception) -> tuple[ErrorType, ErrorCategory]:
        """Classify an error into type and category"""
        
        # Network errors - usually transient
        if isinstance(error, aiohttp.ClientError):
            if isinstance(error, aiohttp.ClientConnectorError):
                return ErrorType.TRANSIENT, ErrorCategory.NETWORK
            elif isinstance(error, aiohttp.ServerTimeoutError):
                return ErrorType.TRANSIENT, ErrorCategory.TIMEOUT
            elif isinstance(error, aiohttp.ClientResponseError):
                if error.status == 429:  # Too Many Requests
                    return ErrorType.TRANSIENT, ErrorCategory.RATE_LIMIT
                elif error.status in [401, 403]:  # Auth errors
                    return ErrorType.PERMANENT, ErrorCategory.AUTHENTICATION
                elif error.status >= 500:  # Server errors
                    return ErrorType.TRANSIENT, ErrorCategory.NETWORK
                elif error.status >= 400:  # Client errors
                    return ErrorType.PERMANENT, ErrorCategory.INVALID_RESPONSE
        
        # Timeout errors
        elif isinstance(error, asyncio.TimeoutError):
            return ErrorType.TRANSIENT, ErrorCategory.TIMEOUT
        
        # JSON parsing errors - usually permanent
        elif isinstance(error, json.JSONDecodeError):
            return ErrorType.PERMANENT, ErrorCategory.INVALID_RESPONSE
        
        # Resource errors
        elif isinstance(error, MemoryError):
            return ErrorType.TRANSIENT, ErrorCategory.RESOURCE_EXHAUSTED
        
        # Default classification
        return ErrorType.UNKNOWN, ErrorCategory.UNKNOWN

class RetryStrategy:
    """Configurable retry strategy with exponential backoff"""
    
    def __init__(self, 
                 max_attempts: int = 3,
                 initial_delay: float = 1.0,
                 max_delay: float = 60.0,
                 exponential_base: float = 2.0,
                 jitter: bool = True):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number"""
        delay = min(
            self.initial_delay * (self.exponential_base ** (attempt - 1)),
            self.max_delay
        )
        
        if self.jitter:
            # Add random jitter (±25%)
            import random
            jitter_factor = 0.75 + (random.random() * 0.5)
            delay *= jitter_factor
        
        return delay
    
    def should_retry(self, attempt: int, error_type: ErrorType) -> bool:
        """Determine if we should retry based on attempt and error type"""
        if error_type == ErrorType.PERMANENT:
            return False
        
        return attempt < self.max_attempts

class ErrorRecoveryHandler:
    """Handles error recovery strategies"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("error_recovery", {})
        self.default_strategy = RetryStrategy(**self.config.get("default_retry", {}))
        self.agent_strategies = self._load_agent_strategies()
        self.fallback_handlers = self._load_fallback_handlers()
        
    def _load_agent_strategies(self) -> Dict[str, RetryStrategy]:
        """Load agent-specific retry strategies"""
        strategies = {}
        
        for agent, config in self.config.get("agent_retry", {}).items():
            strategies[agent] = RetryStrategy(**config)
        
        return strategies
    
    def _load_fallback_handlers(self) -> Dict[str, Callable]:
        """Load fallback handlers for different scenarios"""
        return {
            "preparer": self._fallback_preparer,
            "navigator": self._fallback_navigator,
            "validator": self._fallback_validator,
            "summarizer": self._fallback_summarizer,
            "cache": self._fallback_cache
        }
    
    def get_retry_strategy(self, agent_name: str) -> RetryStrategy:
        """Get retry strategy for specific agent"""
        return self.agent_strategies.get(agent_name, self.default_strategy)
    
    async def handle_with_retry(self, 
                               func: Callable,
                               agent_name: str,
                               context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute function with retry logic"""
        strategy = self.get_retry_strategy(agent_name)
        attempt = 0
        last_error = None
        errors = []
        
        while attempt < strategy.max_attempts:
            attempt += 1
            
            try:
                # Execute the function
                result = await func(**context)
                
                # Success - add metadata
                result["_recovery_metadata"] = {
                    "attempts": attempt,
                    "recovered": attempt > 1,
                    "errors": errors
                }
                
                return result
                
            except Exception as e:
                last_error = e
                error_type, error_category = ErrorClassifier.classify(e)
                
                errors.append({
                    "attempt": attempt,
                    "error": str(e),
                    "type": error_type.value,
                    "category": error_category.value,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                logger.warning(
                    f"Attempt {attempt} failed for {agent_name}: "
                    f"{error_category.value} - {str(e)}"
                )
                
                if not strategy.should_retry(attempt, error_type):
                    break
                
                if attempt < strategy.max_attempts:
                    delay = strategy.get_delay(attempt)
                    logger.info(f"Retrying {agent_name} in {delay:.1f}s...")
                    await asyncio.sleep(delay)
        
        # All retries failed - use fallback
        logger.error(f"All retries failed for {agent_name}")
        return await self.get_fallback_result(agent_name, context, last_error, errors)
    
    async def get_fallback_result(self, 
                                 agent_name: str,
                                 context: Dict[str, Any],
                                 error: Exception,
                                 error_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get fallback result when all retries fail"""
        
        if agent_name in self.fallback_handlers:
            try:
                result = await self.fallback_handlers[agent_name](context, error)
                result["_recovery_metadata"] = {
                    "fallback": True,
                    "error": str(error),
                    "error_history": error_history
                }
                return result
            except Exception as fallback_error:
                logger.error(f"Fallback also failed for {agent_name}: {fallback_error}")
        
        # Default fallback
        return {
            "error": str(error),
            "agent": agent_name,
            "status": "failed",
            "_recovery_metadata": {
                "fallback": True,
                "all_attempts_failed": True,
                "error_history": error_history
            }
        }
    
    # Fallback handlers for specific agents
    
    async def _fallback_preparer(self, context: Dict[str, Any], error: Exception) -> Dict[str, Any]:
        """Fallback for preparer - use simple query expansion"""
        query = context.get("query", "")
        return {
            "job_id": context.get("job_id"),
            "query": query,
            "classification": {
                "primary_intent": "unknown",
                "domains": [],
                "temporal_relevance": "unknown"
            },
            "search_queries": [
                query,
                f"{query} explained",
                f"what is {query}"
            ],
            "fallback": True,
            "error": str(error)
        }
    
    async def _fallback_navigator(self, context: Dict[str, Any], error: Exception) -> Dict[str, Any]:
        """Fallback for navigator - extract basic URLs"""
        search_results = context.get("search_results", [])
        urls = []
        
        for result in search_results[:5]:
            if isinstance(result, dict) and "results" in result:
                for item in result["results"][:2]:
                    if "url" in item:
                        urls.append({
                            "url": item["url"],
                            "priority": len(urls) + 1,
                            "max_retries": 2,
                            "retry_delay": 1.0
                        })
        
        return {
            "job_id": context.get("job_id"),
            "query": context.get("query"),
            "urls_to_fetch": urls,
            "additional_searches": [],
            "navigation_strategy": "Fallback: basic URL extraction",
            "fallback": True
        }
    
    async def _fallback_validator(self, context: Dict[str, Any], error: Exception) -> Dict[str, Any]:
        """Fallback for validator - accept all with low confidence"""
        fetched_content = context.get("fetched_content", [])
        
        validated = []
        for item in fetched_content:
            if item.get("content") and len(item["content"]) > 100:
                validated.append({
                    **item,
                    "quality_assessment": {
                        "quality_score": 0.3,  # Low confidence
                        "source_credibility": "unknown",
                        "content_type": "unknown",
                        "relevance_explanation": "Validation failed - accepting with low confidence",
                        "potential_issues": ["unvalidated"]
                    },
                    "validated_at": datetime.utcnow().isoformat()
                })
        
        return {
            "job_id": context.get("job_id"),
            "query": context.get("query"),
            "validated_content": validated,
            "total_processed": len(fetched_content),
            "total_validated": len(validated),
            "fallback": True
        }
    
    async def _fallback_summarizer(self, context: Dict[str, Any], error: Exception) -> Dict[str, Any]:
        """Fallback for summarizer - extract key sentences"""
        validated_content = context.get("validated_content", [])
        
        summaries = []
        for item in validated_content[:3]:
            content = item.get("content", "")[:1000]
            
            summaries.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "summary": f"Content preview (summarization failed): {content}...",
                "key_points": ["Summarization unavailable"],
                "relevance_score": 0.3
            })
        
        return {
            "job_id": context.get("job_id"),
            "query": context.get("query"),
            "document_summaries": summaries,
            "consolidated_summary": None,
            "needs_more_research": True,
            "fallback": True
        }
    
    async def _fallback_cache(self, context: Dict[str, Any], error: Exception) -> Dict[str, Any]:
        """Fallback for cache - skip caching"""
        return {
            "job_id": context.get("job_id"),
            "query": context.get("query"),
            "cache_hits": 0,
            "relevant_items": [],
            "cache_sufficient": False,
            "recommendation": "fetch_new",
            "error": "Cache unavailable",
            "fallback": True
        }

class PartialResultHandler:
    """Handles partial results when some agents fail"""
    
    @staticmethod
    def build_partial_response(
        successful_agents: List[str],
        failed_agents: List[Dict[str, Any]],
        results: Dict[str, Any]) -> Dict[str, Any]:
        """Build response with partial results"""
        
        # Determine quality level
        critical_agents = ["preparer", "answerer"]
        quality_level = "complete"
        
        for agent_info in failed_agents:
            if agent_info["agent"] in critical_agents:
                quality_level = "degraded"
                break
            else:
                quality_level = "partial"
        
        return {
            **results,
            "_metadata": {
                "quality_level": quality_level,
                "successful_agents": successful_agents,
                "failed_agents": failed_agents,
                "partial_result": len(failed_agents) > 0,
                "timestamp": datetime.utcnow().isoformat()
            }
        }