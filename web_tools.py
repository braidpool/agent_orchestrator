import aiohttp
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib
from urllib.parse import quote, urlparse
import re

from session_pool import SessionPool, PooledClient

logger = logging.getLogger("WebTools")

class WebSearcher(PooledClient):
    """Handles web search with multiple provider options"""
    
    def __init__(self, config: Dict[str, Any], session_pool: Optional[SessionPool] = None):
        super().__init__(session_pool)
        self.config = config.get("web_search", {})
        self.provider = self.config.get("provider", "searxng")
        self.api_key = self.config.get("api_key", "")
        self.endpoint = self.config.get("endpoint", "")
        
        # Provider-specific settings
        self.providers = {
            "searxng": {
                "default_endpoint": "http://localhost:8888/search",
                "requires_api_key": False
            },
            "serper": {
                "default_endpoint": "https://google.serper.dev/search",
                "requires_api_key": True
            },
            "brave": {
                "default_endpoint": "https://api.search.brave.com/res/v1/web/search",
                "requires_api_key": True
            },
            "serpapi": {
                "default_endpoint": "https://serpapi.com/search",
                "requires_api_key": True
            },
            "duckduckgo": {
                "default_endpoint": None,  # Uses duckduckgo-search library
                "requires_api_key": False
            }
        }
        
        # Use default endpoint if not specified
        if not self.endpoint and self.provider in self.providers:
            self.endpoint = self.providers[self.provider]["default_endpoint"]
    
    async def search(self, query: str, num_results: int = 10) -> List[Dict[str, Any]]:
        """Perform web search using configured provider"""
        
        if self.provider == "searxng":
            return await self._search_searxng(query, num_results)
        elif self.provider == "serper":
            return await self._search_serper(query, num_results)
        elif self.provider == "brave":
            return await self._search_brave(query, num_results)
        elif self.provider == "serpapi":
            return await self._search_serpapi(query, num_results)
        elif self.provider == "duckduckgo":
            return await self._search_duckduckgo(query, num_results)
        else:
            logger.warning(f"Unknown search provider: {self.provider}, using mock data")
            return self._mock_search_results(query, num_results)
    
    async def _search_searxng(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Search using SearXNG (self-hosted, no API key needed)"""
        try:
            params = {
                'q': query,
                'format': 'json',
                'categories': 'general',
                'engines': 'google,bing,duckduckgo',
                'pageno': 1,
                'results_on_new_tab': 0
            }
            
            response = await self._request("GET", self.endpoint, params=params)
            
            if response.status == 200:
                data = await response.json()
                results = []
                for item in data.get('results', [])[:num_results]:
                    results.append({
                        'title': item.get('title', ''),
                        'url': item.get('url', ''),
                        'snippet': item.get('content', ''),
                        'engine': item.get('engine', 'unknown')
                    })
                return results
            else:
                logger.error(f"SearXNG error: {response.status}")
                return []
                
        except Exception as e:
            logger.error(f"SearXNG search error: {e}")
            return []
    
    async def _search_serper(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Search using Serper.dev API"""
        if not self.api_key:
            logger.error("Serper API key not configured")
            return []
        
        try:
            headers = {
                'X-API-KEY': self.api_key,
                'Content-Type': 'application/json'
            }
            
            payload = {
                'q': query,
                'num': num_results
            }
            
            response = await self._request("POST", self.endpoint, json=payload, headers=headers)
            
            if response.status == 200:
                data = await response.json()
                results = []
                for item in data.get('organic', []):
                    results.append({
                        'title': item.get('title', ''),
                        'url': item.get('link', ''),
                        'snippet': item.get('snippet', ''),
                        'position': item.get('position', 0)
                    })
                return results
            else:
                logger.error(f"Serper error: {response.status}")
                return []
                
        except Exception as e:
            logger.error(f"Serper search error: {e}")
            return []
    
    async def _search_brave(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Search using Brave Search API"""
        if not self.api_key:
            logger.error("Brave API key not configured")
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Accept': 'application/json',
                    'X-Subscription-Token': self.api_key
                }
                
                params = {
                    'q': query,
                    'count': num_results
                }
                
                async with session.get(self.endpoint, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = []
                        for item in data.get('web', {}).get('results', []):
                            results.append({
                                'title': item.get('title', ''),
                                'url': item.get('url', ''),
                                'snippet': item.get('description', ''),
                                'age': item.get('age', '')
                            })
                        return results
                    else:
                        logger.error(f"Brave error: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"Brave search error: {e}")
            return []
    
    async def _search_serpapi(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Search using SerpAPI (Google results)"""
        if not self.api_key:
            logger.error("SerpAPI key not configured")
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    'q': query,
                    'num': num_results,
                    'api_key': self.api_key,
                    'engine': 'google'
                }
                
                async with session.get(self.endpoint, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = []
                        for item in data.get('organic_results', []):
                            results.append({
                                'title': item.get('title', ''),
                                'url': item.get('link', ''),
                                'snippet': item.get('snippet', ''),
                                'position': item.get('position', 0)
                            })
                        return results
                    else:
                        logger.error(f"SerpAPI error: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"SerpAPI search error: {e}")
            return []
    
    async def _search_duckduckgo(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Search using DuckDuckGo (requires duckduckgo-search package)"""
        try:
            from duckduckgo_search import AsyncDDGS
            
            async with AsyncDDGS() as ddgs:
                results = []
                async for r in ddgs.text(query, max_results=num_results):
                    results.append({
                        'title': r.get('title', ''),
                        'url': r.get('href', ''),
                        'snippet': r.get('body', '')
                    })
                return results
        except ImportError:
            logger.error("duckduckgo-search package not installed. Install with: pip install duckduckgo-search")
            return []
        except Exception as e:
            logger.error(f"DuckDuckGo search error: {e}")
            return []
    
    def _mock_search_results(self, query: str, num_results: int) -> List[Dict[str, Any]]:
        """Fallback mock results for testing"""
        results = []
        for i in range(min(num_results, 5)):
            results.append({
                'title': f"Mock Result {i+1} for: {query}",
                'url': f"https://example.com/{query.replace(' ', '-')}-{i+1}",
                'snippet': f"This is a mock search result about {query}. In production, this would contain real search snippets..."
            })
        return results


class URLFetcher(PooledClient):
    """Handles fetching and processing web page content with connection pooling"""
    
    def __init__(self, config: Dict[str, Any], session_pool: Optional[SessionPool] = None):
        super().__init__(session_pool)
        self.config = config
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.max_content_length = 5 * 1024 * 1024  # 5MB limit
        self.user_agent = "Mozilla/5.0 (compatible; LLM-Research-Bot/1.0)"
    
    async def fetch_url(self, url: str, max_retries: int = 3, retry_delay: float = 1.0) -> Dict[str, Any]:
        """Fetch content from URL with retry logic"""
        
        # If we have a session pool, retries are handled there
        if self.session_pool:
            max_retries = 1  # Pool handles retries
        
        for attempt in range(max_retries):
            try:
                headers = {
                    'User-Agent': self.user_agent,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
                
                response = await self._request(
                    "GET", url, 
                    headers=headers,
                    allow_redirects=True,
                    timeout=self.timeout
                )
                
                # Check content length
                content_length = response.headers.get('Content-Length')
                if content_length and int(content_length) > self.max_content_length:
                    return {
                        'url': url,
                        'error': 'Content too large',
                        'status_code': response.status
                    }
                
                # Read content
                content = await response.text()
                
                # Extract text from HTML
                text_content = self._extract_text_from_html(content)
                
                # Extract metadata
                title = self._extract_title(content)
                
                return {
                    'url': url,
                    'title': title,
                    'content': text_content,
                    'html': content[:10000],  # Store partial HTML for reference
                    'status_code': response.status,
                    'content_type': response.headers.get('Content-Type', ''),
                    'fetch_time': datetime.utcnow().isoformat(),
                    'content_length': len(content)
                }
                    
            except asyncio.TimeoutError:
                logger.warning(f"Timeout fetching {url} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    return {
                        'url': url,
                        'error': 'Timeout',
                        'status_code': 0
                    }
                    
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    return {
                        'url': url,
                        'error': str(e),
                        'status_code': 0
                    }
    
    async def fetch_multiple(self, urls: List[str], max_concurrent: int = 5) -> List[Dict[str, Any]]:
        """Fetch multiple URLs concurrently"""
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def fetch_with_semaphore(url):
            async with semaphore:
                return await self.fetch_url(url)
        
        tasks = [fetch_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks)
    
    def _extract_text_from_html(self, html: str) -> str:
        """Extract readable text from HTML content"""
        # Remove script and style elements
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove HTML comments
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        
        # Extract text from specific content tags
        content_tags = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'div', 'span']
        text_parts = []
        
        for tag in content_tags:
            pattern = f'<{tag}[^>]*>(.*?)</{tag}>'
            matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
            text_parts.extend(matches)
        
        # Join and clean text
        text = ' '.join(text_parts)
        
        # Remove remaining HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Decode HTML entities
        text = self._decode_html_entities(text)
        
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        return text[:50000]  # Limit to 50k characters
    
    def _extract_title(self, html: str) -> str:
        """Extract page title from HTML"""
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = title_match.group(1)
            title = re.sub(r'<[^>]+>', '', title)
            title = self._decode_html_entities(title)
            return title.strip()
        return "Untitled"
    
    def _decode_html_entities(self, text: str) -> str:
        """Decode common HTML entities"""
        entities = {
            '&amp;': '&',
            '&lt;': '<',
            '&gt;': '>',
            '&quot;': '"',
            '&#39;': "'",
            '&nbsp;': ' ',
            '&ndash;': '–',
            '&mdash;': '—',
            '&hellip;': '…'
        }
        
        for entity, char in entities.items():
            text = text.replace(entity, char)
        
        return text