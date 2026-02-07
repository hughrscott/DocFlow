"""
Base classes and interfaces for LLM providers.

Defines the abstract interface that all LLM providers must implement,
ensuring consistent behavior across different provider types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple


class ProviderType(Enum):
    """Enumeration of supported LLM provider types."""
    CLAUDE = "claude"
    OLLAMA = "ollama"
    OPENAI = "openai"


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    
    content: str
    """The actual response text from the LLM."""
    
    model: str
    """The model name used for processing."""
    
    provider: ProviderType
    """The provider that generated this response."""
    
    usage: Optional[Dict[str, int]] = None
    """Token usage information (if available)."""
    
    error: Optional[str] = None
    """Error message if processing failed."""
    
    success: bool = True
    """Whether the request was successful."""


class LLMProvider(ABC):
    """
    Abstract base class for all LLM providers.
    
    All LLM providers must implement this interface to ensure
    consistent behavior and easy provider switching.
    """
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize provider with configuration.
        
        Args:
            config: Provider-specific configuration dictionary.
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if provider is available/configured.
        
        Returns:
            True if provider can be used, False otherwise.
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Verify provider is working and accessible.
        
        Returns:
            True if provider is healthy, False otherwise.
        """
        pass


class VisionProvider(LLMProvider):
    """
    Interface for LLM models that can analyze images.
    
    Vision providers can process document images and extract
    text, structure, and semantic information.
    """
    
    @abstractmethod
    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Analyze an image and return structured response.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.)
            prompt: Analysis prompt/instruction
            max_tokens: Maximum tokens in response

        Returns:
            LLMResponse with analysis results

        Raises:
            Exception: If image analysis fails
        """
        pass

    async def analyze_images(
        self,
        images: List[Tuple[bytes, str]],
        prompt: str,
        max_tokens: int = 4000
    ) -> LLMResponse:
        """
        Analyze multiple images in a single API call.

        Default implementation falls back to analyzing the first image only.
        Providers that support multi-image (e.g. Claude) should override this.

        Args:
            images: List of (image_bytes, label) tuples
            prompt: Analysis prompt/instruction
            max_tokens: Maximum tokens in response

        Returns:
            LLMResponse with analysis results
        """
        if images:
            return await self.analyze_image(images[0][0], prompt, max_tokens)
        return LLMResponse(
            content="",
            model="unknown",
            provider=ProviderType.CLAUDE,
            error="No images provided",
            success=False,
        )


class TextProvider(LLMProvider):
    """
    Interface for text-based LLM models.
    
    Text providers process text inputs and can be used for
    reasoning, analysis, and other text-based tasks.
    """
    
    @abstractmethod
    async def process_text(
        self,
        text: str,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Process text through the LLM.
        
        Args:
            text: Input text to process
            prompt: Processing instruction/context
            max_tokens: Maximum tokens in response
            
        Returns:
            LLMResponse with processing results
            
        Raises:
            Exception: If text processing fails
        """
        pass
