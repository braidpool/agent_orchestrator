import asyncio
import aiohttp
from aiohttp import web
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
import uuid
from pathlib import Path
import signal
import sys

# Import configuration and utilities
from config import Config
from state_manager import StateManager
from web_tools import WebSearcher, URLFetcher
from tool_protocol import ToolRegistry, FeedbackLoop
from error_handler import PartialResultHandler
from session_pool import SessionPool

# Import all agents
from router import RouterAgent
from preparer import PreparerAgent
from navigator import NavigatorAgent
from validator import ValidatorAgent
from cache import CacheAgent
from summarizer import SummarizerAgent
from answerer import AnswererAgent

class AgentOrchestrator:
    def __init__(self):
        self.config = Config()
        self.state_manager = StateManager()
        
        # Initialize session pool
        self.session_pool = SessionPool(self.config.data)
        
        # Initialize tool registry
        self.tool_registry = ToolRegistry()
        
        # Initialize web tools with session pool
        self.web_searcher = WebSearcher(self.config.data, self.session_pool)
        self.url_fetcher = URLFetcher(self.config.data, self.session_pool)
        
        # Initialize agents
        self.router = RouterAgent(self.config, self.state_manager)
        self.preparer = PreparerAgent(self.config, self.state_manager)
        self.navigator = NavigatorAgent(self.config, self.state_manager)
        self.validator = ValidatorAgent(self.config, self.state_manager)
        self.cache = CacheAgent(self.config, self.state_manager)
        self.summarizer = SummarizerAgent(self.config, self.state_manager)
        self.answerer = AnswererAgent(self.config, self.state_manager)
        
        # Set session pool for agents that need it
        for agent in [self.router, self.preparer, self.navigator, 
                     self.validator, self.cache, self.summarizer, self.answerer]:
            agent.session_pool = self.session_pool
        
        # Set tool registry for all agents
        for agent in [self.router, self.preparer, self.navigator, 
                     self.validator, self.cache, self.summarizer, self.answerer]:
            agent.set_tool_registry(self.tool_registry)
        
        # Initialize feedback loop handler
        self.feedback_loop = FeedbackLoop(max_iterations=3)
        
        # Setup logging
        log_dir = Path(self.config.data["logging"]["dir"])
        log_dir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger("Orchestrator")
        handler = logging.FileHandler(log_dir / "orchestrator.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(self.config.data["logging"]["level"])
        
        # Active jobs tracking
        self.active_jobs = {}
        
    async def start(self):
        """Start the orchestrator"""
        await self.state_manager.start()
        self.logger.info("Agent Orchestrator started")
    
    async def stop(self):
        """Stop the orchestrator gracefully"""
        self.logger.info("Stopping Agent Orchestrator...")
        
        # Wait for active jobs to complete (with timeout)
        if self.active_jobs:
            self.logger.info(f"Waiting for {len(self.active_jobs)} active jobs to complete...")
            
            # Give jobs 30 seconds to complete
            timeout = 30
            start_time = asyncio.get_event_loop().time()
            
            while self.active_jobs and (asyncio.get_event_loop().time() - start_time < timeout):
                await asyncio.sleep(1)
            
            if self.active_jobs:
                self.logger.warning(f"{len(self.active_jobs)} jobs still active after timeout")
        
        # Close session pool
        await self.session_pool.close()
        self.logger.info("Session pool closed")
        
        # Stop state manager
        await self.state_manager.stop()
        self.logger.info("Agent Orchestrator stopped")
    
    async def process_query(self, query: str, conversation_context: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process a user query through the agent pipeline with error recovery"""
        job_id = str(uuid.uuid4())
        self.logger.info(f"Processing query: {query[:100]}... Job ID: {job_id}")
        
        # Track active job
        self.active_jobs[job_id] = {
            "status": "started",
            "query": query,
            "started_at": datetime.utcnow().isoformat()
        }
        
        # Track successful and failed agents
        successful_agents = []
        failed_agents = []
        
        try:
            # Create job in state manager
            await self.state_manager.create_job(job_id, query)
            
            # Step 1: Route query (critical - if this fails, use default routing)
            try:
                routing_result = await self.router.process(query, job_id)
                agents_to_use = routing_result["routing"]
                successful_agents.append("router")
            except Exception as e:
                self.logger.error(f"Router failed: {e}, using default routing")
                agents_to_use = ["cache", "preparer", "navigator", "validator", "summarizer", "answerer"]
                failed_agents.append({"agent": "router", "error": str(e), "impact": "using_default_routing"})
            
            # Step 2: Check cache (non-critical)
            cache_result = None
            if "cache" in agents_to_use:
                try:
                    cache_result = await self.cache.check(query, job_id)
                    successful_agents.append("cache")
                    
                    if cache_result.get("cache_sufficient", False):
                        # Use cached data
                        self.logger.info(f"Using cached data for job {job_id}")
                        
                        # Format cached data as summaries
                        cached_summaries = self._format_cached_data(cache_result["relevant_items"])
                        summarized_data = {
                            "document_summaries": cached_summaries,
                            "consolidated_summary": None,
                            "needs_more_research": False
                        }
                        
                        # Skip to answer generation
                        answer = await self._generate_answer_with_recovery(
                            query, job_id, summarized_data, conversation_context,
                            successful_agents, failed_agents
                        )
                        
                        self.active_jobs[job_id]["status"] = "completed"
                        await self.state_manager.update_job_status(job_id, "completed")
                        
                        return self._format_response(job_id, answer, from_cache=True, 
                                                   successful_agents=successful_agents,
                                                   failed_agents=failed_agents)
                except Exception as e:
                    self.logger.warning(f"Cache check failed: {e}, continuing without cache")
                    failed_agents.append({"agent": "cache", "error": str(e), "impact": "no_cache_benefit"})
            
            # Step 3: Prepare searches (critical but has fallback)
            search_queries = []
            if "preparer" in agents_to_use:
                try:
                    preparer_result = await self.preparer.process(query, job_id)
                    search_queries = preparer_result.get("search_queries", [])
                    successful_agents.append("preparer")
                except Exception as e:
                    self.logger.error(f"Preparer failed: {e}, using basic search")
                    search_queries = [query]  # Fallback to original query
                    failed_agents.append({"agent": "preparer", "error": str(e), "impact": "basic_search_only"})
            
            # Step 4: Perform web searches (critical)
            search_results = []
            if search_queries:
                try:
                    search_results = await self._perform_searches(search_queries)
                    if not any(r.get("results") for r in search_results):
                        raise Exception("No search results found")
                except Exception as e:
                    self.logger.error(f"Search failed: {e}")
                    # This is critical - if search fails, try to use cache or fail gracefully
                    if not cache_result or not cache_result.get("relevant_items"):
                        return self._create_error_response(
                            job_id, query, "search_failed", str(e),
                            successful_agents, failed_agents
                        )
            
            # Step 5: Navigate and identify URLs (non-critical)
            urls_to_fetch = []
            if "navigator" in agents_to_use and search_results:
                try:
                    navigator_result = await self.navigator.process(query, job_id, search_results)
                    urls_to_fetch = navigator_result.get("urls_to_fetch", [])
                    successful_agents.append("navigator")
                except Exception as e:
                    self.logger.warning(f"Navigator failed: {e}, using top search URLs")
                    # Fallback: extract URLs directly from search results
                    urls_to_fetch = self._extract_urls_from_search(search_results)
                    failed_agents.append({"agent": "navigator", "error": str(e), "impact": "basic_url_selection"})
            
            # Step 6: Fetch web content (critical)
            fetched_content = []
            if urls_to_fetch:
                try:
                    fetched_content = await self._fetch_urls(urls_to_fetch)
                    # Filter out failed fetches
                    fetched_content = [c for c in fetched_content if not c.get("error")]
                    if not fetched_content:
                        raise Exception("All URL fetches failed")
                except Exception as e:
                    self.logger.error(f"URL fetching failed: {e}")
                    # Try to continue with search snippets only
                    fetched_content = self._create_content_from_snippets(search_results)
            
            # Step 7: Validate content (non-critical)
            validated_content = fetched_content  # Default to unvalidated
            if "validator" in agents_to_use and fetched_content:
                try:
                    validator_result = await self.validator.process(query, job_id, fetched_content)
                    validated_content = validator_result.get("validated_content", fetched_content)
                    successful_agents.append("validator")
                    
                    # Store validated content in cache
                    for item in validated_content:
                        try:
                            await self.cache.store(job_id, "web_content", item)
                        except:
                            pass  # Cache storage failure is non-critical
                            
                except Exception as e:
                    self.logger.warning(f"Validator failed: {e}, using unvalidated content")
                    failed_agents.append({"agent": "validator", "error": str(e), "impact": "unvalidated_content"})
            
            # Step 8: Summarize content (important but has fallback)
            summarized_data = {}
            if "summarizer" in agents_to_use and validated_content:
                try:
                    summarized_data = await self.summarizer.process(query, job_id, validated_content)
                    successful_agents.append("summarizer")
                    
                    # Handle feedback loop for more research
                    if isinstance(summarized_data.get("needs_more_research"), dict):
                        needs_more = summarized_data["needs_more_research"]
                        if needs_more.get("required") and needs_more.get("suggested_searches"):
                            self.logger.info(f"Summarizer requests more research for job {job_id}")
                            
                            try:
                                feedback_result = await self._execute_research_feedback_loop(
                                    query, job_id, needs_more["suggested_searches"], 
                                    summarized_data, validated_content
                                )
                                
                                if feedback_result.get("improved_data"):
                                    summarized_data = feedback_result["improved_data"]
                            except Exception as e:
                                self.logger.warning(f"Feedback loop failed: {e}, using original summaries")
                    
                    # Store summaries in cache
                    if summarized_data.get("document_summaries"):
                        try:
                            await self.cache.store(job_id, "summaries", summarized_data)
                        except:
                            pass
                            
                except Exception as e:
                    self.logger.error(f"Summarizer failed: {e}, using content extracts")
                    # Fallback: create basic summaries from content
                    summarized_data = self._create_basic_summaries(validated_content, query)
                    failed_agents.append({"agent": "summarizer", "error": str(e), "impact": "basic_summaries"})
            
            # Step 9: Generate answer (critical)
            answer = await self._generate_answer_with_recovery(
                query, job_id, summarized_data, conversation_context,
                successful_agents, failed_agents
            )
            
            # Update job status
            self.active_jobs[job_id]["status"] = "completed"
            await self.state_manager.update_job_status(job_id, "completed")
            
            return self._format_response(job_id, answer, from_cache=False,
                                       successful_agents=successful_agents,
                                       failed_agents=failed_agents)
            
        except Exception as e:
            self.logger.error(f"Critical error processing job {job_id}: {e}")
            self.active_jobs[job_id]["status"] = "error"
            await self.state_manager.update_job_status(job_id, "error")
            
            return self._create_error_response(
                job_id, query, "critical_error", str(e),
                successful_agents, failed_agents
            )
    
    async def _perform_searches(self, queries: List[str]) -> List[Dict[str, Any]]:
        """Perform web searches using configured search provider"""
        self.logger.info(f"Performing {len(queries)} searches using {self.web_searcher.provider}")
        
        results = []
        for query in queries:
            try:
                search_results = await self.web_searcher.search(query, num_results=10)
                results.append({
                    "query": query,
                    "results": search_results
                })
            except Exception as e:
                self.logger.error(f"Search error for '{query}': {e}")
                results.append({
                    "query": query,
                    "results": [],
                    "error": str(e)
                })
        
        return results
    
    async def _fetch_urls(self, urls_with_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fetch content from URLs"""
        self.logger.info(f"Fetching {len(urls_with_metadata)} URLs")
        
        # Extract URLs from metadata
        urls = [item["url"] for item in urls_with_metadata]
        
        # Fetch URLs concurrently
        fetched_content = await self.url_fetcher.fetch_multiple(urls, max_concurrent=5)
        
        # Merge with metadata
        for i, content in enumerate(fetched_content):
            if i < len(urls_with_metadata):
                content["priority"] = urls_with_metadata[i].get("priority", 99)
                content["metadata"] = urls_with_metadata[i]
        
        return fetched_content
    
    def _format_cached_data(self, cached_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format cached data as document summaries"""
        summaries = []
        
        for item in cached_items:
            metadata = item.get("metadata", {})
            content = json.loads(item.get("content", "{}"))
            
            summaries.append({
                "url": metadata.get("url", "cached"),
                "title": metadata.get("title", "Cached Content"),
                "summary": content.get("summary", content.get("content", ""))[:500],
                "key_points": content.get("key_points", []),
                "relevance_score": item.get("relevance_score", 0.8),
                "from_cache": True
            })
        
        return summaries
    
    def _format_response(self, job_id: str, answer_result: Dict[str, Any], 
                        from_cache: bool, successful_agents: List[str], 
                        failed_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format final response with error recovery metadata"""
        response = {
            "job_id": job_id,
            "status": "completed",
            "query": answer_result.get("query", ""),
            "answer": answer_result.get("answer", ""),
            "citations": answer_result.get("citations", []),
            "confidence_score": answer_result.get("confidence_score", 0.0),
            "from_cache": from_cache,
            "suggest_more_research": answer_result.get("suggest_more_research", False),
            "follow_up_questions": answer_result.get("follow_up_questions", []),
            "timestamp": answer_result.get("timestamp", datetime.utcnow().isoformat())
        }
        
        # Add partial result metadata if any agents failed
        if failed_agents:
            response = PartialResultHandler.build_partial_response(
                successful_agents, failed_agents, response
            )
        
        return response
    
    async def _generate_answer_with_recovery(self, query: str, job_id: str,
                                           summarized_data: Dict[str, Any],
                                           conversation_context: List[Dict[str, Any]],
                                           successful_agents: List[str],
                                           failed_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate answer with fallback on failure"""
        try:
            answer = await self.answerer.process(query, job_id, summarized_data, conversation_context)
            successful_agents.append("answerer")
            return answer
        except Exception as e:
            self.logger.error(f"Answerer failed: {e}, using fallback answer")
            failed_agents.append({"agent": "answerer", "error": str(e), "impact": "basic_answer"})
            
            # Fallback: create basic answer from summaries
            return self._create_fallback_answer(query, job_id, summarized_data)
    
    def _extract_urls_from_search(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract URLs directly from search results as fallback"""
        urls = []
        for result in search_results:
            if "results" in result:
                for item in result["results"][:3]:  # Top 3 from each search
                    if "url" in item:
                        urls.append({
                            "url": item["url"],
                            "priority": len(urls) + 1,
                            "max_retries": 2,
                            "retry_delay": 1.0
                        })
        return urls[:10]  # Limit to 10 URLs
    
    def _create_content_from_snippets(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create content from search snippets as fallback"""
        content = []
        for result in search_results:
            if "results" in result:
                for item in result["results"]:
                    if "snippet" in item:
                        content.append({
                            "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "content": item["snippet"],
                            "fetch_time": datetime.utcnow().isoformat(),
                            "from_snippet": True
                        })
        return content
    
    def _create_basic_summaries(self, content: List[Dict[str, Any]], query: str) -> Dict[str, Any]:
        """Create basic summaries from content as fallback"""
        summaries = []
        for item in content[:5]:  # Limit to 5 items
            text = item.get("content", "")[:500]
            summaries.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "summary": f"{text}..." if len(text) == 500 else text,
                "key_points": ["Content extract only - summarization unavailable"],
                "relevance_score": 0.5
            })
        
        return {
            "document_summaries": summaries,
            "consolidated_summary": None,
            "needs_more_research": True,
            "fallback": True
        }
    
    def _create_fallback_answer(self, query: str, job_id: str, 
                               summarized_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create fallback answer when answerer fails"""
        summaries = summarized_data.get("document_summaries", [])
        
        if summaries:
            # Create basic answer from summaries
            answer_parts = [f"Based on available information about '{query}':"]
            
            for i, summary in enumerate(summaries[:3], 1):
                answer_parts.append(f"\n{i}. From {summary.get('title', 'Source')}: {summary.get('summary', '')[:200]}...")
            
            answer_parts.append("\n\nNote: This is a basic summary. The full answer generation was unavailable.")
            
            answer = "\n".join(answer_parts)
            
        else:
            answer = (f"I was unable to generate a complete answer for your query: '{query}'. "
                     "This may be due to technical issues or insufficient information. "
                     "Please try rephrasing your question or try again later.")
        
        return {
            "job_id": job_id,
            "query": query,
            "answer": answer,
            "citations": [{"title": s.get("title", ""), "url": s.get("url", "")} 
                         for s in summaries[:3]],
            "confidence_score": 0.3,
            "answer_type": "fallback",
            "key_insights": [],
            "suggest_more_research": True,
            "follow_up_questions": ["Can you provide more context?", "What specific aspect interests you?"],
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def _create_error_response(self, job_id: str, query: str, error_type: str, 
                              error_message: str, successful_agents: List[str],
                              failed_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create error response with helpful information"""
        response = {
            "job_id": job_id,
            "status": "partial_failure" if successful_agents else "failed",
            "query": query,
            "error_type": error_type,
            "error_message": error_message,
            "answer": self._get_error_message(error_type, query),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if successful_agents or failed_agents:
            response = PartialResultHandler.build_partial_response(
                successful_agents, failed_agents, response
            )
        
        return response
    
    def _get_error_message(self, error_type: str, query: str) -> str:
        """Get user-friendly error message"""
        messages = {
            "search_failed": (
                "I'm having trouble searching for information right now. "
                "This might be a temporary issue with the search service. "
                "Please try again in a few moments."
            ),
            "critical_error": (
                "I encountered an unexpected error while processing your request. "
                "Please try rephrasing your question or try again later."
            ),
            "no_results": (
                f"I couldn't find any relevant information about '{query}'. "
                "Try using different keywords or being more specific."
            )
        }
        
        return messages.get(error_type, messages["critical_error"])
    
    async def _execute_research_feedback_loop(self, query: str, job_id: str, 
                                            suggested_searches: List[str],
                                            current_data: Dict[str, Any],
                                            existing_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute feedback loop for additional research"""
        self.logger.info(f"Executing research feedback loop for job {job_id}")
        
        try:
            # Perform additional searches
            additional_results = await self._perform_searches(suggested_searches)
            
            # Navigate and fetch new URLs
            new_urls = []
            if additional_results:
                nav_result = await self.navigator.process(query, job_id, additional_results)
                new_urls = nav_result.get("urls_to_fetch", [])
            
            # Fetch new content
            new_content = []
            if new_urls:
                new_content = await self._fetch_urls(new_urls)
                new_content = [c for c in new_content if not c.get("error")]
            
            # Combine with existing content
            all_content = existing_content + new_content
            
            # Re-summarize with all content
            if all_content:
                improved_summary = await self.summarizer.process(query, job_id, all_content)
                return {"improved_data": improved_summary}
            
        except Exception as e:
            self.logger.error(f"Feedback loop error: {e}")
        
        return {"improved_data": current_data}
    
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get status of a job"""
        if job_id in self.active_jobs:
            return self.active_jobs[job_id]
        
        # Check database
        job = await self.state_manager.get_job(job_id)
        if job:
            return job
        
        return {"status": "not_found", "job_id": job_id}

# Web server routes
async def handle_query(request):
    """Handle query endpoint"""
    try:
        data = await request.json()
        query = data.get("query", "").strip()
        
        if not query:
            return web.json_response({"error": "Query is required"}, status=400)
        
        # Get conversation context if provided
        context = data.get("conversation_context", [])
        
        # Process query
        orchestrator = request.app["orchestrator"]
        result = await orchestrator.process_query(query, context)
        
        return web.json_response(result)
        
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_status(request):
    """Handle status endpoint"""
    job_id = request.match_info.get("job_id")
    
    if not job_id:
        return web.json_response({"error": "Job ID is required"}, status=400)
    
    orchestrator = request.app["orchestrator"]
    status = await orchestrator.get_job_status(job_id)
    
    if status.get("status") == "not_found":
        return web.json_response({"error": "Job not found"}, status=404)
    
    return web.json_response(status)

async def handle_health(request):
    """Health check endpoint"""
    orchestrator = request.app["orchestrator"]
    
    # Get state manager status
    queue_status = await orchestrator.state_manager.get_queue_status()
    
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "state_manager": queue_status,
        "active_jobs": len(orchestrator.active_jobs)
    }
    
    # Mark as unhealthy if queue is backing up
    if queue_status["queue_size"] > 100:
        health_status["status"] = "degraded"
        health_status["reason"] = "Write queue backing up"
    
    return web.json_response(health_status)

async def handle_monitoring(request):
    """Detailed monitoring endpoint"""
    orchestrator = request.app["orchestrator"]
    
    # Get detailed status
    queue_status = await orchestrator.state_manager.get_queue_status()
    session_stats = orchestrator.session_pool.get_stats()
    
    monitoring_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "state_manager": {
            **queue_status,
            "db_path": str(orchestrator.state_manager.db_path),
            "shutdown_timeout": orchestrator.state_manager._shutdown_timeout
        },
        "session_pool": session_stats,
        "active_jobs": {
            "count": len(orchestrator.active_jobs),
            "jobs": [
                {
                    "job_id": job_id,
                    "status": job_data["status"],
                    "started_at": job_data["started_at"],
                    "query": job_data["query"][:100] + "..." if len(job_data["query"]) > 100 else job_data["query"]
                }
                for job_id, job_data in orchestrator.active_jobs.items()
            ]
        },
        "agents": {
            "total": 7,
            "list": ["router", "preparer", "navigator", "validator", "cache", "summarizer", "answerer"]
        }
    }
    
    return web.json_response(monitoring_data)

async def handle_index(request):
    """Serve the index.html file"""
    index_path = Path(__file__).parent / "index.html"
    if index_path.exists():
        with open(index_path, 'r') as f:
            content = f.read()
        return web.Response(text=content, content_type='text/html')
    else:
        return web.Response(text="Index.html not found", status=404)

# CORS middleware
@web.middleware
async def cors_middleware(request, handler):
    response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

async def init_app():
    """Initialize the web application"""
    app = web.Application(middlewares=[cors_middleware])
    
    # Create and start orchestrator
    orchestrator = AgentOrchestrator()
    await orchestrator.start()
    app["orchestrator"] = orchestrator
    
    # Add routes
    app.router.add_post('/api/query', handle_query)
    app.router.add_get('/api/status/{job_id}', handle_status)
    app.router.add_get('/api/health', handle_health)
    app.router.add_get('/api/monitoring', handle_monitoring)
    
    # Serve index.html at root
    app.router.add_get('/', handle_index)
    
    # Handle OPTIONS for CORS
    app.router.add_options('/api/query', lambda r: web.Response(status=200))
    
    # Setup cleanup on app shutdown
    async def cleanup(app):
        await app["orchestrator"].stop()
    
    app.on_cleanup.append(cleanup)
    
    return app

async def shutdown_handler(app):
    """Handle shutdown signals"""
    logging.info("Shutdown signal received")
    
    # Get all running tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    
    # Cancel them
    for task in tasks:
        task.cancel()
    
    # Wait for all tasks to complete
    await asyncio.gather(*tasks, return_exceptions=True)

def setup_signal_handlers(app):
    """Setup signal handlers for graceful shutdown"""
    loop = asyncio.get_event_loop()
    
    def signal_handler(sig):
        logging.info(f"Received signal {sig}")
        asyncio.create_task(shutdown_handler(app))
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

def main():
    """Main entry point"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Initialize app
        app = loop.run_until_complete(init_app())
        
        # Setup signal handlers
        if sys.platform != "win32":  # Signal handlers don't work on Windows
            setup_signal_handlers(app)
        
        config = Config()
        host = config.data["server"]["host"]
        port = config.data["server"]["port"]
        
        print(f"Starting Agent Orchestrator on http://{host}:{port}")
        print(f"API endpoints:")
        print(f"  POST http://{host}:{port}/api/query")
        print(f"  GET  http://{host}:{port}/api/status/{{job_id}}")
        print(f"  GET  http://{host}:{port}/api/health")
        print(f"  GET  http://{host}:{port}/api/monitoring")
        print(f"\nPress Ctrl+C to stop the server gracefully")
        
        # Run app
        web.run_app(app, host=host, port=port, loop=loop, print=None)
        
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received")
    finally:
        # Cleanup
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

if __name__ == "__main__":
    main()
                        