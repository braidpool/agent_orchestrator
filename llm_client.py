import aiohttp
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Union
from enum import Enum

from session_pool import SessionPool, PooledClient

logger = logging.getLogger("LLMClient")

class LLMProvider(Enum):
    OPENAI = "openai"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    LLAMACPP = "llamacpp"
    CUSTOM = "custom"

class LLMClient(PooledClient):
    """Unified client for different LLM providers with connection pooling"""
    
    def __init__(self, config: Dict[str, Any], session_pool: Optional[SessionPool] = None):
        super().__init__(session_pool)
        self.host = config.get("host", "http://localhost:11434")
        self.model = config.get("model", "llama3")
        self.api_key = config.get("api_key")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 2048)
        
        # Detect provider from host
        self.provider = self._detect_provider()
        
        # Provider-specific endpoints
        self.endpoints = {
            LLMProvider.OPENAI: {
                "chat": "/v1/chat/completions",
                "completion": "/v1/completions",
                "models": "/v1/models"
            },
            LLMProvider.OLLAMA: {
                "chat": "/api/chat",
                "generate": "/api/generate",
                "models": "/api/tags"
            },
            LLMProvider.ANTHROPIC: {
                "messages": "/v1/messages",
                "completion": "/v1/complete"
            },
            LLMProvider.LLAMACPP: {
                "completion": "/completion",
                "chat": "/v1/chat/completions"
            }
        }
    
    def _detect_provider(self) -> LLMProvider:
        """Detect LLM provider from host configuration"""
        host_lower = self.host.lower()
        
        if "openai.com" in host_lower or "v1/chat/completions" in host_lower:
            return LLMProvider.OPENAI
        elif "anthropic.com" in host_lower:
            return LLMProvider.ANTHROPIC
        elif "11434" in host_lower or "ollama" in host_lower:
            return LLMProvider.OLLAMA
        elif "llama.cpp" in host_lower or "8080" in host_lower:
            return LLMProvider.LLAMACPP
        else:
            # Default to OpenAI-compatible
            return LLMProvider.OPENAI
    
    async def chat(self, messages: List[Dict[str, str]], 
                  temperature: Optional[float] = None,
                  max_tokens: Optional[int] = None,
                  stream: bool = False) -> Union[str, Dict[str, Any]]:
        """Send chat completion request with provider-specific formatting"""
        
        temp = temperature or self.temperature
        max_tok = max_tokens or self.max_tokens
        
        if self.provider == LLMProvider.OLLAMA:
            return await self._ollama_chat(messages, temp, max_tok, stream)
        elif self.provider == LLMProvider.ANTHROPIC:
            return await self._anthropic_chat(messages, temp, max_tok, stream)
        else:
            # OpenAI-compatible (OpenAI, llama.cpp, etc.)
            return await self._openai_chat(messages, temp, max_tok, stream)
    
    async def _openai_chat(self, messages: List[Dict[str, str]], 
                          temperature: float, max_tokens: int, 
                          stream: bool) -> Union[str, Dict[str, Any]]:
        """OpenAI-compatible chat completion"""
        
        url = f"{self.host}{self.endpoints[LLMProvider.OPENAI]['chat']}"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }
        
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        try:
            response = await self._request("POST", url, json=payload, headers=headers)
            
            if response.status == 200:
                data = await response.json()
                if stream:
                    return data  # Return full response for streaming
                else:
                    return data["choices"][0]["message"]["content"]
            else:
                error = await response.text()
                logger.error(f"OpenAI API error {response.status}: {error}")
                raise Exception(f"API error: {response.status}")
                
        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            raise
    
    async def _ollama_chat(self, messages: List[Dict[str, str]], 
                          temperature: float, max_tokens: int,
                          stream: bool) -> Union[str, Dict[str, Any]]:
        """Ollama-specific chat completion"""
        
        url = f"{self.host}{self.endpoints[LLMProvider.OLLAMA]['chat']}"
        
        # Convert messages to Ollama format
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        
        try:
            if stream:
                # For streaming, we need to handle differently
                session = await self._get_session(url)
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        full_response = ""
                        async for line in response.content:
                            if line:
                                try:
                                    data = json.loads(line)
                                    if "message" in data:
                                        full_response += data["message"].get("content", "")
                                except json.JSONDecodeError:
                                    continue
                        return full_response
                    else:
                        error = await response.text()
                        logger.error(f"Ollama API error {response.status}: {error}")
                        raise Exception(f"API error: {response.status}")
            else:
                response = await self._request("POST", url, json=payload)
                
                if response.status == 200:
                    data = await response.json()
                    return data["message"]["content"]
                else:
                    error = await response.text()
                    logger.error(f"Ollama API error {response.status}: {error}")
                    raise Exception(f"API error: {response.status}")
                    
        except Exception as e:
            logger.error(f"Ollama chat error: {e}")
            raise
    
    async def _anthropic_chat(self, messages: List[Dict[str, str]], 
                             temperature: float, max_tokens: int,
                             stream: bool) -> Union[str, Dict[str, Any]]:
        """Anthropic Claude API chat completion"""
        
        url = f"{self.host}{self.endpoints[LLMProvider.ANTHROPIC]['messages']}"
        
        # Extract system message if present
        system_message = None
        user_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                user_messages.append(msg)
        
        payload = {
            "model": self.model,
            "messages": user_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream
        }
        
        if system_message:
            payload["system"] = system_message
        
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        
        try:
            response = await self._request("POST", url, json=payload, headers=headers)
            
            if response.status == 200:
                data = await response.json()
                if stream:
                    return data
                else:
                    return data["content"][0]["text"]
            else:
                error = await response.text()
                logger.error(f"Anthropic API error {response.status}: {error}")
                raise Exception(f"API error: {response.status}")
                
        except Exception as e:
            logger.error(f"Anthropic chat error: {e}")
            raise
    
    async def complete(self, prompt: str,
                      temperature: Optional[float] = None,
                      max_tokens: Optional[int] = None) -> str:
        """Simple completion request (non-chat)"""
        
        temp = temperature or self.temperature
        max_tok = max_tokens or self.max_tokens
        
        if self.provider == LLMProvider.OLLAMA:
            return await self._ollama_generate(prompt, temp, max_tok)
        else:
            # Convert to chat format for providers that don't have completion
            messages = [{"role": "user", "content": prompt}]
            return await self.chat(messages, temp, max_tok)
    
    async def _ollama_generate(self, prompt: str, temperature: float, 
                              max_tokens: int) -> str:
        """Ollama generate endpoint for simple completions"""
        
        url = f"{self.host}{self.endpoints[LLMProvider.OLLAMA]['generate']}"
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        
        try:
            response = await self._request("POST", url, json=payload)
            
            if response.status == 200:
                data = await response.json()
                return data["response"]
            else:
                error = await response.text()
                logger.error(f"Ollama generate error {response.status}: {error}")
                raise Exception(f"API error: {response.status}")
                
        except Exception as e:
            logger.error(f"Ollama generate error: {e}")
            raise
    
    async def list_models(self) -> List[str]:
        """List available models from the provider"""
        
        if self.provider == LLMProvider.OLLAMA:
            url = f"{self.host}{self.endpoints[LLMProvider.OLLAMA]['models']}"
        else:
            url = f"{self.host}{self.endpoints[LLMProvider.OPENAI]['models']}"
        
        try:
            headers = {}
            if self.api_key and self.provider != LLMProvider.OLLAMA:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            response = await self._request("GET", url, headers=headers)
            
            if response.status == 200:
                data = await response.json()
                
                if self.provider == LLMProvider.OLLAMA:
                    return [model["name"] for model in data.get("models", [])]
                else:
                    return [model["id"] for model in data.get("data", [])]
            else:
                logger.error(f"Failed to list models: {response.status}")
                return []
                
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return []
    
    def get_provider_name(self) -> str:
        """Get human-readable provider name"""
        return self.provider.value