"""Read-only ImageIO metadata normalization for the local photo inspector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from Foundation import NSURL
from Quartz import CGImageSourceCopyPropertiesAtIndex, CGImageSourceCreateWithURL


_MAX_RAW_FIELDS = 120
_MAX_RAW_VALUE_LENGTH = 256
_SKIPPED_RAW_KEYS = {"MakerNote", "Thumbnail", "ImageUniqueID"}
_SENSITIVE_KEY_PARTS = ("gps", "serial", "owner", "path")


@dataclass(frozen=True)
class MetadataField:
    key: str
    label: str
    value: str
    sensitive: bool = False


@dataclass(frozen=True)
class MetadataSection:
    key: str
    title: str
    fields: tuple[MetadataField, ...]
    collapsed_by_default: bool = False


@dataclass(frozen=True)
class LocalPhotoMetadata:
    path: str
    summary: str
    sections: tuple[MetadataSection, ...]
    error: str = ""

    def section(self, key: str) -> MetadataSection | None:
        return next((section for section in self.sections if section.key == key), None)

    def clipboard_text(self, *, include_sensitive: bool = False) -> str:
        lines: list[str] = []
        for section in self.sections:
            visible = [field for field in section.fields if include_sensitive or not field.sensitive]
            if not visible:
                continue
            if lines:
                lines.append("")
            lines.append(f"[{section.title}]")
            lines.extend(f"{field.label}: {field.value}" for field in visible)
        return "\n".join(lines)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], ()):  # ImageIO omits unsupported values inconsistently.
            return value
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _compact_number(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return ""
    rounded = round(number, digits)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.{digits}f}".rstrip("0").rstrip(".")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return ""
    if isinstance(value, Mapping):
        return ""
    if isinstance(value, Sequence) and not isinstance(value, str):
        parts = [_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    rendered = str(value).strip()
    return rendered if len(rendered) <= _MAX_RAW_VALUE_LENGTH else f"{rendered[:253]}..."


def _format_exposure(value: Any) -> str:
    seconds = _number(value)
    if seconds is None or seconds <= 0:
        return ""
    if seconds < 1:
        denominator = round(1.0 / seconds)
        return f"1/{denominator}초" if denominator > 1 else f"{seconds:g}초"
    return f"{_compact_number(seconds, 3)}초"


def _format_focal(value: Any) -> str:
    rendered = _compact_number(value, 1)
    return f"{rendered}mm" if rendered else ""


def _format_aperture(value: Any) -> str:
    rendered = _compact_number(value, 1)
    return f"f/{rendered}" if rendered else ""


def _format_iso(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        value = next(iter(value), None)
    rendered = _compact_number(value, 0)
    return f"ISO {rendered}" if rendered else ""


def _format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes}B"


def _gps_decimal(value: Any, reference: Any) -> float | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts = [_number(item) for item in value]
        if len(parts) >= 3 and all(part is not None for part in parts[:3]):
            decimal = float(parts[0]) + float(parts[1]) / 60.0 + float(parts[2]) / 3600.0
        else:
            return None
    else:
        decimal = _number(value)
        if decimal is None:
            return None
    if str(reference or "").upper() in {"S", "W"}:
        decimal = -abs(decimal)
    return round(decimal, 6)


def _field(key: str, label: str, value: Any, *, sensitive: bool = False) -> MetadataField | None:
    rendered = _text(value)
    return MetadataField(key, label, rendered, sensitive) if rendered else None


def _append(fields: list[MetadataField], field: MetadataField | None) -> None:
    if field is not None:
        fields.append(field)


def _flatten_human_readable(
    value: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    output: list[MetadataField] | None = None,
) -> list[MetadataField]:
    fields = output if output is not None else []
    if depth > 3 or len(fields) >= _MAX_RAW_FIELDS:
        return fields
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip()
            plain_key = key.strip("{}")
            if plain_key in _SKIPPED_RAW_KEYS:
                continue
            path = f"{prefix}.{plain_key}" if prefix else plain_key
            _flatten_human_readable(nested, prefix=path, depth=depth + 1, output=fields)
            if len(fields) >= _MAX_RAW_FIELDS:
                break
        return fields
    rendered = _text(value)
    if not rendered:
        return fields
    lowered = prefix.casefold()
    fields.append(
        MetadataField(
            key=f"raw:{prefix}",
            label=prefix,
            value=rendered,
            sensitive=any(part in lowered for part in _SENSITIVE_KEY_PARTS),
        )
    )
    return fields


def normalize_imageio_metadata(path: str | Path, properties: Mapping[str, Any], stat: Any) -> LocalPhotoMetadata:
    source_path = Path(path).expanduser().resolve()
    root = _mapping(properties)
    exif = _mapping(root.get("{Exif}"))
    tiff = _mapping(root.get("{TIFF}"))
    gps = _mapping(root.get("{GPS}"))
    aux = _mapping(root.get("{ExifAux}"))

    width = _first(root, "PixelWidth")
    height = _first(root, "PixelHeight")
    capture_date = _first(exif, "DateTimeOriginal", "DateTimeDigitized") or _first(tiff, "DateTime")

    file_fields: list[MetadataField] = []
    _append(file_fields, _field("filename", "파일명", source_path.name))
    _append(file_fields, _field("format", "형식", source_path.suffix.lstrip(".").upper() or "알 수 없음"))
    _append(file_fields, _field("path", "원본 경로", str(source_path), sensitive=True))
    _append(file_fields, _field("file_size", "파일 크기", _format_file_size(int(stat.st_size))))
    _append(file_fields, _field("modified", "수정 시각", datetime.fromtimestamp(stat.st_mtime).strftime("%Y. %m. %d. %H:%M:%S")))
    _append(file_fields, _field("created", "생성 시각", datetime.fromtimestamp(stat.st_ctime).strftime("%Y. %m. %d. %H:%M:%S")))
    _append(file_fields, _field("captured", "촬영 시각", capture_date))
    if width and height:
        _append(file_fields, _field("dimensions", "해상도", f"{width} × {height} px"))
    _append(file_fields, _field("orientation", "방향", _first(root, "Orientation")))
    dpi_width = _compact_number(_first(root, "DPIWidth"), 1)
    dpi_height = _compact_number(_first(root, "DPIHeight"), 1)
    if dpi_width or dpi_height:
        _append(file_fields, _field("dpi", "DPI", f"{dpi_width or '?'} × {dpi_height or '?'}"))
    _append(file_fields, _field("depth", "Bit depth", _first(root, "Depth")))

    camera_fields: list[MetadataField] = []
    identifier_fields: list[MetadataField] = []
    _append(camera_fields, _field("camera_make", "카메라 제조사", _first(tiff, "Make")))
    _append(camera_fields, _field("camera_model", "카메라 모델", _first(tiff, "Model")))
    _append(camera_fields, _field("lens_make", "렌즈 제조사", _first(aux, "LensMake")))
    _append(camera_fields, _field("lens_model", "렌즈 모델", _first(aux, "LensModel")))
    _append(camera_fields, _field("lens_spec", "렌즈 사양", _first(aux, "LensSpecification", "LensInfo")))
    _append(camera_fields, _field("focal_length", "초점 거리", _format_focal(_first(exif, "FocalLength"))))
    _append(camera_fields, _field("focal_35mm", "35mm 환산", _format_focal(_first(exif, "FocalLenIn35mmFilm"))))
    _append(camera_fields, _field("max_aperture", "최대 조리개", _format_aperture(_first(exif, "MaxApertureValue"))))
    _append(camera_fields, _field("focus_distance", "초점 거리(피사체)", _first(exif, "SubjectDistance")))
    _append(identifier_fields, _field("camera_serial", "카메라 일련번호", _first(aux, "CameraSerialNumber", "SerialNumber"), sensitive=True))
    _append(identifier_fields, _field("lens_serial", "렌즈 일련번호", _first(aux, "LensSerialNumber"), sensitive=True))

    exposure_fields: list[MetadataField] = []
    aperture = _format_aperture(_first(exif, "FNumber"))
    exposure_time = _format_exposure(_first(exif, "ExposureTime"))
    iso = _format_iso(_first(exif, "ISOSpeedRatings", "PhotographicSensitivity"))
    _append(exposure_fields, _field("aperture", "조리개", aperture))
    _append(exposure_fields, _field("exposure_time", "셔터 속도", exposure_time))
    _append(exposure_fields, _field("iso", "ISO", iso.removeprefix("ISO ")))
    _append(exposure_fields, _field("exposure_bias", "노출 보정", _first(exif, "ExposureBiasValue")))
    _append(exposure_fields, _field("exposure_program", "노출 프로그램", _first(exif, "ExposureProgram")))
    _append(exposure_fields, _field("metering", "측광 방식", _first(exif, "MeteringMode")))
    _append(exposure_fields, _field("flash", "Flash", _first(exif, "Flash")))
    _append(exposure_fields, _field("white_balance", "White balance", _first(exif, "WhiteBalance")))
    _append(exposure_fields, _field("light_source", "광원", _first(exif, "LightSource")))
    _append(exposure_fields, _field("capture_mode", "촬영 모드", _first(exif, "SceneCaptureType")))
    _append(exposure_fields, _field("digital_zoom", "Digital zoom", _first(exif, "DigitalZoomRatio")))

    location_fields: list[MetadataField] = []
    latitude = _gps_decimal(_first(gps, "Latitude"), _first(gps, "LatitudeRef"))
    longitude = _gps_decimal(_first(gps, "Longitude"), _first(gps, "LongitudeRef"))
    if latitude is not None:
        _append(location_fields, _field("latitude", "위도", f"{latitude:.6f}", sensitive=True))
    if longitude is not None:
        _append(location_fields, _field("longitude", "경도", f"{longitude:.6f}", sensitive=True))
    _append(location_fields, _field("altitude", "고도", _first(gps, "Altitude"), sensitive=True))
    _append(location_fields, _field("direction", "촬영 방향", _first(gps, "ImgDirection"), sensitive=True))
    _append(location_fields, _field("gps_time", "GPS 시각", _first(gps, "TimeStamp", "DateStamp"), sensitive=True))

    advanced_fields: list[MetadataField] = []
    _append(advanced_fields, _field("color_model", "Color model", _first(root, "ColorModel")))
    _append(advanced_fields, _field("profile", "Color profile", _first(root, "ProfileName")))
    _append(advanced_fields, _field("software", "Software", _first(tiff, "Software")))
    _append(advanced_fields, _field("artist", "Artist", _first(tiff, "Artist")))
    _append(advanced_fields, _field("copyright", "Copyright", _first(tiff, "Copyright")))
    advanced_fields.extend(field for field in _flatten_human_readable(root) if not field.sensitive)

    summary_parts = [
        _format_focal(_first(exif, "FocalLength")),
        aperture,
        exposure_time,
        iso,
    ]
    sections = [
        MetadataSection("file", "파일 및 이미지", tuple(file_fields)),
        MetadataSection("camera", "카메라와 렌즈", tuple(camera_fields)),
        MetadataSection("exposure", "촬영 설정", tuple(exposure_fields)),
    ]
    if location_fields:
        sections.append(MetadataSection("location", "위치 정보 포함", tuple(location_fields), collapsed_by_default=True))
    if identifier_fields:
        sections.append(MetadataSection("identifiers", "기기 식별 정보 포함", tuple(identifier_fields), collapsed_by_default=True))
    if advanced_fields:
        sections.append(MetadataSection("advanced", "색상 및 고급 정보", tuple(advanced_fields), collapsed_by_default=True))
    return LocalPhotoMetadata(
        path=str(source_path),
        summary=" · ".join(part for part in summary_parts if part),
        sections=tuple(section for section in sections if section.fields),
    )


def extract_local_photo_metadata(path: str | Path) -> LocalPhotoMetadata:
    source_path = Path(path).expanduser().resolve()
    try:
        stat = source_path.stat()
        source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(source_path)), None)
        if source is None:
            return LocalPhotoMetadata(str(source_path), "", (), "이미지 메타데이터를 읽을 수 없습니다.")
        properties = CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
        return normalize_imageio_metadata(source_path, properties, stat)
    except (OSError, PermissionError) as exc:
        return LocalPhotoMetadata(str(source_path), "", (), f"메타데이터를 읽을 수 없습니다: {exc}")
