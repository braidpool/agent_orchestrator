import asyncio
import aiohttp
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from urllib.parse import urlparse
import ssl
import certifi

logger = logging.getLogger("SessionPool")

class SessionConfig:
    """Configuration for HTTP sessions"""
    def __init__(self, config: Dict[str, Any]):
        pool_config = config.get("connection_pool", {})
        
        # Connection limits
        self.limit = pool_config.get("limit", 100)  # Total connections
        self.limit_per_host = pool_config.get("limit_per_host", 30)  # Per host
        
        # Timeouts
        self.connect_timeout = pool_config.get("connect_timeout", 10.0)
        self.sock_read_timeout = pool_config.get("sock_read_timeout", 30.0)
        self.total_timeout = pool_config.get("total_timeout", 300.0)
        
        # Keep-alive
        self.keepalive_timeout = pool_config.get("keepalive_timeout", 30.0)
        self.force_close = pool_config.get("force_close", False)
        
        # SSL
        self.verify_ssl = pool_config.get("verify_ssl", True)
        
        # Retry
        self.retry_attempts = pool_config.get("retry_attempts", 3)
        self.retry_delay = pool_config.get("retry_delay", 0.5)

class SessionPool:
    """Manages HTTP session pools for different endpoints"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = SessionConfig(config)
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        self.session_stats: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        
        # SSL context
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        if not self.config.verify_ssl:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
    
    async def get_session(self, url: str) -> aiohttp.ClientSession:
        """Get or create a session for the given URL"""
        # Extract base URL (scheme + netloc)
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        # Check if we already have a session
        if base_url in self.sessions:
            session = self.sessions[base_url]
            if not session.closed:
                self._update_stats(base_url, "reused")
                return session
        
        # Create new session
        async with self._lock:
            # Double-check after acquiring lock
            if base_url in self.sessions and not self.sessions[base_url].closed:
                return self.sessions[base_url]
            
            session = await self._create_session(base_url)
            self.sessions[base_url] = session
            self._update_stats(base_url, "created")
            
            return session
    
    async def _create_session(self, base_url: str) -> aiohttp.ClientSession:
        """Create a new session with optimized settings"""
        # Connector with connection pooling
        connector = aiohttp.TCPConnector(
            limit=self.config.limit,
            limit_per_host=self.config.limit_per_host,
            ttl_dns_cache=300,  # DNS cache for 5 minutes
            enable_cleanup_closed=True,
            force_close=self.config.force_close,
            keepalive_timeout=self.config.keepalive_timeout,
            ssl=self.ssl_context
        )
        
        # Timeout settings
        timeout = aiohttp.ClientTimeout(
            total=self.config.total_timeout,
            connect=self.config.connect_timeout,
            sock_read=self.config.sock_read_timeout
        )
        
        # Create session
        session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                'User-Agent': 'LLM-Agent-Orchestrator/1.0',
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            },
            connector_owner=True,  # Session owns the connector
            raise_for_status=False  # Handle status codes ourselves
        )
        
        logger.info(f"Created new session for {base_url}")
        return session
    
    def _update_stats(self, base_url: str, action: str):
        """Update session statistics"""
        if base_url not in self.session_stats:
            self.session_stats[base_url] = {
                "created_at": datetime.utcnow(),
                "requests": 0,
                "reuses": 0,
                "errors": 0
            }
        
        stats = self.session_stats[base_url]
        stats["last_used"] = datetime.utcnow()
        
        if action == "created":
            stats["created_at"] = datetime.utcnow()
        elif action == "reused":
            stats["reuses"] += 1
        elif action == "request":
            stats["requests"] += 1
        elif action == "error":
            stats["errors"] += 1
    
    async def request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """Make a request using a pooled session"""
        if self._closed:
            raise RuntimeError("SessionPool is closed")
        
        session = await self.get_session(url)
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        # Retry logic
        last_error = None
        for attempt in range(self.config.retry_attempts):
            try:
                self._update_stats(base_url, "request")
                
                # Make request
                async with session.request(method, url, **kwargs) as response:
                    # Read response body to avoid connection issues
                    await response.read()
                    return response
                    
            except aiohttp.ClientConnectorError as e:
                last_error = e
                logger.warning(f"Connection error on attempt {attempt + 1}: {e}")
                
                if attempt < self.config.retry_attempts - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                    
                    # Check if session needs recreation
                    if session.closed:
                        async with self._lock:
                            if base_url in self.sessions:
                                del self.sessions[base_url]
                        session = await self.get_session(url)
                        
            except Exception as e:
                last_error = e
                self._update_stats(base_url, "error")
                logger.error(f"Request error: {e}")
                raise
        
        # All retries failed
        self._update_stats(base_url, "error")
        raise last_error
    
    async def close(self):
        """Close all sessions"""
        if self._closed:
            return
            
        self._closed = True
        logger.info("Closing all sessions...")
        
        # Close all sessions
        tasks = []
        for base_url, session in self.sessions.items():
            if not session.closed:
                tasks.append(session.close())
                logger.info(f"Closing session for {base_url}")
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self.sessions.clear()
        logger.info("All sessions closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get session pool statistics"""
        stats = {
            "total_sessions": len(self.sessions),
            "active_sessions": sum(1 for s in self.sessions.values() if not s.closed),
            "endpoints": {}
        }
        
        for base_url, session_stats in self.session_stats.items():
            session = self.sessions.get(base_url)
            stats["endpoints"][base_url] = {
                **session_stats,
                "is_closed": session.closed if session else True,
                "age_seconds": (datetime.utcnow() - session_stats["created_at"]).total_seconds()
            }
        
        return stats
    
    async def health_check(self) -> Dict[str, bool]:
        """Check health of all sessions"""
        health = {}
        
        for base_url, session in self.sessions.items():
            if session.closed:
                health[base_url] = False
            else:
                # Try a simple request
                try:
                    async with session.get(base_url, timeout=5) as response:
                        health[base_url] = response.status < 500
                except:
                    health[base_url] = False
        
        return health

class PooledClient:
    """Base class for clients using session pool"""
    
    def __init__(self, session_pool: Optional[SessionPool] = None):
        self.session_pool = session_pool
        self._local_session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self, url: str) -> aiohttp.ClientSession:
        """Get session from pool or create local one"""
        if self.session_pool:
            return await self.session_pool.get_session(url)
        
        # Fallback to local session
        if not self._local_session or self._local_session.closed:
            self._local_session = aiohttp.ClientSession()
        
        return self._local_session
    
    async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """Make request using pooled or local session"""
        if self.session_pool:
            return await self.session_pool.request(method, url, **kwargs)
        
        # Fallback to local session
        session = await self._get_session(url)
        async with session.request(method, url, **kwargs) as response:
            await response.read()  # Read body before returning
            return response
    
    async def close(self):
        """Close local session if any"""
        if self._local_session and not self._local_session.closed:
            await self._local_session.close()