from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class JobKind(str, Enum):
    image = "image"              # text->image, or AI edit when references are given (heavy)
    video = "video"              # text->video / image->video (heavy)
    image_adjust = "image_adjust"  # Pillow retouch (light)
    video_edit = "video_edit"      # ffmpeg edit (light)


HEAVY_KINDS = {JobKind.image, JobKind.video}


class _Common(BaseModel):
    model: str | None = Field(default=None, description="Model key; default chosen by the server.")
    callback_url: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=128)


# ----------------------------------------------------------------- generation
class ImageJobRequest(_Common):
    prompt: str = Field(min_length=1, max_length=4000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int = Field(default=4, ge=1, le=50)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    count: int = Field(default=1, ge=1, le=4)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _size(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("set both width and height, or neither")
        for v in (self.width, self.height):
            if v is not None and v % 16:
                raise ValueError("width and height must be divisible by 16")
        return self


class VideoJobRequest(_Common):
    prompt: str = Field(min_length=1, max_length=5000)
    negative_prompt: str = Field(default="", max_length=2000)
    width: int | None = Field(default=None, ge=256, le=1280)
    height: int | None = Field(default=None, ge=256, le=1280)
    frames: int | None = Field(default=None, ge=9, le=241)
    duration_seconds: float | None = Field(default=None, ge=0.3, le=10)
    fps: float = Field(default=24.0, ge=8, le=60)
    steps: int | None = Field(default=None, ge=1, le=60)
    guidance_scale: float | None = Field(default=None, ge=0, le=15)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    reference_asset_id: str | None = Field(default=None, description="Start frame (image -> video).")
    audio: bool = True

    @model_validator(mode="after")
    def _check(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("set both width and height, or neither")
        for v in (self.width, self.height):
            if v is not None and v % 32:
                raise ValueError("width and height must be divisible by 32")
        if self.frames is not None:
            if (self.frames - 1) % 8:
                raise ValueError("frames must satisfy frames % 8 == 1 (e.g. 25, 49, 97, 121)")
        return self

    def resolved_frames(self, default: int) -> int:
        if self.frames is not None:
            return self.frames
        if self.duration_seconds is not None:
            return max(9, round(self.duration_seconds * self.fps / 8) * 8 + 1)
        return default


# ---------------------------------------------------------------- video edits
class TrimOp(BaseModel):
    op: Literal["trim"]
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)


class SpeedOp(BaseModel):
    op: Literal["speed"]
    factor: float = Field(ge=0.25, le=4)


class ReverseOp(BaseModel):
    op: Literal["reverse"]


class ResizeOp(BaseModel):
    op: Literal["resize"]
    width: int = Field(ge=64, le=2048)
    height: int = Field(ge=64, le=2048)
    mode: Literal["fit", "fill", "stretch"] = "fit"


class RotateOp(BaseModel):
    op: Literal["rotate"]
    degrees: Literal[90, 180, 270]


class FlipOp(BaseModel):
    op: Literal["flip"]
    axis: Literal["h", "v"]


class FilterOp(BaseModel):
    op: Literal["filter"]
    name: Literal["grayscale", "sepia", "vivid", "warm", "cool", "sharpen", "blur", "negative"]


class AdjustOp(BaseModel):
    op: Literal["adjust"]
    brightness: float = Field(default=0, ge=-1, le=1)
    contrast: float = Field(default=1, ge=0, le=3)
    saturation: float = Field(default=1, ge=0, le=3)


class FadeOp(BaseModel):
    op: Literal["fade"]
    fade_in: float = Field(default=0, ge=0, le=10)
    fade_out: float = Field(default=0, ge=0, le=10)


class MuteOp(BaseModel):
    op: Literal["mute"]


VideoOp = Annotated[
    Union[TrimOp, SpeedOp, ReverseOp, ResizeOp, RotateOp, FlipOp, FilterOp, AdjustOp, FadeOp, MuteOp],
    Field(discriminator="op"),
]


class _Source(BaseModel):
    source_asset_id: str | None = None
    source_job_id: str | None = None
    source_index: int = Field(default=0, ge=0, le=3)

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.source_asset_id) == bool(self.source_job_id):
            raise ValueError("provide exactly one of source_asset_id or source_job_id")
        return self


class VideoEditRequest(_Common, _Source):
    ops: list[VideoOp] = Field(default_factory=list, max_length=20)
    output: Literal["mp4", "gif", "frame"] = "mp4"
    frame_time: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _need_something(self):
        if self.output == "mp4" and not self.ops:
            raise ValueError("add at least one edit operation")
        return self


# ---------------------------------------------------------------- image adjust
class ImgRotate(BaseModel):
    op: Literal["rotate"]
    degrees: Literal[90, 180, 270]


class ImgFlip(BaseModel):
    op: Literal["flip"]
    axis: Literal["h", "v"]


class ImgGrayscale(BaseModel):
    op: Literal["grayscale"]


class ImgAdjust(BaseModel):
    op: Literal["adjust"]
    brightness: float = Field(default=1, ge=0, le=3)
    contrast: float = Field(default=1, ge=0, le=3)
    saturation: float = Field(default=1, ge=0, le=3)
    sharpness: float = Field(default=1, ge=0, le=4)


class ImgBlur(BaseModel):
    op: Literal["blur"]
    radius: float = Field(ge=0.5, le=20)


class ImgResize(BaseModel):
    op: Literal["resize"]
    width: int = Field(ge=16, le=4096)
    height: int = Field(ge=16, le=4096)
    mode: Literal["fit", "fill", "stretch"] = "fit"


class ImgCrop(BaseModel):
    op: Literal["crop_aspect"]
    ratio_w: int = Field(ge=1, le=100)
    ratio_h: int = Field(ge=1, le=100)


ImageOp = Annotated[
    Union[ImgRotate, ImgFlip, ImgGrayscale, ImgAdjust, ImgBlur, ImgResize, ImgCrop],
    Field(discriminator="op"),
]


class ImageAdjustRequest(_Common, _Source):
    ops: list[ImageOp] = Field(min_length=1, max_length=12)
    format: Literal["png", "jpeg", "webp"] = "png"


# ------------------------------------------------------------------- responses
class ArtifactOut(BaseModel):
    index: int
    url: str
    thumb_url: str
    mime: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    bytes: int | None = None


class JobResponse(BaseModel):
    job_id: str
    kind: JobKind
    status: str
    model: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    artifact_url: str | None = None
    artifact_mime: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("progress")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))
