"""2-stage classification pipeline.

Stage 1 (Filter): lightweight checks (~180ms/photo)
  - EXIF metadata extraction + orientation correction
  - technical quality (blur/exposure)
  - duplicate detection
  - face detection + known person matching

Stage 2 (VLM): heavy inference (~5s/photo)
  - scene description via VLM
  - event classification (with EXIF GPS correction)
  - final ranking
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field

from photos_mcp.infrastructure.vendor_adapter.compat import ToolLogContext, log_context
from photos_mcp.infrastructure.vendor_adapter.compat import default_runtime_broker_client

from . import db as db_module
from .engines.aesthetic import score_technical_quality
from .engines.dedup import DedupEngine
from .engines.exif import ExifEngine
from .engines.face import FaceEngine
from .jobs import Job, JobProgress
from .models import DuplicateGroup, EventType, QualityScore, RankedPhoto
from .scoring import (
    compute_event_score,
    compute_family_score,
    compute_quality_score,
    compute_uniqueness_score,
    rank_photos,
)
from .scene_selection import (
    SceneClusterer,
    SceneSignal,
    VisualFeatureEngine,
    annotate_cluster_ranks,
    detail_candidate_ranks,
    choose_quality_representatives,
    parse_capture_time,
)

logger = logging.getLogger(__name__)


@dataclass
class PhotoCandidate:
    """Intermediate representation between stages."""

    photo_id: str
    image_b64: str
    technical_score: float = 0.0
    face_count: int = 0
    is_duplicate: bool = False
    quality_score: float = 0.0
    family_score: float = 0.0
    event_score: float = 0.0
    uniqueness_score: float = 0.0
    scene_description: str = ""
    event_type: str = EventType.OTHER.value
    known_persons: list[str] = field(default_factory=list)
    passed_stage1: bool = True
    has_gps: bool = False
    latitude: float | None = None
    longitude: float | None = None
    location_provenance: str = ""
    faces: list = field(default_factory=list)  # FaceResult list from stage1
    meaningful_score: int = 5  # VLM 1-10, default midpoint
    capture_date: str = ""  # ISO date from EXIF
    burst_group_id: str = ""
    visual_feature: object | None = None


@dataclass
class PipelineConfig:
    """Tunable thresholds for the 2-stage pipeline."""

    # Stage 1: minimum technical score (0-50) to pass to Stage 2
    min_technical_score: float = 10.0
    # Stage 1: skip confirmed duplicates in Stage 2
    skip_duplicates: bool = True
    # Duplicate detection Hamming threshold
    dedup_threshold: int = 8
    # Stage 2: top-N to run VLM on (0 = all that pass Stage 1)
    vlm_top_n: int = 0
    # VLM model path (empty = use default)
    vlm_model_path: str = ""
    # Maximum detailed VLM candidates retained from each similar scene.
    scene_detail_candidates: int = 4
    # Vision feature cosine distance used for normal same-scene grouping.
    scene_visual_distance_threshold: float = 0.08
    # Relaxed distance when Apple Photos identifies the same person.
    scene_relaxed_visual_distance_threshold: float = 0.10
    # Maximum normal and extended capture-time windows.
    scene_time_window_seconds: float = 120.0
    scene_extended_time_window_seconds: float = 300.0
    # Score required for an automatic recommendation outside select-best mode.
    scene_recommendation_min_score: float = 80.0


class Pipeline:
    """2-stage photo classification pipeline."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        db: "db_module.JobDB | None" = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self._dedup = DedupEngine()
        self._face = FaceEngine()
        self._exif = ExifEngine()
        self._db = db  # Optional DB for face embedding caching
        self._vlm = None  # Lazy-initialized VLMEngine (reused across stage2 calls)
        self._aesthetic = None  # Lazy-initialized AestheticEngine
        self._visual_features = VisualFeatureEngine()
        self._scene_clusterer = SceneClusterer(
            time_window_seconds=self.config.scene_time_window_seconds,
            extended_time_window_seconds=self.config.scene_extended_time_window_seconds,
            visual_distance_threshold=self.config.scene_visual_distance_threshold,
            relaxed_visual_distance_threshold=self.config.scene_relaxed_visual_distance_threshold,
        )
        # Known face embeddings: name -> list of embeddings
        self._known_faces: dict[str, list[list[float]]] = {}

    def _release_vlm_if_needed(self) -> None:
        if self._vlm is None:
            return
        if getattr(self._vlm, "should_auto_unload", False):
            self._vlm.unload()
            self._vlm = None

    def register_known_face(self, name: str, embedding: list[float]) -> None:
        """Register a known person's face embedding for family scoring."""
        if name not in self._known_faces:
            self._known_faces[name] = []
        self._known_faces[name].append(embedding)

    @staticmethod
    def _workflow_context(job: Job | None, step_index: int) -> ToolLogContext | None:
        if job is None or not isinstance(job.request_options, dict):
            return None
        tool_name = str(job.request_options.get("log_tool_name") or "")
        total_steps = int(job.request_options.get("log_total_steps") or 0)
        if not tool_name or total_steps < 1:
            return None
        return ToolLogContext(tool_name=tool_name, step_index=step_index, total_steps=total_steps)

    def _log_workflow(self, level: int, job: Job | None, step_index: int, message: str, *args: object) -> None:
        context = self._workflow_context(job, step_index)
        if context is None:
            logger.log(level, message, *args)
            return
        log_context(logger, level, context, message, *args)

    def _identify_known_persons(
        self, face_embeddings: list[list[float] | None],
    ) -> list[str]:
        """Match detected face embeddings against registered known faces."""
        if not self._known_faces or not face_embeddings:
            return []

        import numpy as np

        matched_names: list[str] = []
        for emb in face_embeddings:
            if emb is None:
                continue
            emb_arr = np.array(emb)
            best_name = None
            best_sim = 0.0

            for name, known_embs in self._known_faces.items():
                for known_emb in known_embs:
                    known_arr = np.array(known_emb)
                    # Cosine similarity
                    dot = np.dot(emb_arr, known_arr)
                    norm = np.linalg.norm(emb_arr) * np.linalg.norm(known_arr)
                    if norm > 0:
                        sim = dot / norm
                        if sim > best_sim:
                            best_sim = sim
                            best_name = name

            # Threshold: cosine > 0.4 for same person
            if best_name and best_sim > 0.4:
                if best_name not in matched_names:
                    matched_names.append(best_name)

        return matched_names

    async def run(
        self,
        photos: list[dict],
        job: Job | None = None,
        selection_profile: str = "general",
        allow_face_analysis: bool = True,
    ) -> list[RankedPhoto]:
        """Run full 2-stage pipeline.

        Args:
            photos: list of {"photo_id": str, "image_b64": str}
            job: optional Job for progress tracking

        Returns:
            Ranked list of photos.
        """
        t_start = time.perf_counter()
        stage1_step = int(job.request_options.get("log_stage1_step") or 0) if job and isinstance(job.request_options, dict) else 0
        stage2_step = int(job.request_options.get("log_stage2_step") or 0) if job and isinstance(job.request_options, dict) else 0

        if job:
            job.progress = JobProgress(total=len(photos), stage="filter")

        if stage1_step:
            self._log_workflow(logging.INFO, job, stage1_step, "pipeline start photos=%d", len(photos))
        else:
            logger.info("Pipeline start: %d photos", len(photos))

        # Load existing checkpoints for resume support
        s1_done: dict[str, dict] = {}
        s2_done: dict[str, dict] = {}
        if self._db and job:
            s1_done = self._db.load_checkpoints(job.id, "filter")
            s2_done = self._db.load_checkpoints(job.id, "vlm")
            if s1_done or s2_done:
                self._log_workflow(
                    logging.INFO,
                    job,
                    stage1_step or stage2_step or 1,
                    "resume checkpoints filter=%d vlm=%d",
                    len(s1_done),
                    len(s2_done),
                )

        # ── Stage 1: Filter ──
        candidates = []
        for i, p in enumerate(photos):
            pid = p["photo_id"]
            if pid in s1_done:
                cand = self._restore_candidate(
                    s1_done[pid],
                    p["image_b64"],
                    source_metadata=p,
                )
            else:
                if allow_face_analysis:
                    cand = await self._stage1(pid, p["image_b64"], source_metadata=p)
                else:
                    cand = await self._stage1(
                        pid,
                        p["image_b64"],
                        source_metadata=p,
                        allow_face_analysis=False,
                    )
                if self._db and job:
                    self._db.save_checkpoint(
                        job.id, "filter", pid, self._snapshot_candidate(cand),
                    )
            candidates.append(cand)
            if self._db and job:
                self._db.save_photo_location(
                    job.id,
                    pid,
                    has_gps=cand.has_gps,
                    latitude=cand.latitude,
                    longitude=cand.longitude,
                    provenance=cand.location_provenance,
                    capture_date=cand.capture_date,
                )
            if job:
                job.progress.completed = i + 1
                job.progress.current_file = pid
            if stage1_step and ((i + 1) % 5 == 0 or i + 1 == len(photos)):
                self._log_workflow(
                    logging.INFO,
                    job,
                    stage1_step,
                    "stage1 progress %d/%d",
                    i + 1,
                    len(photos),
                )

        t_s1 = time.perf_counter() - t_start
        self._log_workflow(logging.INFO, job, stage1_step or 1, "stage1 done candidates=%d in %.2fs", len(candidates), t_s1)

        # Generate local Vision features before selecting expensive VLM candidates.
        t_scene_start = time.perf_counter()
        visual_features = await self._extract_scene_features(candidates)

        # Duplicate detection across all
        t_dedup_start = time.perf_counter()
        dup_groups = self._detect_duplicates(candidates)

        technical_scores = {candidate.photo_id: candidate.technical_score for candidate in candidates}
        for group in dup_groups:
            ranked_ids = choose_quality_representatives(
                group.photo_ids,
                technical_scores=technical_scores,
            )
            group.photo_ids = ranked_ids
            group.representative_id = ranked_ids[0]

        # Mark duplicates
        dup_photo_ids = set()
        for g in dup_groups:
            for pid in g.photo_ids:
                if pid != g.representative_id:
                    dup_photo_ids.add(pid)

        for c in candidates:
            if c.photo_id in dup_photo_ids:
                c.is_duplicate = True
            if c.technical_score < self.config.min_technical_score:
                c.passed_stage1 = False

        t_dedup = time.perf_counter() - t_dedup_start
        self._log_workflow(
            logging.INFO,
            job,
            stage1_step or 1,
            "dedup done duplicates=%d in %.2fs",
            len(dup_photo_ids),
            t_dedup,
        )

        # Compute uniqueness for all
        for c in candidates:
            c.uniqueness_score = compute_uniqueness_score(c.photo_id, dup_groups)

        scene_clusters = self._scene_clusterer.cluster(
            [
                SceneSignal(
                    photo_id=candidate.photo_id,
                    capture_time=parse_capture_time(candidate.capture_date),
                    visual_feature=candidate.visual_feature,
                    technical_score=candidate.technical_score,
                    known_persons=tuple(candidate.known_persons),
                    burst_group_id=candidate.burst_group_id,
                )
                for candidate in candidates
            ],
            exact_duplicate_groups=(group.photo_ids for group in dup_groups),
        )
        detail_candidate_ranks_by_id = detail_candidate_ranks(
            scene_clusters,
            technical_scores=technical_scores,
            face_counts={candidate.photo_id: candidate.face_count for candidate in candidates},
            limit_per_cluster=self.config.scene_detail_candidates,
        )
        detail_candidate_ids = set(detail_candidate_ranks_by_id)
        t_scene = time.perf_counter() - t_scene_start

        passed_count = sum(1 for c in candidates if c.passed_stage1)
        filtered_count = len(candidates) - passed_count
        self._log_workflow(
            logging.INFO,
            job,
            stage1_step or 1,
            "stage1 filter passed=%d filtered=%d quality_filtered=%d duplicates=%d",
            passed_count,
            filtered_count,
            sum(1 for c in candidates if c.technical_score < self.config.min_technical_score),
            len(dup_photo_ids),
        )

        # ── Stage 2: VLM (only for candidates that passed) ──
        stage2_candidates = [
            c
            for c in candidates
            if c.passed_stage1 and c.photo_id in detail_candidate_ids
        ]
        if self.config.vlm_top_n > 0:
            stage2_candidates = sorted(
                stage2_candidates,
                key=lambda c: c.technical_score,
                reverse=True,
            )[: self.config.vlm_top_n]

        t_s2_start = time.perf_counter()

        if job:
            job.progress.stage = "vlm"
            job.progress.completed = 0
            job.progress.total = len(stage2_candidates)

        self._log_workflow(logging.INFO, job, stage2_step or stage1_step or 1, "stage2 start candidates=%d", len(stage2_candidates))

        stage2_runtime_client = (
            default_runtime_broker_client()
            if any(cand.photo_id not in s2_done for cand in stage2_candidates)
            else None
        )
        existing_runtime = (
            dict(job.result_summary.get("vlm_runtime") or {})
            if job and isinstance(job.result_summary, dict)
            else {}
        )
        vlm_runtime_metadata: dict[str, object] = {**existing_runtime, "used": False}
        try:
            if stage2_runtime_client is not None:
                if job:
                    job.progress.stage = "waiting_model"
                    if self._db:
                        self._db.save_job(job)
                await stage2_runtime_client.acquire()
                if job:
                    job.progress.stage = "vlm"

            for i, cand in enumerate(stage2_candidates):
                if cand.photo_id in s2_done:
                    self._apply_vlm_checkpoint(cand, s2_done[cand.photo_id])
                else:
                    await self._stage2(cand)
                    if stage2_runtime_client is not None:
                        await stage2_runtime_client.mark_used()
                    if self._db and job:
                        self._db.save_checkpoint(
                            job.id, "vlm", cand.photo_id,
                            self._snapshot_candidate(cand),
                        )
                if job:
                    job.progress.completed = i + 1
                    job.progress.current_file = cand.photo_id
                if stage2_step:
                    self._log_workflow(
                        logging.INFO,
                        job,
                        stage2_step,
                        "stage2 progress %d/%d",
                        i + 1,
                        len(stage2_candidates),
                    )
        finally:
            if self._vlm is not None and hasattr(self._vlm, "runtime_metadata"):
                vlm_runtime_metadata = {
                    **existing_runtime,
                    "used": True,
                    **self._vlm.runtime_metadata(),
                }
            if stage2_runtime_client is not None:
                await stage2_runtime_client.release()
            self._release_vlm_if_needed()

        t_s2 = time.perf_counter() - t_s2_start
        self._log_workflow(logging.INFO, job, stage2_step or stage1_step or 1, "stage2 done processed=%d in %.2fs", len(stage2_candidates), t_s2)

        # ── Rank results ──
        ranked = self._rank(candidates, dup_groups, selection_profile)
        for item in ranked:
            item.detail_candidate = str(item.photo_id) in detail_candidate_ranks_by_id
            item.detail_candidate_rank = int(
                detail_candidate_ranks_by_id.get(str(item.photo_id), 0)
            )
        recommendation_threshold = self._scene_recommendation_threshold(ranked, job)
        annotate_cluster_ranks(
            ranked,
            scene_clusters,
            visual_features=visual_features,
            recommendation_min_score=recommendation_threshold,
        )

        t_total = time.perf_counter() - t_start
        stage_times = {
            "stage1_s": round(t_s1, 2),
            "dedup_s": round(t_dedup, 2),
            "scene_cluster_s": round(t_scene, 2),
            "stage2_s": round(t_s2, 2),
            "total_s": round(t_total, 2),
        }

        if job:
            job.result_summary = {
                "total_input": len(photos),
                "passed_stage1": passed_count,
                "duplicates_found": len(dup_photo_ids),
                "scene_cluster_count": len(scene_clusters),
                "multi_photo_scene_count": sum(1 for cluster in scene_clusters if cluster.size > 1),
                "detail_candidate_count": len(stage2_candidates),
                "scene_recommended_count": sum(
                    1 for item in ranked if item.recommended_in_cluster
                ),
                "scene_recommendation_policy": (
                    "relative_scene_top_2"
                    if recommendation_threshold is None
                    else "absolute_quality_percentile"
                ),
                "scene_recommendation_min_score": (
                    round(recommendation_threshold, 2)
                    if recommendation_threshold is not None
                    else None
                ),
                "ranked_count": len(ranked),
                "vlm_runtime": {
                    **vlm_runtime_metadata,
                    "processed_count": len(stage2_candidates),
                    "duration_seconds": round(t_s2, 2),
                },
                **stage_times,
            }

        # Multi-stage workflows retain analysis checkpoints until their write
        # phase is finalized, allowing an interrupted run to resume in place.
        if self._db and job and not job.request_options.get("retain_checkpoints"):
            self._db.clear_checkpoints(job.id)

        self._log_workflow(
            logging.INFO,
            job,
            stage2_step or stage1_step or 1,
            "pipeline complete input=%d ranked=%d total=%.2fs s1=%.2fs dedup=%.2fs s2=%.2fs",
            len(photos),
            len(ranked),
            t_total,
            t_s1,
            t_dedup,
            t_s2,
        )

        return ranked

    def _scene_recommendation_threshold(
        self,
        ranked_items: list[RankedPhoto],
        job: Job | None,
    ) -> float | None:
        """Use relative recommendations for classification and percentiles for curation."""
        if not ranked_items or job is None:
            return self.config.scene_recommendation_min_score
        options = getattr(job, "request_options", {}) or {}
        if str(options.get("selection_mode") or "classify") != "select_best":
            return None
        try:
            top_percent = max(1, min(int(options.get("quality_top_percent") or 30), 100))
        except (TypeError, ValueError):
            top_percent = 30
        scores = sorted(
            (float(item.total_score) for item in ranked_items),
            reverse=True,
        )
        selected_count = max(1, math.ceil(len(scores) * top_percent / 100))
        return scores[selected_count - 1]

    async def _stage1(
        self,
        photo_id: str,
        image_b64: str,
        source_metadata: dict | None = None,
        allow_face_analysis: bool = True,
    ) -> PhotoCandidate:
        """Lightweight checks: EXIF, orientation, technical quality, face count.

        Runs EXIF/technical/face tasks concurrently for speed.
        """
        metadata = source_metadata or {}
        cand = PhotoCandidate(photo_id=photo_id, image_b64=image_b64)
        cand.capture_date = str(metadata.get("capture_date") or "")
        cand.has_gps = bool(metadata.get("has_gps") or metadata.get("gps"))
        gps = metadata.get("gps") if isinstance(metadata.get("gps"), dict) else {}
        cand.latitude = metadata.get("latitude", gps.get("lat"))
        cand.longitude = metadata.get("longitude", gps.get("lon"))
        if cand.latitude is not None and cand.longitude is not None:
            cand.location_provenance = "provider_metadata"
        cand.known_persons = [
            str(name)
            for name in list(metadata.get("known_persons") or metadata.get("persons") or [])
            if str(name).strip()
        ]
        cand.burst_group_id = str(metadata.get("burst_group_id") or "")

        # EXIF extraction + orientation correction
        exif_data = self._exif.extract(image_b64)
        if exif_data.has_gps:
            cand.has_gps = True
            cand.latitude = exif_data.latitude
            cand.longitude = exif_data.longitude
            cand.location_provenance = "embedded_exif"
        if exif_data.capture_date and not cand.capture_date:
            cand.capture_date = exif_data.capture_date.isoformat()
        if exif_data.orientation != 1:
            corrected = self._exif.correct_orientation(image_b64)
            cand.image_b64 = corrected

        # Run technical quality and face detection concurrently
        loop = asyncio.get_event_loop()

        async def _technical() -> float:
            return await loop.run_in_executor(
                None, score_technical_quality, cand.image_b64
            )

        async def _face_detect() -> tuple:
            faces = await loop.run_in_executor(
                None, self._face.detect_faces, cand.image_b64
            )
            return faces

        if allow_face_analysis:
            tech_score, faces = await asyncio.gather(_technical(), _face_detect())
        else:
            tech_score = await _technical()
            faces = ()

        cand.technical_score = tech_score
        cand.face_count = len(faces)
        cand.faces = list(faces)

        # Cache face embeddings in DB for later registration
        if self._db and faces:
            for i, f in enumerate(faces):
                if f.embedding:
                    self._db.save_face_embedding(
                        photo_id, i, f.embedding,
                        bbox=list(f.bbox),
                        gender=f.gender, age=f.age, expression=f.expression,
                    )

        # Known person matching
        embeddings = [f.embedding for f in faces]
        detected_persons = self._identify_known_persons(embeddings)
        cand.known_persons = list(dict.fromkeys([*cand.known_persons, *detected_persons]))
        cand.family_score = compute_family_score(faces, cand.known_persons or None)

        # Quality score (aesthetic defaults to 5.0 in stage1)
        qs = compute_quality_score(5.0, cand.technical_score)
        cand.quality_score = qs.total

        return cand

    async def _extract_scene_features(
        self,
        candidates: list[PhotoCandidate],
    ) -> dict[str, object | None]:
        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(4)

        async def extract(candidate: PhotoCandidate) -> tuple[str, object | None]:
            async with semaphore:
                try:
                    feature = await loop.run_in_executor(
                        None,
                        self._visual_features.extract,
                        candidate.image_b64,
                    )
                except Exception as exc:
                    logger.warning("Scene feature extraction failed: %s", exc)
                    feature = None
                candidate.visual_feature = feature
                return candidate.photo_id, feature

        pairs = await asyncio.gather(*(extract(candidate) for candidate in candidates))
        return dict(pairs)

    async def _stage2(self, cand: PhotoCandidate) -> None:
        """Heavy VLM inference: scene description + event classification."""
        try:
            from .engines.vlm import VLMEngine

            if self._vlm is None:
                t_init = time.perf_counter()
                model_path = self.config.vlm_model_path or None
                self._vlm = VLMEngine(model_path) if model_path else VLMEngine()
                logger.info("VLM engine init: %.2fs", time.perf_counter() - t_init)
            scene = self._vlm.describe_scene(cand.image_b64)
            cand.scene_description = scene.scene
            cand.event_type = scene.event_type.value
            cand.event_score = compute_event_score(scene)
            cand.meaningful_score = scene.meaningful_score

            # A-1: GPS travel correction — if VLM says outdoor but EXIF has GPS,
            # boost toward travel (tourists usually have GPS-tagged photos)
            if (
                cand.has_gps
                and scene.event_type == EventType.OUTDOOR
                and scene.event_confidence < 0.8
            ):
                cand.event_type = EventType.TRAVEL.value
                scene.event_type = EventType.TRAVEL
                scene.event_confidence = max(scene.event_confidence, 0.5)
                cand.event_score = compute_event_score(scene)
                logger.info(
                    "GPS correction: outdoor to travel (GPS present, conf=%.2f)",
                    scene.event_confidence,
                )

            # A-1b: GPS travel correction for daily/portrait with low confidence
            # — tourist selfies or casual shots at travel destinations
            if (
                cand.has_gps
                and scene.event_type in (EventType.DAILY, EventType.PORTRAIT)
                and scene.event_confidence < 0.6
            ):
                cand.event_type = EventType.TRAVEL.value
                scene.event_type = EventType.TRAVEL
                scene.event_confidence = max(scene.event_confidence, 0.4)
                cand.event_score = compute_event_score(scene)
                logger.info(
                    "GPS correction: event type to travel (GPS present, low conf=%.2f)",
                    scene.event_confidence,
                )

            # B-2: Apply VLM expressions to detected faces for scoring
            if scene.expressions and cand.faces:
                # Map expressions to faces: handle count mismatch
                expr_list = [e.lower() for e in scene.expressions]
                for i, face in enumerate(cand.faces):
                    if i < len(expr_list):
                        face.expression = expr_list[i]
                    elif expr_list:
                        # More faces than expressions: apply majority expression
                        face.expression = max(set(expr_list), key=expr_list.count)
                # Recompute family score with expression data
                cand.family_score = compute_family_score(
                    cand.faces, cand.known_persons or None
                )
            elif scene.expressions and not cand.faces and scene.people_count > 0:
                # VLM saw people but face engine didn't — still use expression info
                # as a small family_score boost if positive expressions present
                positive = {"happy", "smiling", "laughing", "joyful", "excited"}
                pos_count = sum(
                    1 for e in scene.expressions if e.lower() in positive
                )
                if pos_count > 0:
                    cand.family_score = min(100.0, cand.family_score + pos_count * 5.0)

            # Re-score quality with real aesthetic if available
            try:
                from .engines.aesthetic import AestheticEngine

                if self._aesthetic is None:
                    t_ae = time.perf_counter()
                    self._aesthetic = AestheticEngine()
                    logger.info("Aesthetic engine init: %.2fs", time.perf_counter() - t_ae)
                aesthetic_raw = self._aesthetic.score(cand.image_b64)
                qs = compute_quality_score(aesthetic_raw, cand.technical_score)
                cand.quality_score = qs.total
            except RuntimeError:
                pass

        except RuntimeError as e:
            logger.warning("VLM not available for this candidate: %s", e)

    @staticmethod
    def _snapshot_candidate(cand: PhotoCandidate) -> dict:
        """Serialize candidate fields for checkpoint (excludes image_b64)."""
        return {
            "photo_id": cand.photo_id,
            "technical_score": cand.technical_score,
            "face_count": cand.face_count,
            "is_duplicate": cand.is_duplicate,
            "quality_score": cand.quality_score,
            "family_score": cand.family_score,
            "event_score": cand.event_score,
            "uniqueness_score": cand.uniqueness_score,
            "scene_description": cand.scene_description,
            "event_type": cand.event_type,
            "known_persons": cand.known_persons,
            "passed_stage1": cand.passed_stage1,
            "has_gps": cand.has_gps,
            "latitude": cand.latitude,
            "longitude": cand.longitude,
            "location_provenance": cand.location_provenance,
            "meaningful_score": cand.meaningful_score,
            "capture_date": cand.capture_date,
            "burst_group_id": cand.burst_group_id,
        }

    @staticmethod
    def _restore_candidate(
        snap: dict,
        image_b64: str,
        source_metadata: dict | None = None,
    ) -> PhotoCandidate:
        """Recreate a PhotoCandidate from a checkpoint snapshot."""
        metadata = source_metadata or {}
        cand = PhotoCandidate(photo_id=snap["photo_id"], image_b64=image_b64)
        cand.technical_score = snap.get("technical_score", 0.0)
        cand.face_count = snap.get("face_count", 0)
        cand.is_duplicate = snap.get("is_duplicate", False)
        cand.quality_score = snap.get("quality_score", 0.0)
        cand.family_score = snap.get("family_score", 0.0)
        cand.event_score = snap.get("event_score", 0.0)
        cand.uniqueness_score = snap.get("uniqueness_score", 0.0)
        cand.scene_description = snap.get("scene_description", "")
        cand.event_type = snap.get("event_type", EventType.OTHER.value)
        source_persons = list(metadata.get("known_persons") or metadata.get("persons") or [])
        cand.known_persons = list(
            dict.fromkeys([*snap.get("known_persons", []), *source_persons])
        )
        cand.passed_stage1 = snap.get("passed_stage1", True)
        cand.has_gps = snap.get("has_gps", False)
        gps = metadata.get("gps") if isinstance(metadata.get("gps"), dict) else {}
        cand.latitude = snap.get("latitude", metadata.get("latitude", gps.get("lat")))
        cand.longitude = snap.get("longitude", metadata.get("longitude", gps.get("lon")))
        cand.location_provenance = snap.get("location_provenance", "") or (
            "provider_metadata"
            if cand.latitude is not None and cand.longitude is not None
            else ""
        )
        cand.meaningful_score = snap.get("meaningful_score", 5)
        cand.capture_date = snap.get("capture_date", "") or str(
            metadata.get("capture_date") or ""
        )
        cand.burst_group_id = snap.get("burst_group_id", "") or str(
            metadata.get("burst_group_id") or ""
        )
        return cand

    @staticmethod
    def _apply_vlm_checkpoint(cand: PhotoCandidate, snap: dict) -> None:
        """Apply VLM stage checkpoint data onto an existing candidate."""
        cand.scene_description = snap.get("scene_description", "")
        cand.event_type = snap.get("event_type", cand.event_type)
        cand.event_score = snap.get("event_score", cand.event_score)
        cand.quality_score = snap.get("quality_score", cand.quality_score)
        cand.family_score = snap.get("family_score", cand.family_score)
        cand.meaningful_score = snap.get("meaningful_score", cand.meaningful_score)

    def _detect_duplicates(
        self, candidates: list[PhotoCandidate]
    ) -> list[DuplicateGroup]:
        """Run dedup across all candidates by computing hashes first."""
        photo_hashes: dict[str, str] = {}
        for c in candidates:
            try:
                h = self._dedup.compute_default_hash(c.image_b64)
                photo_hashes[c.photo_id] = h
            except Exception as e:
                logger.warning("Image hash calculation failed: %s", e)
        return self._dedup.find_duplicates(
            photo_hashes, threshold=self.config.dedup_threshold
        )

    def _rank(
        self,
        candidates: list[PhotoCandidate],
        dup_groups: list[DuplicateGroup],
        selection_profile: str,
    ) -> list[RankedPhoto]:
        """Aggregate scores and rank."""
        photo_scores = []
        for c in candidates:
            photo_scores.append(
                {
                    "photo_id": c.photo_id,
                    "quality_score": round(c.quality_score, 2),
                    "family_score": round(c.family_score, 2),
                    "event_score": round(c.event_score, 2),
                    "uniqueness_score": round(c.uniqueness_score, 2),
                    "scene_description": c.scene_description,
                    "event_type": c.event_type,
                    "faces_detected": c.face_count,
                    "known_persons": c.known_persons,
                    "has_gps": c.has_gps,
                    "meaningful_score": c.meaningful_score,
                    "capture_date": c.capture_date,
                    "technical_score": round(c.technical_score, 2),
                }
            )

        return rank_photos(photo_scores, selection_profile=selection_profile)
