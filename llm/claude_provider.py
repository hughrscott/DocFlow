"""
Claude API provider implementation for DocFlow.

Implements vision and text capabilities using Anthropic's Claude API.
Handles image analysis and text processing with proper error handling.
"""

import base64
import asyncio
from typing import Dict, Any, Optional
from llm.base import VisionProvider, TextProvider, LLMResponse, ProviderType
from anthropic import AsyncAnthropic, APIError, APIConnectionError, APITimeoutError
import logging

logger = logging.getLogger(__name__)


class ClaudeProvider(VisionProvider, TextProvider):
    """
    Claude API provider supporting both vision and text capabilities.
    
    Uses Anthropic's official API client for reliable communication
    with Claude models. Supports streaming responses and proper
    timeout/retry handling.
    """
    
    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize Claude provider with API configuration.
        
        Args:
            config: Configuration dictionary containing:
                - api_key: Anthropic API key
                - vision_model: Model for image analysis (default: claude-3-5-sonnet-20241022)
                - text_model: Model for text processing (optional, uses vision_model if not specified)
                - timeout: Request timeout in seconds
                - max_retries: Maximum number of retry attempts
        """
        self.api_key = config.get("api_key")
        self.vision_model = config.get("vision_model", "claude-3-5-sonnet-20241022")
        self.text_model = config.get("text_model", self.vision_model)
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 3)
        
        if not self.api_key:
            logger.warning("Claude API key not provided")
            self.client = None
        else:
            self.client = AsyncAnthropic(api_key=self.api_key)
    
    def is_available(self) -> bool:
        """
        Check if Claude API is configured.
        
        Returns:
            True if API key is present and client is initialized.
        """
        return self.api_key is not None and len(self.api_key) > 0 and self.client is not None
    
    async def health_check(self) -> bool:
        """
        Verify Claude API is accessible.
        
        Sends a simple test message to verify API connectivity.
        
        Returns:
            True if API is responsive, False otherwise.
        """
        if not self.is_available():
            return False
        
        try:
            response = await self.client.messages.create(
                model=self.vision_model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ok"}],
                timeout=5.0,
            )
            logger.info(f"Claude health check passed for model {self.vision_model}")
            return True
        except (APIConnectionError, APITimeoutError) as e:
            logger.error(f"Claude health check failed - connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Claude health check failed: {e}")
            return False
    
    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Analyze an image using Claude's vision capabilities.
        
        Converts image bytes to base64 and sends to Claude for analysis.
        Implements retry logic for transient failures.
        
        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.)
            prompt: Analysis instruction/prompt
            max_tokens: Maximum tokens in response
            
        Returns:
            LLMResponse with analysis results or error information
        """
        if not self.is_available():
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.CLAUDE,
                error="Claude API not configured",
                success=False
            )
        
        try:
            # Convert image to base64
            base64_image = base64.standard_b64encode(image_data).decode("utf-8")
            
            # Determine image media type (simplified - assumes PNG for now)
            media_type = "image/png"
            
            # Retry logic for transient failures
            for attempt in range(self.max_retries):
                try:
                    response = await self.client.messages.create(
                        model=self.vision_model,
                        max_tokens=max_tokens,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": base64_image,
                                        },
                                    },
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ],
                            }
                        ],
                        timeout=self.timeout,
                    )
                    
                    logger.info(f"Successfully analyzed image with Claude (attempt {attempt + 1})")
                    
                    return LLMResponse(
                        content=response.content[0].text,
                        model=self.vision_model,
                        provider=ProviderType.CLAUDE,
                        usage={
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens
                        },
                        success=True
                    )
                
                except APITimeoutError as e:
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff
                        logger.warning(f"Claude timeout (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        raise
        
        except APIConnectionError as e:
            error_msg = f"Claude API connection error: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.CLAUDE,
                error=error_msg,
                success=False
            )
        except APIError as e:
            error_msg = f"Claude API error: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.CLAUDE,
                error=error_msg,
                success=False
            )
        except Exception as e:
            error_msg = f"Unexpected error during image analysis: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.vision_model,
                provider=ProviderType.CLAUDE,
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
        Process text using Claude's language capabilities.
        
        Args:
            text: Input text to process
            prompt: Processing instruction
            max_tokens: Maximum tokens in response
            
        Returns:
            LLMResponse with processing results or error information
        """
        if not self.is_available():
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.CLAUDE,
                error="Claude API not configured",
                success=False
            )
        
        try:
            for attempt in range(self.max_retries):
                try:
                    response = await self.client.messages.create(
                        model=self.text_model,
                        max_tokens=max_tokens,
                        messages=[
                            {
                                "role": "user",
                                "content": f"{prompt}\n\n{text}"
                            }
                        ],
                        timeout=self.timeout,
                    )
                    
                    logger.info(f"Successfully processed text with Claude (attempt {attempt + 1})")
                    
                    return LLMResponse(
                        content=response.content[0].text,
                        model=self.text_model,
                        provider=ProviderType.CLAUDE,
                        usage={
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens
                        },
                        success=True
                    )
                
                except APITimeoutError as e:
                    if attempt < self.max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Claude timeout (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        raise
        
        except APIConnectionError as e:
            error_msg = f"Claude API connection error: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.CLAUDE,
                error=error_msg,
                success=False
            )
        except APIError as e:
            error_msg = f"Claude API error: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.CLAUDE,
                error=error_msg,
                success=False
            )
        except Exception as e:
            error_msg = f"Unexpected error during text processing: {str(e)}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self.text_model,
                provider=ProviderType.CLAUDE,
                error=error_msg,
                success=False
            )
