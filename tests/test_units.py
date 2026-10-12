import pytest
from pydantic import TypeAdapter, ValidationError

from studio import media_tools as mt
from studio.policy import validate_callback, validate_prompt
from studio.schemas import ImageJobRequest, VideoEditRequest, VideoJobRequest, VideoOp


def test_image_schema_rules():
    assert ImageJobRequest(prompt="x").width is None
    assert ImageJobRequest(prompt="x", width=1024, height=768).height == 768
    with pytest.raises(ValidationError):
        ImageJobRequest(prompt="x", width=1000, height=1024)      # not divisible by 16
    with pytest.raises(ValidationError):
        ImageJobRequest(prompt="x", width=1024)                   # only one side


def test_video_schema_rules():
    assert VideoJobRequest(prompt="x", frames=97).frames == 97
    for bad in (dict(frames=100), dict(width=700, height=480)):
        with pytest.raises(ValidationError):
            VideoJobRequest(prompt="x", **bad)
    assert VideoJobRequest(prompt="x", duration_seconds=4, fps=24).resolved_frames(49) == 97


def test_video_edit_needs_source_and_ops():
    with pytest.raises(ValidationError):
        VideoEditRequest(ops=[{"op": "mute"}])                    # no source
    with pytest.raises(ValidationError):
        VideoEditRequest(source_asset_id="a", source_job_id="b", ops=[{"op": "mute"}])
    with pytest.raises(ValidationError):
        VideoEditRequest(source_asset_id="a", ops=[])             # mp4 with nothing to do
    assert VideoEditRequest(source_asset_id="a", ops=[], output="gif").output == "gif"
    with pytest.raises(ValidationError):
        TypeAdapter(VideoOp).validate_python({"op": "speed", "factor": 10})


def test_policy():
    validate_prompt("a quiet harbour at dawn")
    for bad in ("child porn", "nude teenager", "undress her"):
        with pytest.raises(ValueError):
            validate_prompt(bad, is_edit=True)
    validate_prompt("remove the background", is_edit=True)
    with pytest.raises(ValueError):
        validate_prompt("   ")


def test_callback_allowlist():
    validate_callback(None, ())
    with pytest.raises(ValueError):
        validate_callback("http://169.254.169.254/x", ())
    validate_callback("https://os.enfactum.local/hook", ("os.enfactum.local",))


def test_audio_tempo_chain_stays_in_range():
    assert mt._atempo(4) == ["atempo=2.0", "atempo=2.0000"]
    assert mt._atempo(0.25)[0] == "atempo=0.5"
    assert mt._atempo(1.5) == ["atempo=1.5000"]


def test_filter_builder_tracks_duration():
    from studio.schemas import FadeOp, SpeedOp, TrimOp
    vf, af, mute, dur = mt.build_video_filters(
        [TrimOp(op="trim", start=1, end=5), SpeedOp(op="speed", factor=2), FadeOp(op="fade", fade_out=0.5)],
        {"duration": 10.0})
    assert dur == 2.0
    assert any("fade=t=out:st=1.500" in f for f in vf)
