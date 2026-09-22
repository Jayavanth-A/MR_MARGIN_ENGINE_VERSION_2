"""CapCut Desktop Project Exporter for MR_MARGIN_ENGINE.

Generates an editable, self-contained CapCut Desktop project (v8.6+) directly from
the authoritative final_timeline.json and image manifest.

Guarantees:
- Single Source of Truth: exact timing derived from the authoritative Timeline object.
- Microsecond timing conversion with zero gaps and zero overlaps.
- Complete 1..N sequential image order without skipping, reordering, or duplicating.
- Complete voiceover audio starting at 0.000.
- All media assets packaged self-contained into assets/.
- Full multi-timeline directory hierarchy compatible with CapCut v8.6 Desktop.
- Optional registration into CapCut Desktop's local draft store (root_meta_info.json).
"""

import json
import logging
import os
from pathlib import Path
import shutil
import struct
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from engine.capcut_validator import CapCutValidationError, CapCutValidator
from engine.config import AppConfig
from engine.models import CapCutExportReport, ImageRecord, Timeline, TimelineEntry

logger = logging.getLogger(__name__)


def _get_image_dimensions(image_path: Path) -> Tuple[int, int]:
    """Extract width and height from image file without external heavy dependencies."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return img.width, img.height
    except Exception:
        pass

    # Pure Python JPEG dimension reader fallback
    try:
        with open(image_path, "rb") as f:
            data = f.read(4096)
            # Scan for SOF0/SOF2 marker
            i = 0
            while i < len(data) - 9:
                if data[i] == 0xFF and data[i+1] in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i+5:i+9])
                    return w, h
                i += 1
    except Exception:
        pass

    return 1920, 1080  # Default HD fallback


def detect_capcut_draft_root() -> Optional[Path]:
    """Auto-detect CapCut Desktop draft root directory on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
        if candidate.exists():
            return candidate
    return None


class CapCutExporter:
    """Exports an authoritative Timeline into an editable CapCut Desktop project package."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()

    def export(
        self,
        timeline: Timeline,
        images_manifest: List[ImageRecord],
        audio_path: Path,
        output_dir: Path,
        project_name: str = "MR_MARGIN_PROJECT",
        copy_assets: bool = True,
        auto_register: Optional[bool] = None
    ) -> CapCutExportReport:
        """Export timeline and assets into a CapCut Desktop project package."""
        logger.info(f"Starting CapCut project export for '{project_name}'...")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        assets_dir = output_dir / "assets"
        if copy_assets:
            assets_dir.mkdir(parents=True, exist_ok=True)

        project_id = str(uuid.uuid4()).upper()
        main_timeline_id = str(uuid.uuid4()).upper()
        now_time = int(time.time())
        now_time_us = int(now_time * 1_000_000)
        total_duration_us = int(round(timeline.audio_duration * 1_000_000))

        # 1. Resolve Audio Asset
        audio_path = Path(audio_path)
        if copy_assets and audio_path.exists():
            dest_audio = assets_dir / audio_path.name
            shutil.copy2(audio_path, dest_audio)
            final_audio_path_str = str(dest_audio.resolve()).replace("\\", "/")
            total_materials_bytes = dest_audio.stat().st_size
        else:
            final_audio_path_str = str(audio_path.resolve()).replace("\\", "/")
            total_materials_bytes = audio_path.stat().st_size if audio_path.exists() else 0

        # 2. Build Audio Material & Segment
        audio_material_id = str(uuid.uuid4()).upper()
        audio_mat = {
            "id": audio_material_id,
            "unique_id": "",
            "type": "extract_music",
            "name": audio_path.name,
            "duration": total_duration_us,
            "path": final_audio_path_str,
            "category_name": "local",
            "wave_points": [],
            "music_id": "",
            "app_id": 0,
            "text_id": "",
            "tone_type": "",
            "source_platform": 0,
            "video_id": "",
            "effect_id": "",
            "resource_id": "",
            "third_resource_id": "",
            "category_id": "",
            "intensifies_path": "",
            "formula_id": "",
            "check_flag": 1,
            "team_id": "",
            "local_material_id": "",
            "tone_speaker": "",
            "mock_tone_speaker": "",
            "tone_effect_id": "",
            "tone_effect_name": "",
            "tone_platform": "",
            "cloned_model_type": "",
            "tone_category_id": "",
            "tone_category_name": "",
            "tone_second_category_id": "",
            "tone_second_category_name": "",
            "tone_emotion_name_key": "",
            "tone_emotion_style": "",
            "tone_emotion_role": "",
            "tone_emotion_selection": "",
            "tone_emotion_scale": 0.0,
            "moyin_emotion": "",
            "request_id": "",
            "query": "",
            "search_id": "",
            "sound_separate_type": "",
            "is_text_edit_overdub": False,
            "is_ugc": False,
            "is_ai_clone_tone": False,
            "is_ai_clone_tone_post": False,
            "source_from": "",
            "copyright_limit_type": "none",
            "aigc_history_id": "",
            "aigc_item_id": "",
            "music_source": "",
            "pgc_id": "",
            "pgc_name": "",
            "similiar_music_info": {"original_song_id": "", "original_song_name": ""},
            "ai_music_type": 0,
            "ai_music_enter_from": "",
            "lyric_type": 0,
            "tts_task_id": "",
            "tts_generate_scene": "",
            "ai_music_generate_scene": 0,
            "tts_benefit_info": {"benefit_type": "none", "benefit_log_id": "", "benefit_log_extra": "", "benefit_amount": -1}
        }

        a_speed_id = str(uuid.uuid4()).upper()
        a_placeholder_id = str(uuid.uuid4()).upper()
        a_beats_id = str(uuid.uuid4()).upper()
        a_channel_id = str(uuid.uuid4()).upper()
        a_vocal_id = str(uuid.uuid4()).upper()

        audio_segment = {
            "id": str(uuid.uuid4()).upper(),
            "source_timerange": {"start": 0, "duration": total_duration_us},
            "target_timerange": {"start": 0, "duration": total_duration_us},
            "render_timerange": {"start": 0, "duration": 0},
            "desc": "",
            "state": 0,
            "speed": 1.0,
            "is_loop": False,
            "is_tone_modify": False,
            "reverse": False,
            "intensifies_audio": False,
            "cartoon": False,
            "volume": 1.0,
            "last_nonzero_volume": 1.0,
            "clip": None,
            "uniform_scale": None,
            "material_id": audio_material_id,
            "extra_material_refs": [a_speed_id, a_placeholder_id, a_beats_id, a_channel_id, a_vocal_id],
            "render_index": 0,
            "keyframe_refs": [],
            "enable_lut": False,
            "enable_adjust": False,
            "enable_hsl": False,
            "visible": True,
            "group_id": "",
            "enable_color_curves": True,
            "enable_hsl_curves": True,
            "track_render_index": 1,
            "hdr_settings": None,
            "enable_color_wheels": True,
            "track_attribute": 0,
            "is_placeholder": False,
            "template_id": "",
            "enable_smart_color_adjust": False,
            "template_scene": "default",
            "common_keyframes": [],
            "caption_info": None,
            "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0, "horizontal_pos_layout": 0, "vertical_pos_layout": 0},
            "enable_color_match_adjust": False,
            "enable_color_correct_adjust": False,
            "enable_adjust_mask": False,
            "raw_segment_id": "",
            "lyric_keyframes": None,
            "enable_video_mask": True,
            "digital_human_template_group_id": "",
            "color_correct_alg_result": "",
            "source": "segmentsourcenormal",
            "enable_mask_stroke": False,
            "enable_mask_shadow": False,
            "enable_color_adjust_pro": False
        }

        # 3. Build Video Materials & Segments
        video_materials: List[Dict[str, Any]] = []
        video_segments: List[Dict[str, Any]] = []
        speeds: List[Dict[str, Any]] = [{
            "id": a_speed_id, "type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None
        }]
        placeholder_infos: List[Dict[str, Any]] = [{
            "id": a_placeholder_id, "type": "placeholder_info", "meta_type": "none", "res_path": "", "res_text": "", "error_path": "", "error_text": ""
        }]
        beats: List[Dict[str, Any]] = [{
            "id": a_beats_id, "type": "beats", "enable_ai_beats": False, "gear": 404, "gear_count": 0, "mode": 404, "user_beats": [], "user_delete_ai_beats": None,
            "ai_beats": {"melody_url": "", "melody_path": "", "beats_url": "", "beats_path": "", "melody_percents": [0.0], "beat_speed_infos": []}
        }]
        sound_channel_mappings: List[Dict[str, Any]] = [{
            "id": a_channel_id, "type": "", "audio_channel_mapping": 0, "is_config_open": False
        }]
        vocal_separations: List[Dict[str, Any]] = [{
            "id": a_vocal_id, "type": "vocal_separation", "choice": 0, "removed_sounds": [], "time_range": None, "production_path": "", "final_algorithm": "", "enter_from": ""
        }]
        canvases: List[Dict[str, Any]] = []
        material_colors: List[Dict[str, Any]] = []

        meta_photo_materials: List[Dict[str, Any]] = []
        cover_image_path: Optional[Path] = None

        manifest_by_index = {img.index: img for img in images_manifest}
        timeline_list = timeline.timeline
        total_images = len(timeline_list)

        for i, entry in enumerate(timeline_list):
            manifest_img = manifest_by_index.get(entry.image_index)
            if manifest_img:
                src_path = manifest_img.path
                display_filename = manifest_img.filename
            else:
                src_path = Path(entry.filename)
                display_filename = entry.filename

            if copy_assets and src_path.exists():
                dest_img = assets_dir / src_path.name
                if not dest_img.exists() or dest_img.stat().st_size != src_path.stat().st_size:
                    shutil.copy2(src_path, dest_img)
                final_img_path_str = str(dest_img.resolve()).replace("\\", "/")
                total_materials_bytes += dest_img.stat().st_size
                if cover_image_path is None:
                    cover_image_path = dest_img
            else:
                final_img_path_str = str(src_path.resolve()).replace("\\", "/")
                if src_path.exists():
                    total_materials_bytes += src_path.stat().st_size
                if cover_image_path is None and src_path.exists():
                    cover_image_path = src_path

            # Image dimensions
            w, h = _get_image_dimensions(src_path if src_path.exists() else Path(final_img_path_str))

            # Microsecond timing with strict boundary continuity:
            # Segment i ends exactly where segment i+1 starts.
            st_sec = float(entry.start)
            st_us = int(round(st_sec * 1_000_000))
            if i == total_images - 1:
                dur_us = total_duration_us - st_us
            else:
                next_st_sec = float(timeline_list[i + 1].start)
                dur_us = int(round(next_st_sec * 1_000_000)) - st_us

            v_mat_id = str(uuid.uuid4()).upper()
            v_mat = {
                "id": v_mat_id,
                "unique_id": "",
                "type": "photo",
                "duration": 10800000000,
                "path": final_img_path_str,
                "media_path": "",
                "local_id": "",
                "has_audio": False,
                "reverse_path": "",
                "intensifies_path": "",
                "reverse_intensifies_path": "",
                "intensifies_audio_path": "",
                "cartoon_path": "",
                "width": w,
                "height": h,
                "category_id": "",
                "category_name": "local",
                "material_id": "",
                "material_name": display_filename,
                "material_url": "",
                "crop": {
                    "upper_left_x": 0.0, "upper_left_y": 0.0,
                    "upper_right_x": 1.0, "upper_right_y": 0.0,
                    "lower_left_x": 0.0, "lower_left_y": 1.0,
                    "lower_right_x": 1.0, "lower_right_y": 1.0
                },
                "crop_ratio": "free",
                "audio_fade": None,
                "crop_scale": 1.0,
                "extra_type_option": 0,
                "stable": {"stable_level": 0, "matrix_path": "", "time_range": {"start": 0, "duration": 0}},
                "matting": {
                    "flag": 0, "path": "", "interactiveTime": [], "has_use_quick_brush": False, "strokes": [],
                    "has_use_quick_eraser": False, "expansion": 0, "feather": 0, "reverse": False, "custom_matting_id": "",
                    "enable_matting_stroke": False, "is_clould": False, "mask_video_path": "", "cloud_product_fps": 0.0
                },
                "source": 0,
                "source_platform": 0,
                "formula_id": "",
                "check_flag": 62978047,
                "video_algorithm": {
                    "algorithms": [], "time_range": None, "path": "", "gameplay_configs": [], "ai_in_painting_config": [],
                    "complement_frame_config": None, "motion_blur_config": None, "deflicker": None, "noise_reduction": None,
                    "quality_enhance": None, "super_resolution": None, "ai_background_configs": [], "smart_complement_frame": None,
                    "aigc_generate": None, "aigc_generate_list": [], "mouth_shape_driver": None, "ai_expression_driven": None,
                    "ai_motion_driven": None, "image_interpretation": None,
                    "story_video_modify_video_config": {"task_id": "", "is_overwrite_last_video": False, "tracker_task_id": ""},
                    "skip_algorithm_index": []
                },
                "is_unified_beauty_mode": False,
                "is_set_beauty_mode": False,
                "object_locked": None,
                "smart_motion": None,
                "multi_camera_info": None,
                "freeze": None,
                "picture_from": "none",
                "picture_set_category_id": "",
                "picture_set_category_name": "",
                "team_id": "",
                "local_material_id": "",
                "origin_material_id": "",
                "request_id": "",
                "has_sound_separated": False,
                "is_text_edit_overdub": False,
                "is_ai_generate_content": False,
                "aigc_type": "none",
                "is_copyright": False,
                "aigc_history_id": "",
                "aigc_item_id": "",
                "local_material_from": "",
                "smart_match_info": None,
                "beauty_face_preset_infos": [],
                "beauty_body_preset_id": "",
                "beauty_face_auto_preset": {"preset_id": "", "name": "", "rate_map": "", "scene": ""},
                "beauty_face_auto_preset_infos": [],
                "beauty_body_auto_preset": None,
                "live_photo_timestamp": -1,
                "live_photo_cover_path": "",
                "content_feature_info": None,
                "corner_pin": None,
                "surface_trackings": [],
                "video_mask_stroke": {"resource_id": "", "path": "", "type": "", "color": "", "size": 0.0, "alpha": 0.0, "distance": 0.0, "texture": 0.0, "horizontal_shift": 0.0, "vertical_shift": 0.0},
                "video_mask_shadow": {"resource_id": "", "path": "", "color": "", "alpha": 0.0, "blur": 0.0, "distance": 0.0, "angle": 0.0}
            }
            video_materials.append(v_mat)

            # Segment auxiliary references
            s_speed_id = str(uuid.uuid4()).upper()
            s_placeholder_id = str(uuid.uuid4()).upper()
            s_canvas_id = str(uuid.uuid4()).upper()
            s_channel_id = str(uuid.uuid4()).upper()
            s_color_id = str(uuid.uuid4()).upper()
            s_vocal_id = str(uuid.uuid4()).upper()

            speeds.append({"id": s_speed_id, "type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None})
            placeholder_infos.append({"id": s_placeholder_id, "type": "placeholder_info", "meta_type": "none", "res_path": "", "res_text": "", "error_path": "", "error_text": ""})
            canvases.append({"id": s_canvas_id, "type": "canvas_color", "color": "", "blur": 0.0, "image": "", "album_image": "", "image_id": "", "image_name": "", "source_platform": 0, "team_id": ""})
            sound_channel_mappings.append({"id": s_channel_id, "type": "", "audio_channel_mapping": 0, "is_config_open": False})
            material_colors.append({"id": s_color_id, "is_color_clip": False, "is_gradient": False, "solid_color": "", "gradient_colors": [], "gradient_percents": [], "gradient_angle": 90.0, "width": 0.0, "height": 0.0})
            vocal_separations.append({"id": s_vocal_id, "type": "vocal_separation", "choice": 0, "removed_sounds": [], "time_range": None, "production_path": "", "final_algorithm": "", "enter_from": ""})

            v_seg = {
                "id": str(uuid.uuid4()).upper(),
                "source_timerange": {"start": 0, "duration": dur_us},
                "target_timerange": {"start": st_us, "duration": dur_us},
                "render_timerange": {"start": 0, "duration": 0},
                "desc": "",
                "state": 0,
                "speed": 1.0,
                "is_loop": False,
                "is_tone_modify": False,
                "reverse": False,
                "intensifies_audio": False,
                "cartoon": False,
                "volume": 1.0,
                "last_nonzero_volume": 1.0,
                "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0}, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
                "uniform_scale": {"on": True, "value": 1.0},
                "material_id": v_mat_id,
                "extra_material_refs": [s_speed_id, s_placeholder_id, s_canvas_id, s_channel_id, s_color_id, s_vocal_id],
                "render_index": 0,
                "keyframe_refs": [],
                "enable_lut": True,
                "enable_adjust": True,
                "enable_hsl": False,
                "visible": True,
                "group_id": "",
                "enable_color_curves": True,
                "enable_hsl_curves": True,
                "track_render_index": 0,
                "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000},
                "enable_color_wheels": True,
                "track_attribute": 0,
                "is_placeholder": False,
                "template_id": "",
                "enable_smart_color_adjust": False,
                "template_scene": "default",
                "common_keyframes": [],
                "caption_info": None,
                "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0, "horizontal_pos_layout": 0, "vertical_pos_layout": 0},
                "enable_color_match_adjust": False,
                "enable_color_correct_adjust": False,
                "enable_adjust_mask": False,
                "raw_segment_id": "",
                "lyric_keyframes": None,
                "enable_video_mask": True,
                "digital_human_template_group_id": "",
                "color_correct_alg_result": "",
                "source": "segmentsourcenormal",
                "enable_mask_stroke": False,
                "enable_mask_shadow": False,
                "enable_color_adjust_pro": False
            }
            video_segments.append(v_seg)

            meta_photo_materials.append({
                "ai_group_type": "",
                "create_time": now_time,
                "duration": 5000000,
                "enter_from": 0,
                "extra_info": display_filename,
                "file_Path": final_img_path_str,
                "height": h,
                "id": v_mat_id.lower(),
                "import_time": now_time,
                "import_time_ms": now_time_us,
                "item_source": 1,
                "md5": "",
                "metetype": "photo",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": w
            })

        # 4. Copy / Generate Cover Image
        cover_dest = output_dir / "draft_cover.jpg"
        if cover_image_path and cover_image_path.exists():
            shutil.copy2(cover_image_path, cover_dest)
        else:
            cover_dest.write_bytes(b"")

        # 5. Assemble Materials & Tracks
        all_materials = {
            "ai_translates": [], "audio_balances": [], "audio_effects": [], "audio_fades": [],
            "audio_pannings": [], "audio_pitch_shifts": [], "audio_track_indexes": [],
            "audios": [audio_mat],
            "beats": beats,
            "canvases": canvases,
            "chromas": [], "color_curves": [], "common_mask": [], "digital_human_model_dressing": [],
            "digital_humans": [], "drafts": [], "effects": [], "flowers": [], "green_screens": [],
            "handwrites": [], "hsl": [], "hsl_curves": [], "images": [], "log_color_wheels": [],
            "loudnesses": [], "manual_beautys": [], "manual_deformations": [], "material_animations": [],
            "material_colors": material_colors, "multi_language_refs": [], "placeholder_infos": placeholder_infos,
            "placeholders": [], "plugin_effects": [], "primary_color_wheels": [], "realtime_denoises": [],
            "shapes": [], "smart_crops": [], "smart_relights": [], "sound_channel_mappings": sound_channel_mappings,
            "speeds": speeds, "stickers": [], "tail_leaders": [], "text_templates": [], "texts": [],
            "time_marks": [], "transitions": [], "video_effects": [], "video_radius": [], "video_shadows": [],
            "video_strokes": [], "video_trackings": [],
            "videos": video_materials,
            "vocal_beautifys": [], "vocal_separations": vocal_separations
        }

        tracks = [
            {
                "attribute": 0,
                "flag": 0,
                "id": str(uuid.uuid4()).upper(),
                "is_default_name": True,
                "name": "",
                "segments": video_segments,
                "type": "video"
            },
            {
                "attribute": 0,
                "flag": 0,
                "id": str(uuid.uuid4()).upper(),
                "is_default_name": True,
                "name": "",
                "segments": [audio_segment],
                "type": "audio"
            }
        ]

        canvas_w = self.config.video_width if self.config else 1920
        canvas_h = self.config.video_height if self.config else 1080
        fps_val = float(self.config.video_fps if self.config else 30.0)

        draft_content = {
            "id": main_timeline_id,
            "version": 360000,
            "new_version": "169.0.0",
            "name": "",
            "duration": total_duration_us,
            "create_time": 0,
            "update_time": 0,
            "fps": fps_val,
            "is_drop_frame_timecode": False,
            "color_space": 0,
            "config": {
                "video_mute": False, "record_audio_last_index": 1, "extract_audio_last_index": 1,
                "original_sound_last_index": 1, "subtitle_recognition_id": "", "subtitle_taskinfo": [],
                "lyrics_recognition_id": "", "lyrics_taskinfo": [], "subtitle_sync": True,
                "lyrics_sync": True, "voice_change_sync": False, "sticker_max_index": 1,
                "adjust_max_index": 1, "material_save_mode": 0, "export_range": None,
                "maintrack_adsorb": True, "combination_max_index": 1, "attachment_info": [],
                "zoom_info_params": None, "system_font_list": [], "multi_language_mode": "none",
                "multi_language_main": "none", "multi_language_current": "none", "multi_language_list": [],
                "subtitle_keywords_config": None, "use_float_render": False
            },
            "canvas_config": {
                "ratio": "original", "width": canvas_w, "height": canvas_h, "background": None
            },
            "tracks": tracks,
            "group_container": None,
            "materials": all_materials,
            "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [], "stickers": [], "texts": [], "videos": []},
            "keyframe_graph_list": [],
            "platform": {"app_id": 3704, "app_source": "cc", "app_version": "8.6.0", "os": "windows"},
            "last_modified_platform": {"app_id": 3704, "app_source": "cc", "app_version": "8.6.0", "os": "windows"},
            "mutable_config": None,
            "cover": None,
            "retouch_cover": None,
            "extra_info": None,
            "relationships": [],
            "render_index_track_mode_on": True,
            "free_render_index_mode_on": False,
            "static_cover_image_path": "",
            "source": "default",
            "time_marks": None,
            "path": "",
            "lyrics_effects": [],
            "uneven_animation_template_info": None,
            "draft_type": "video",
            "smart_ads_info": None,
            "function_assistant_info": None
        }

        # 6. Write Root Project Files
        content_bytes = json.dumps(draft_content, ensure_ascii=False, indent=2).encode("utf-8")
        (output_dir / "draft_content.json").write_bytes(content_bytes)
        (output_dir / "draft_content.json.bak").write_bytes(content_bytes)
        (output_dir / "template-2.tmp").write_bytes(content_bytes)

        draft_meta_info = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "cloud_package_completed_time": "",
            "draft_cloud_capcut_purchase_info": "",
            "draft_cloud_last_action_download": False,
            "draft_cloud_package_type": "",
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "draft_cover.jpg",
            "draft_deeplink_url": "",
            "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "enterprise_material": 0},
            "draft_fold_path": str(output_dir.resolve()).replace("\\", "/"),
            "draft_id": project_id,
            "draft_is_ae_produce": False,
            "draft_is_ai_packaging_used": False,
            "draft_is_ai_shorts": False,
            "draft_is_ai_translate": False,
            "draft_is_article_video_draft": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_from_deeplink": False,
            "draft_is_invisible": False,
            "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False,
            "draft_materials": [
                {"type": 0, "value": meta_photo_materials},
                {"type": 1, "value": []},
                {"type": 2, "value": []},
                {"type": 3, "value": []},
                {"type": 6, "value": []},
                {"type": 7, "value": []},
                {"type": 8, "value": []}
            ],
            "draft_materials_copied_info": [],
            "draft_name": project_name,
            "draft_need_rename_folder": False,
            "draft_new_version": "",
            "draft_removable_storage_device": "",
            "draft_root_path": str(output_dir.parent.resolve()).replace("\\", "/"),
            "draft_segment_extra_info": [],
            "draft_timeline_materials_size_": total_materials_bytes,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_time_us,
            "tm_draft_modified": now_time_us,
            "tm_draft_removed": 0,
            "tm_duration": total_duration_us
        }
        (output_dir / "draft_meta_info.json").write_text(json.dumps(draft_meta_info, indent=2), encoding="utf-8")

        # 7. Write Sidecar Files
        (output_dir / "attachment_pc_common.json").write_text('{"ai_packaging_infos":[],"ai_packaging_report_info":{"caption_id_list":[],"commercial_material":"","material_source":"","method":"","page_from":"","style":"","task_id":"","text_style":"","tos_id":"","video_category":""},"broll":{"ai_packaging_infos":[],"ai_packaging_report_info":{"caption_id_list":[],"commercial_material":"","material_source":"","method":"","page_from":"","style":"","task_id":"","text_style":"","tos_id":"","video_category":""}}}', encoding="utf-8")
        (output_dir / "draft_agency_config.json").write_text('{"is_auto_agency_enabled":false,"is_auto_agency_popup":false,"is_single_agency_mode":false,"marterials":null,"use_converter":false,"video_resolution":720}', encoding="utf-8")
        (output_dir / "draft_biz_config.json").write_text('', encoding="utf-8")
        (output_dir / "performance_opt_info.json").write_text('{"manual_cancle_precombine_segs":null,"need_auto_precombine_segs":null}', encoding="utf-8")
        (output_dir / "draft_settings").write_text(f"[General]\ndraft_create_time={now_time}\ndraft_last_edit_time={now_time}\nreal_edit_seconds=0\nreal_edit_keys=0\n", encoding="utf-8")

        virtual_items = [{"child_id": m["id"], "parent_id": ""} for m in meta_photo_materials]
        virtual_store = {
            "draft_materials": [],
            "draft_virtual_store": [
                {"type": 0, "value": [{"creation_time": 0, "display_name": "", "filter_type": 0, "id": "", "import_time": 0, "import_time_us": 0, "sort_sub_type": 0, "sort_type": 0, "subdraft_filter_type": 0}]},
                {"type": 1, "value": virtual_items},
                {"type": 2, "value": []}
            ]
        }
        (output_dir / "draft_virtual_store.json").write_text(json.dumps(virtual_store, indent=2), encoding="utf-8")
        (output_dir / "key_value.json").write_text("{}", encoding="utf-8")

        timeline_layout = {
            "dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [main_timeline_id], "timelineNames": ["Timeline 01"]}],
            "layoutOrientation": 1
        }
        (output_dir / "timeline_layout.json").write_text(json.dumps(timeline_layout, indent=2), encoding="utf-8")

        common_att_dir = output_dir / "common_attachment"
        common_att_dir.mkdir(parents=True, exist_ok=True)
        (common_att_dir / "attachment_pc_timeline.json").write_text('{"reference_lines_config":{"horizontal_lines":[],"is_lock":false,"is_visible":false,"vertical_lines":[]},"safe_area_type":0}', encoding="utf-8")

        # 8. Write Timelines Directory (Multi-Timeline Support)
        timelines_dir = output_dir / "Timelines"
        timelines_dir.mkdir(parents=True, exist_ok=True)
        project_json = {
            "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
            "create_time": now_time_us,
            "id": str(uuid.uuid4()).upper(),
            "main_timeline_id": main_timeline_id,
            "timelines": [{"create_time": now_time_us, "id": main_timeline_id, "is_marked_delete": False, "name": "Timeline 01", "update_time": now_time_us}],
            "update_time": now_time_us,
            "version": 0
        }
        pj_bytes = json.dumps(project_json, indent=2).encode("utf-8")
        (timelines_dir / "project.json").write_bytes(pj_bytes)
        (timelines_dir / "project.json.bak").write_bytes(pj_bytes)

        main_tl_dir = timelines_dir / main_timeline_id
        main_tl_dir.mkdir(parents=True, exist_ok=True)
        (main_tl_dir / "draft_content.json").write_bytes(content_bytes)
        (main_tl_dir / "draft_content.json.bak").write_bytes(content_bytes)
        (main_tl_dir / "template-2.tmp").write_bytes(content_bytes)
        (main_tl_dir / "template.tmp").write_bytes(content_bytes)
        if cover_dest.exists():
            shutil.copy2(cover_dest, main_tl_dir / "draft_cover.jpg")
        (main_tl_dir / "attachment_pc_common.json").write_text('{"ai_packaging_infos":[],"ai_packaging_report_info":{"caption_id_list":[],"commercial_material":"","material_source":"","method":"","page_from":"","style":"","task_id":"","text_style":"","tos_id":"","video_category":""},"broll":{"ai_packaging_infos":[],"ai_packaging_report_info":{"caption_id_list":[],"commercial_material":"","material_source":"","method":"","page_from":"","style":"","task_id":"","text_style":"","tos_id":"","video_category":""}}}', encoding="utf-8")
        (main_tl_dir / "attachment_editing.json").write_text('{"editing_draft":{"ai_remove_filter_words":{"enter_source":"","right_id":""},"ai_shorts_info":{"report_params":"","type":0},"cover_extra_info":{"draft_id":"","position":0,"select_segment_id":"","select_segment_source_start":0,"select_segment_target_start":0,"type":1},"crop_info_extra":{"crop_mirror_info":{},"crop_scale_info":{},"crop_scale_type_info":{},"crop_scale_v2_info":{}},"multi_track_info":{"enable":false},"subdraft_info":{"is_subdraft":false},"sync_video_audio_track_info":{}}}', encoding="utf-8")

        sub_common_att = main_tl_dir / "common_attachment"
        sub_common_att.mkdir(parents=True, exist_ok=True)
        (sub_common_att / "attachment_action_scene.json").write_text('{"action_scene":{"removed_segments":[],"segment_infos":[]}}', encoding="utf-8")
        (sub_common_att / "attachment_gen_ai_info.json").write_text('{"gen_ai":{"ai_func_config":{"ai_common_configs":[],"ai_effect_configs":[],"ai_func_list":[],"aigc_generation_configs":[]},"cc_agent_info":{"agent_stringent_section_id_list":[],"agent_stringent_used_tool_list":[],"click_cnt":0,"conversation_ids":[],"is_click_like":false,"is_click_unlike":false,"is_used_tool":false,"parent_conversation_id":"","prompt_category":"","rate_id":"","suggest_prompt_category":"","unlike_reasons":[]}}}', encoding="utf-8")
        (sub_common_att / "attachment_pc_timeline.json").write_text('{"reference_lines_config":{"horizontal_lines":[],"is_lock":false,"is_visible":false,"vertical_lines":[]},"safe_area_type":0}', encoding="utf-8")
        (sub_common_att / "attachment_plugin_draft.json").write_text('{"plugin_draft":{"plugin_segments":[],"version":"1.0.0"}}', encoding="utf-8")
        (sub_common_att / "attachment_script_video.json").write_text('{"script_video":{"attachment_valid":false,"language":"","overdub_recover":[],"overdub_sentence_ids":[],"parts":[],"sync_subtitle":false,"translate_segments":[],"translate_type":"","version":"1.0.0"}}', encoding="utf-8")

        # 9. Deterministic Validation
        is_valid, validation_errors = CapCutValidator.validate(
            project_dir=output_dir,
            expected_timeline=timeline,
            expected_manifest=images_manifest,
            expected_audio_path=audio_path
        )

        if not is_valid:
            err_msg = "; ".join(validation_errors)
            logger.error(f"CapCut export validation failed: {err_msg}")
            raise CapCutValidationError(f"CapCut project validation failed:\n{err_msg}")

        # 10. Optional Registration into CapCut's Desktop GUI Store
        should_register = auto_register if auto_register is not None else getattr(self.config, "capcut_auto_register", True)
        registered_in_capcut = False
        capcut_draft_path = None

        if should_register:
            custom_draft_root = getattr(self.config, "capcut_draft_root", None)
            draft_root = Path(custom_draft_root) if custom_draft_root else detect_capcut_draft_root()
            if draft_root and draft_root.exists():
                try:
                    registered_in_capcut, capcut_draft_path = self._register_in_local_capcut(
                        output_dir=output_dir,
                        draft_root=draft_root,
                        project_id=project_id,
                        project_name=project_name,
                        duration_us=total_duration_us,
                        total_materials_bytes=total_materials_bytes,
                        now_us=now_time_us
                    )
                except Exception as e:
                    logger.warning(f"Non-fatal warning: failed to register project into CapCut root_meta_info: {e}")

        logger.info(f"✓ CapCut project successfully exported to {output_dir}")
        return CapCutExportReport(
            success=True,
            project_dir=output_dir,
            project_name=project_name,
            image_count=total_images,
            duration_seconds=round(timeline.audio_duration, 4),
            duration_us=total_duration_us,
            registered_in_capcut=registered_in_capcut,
            capcut_draft_path=capcut_draft_path,
            validation_passed=True,
            errors=[]
        )

    def _register_in_local_capcut(
        self,
        output_dir: Path,
        draft_root: Path,
        project_id: str,
        project_name: str,
        duration_us: int,
        total_materials_bytes: int,
        now_us: int
    ) -> Tuple[bool, Optional[Path]]:
        """Syncs project into CapCut's local draft directory and updates root_meta_info.json."""
        target_dir = draft_root / project_name
        if target_dir.resolve() != output_dir.resolve():
            target_dir.mkdir(parents=True, exist_ok=True)
            # Copy project files (shallow copy of files and Timelines folder, assets remain referenced)
            for item in output_dir.iterdir():
                dest = target_dir / item.name
                if item.is_file():
                    shutil.copy2(item, dest)
                elif item.is_dir() and item.name in ("Timelines", "common_attachment"):
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)

        root_meta_file = draft_root / "root_meta_info.json"
        if not root_meta_file.exists():
            return False, target_dir

        with open(root_meta_file, "r", encoding="utf-8") as f:
            root_data = json.load(f)

        entry = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "cloud_package_completed_time": "",
            "draft_cloud_capcut_purchase_info": "",
            "draft_cloud_last_action_download": False,
            "draft_cloud_package_type": "",
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "draft_cover.jpg",
            "draft_deeplink_url": "",
            "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "enterprise_material": 0},
            "draft_fold_path": str(target_dir.resolve()).replace("\\", "/"),
            "draft_id": project_id,
            "draft_is_ae_produce": False,
            "draft_is_ai_packaging_used": False,
            "draft_is_ai_shorts": False,
            "draft_is_ai_translate": False,
            "draft_is_article_video_draft": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_from_deeplink": False,
            "draft_is_invisible": False,
            "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False,
            "draft_materials": [],
            "draft_materials_copied_info": [],
            "draft_name": project_name,
            "draft_need_rename_folder": False,
            "draft_new_version": "",
            "draft_removable_storage_device": "",
            "draft_root_path": str(draft_root.resolve()).replace("\\", "/"),
            "draft_segment_extra_info": [],
            "draft_timeline_materials_size": total_materials_bytes,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            "tm_duration": duration_us
        }

        all_drafts = [d for d in root_data.get("all_draft_store", []) if d.get("draft_id") != project_id and d.get("draft_name") != project_name]
        all_drafts.insert(0, entry)
        root_data["all_draft_store"] = all_drafts
        root_data["draft_ids"] = len(all_drafts)

        temp_file = draft_root / "root_meta_info.json.tmp"
        temp_file.write_text(json.dumps(root_data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(root_meta_file)

        return True, target_dir
