from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # AICredits LLM Configuration
    aicredits_api_key: Optional[str] = Field(default=None, alias="AICREDITS_API_KEY")
    aicredits_base_url: str = Field(
        default="https://api.aicredits.in/v1",
        alias="AICREDITS_BASE_URL"
    )
    aicredits_model: str = Field(
        default="gemini-2.5-flash",
        alias="AICREDITS_MODEL"
    )

    # Video Encoding Parameters
    video_width: int = Field(default=1920, alias="VIDEO_WIDTH")
    video_height: int = Field(default=1080, alias="VIDEO_HEIGHT")
    video_fps: int = Field(default=30, alias="VIDEO_FPS")
    video_crf: int = Field(default=20, alias="VIDEO_CRF")
    video_preset: str = Field(default="medium", alias="VIDEO_PRESET")
    video_scale_mode: str = Field(
        default="pad",
        description="pad (letterbox/pillarbox) or crop (fill screen preserving center)"
    )

    # Audio Encoding Parameters
    audio_bitrate: str = Field(default="192k", alias="AUDIO_BITRATE")

    # Directory Paths
    cache_dir: Path = Field(default=Path("cache"), alias="CACHE_DIR")
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")

    # AI Video Editorial Chunking & Quality Heuristics
    max_chunk_images: int = Field(
        default=40,
        description="Maximum number of images per LLM planning chunk to avoid token limits"
    )
    min_image_duration: float = Field(
        default=0.8,
        description="Threshold below which an image duration is considered fast/rushed"
    )
    max_image_duration: float = Field(
        default=25.0,
        description="Threshold above which an image duration is considered excessively long"
    )
    tail_dumping_ratio_threshold: float = Field(
        default=0.35,
        description="If tail images duration ratio to median duration is below this, flag leftover dumping"
    )
    llm_temperature: float = Field(default=0.2)
    llm_timeout_seconds: int = Field(default=120)
    llm_max_retries: int = Field(default=3)


config = AppConfig()
