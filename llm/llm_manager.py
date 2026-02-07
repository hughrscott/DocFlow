"""
LLM Manager for DocFlow - Orchestrates provider selection and fallback chains.

Manages initialization, health checking, and provider selection with
automatic fallback support for resilient document analysis.
"""

from typing import Dict, Any, Optional, List, Tuple
import yaml
import logging
from llm.base import VisionProvider, TextProvider, LLMResponse, ProviderType
from llm.claude_provider import ClaudeProvider
from llm.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class LLMManager:
    """
    Manages LLM providers with intelligent fallback support.
    
    Initializes configured providers, performs health checks,
    and automatically selects providers with fallback chains
    for reliable document analysis.
    """
    
    def __init__(self, config_path: str) -> None:
        """
        Initialize LLM Manager with configuration.
        
        Args:
            config_path: Path to llm_config.yaml file
            
        Raises:
            FileNotFoundError: If config file not found
            ValueError: If config is invalid
        """
        self.config = self._load_config(config_path)
        self.providers: Dict[str, Any] = {}
        self._initialize_providers()
        logger.info(f"LLMManager initialized with {len(self.providers)} providers")
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from YAML file with environment variable expansion.
        
        Supports ${VAR_NAME} syntax for environment variables.
        
        Args:
            config_path: Path to YAML configuration file
            
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file not found
            ValueError: If YAML is invalid
        """
        try:
            import os
            
            with open(config_path, 'r') as f:
                content = f.read()
            
            # Simple environment variable substitution
            for key, value in os.environ.items():
                content = content.replace(f"${{{key}}}", value)
            
            config = yaml.safe_load(content)
            logger.info(f"Loaded LLM configuration from {config_path}")
            return config
        
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML configuration: {e}")
            raise ValueError(f"Invalid YAML: {e}")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def _initialize_providers(self) -> None:
        """
        Initialize all enabled providers from configuration.
        
        Creates provider instances based on config settings.
        Logs warnings if enabled providers fail to initialize.
        """
        providers_config = self.config.get("providers", {})
        
        # Initialize Claude if enabled
        if providers_config.get("claude", {}).get("enabled", False):
            try:
                self.providers["claude"] = ClaudeProvider(providers_config["claude"])
                logger.info("Claude provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Claude provider: {e}")
        
        # Initialize Ollama if enabled
        if providers_config.get("ollama", {}).get("enabled", False):
            try:
                self.providers["ollama"] = OllamaProvider(providers_config["ollama"])
                logger.info("Ollama provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Ollama provider: {e}")
        
        # Initialize OpenAI if enabled (future)
        if providers_config.get("openai", {}).get("enabled", False):
            logger.info("OpenAI provider support coming soon")
    
    async def get_vision_provider(self) -> VisionProvider:
        """
        Get primary vision provider with fallback chain.
        
        Attempts to use primary provider, then falls back through
        the configured fallback chain until a healthy provider is found.
        
        Returns:
            Available VisionProvider instance
            
        Raises:
            Exception: If no vision providers are available
        """
        primary = self.config.get("llm", {}).get("vision_provider", "claude")
        fallbacks = self.config.get("llm", {}).get("fallback_providers", [])
        
        # Try primary provider
        if primary in self.providers:
            try:
                if await self.providers[primary].health_check():
                    logger.debug(f"Using primary vision provider: {primary}")
                    return self.providers[primary]
            except Exception as e:
                logger.warning(f"Primary vision provider {primary} failed health check: {e}")
        
        # Try fallback providers
        for fallback in fallbacks:
            if fallback in self.providers:
                try:
                    if await self.providers[fallback].health_check():
                        logger.warning(f"⚠️  Primary vision provider unavailable, using {fallback}")
                        return self.providers[fallback]
                except Exception as e:
                    logger.warning(f"Fallback vision provider {fallback} failed: {e}")
        
        error_msg = "No vision providers available!"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    async def get_text_provider(self) -> TextProvider:
        """
        Get primary text provider with fallback chain.
        
        Similar to get_vision_provider but for text processing.
        
        Returns:
            Available TextProvider instance
            
        Raises:
            Exception: If no text providers are available
        """
        primary = self.config.get("llm", {}).get("text_provider", "claude")
        fallbacks = self.config.get("llm", {}).get("fallback_providers", [])
        
        # Try primary provider
        if primary in self.providers:
            try:
                if await self.providers[primary].health_check():
                    logger.debug(f"Using primary text provider: {primary}")
                    return self.providers[primary]
            except Exception as e:
                logger.warning(f"Primary text provider {primary} failed health check: {e}")
        
        # Try fallback providers
        for fallback in fallbacks:
            if fallback in self.providers:
                try:
                    if await self.providers[fallback].health_check():
                        logger.warning(f"⚠️  Primary text provider unavailable, using {fallback}")
                        return self.providers[fallback]
                except Exception as e:
                    logger.warning(f"Fallback text provider {fallback} failed: {e}")
        
        error_msg = "No text providers available!"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    async def analyze_image(
        self,
        image_data: bytes,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Analyze an image using the best available vision provider.
        
        Args:
            image_data: Image bytes to analyze
            prompt: Analysis prompt/instruction
            max_tokens: Maximum response tokens
            
        Returns:
            LLMResponse with analysis results
        """
        try:
            provider = await self.get_vision_provider()
            return await provider.analyze_image(image_data, prompt, max_tokens)
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return LLMResponse(
                content="",
                model="unknown",
                provider=ProviderType.CLAUDE,
                error=str(e),
                success=False
            )
    
    async def analyze_images(
        self,
        images: List[Tuple[bytes, str]],
        prompt: str,
        max_tokens: int = 4000
    ) -> LLMResponse:
        """
        Analyze multiple images using the best available vision provider.

        Args:
            images: List of (image_bytes, label) tuples
            prompt: Analysis prompt
            max_tokens: Maximum response tokens

        Returns:
            LLMResponse with analysis results
        """
        try:
            provider = await self.get_vision_provider()
            return await provider.analyze_images(images, prompt, max_tokens)
        except Exception as e:
            logger.error(f"Multi-image analysis failed: {e}")
            return LLMResponse(
                content="",
                model="unknown",
                provider=ProviderType.CLAUDE,
                error=str(e),
                success=False,
            )

    def get_provider_name(self) -> str:
        """Return the name of the primary configured vision provider."""
        return self.config.get("llm", {}).get("vision_provider", "claude")

    async def process_text(
        self,
        text: str,
        prompt: str,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """
        Process text using the best available text provider.
        
        Args:
            text: Text to process
            prompt: Processing instruction
            max_tokens: Maximum response tokens
            
        Returns:
            LLMResponse with processing results
        """
        try:
            provider = await self.get_text_provider()
            return await provider.process_text(text, prompt, max_tokens)
        except Exception as e:
            logger.error(f"Text processing failed: {e}")
            return LLMResponse(
                content="",
                model="unknown",
                provider=ProviderType.CLAUDE,
                error=str(e),
                success=False
            )
    
    def get_active_providers(self) -> Dict[str, str]:
        """
        Get list of active and available providers.
        
        Returns:
            Dictionary mapping provider names to their types
        """
        active = {}
        for name, provider in self.providers.items():
            if provider.is_available():
                active[name] = type(provider).__name__
        return active
