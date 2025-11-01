"""
Ollama local LLM provider implementation for DocFlow.

Implements vision and text capabilities using Ollama's local API.
Allows running document analysis entirely on local hardware.
"""

import base64
import asyncio
import httpx
import json
from typing import Dict, Any, Optional
from llm.base import VisionProvider, TextProvider, LLMResponse, ProviderType
import logging

logger = logging.getLogger(__name__)


class OllamaProvider(VisionProvider, TextProvider):
    """
    Ollama local LLM provider supporting vision and text capabilities.
    
    Communicates with Ollama API for local model inference.
    Supports models like LLaVA for vision and Mistral/Llama for text.
    """
    
    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize Ollama provider with local API configuration.
        
        Args:
            config: Configuration dictionary containing:
                - base_url: Ollama API endpoint (e.g., http://localhost:11434)
                - vision_model: Model for image analysis (e.g., llava:latest)
                - text_model: Model for text processing (e.g., mistral:latest)
                - timeout: Request timeout in seconds
                - max_retries: Maximum number of retry attempts
        """
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.vision_model = config.get("vision_model", "llava:latest")
        self.text_model = config.get("text_model", "mistral:latest")
        self.timeout = config.get("timeout", 60)
        self.max_retries = config.get("max_retries", 2)
        self.client: Optional[httpx.AsyncClient] = None
    
    def _get_client(self) -> httpx.AsyncClient:
        """
        Get or create HTTP client for Ollama API.
        
        Returns:
            AsyncClient instance for making requests to Ollama.
        """
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout
            )
        return self.client
    
    def is_available(self) -> bool:
        """
        Check if Ollama is configured.
        
        Returns:
            True if base_url is configured, False otherwise.
        """
        return self.base_url is not None
    
    async def health_check(self) -> bool:
        """
        Verify Ollama is running and accessible.
        
        Checks the /api/tags endpoint to verify connectivity.
        
        Returns:
            True if Ollama is responsive, False otherwise.
        """
        try:
            client = self._get_client()
            response = await client.get("/api/tags")
            is_healthy = response.status_code == 200
            
            if is_healthy:
                logger.info("Ollama health check passed")
            else:
                logger.warning(f"Ollama health check failed with status {response.status_code}")
            
            return is_healthy
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"Ollama health check failed - connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Ollama health check failed: {e}")
            return False
    
    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Analyze an image using Ollama's vision model.
        
        Sends the image to Ollama for local processing using
        configured vision model (typically LLaVA).
        
        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.)
            prompt: Analysis instruction/prompt
            max_tokens: Maximum tokens in response (advisory for Ollama)
            
        Returns:
            LLMResponse with analysis results or error information
        """
        if not self.is_available():
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.OLLAMA,
                error="Ollama not configured",
                success=False
            )
        
        try:
            # Convert image to base64
            base64_image = base64.standard_b64encode(image_data).decode("utf-8")
            
            client = self._get_client()
            
            # Retry logic for transient failures
            for attempt in range(self.max_retries):
                try:
                    logger.debug(f"Sending image analysis request to Ollama (attempt {attempt + 1})")
                    
                    response = await client.post(
                        "/api/generate",
                        json={
                            "model": self.vision_model,
                            "prompt": prompt,
                            "images": [base64_image],
                            "stream": False,
                        },
                        timeout=self.timeout,
                    )
                    
                    if response.status_code != 200:
                        error_msg = f"Ollama returned status {response.status_code}"
                        logger.warning(f"{error_msg}: {response.text}")
                        
                        if attempt < self.max_retries - 1:
                            wait_time = 2 ** attempt
                            await asyncio.sleep(wait_time)
                            continue
                        
                        return LLMResponse(
                            content="",
                            model=self.vision_model,
                            provider=ProviderType.OLLAMA,
                            error=error_msg,
                            success=False
                        )
                    
                    data = response.json()
                    logger.info(f"Successfully analyzed image with Ollama (attempt {attempt + 1})")
                    
                    return LLMResponse(
                        content=data.get("response", ""),
                        model=self.vision_model,
                        provider=ProviderType.OLLAMA,
                        success=True
                    )
                
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Ollama timeout (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        raise
        
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to Ollama at {self.base_url}: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
        except httpx.TimeoutException as e:
            error_msg = f"Ollama request timeout after {self.timeout}s: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
        except Exception as e:
            error_msg = f"Unexpected error during image analysis: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
    
    async def process_text(
        self,
        text: str,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Process text using Ollama's language model.
        
        Args:
            text: Input text to process
            prompt: Processing instruction
            max_tokens: Maximum tokens in response (advisory)
            
        Returns:
            LLMResponse with processing results or error information
        """
        if not self.is_available():
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.OLLAMA,
                error="Ollama not configured",
                success=False
            )
        
        try:
            client = self._get_client()
            
            for attempt in range(self.max_retries):
                try:
                    logger.debug(f"Sending text processing request to Ollama (attempt {attempt + 1})")
                    
                    response = await client.post(
                        "/api/generate",
                        json={
                            "model": self.text_model,
                            "prompt": f"{prompt}\n\n{text}",
                            "stream": False,
                        },
                        timeout=self.timeout,
                    )
                    
                    if response.status_code != 200:
                        error_msg = f"Ollama returned status {response.status_code}"
                        logger.warning(f"{error_msg}: {response.text}")
                        
                        if attempt < self.max_retries - 1:
                            wait_time = 2 ** attempt
                            await asyncio.sleep(wait_time)
                            continue
                        
                        return LLMResponse(
                            content="",
                            model=self.text_model,
                            provider=ProviderType.OLLAMA,
                            error=error_msg,
                            success=False
                        )
                    
                    data = response.json()
                    logger.info(f"Successfully processed text with Ollama (attempt {attempt + 1})")
                    
                    return LLMResponse(
                        content=data.get("response", ""),
                        model=self.text_model,
                        provider=ProviderType.OLLAMA,
                        success=True
                    )
                
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Ollama timeout (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        raise
        
        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to Ollama at {self.base_url}: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
        except httpx.TimeoutException as e:
            error_msg = f"Ollama request timeout after {self.timeout}s: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
        except Exception as e:
            error_msg = f"Unexpected error during text processing: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.OLLAMA,
                error=error_msg,
                success=False
            )
