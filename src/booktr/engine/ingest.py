"""Extract book geometry, original art and note provenance.

PDFium stays on one thread. OCR works from persisted page images in parallel;
a long scanned book never accumulates every rendered page in memory.
"""
from __future__ import annotations

import io
import json
import logging
import re
from collections import Counter
from pathlib import Path

from ..errors import BookTrError
from .document import (NOTE_TOKEN, SCHEMA_VERSION, extract_layout, footnote_id,
                       margin_key, note_tokens, without_running_margins)

log = logging.getLogger("booktr.ingest")
EXTRACTION_VERSION = 6
MIN_TEXT_CHARS = 50
RENDER_SCALE = 160 / 72
OCR_SYSTEM = """[OCR] Transcribe this original book page faithfully; never translate or invent text.
Return one JSON object:
{"blocks":[{"type":"paragraph|heading", "text":"verbatim text", "bbox":[left,top,right,bottom]}],
 "footnotes":[{"key":"note-1", "label":"original marker", "text":"complete original note body"}],
 "footnote_continuations":[{"text":"continued original note from the previous page"}],
 "figures":[{"bbox":[left,top,right,bottom]}]}
Preserve every paragraph, quotation, caption and footnote; join line-wrap hyphens.
Drop running headers, footers, and standalone page numbers.
Heading means a genuine chapter title, never shouted dialogue.
Give each note a unique key, even when printed markers restart (two notes labeled 1).
At each reference write [[FN:note key]] in its exact text position, matching that note.
Extract its complete body separately in footnotes, excluding its printed marker.
If a bottom note continues from the preceding page without a new printed marker,
put its text in footnote_continuations, never body prose and never invent a marker.
All block and figure coordinates are integers 0..1000 relative to the page, origin top left.
Include ONLY visible artwork, photographs or diagrams; never prose or a whole text page.
A whole page may be a figure only if it actually consists of artwork.
Do not transcribe lettering inside artwork as body text; preserve captions.
An empty page has empty arrays. Return JSON only."""

ANCHOR_SYSTEM = """[OCR_ANCHORS] Locate original footnote references in this visual PDF page.
The supplied numbered blocks contain exact extracted original prose. Preserve every
letter, space and punctuation. Replace each printed note marker with [[FN:note id]]
using the supplied note IDs, labels, and original bodies. Printed labels may restart;
identify each note from its position and body. Do not translate or rewrite prose.
Markers already expressed as [[FN:...]] must remain unchanged. Every supplied note
must be referenced. Return {"blocks":{"0":"exact block with inserted note tokens",...}}.
Return every supplied block; change only printed reference markers."""


def _page_valid(page: object, directory: Path, expected_index: int) -> bool:
    if (not isinstance(page, dict) or page.get("schema_version") != SCHEMA_VERSION
            or page.get("index") != expected_index or not isinstance(page.get("blocks"), list)
            or not isinstance(page.get("footnotes"), list) or not isinstance(page.get("images"), list)):
        return False
    try:
        assets = list(page["images"])
        if page.get("cover"):
            assets.append(page["cover"])
        for asset in assets:
            path = Path(asset["path"])
            if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("assets",):
                return False
            destination = directory / path
            if not destination.is_file() or destination.stat().st_size == 0:
                return False
            # A broken PNG is as unusable as a missing one. verify() does not
            # decode the full-resolution raster into memory.
            from PIL import Image

            with Image.open(destination) as picture:
                picture.verify()
        note_ids = {note["id"] for note in page["footnotes"]}
        if len(note_ids) != len(page["footnotes"]):
            return False
        if any(not isinstance(note["text"], str) or not note["text"].strip() for note in page["footnotes"]):
            return False
        for block in page["blocks"]:
            if not isinstance(block["text"], str) or not set(note_tokens(block["text"])).issubset(note_ids | set(page.get("external_footnotes", []))):
                return False
        for continuation in page.get("footnote_continuations", []):
            if not continuation.get("id") or not isinstance(continuation.get("text"), str) or not continuation["text"].strip():
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def artifacts_valid(job) -> bool:
    """Check resumable metadata and all linked source artwork before reusing it."""
    try:
        pages = job.read_json("pages.json")
        if not isinstance(pages, list):
            return False
        known_notes = {note["id"] for page in pages for note in page.get("footnotes", [])}
        return bool(isinstance(pages, list) and pages and all(
            _page_valid(page, job.dir, index) and set(page.get("external_footnotes", [])).issubset(known_notes)
            and {c["id"] for c in page.get("footnote_continuations", [])}.issubset(known_notes)
            for index, page in enumerate(pages, start=1)
        ))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def _running_margins(pdf) -> set[str]:
    """Find recurring short margin lines without a second character parse."""
    counts = Counter()
    for index in range(len(pdf)):
        page = pdf[index]
        text_page = page.get_textpage()
        try:
            width, height = page.get_size()
            header = text_page.get_text_bounded(left=0, bottom=height * .915, right=width, top=height)
            footer = text_page.get_text_bounded(left=0, bottom=0, right=width, top=height * .06)
            seen = set()
            for region, text in [("header", header), ("footer", footer)]:
                for line in text.splitlines():
                    key = margin_key(line)
                    if not key or len(key) > 120 or key == "#":
                        continue
                    if region == "footer" and re.match(r"^\s*(?:\d{1,3}|[*†‡§]+)[.)]?\s*\D", line):
                        continue  # repeated short author notes are still notes
                    seen.add(key)
            counts.update(seen)
        finally:
            text_page.close()
            page.close()
    return {key for key, count in counts.items() if count >= max(3, len(pdf) * .15)}


def _note_annotations(pdf) -> dict[int, list[dict]]:
    """Resolve original internal note links across pages, before reading prose."""
    pages = pdf.pages
    by_object = {page.page_obj.pageid: index for index, page in enumerate(pages)}
    annotations = {index: [dict(a) for a in page.annots] for index, page in enumerate(pages)}
    definitions: dict[int, list[dict]] = {}
    references = []
    for index, annots in annotations.items():
        for annotation in annots:
            contents = str(annotation.get("contents") or "").strip()
            match = re.fullmatch(r"Footnote\s+(\d{1,3}|[*†‡§]+|[A-Za-z]{1,8})", contents, re.IGNORECASE)
            destination = annotation.get("data", {}).get("Dest")
            if not match or not isinstance(destination, list) or len(destination) < 4:
                continue
            target = by_object.get(getattr(destination[0], "objid", None))
            if target is None:
                continue
            candidates = [a for a in annotations[target] if str(a.get("contents") or "").strip() == match[1]]
            if not candidates:
                continue
            reciprocal = []
            for candidate in candidates:
                backlink = candidate.get("data", {}).get("Dest")
                if (isinstance(backlink, list) and len(backlink) >= 4
                        and by_object.get(getattr(backlink[0], "objid", None)) == index
                        and abs(float(backlink[2]) - annotation["x0"]) < 3
                        and abs(pages[index].height - float(backlink[3]) - annotation["top"]) < 20):
                    reciprocal.append(candidate)
            if reciprocal:
                candidates = reciprocal
            elif not re.fullmatch(r"\d{1,3}|[*†‡§]+", match[1]):
                # A word in link contents is not sufficient evidence for a
                # letter/Roman note; its original definition must point back.
                continue
            y = pages[target].height - float(destination[3])
            following = [a for a in candidates if y - 1 <= a["top"] <= y + 25]
            definition = min(following or candidates, key=lambda a: abs(a["top"] - y) + abs(a["x0"] - float(destination[2])))
            if definition not in definitions.setdefault(target, []):
                definitions[target].append(definition)
            references.append((annotation, definition, target, match[1]))
    for index, defs in definitions.items():
        for n, definition in enumerate(sorted(defs, key=lambda a: (a["top"], a["x0"])), start=1):
            definition["footnote_id"] = footnote_id(index + 1, n)
            definition["footnote_label"] = str(definition["contents"]).strip()
    for reference, definition, target, label in references:
        reference["note_id"] = definition["footnote_id"]
        reference["note_label"] = label
        reference["note_source_page"] = target + 1
    return annotations


def _render_png(page) -> bytes:
    bitmap = page.render(scale=RENDER_SCALE)
    try:
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        return buf.getvalue()
    finally:
        bitmap.close()


def _save_crop(page, bbox: list[float], destination: Path) -> None:
    left, top, right, bottom = bbox
    width, height = page.get_size()
    bitmap = page.render(scale=RENDER_SCALE,
                         crop=(max(0, left), max(0, height - bottom),
                               max(0, width - right), max(0, top)))
    try:
        bitmap.to_pil().save(destination, "PNG")
    finally:
        bitmap.close()


def _extract_images(page, page_no: int, directory: Path) -> list[dict]:
    import pypdfium2.raw as raw

    width, height = page.get_size()
    assets: list[dict] = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=15):
        x0, y0, x1, y1 = obj.get_bounds()
        bbox = [max(0, x0), max(0, height - y1), min(width, x1), min(height, height - y0)]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        ident = f"img-p{page_no:04d}-{len(assets) + 1:03d}"
        relative = f"assets/{ident}.png"
        # Preserve the original raster resolution and its PDF alpha mask.
        # Complex PDF image encodings can still fall back to a displayed crop.
        try:
            bitmap = obj.get_bitmap(render=True)
            try:
                bitmap.to_pil().save(directory / relative, "PNG")
            finally:
                bitmap.close()
        except Exception:
            log.warning("Falling back to displayed raster for source page %s image %s", page_no, ident)
            _save_crop(page, bbox, directory / relative)
        coverage = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / (width * height)
        assets.append({"id": ident, "path": relative, "source_page": page_no,
                       "bbox": bbox, "role": "scan_background" if coverage > .85 else "illustration"})
    return assets


def _vector_images(page, page_no: int, directory: Path, first_index: int,
                   raster_boxes: list[list[float]]) -> list[dict]:
    """Retain vector illustrations, joining nearby drawing components."""
    import pypdfium2.raw as raw

    width, height = page.get_size()
    groups: list[list[float]] = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_SHADING], max_depth=15):
        x0, y0, x1, y1 = obj.get_bounds()
        box = [max(0, x0), max(0, height - y1), min(width, x1), min(height, height - y0)]
        if box[2] - box[0] < .5 or box[3] - box[1] < .5:
            continue  # rules, including footnote separators
        if (box[2] - box[0]) * (box[3] - box[1]) > width * height * .85:
            continue  # page background
        if any(box[0] >= b[0] and box[1] >= b[1] and box[2] <= b[2] and box[3] <= b[3]
               for b in raster_boxes):
            continue
        touching = [g for g in groups if not (box[2] + 12 < g[0] or g[2] + 12 < box[0]
                                               or box[3] + 12 < g[1] or g[3] + 12 < box[1])]
        for group in touching:
            box = [min(box[0], group[0]), min(box[1], group[1]),
                   max(box[2], group[2]), max(box[3], group[3])]
            groups.remove(group)
        groups.append(box)
    images: list[dict] = []
    for bbox in groups:
        if bbox[2] - bbox[0] < 18 or bbox[3] - bbox[1] < 18:
            continue
        ident = f"img-p{page_no:04d}-{first_index + len(images):03d}"
        relative = f"assets/{ident}.png"
        _save_crop(page, bbox, directory / relative)
        images.append({"id": ident, "path": relative, "source_page": page_no,
                       "bbox": bbox, "role": "illustration"})
    return images


def _normalize_ocr(raw: str, metadata: dict, directory: Path) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    # Old adapters/checkpoints can still return prose. Broken JSON is an error.
    if not cleaned.startswith("{"):
        text = "" if cleaned == "[EMPTY]" else cleaned
        return {**metadata, "text": text,
                "blocks": [{"type": "paragraph", "text": p} for p in re.split(r"\n\s*\n", text) if p],
                "footnotes": [], "warnings": ["Legacy plain-text OCR did not provide layout metadata."]}
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
            raise ValueError("OCR needs a blocks array")
        notes, figures = data.get("footnotes", []), data.get("figures", [])
        if not isinstance(notes, list) or not isinstance(figures, list):
            raise ValueError("OCR notes and figures must be arrays")
        continuations = data.get("footnote_continuations", [])
        if not isinstance(continuations, list):
            raise ValueError("OCR note continuations must be an array")
        continued = []
        for continuation in continuations:
            text = continuation.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty OCR note continuation")
            continued.append({"text": text.strip(), "source_page": metadata["index"]})
        page_no = metadata["index"]
        footnotes, labels = [], {}
        for n, note in enumerate(notes, start=1):
            label, text = str(note["label"]).strip(), str(note["text"]).strip()
            key = str(note.get("key", label)).strip()
            if not label or not text or key in labels:
                raise ValueError("empty or duplicate OCR footnote")
            ident = footnote_id(page_no, n)
            labels[key] = ident
            footnotes.append({"id": ident, "label": label, "text": text, "source_page": page_no})
        blocks = []
        for block in data["blocks"]:
            text = str(block["text"]).strip()
            if not text:
                continue
            for label, ident in labels.items():
                text = text.replace(f"[[FN:{label}]]", f"[[FN:{ident}]]")
            if re.search(r"\[\[FN:(?!fn-p)[^\]]+\]\]", text):
                raise ValueError("OCR reference has no matching note")
            normalized = {"type": "heading" if block.get("type") == "heading" else "paragraph", "text": text}
            if block.get("bbox") is not None:
                box = block["bbox"]
                if (not isinstance(box, list) or len(box) != 4
                        or not all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in box)
                        or box[2] <= box[0] or box[3] <= box[1]):
                    raise ValueError("invalid OCR paragraph coordinates")
                normalized["bbox"] = [v / 1000 * (metadata["width"] if k % 2 == 0 else metadata["height"])
                                      for k, v in enumerate(box)]
            blocks.append(normalized)
        images = list(metadata["images"])
        if figures:
            from PIL import Image

            with Image.open(directory / metadata["ocr_image"]) as page_image:
                for n, figure in enumerate(figures, start=1):
                    box = figure["bbox"]
                    if (not isinstance(box, list) or len(box) != 4
                            or not all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in box)
                            or box[2] <= box[0] or box[3] <= box[1]):
                        raise ValueError("invalid OCR figure coordinates")
                    ident = f"figure-p{page_no:04d}-{n:03d}"
                    relative = f"assets/{ident}.png"
                    bbox = [v / 1000 * (metadata["width"] if k % 2 == 0 else metadata["height"])
                            for k, v in enumerate(box)]
                    duplicate = False
                    for existing in images:
                        a = existing["bbox"]
                        overlap = max(0, min(a[2], bbox[2]) - max(a[0], bbox[0])) * max(0, min(a[3], bbox[3]) - max(a[1], bbox[1]))
                        area = (a[2] - a[0]) * (a[3] - a[1])
                        new_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                        if overlap / max(1, area + new_area - overlap) > .75:
                            existing["role"] = "illustration"
                            duplicate = True
                            break
                    if duplicate:
                        continue
                    crop = tuple(round(v / 1000 * (page_image.width if k % 2 == 0 else page_image.height))
                                 for k, v in enumerate(box))
                    page_image.crop(crop).save(directory / relative, "PNG")
                    images.append({"id": ident, "path": relative, "source_page": page_no,
                                   "bbox": bbox, "role": "illustration"})
        text = "\n\n".join(("## " if b["type"] == "heading" else "") + b["text"] for b in blocks)
        references = set(note_tokens(text))
        note_ids = {note["id"] for note in footnotes}
        if references - note_ids:
            raise ValueError("OCR reference has no matching original note")
        warnings = [f"Footnote {note['label']} on source page {page_no} needs visual anchor repair."
                    for note in footnotes if note["id"] not in references]
        return {**metadata, "text": text, "blocks": blocks, "footnotes": footnotes,
                "images": images, "warnings": warnings, "footnote_continuations": continued}
    except (ValueError, TypeError, KeyError) as exc:
        raise BookTrError("bad_pdf", f"Invalid OCR layout for page {metadata['index']}: {exc}") from exc


def _repair_anchors(raw: str, metadata: dict) -> dict:
    """Accept visual annotation only when all original prose remains exact."""
    try:
        data = json.loads(raw)
        annotated = data["blocks"]
        notes = {note["id"]: note["label"] for note in metadata["footnotes"]}
        blocks = []
        seen = set()
        for index, block in enumerate(metadata["blocks"]):
            replacement = annotated[str(index)]
            if not isinstance(replacement, str) or not set(note_tokens(replacement)).issubset(notes):
                raise ValueError("unknown note reference")
            # Each token consumes only the original printed marker (or an
            # existing token); nothing else may change during visual repair.
            pieces = []
            cursor = 0
            for match in NOTE_TOKEN.finditer(replacement):
                pieces.append((replacement[cursor:match.start()], match[1]))
                cursor = match.end()
            original = block["text"]
            positions = {0}
            for prefix, ident in pieces:
                positions = {position + len(prefix) for position in positions
                             if original.startswith(prefix, position)}
                label = notes[ident]
                choices = (f"[[FN:{ident}]]", f"[{label}]", label, "")
                positions = {position + len(choice) for position in positions for choice in choices
                             if original.startswith(choice, position)}
                seen.add(ident)
            suffix = replacement[cursor:]
            if not any(original[position:] == suffix for position in positions):
                raise ValueError("visual anchor repair altered original prose")
            blocks.append({**block, "text": replacement})
        if seen != set(notes):
            raise ValueError("not every original footnote has a reference")
        result = {**metadata, "blocks": blocks, "warnings": [], "anchor_repair": False}
        result["text"] = "\n\n".join(("## " if b["type"] == "heading" else "") + b["text"] for b in blocks)
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise BookTrError("bad_pdf", f"Cannot confidently locate source footnotes on page {metadata['index']}: {exc}") from exc


def run(engine):
    job, api, cfg = engine.job, engine.api, engine.config
    try:
        import pdfplumber
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(job.source_pdf))
        layout_pdf = pdfplumber.open(job.source_pdf)
    except Exception as exc:
        raise BookTrError("bad_pdf", str(exc)) from exc
    total = len(pdf)
    if not total:
        pdf.close()
        layout_pdf.close()
        raise BookTrError("bad_pdf", "PDF has no pages")
    (job.dir / "assets").mkdir(exist_ok=True)
    (job.dir / "ocr-pages").mkdir(exist_ok=True)
    items: list[tuple[str, object]] = []
    try:
        repeated_margins = _running_margins(pdf)
        annotations = _note_annotations(layout_pdf)
        previous_note = None
        for index in range(total):
            engine.checkpoint_wait()
            key = f"p{index:04d}"
            if job.has_chunk("ingest", key):
                try:
                    cached = job.load_chunk("ingest", key)
                except (OSError, ValueError, TypeError):
                    cached = None
                if _page_valid(cached, job.dir, index + 1):
                    if cached.get("footnotes"):
                        previous_note = cached["footnotes"][-1]
                    items.append((key, None))
                    continue
                job._chunk_path("ingest", key).unlink()  # old text-only extraction
            page = pdf[index]
            try:
                width, height = page.get_size()
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_bounded() or ""
                finally:
                    text_page.close()
                assets = _extract_images(page, index + 1, job.dir)
                assets.extend(_vector_images(page, index + 1, job.dir, len(assets) + 1,
                                              [a["bbox"] for a in assets]))
                metadata = {"schema_version": SCHEMA_VERSION, "index": index + 1,
                            "width": width, "height": height, "images": assets, "cover": None}
                if index == 0:
                    cover_path = "assets/cover.png"
                    (job.dir / cover_path).write_bytes(_render_png(page))
                    metadata["cover"] = {"id": "cover", "path": cover_path, "source_page": 1}
                full_scan = any(asset.get("role") == "scan_background" for asset in assets)
                large_partial_scan = (len(text.strip()) <= 1000 and any(
                    (asset["bbox"][2] - asset["bbox"][0]) * (asset["bbox"][3] - asset["bbox"][1])
                    >= width * height * .35 for asset in assets
                ))
                if large_partial_scan:
                    for asset in assets:
                        if ((asset["bbox"][2] - asset["bbox"][0]) * (asset["bbox"][3] - asset["bbox"][1])
                                >= width * height * .35):
                            asset["role"] = "scan_background"
                if len(text.strip()) >= MIN_TEXT_CHARS and not full_scan and not large_partial_scan:
                    pl_page = layout_pdf.pages[index]
                    separators = [line["top"] for line in pl_page.lines
                                  if abs(line["bottom"] - line["top"]) < 2
                                  and width * .08 < line["x1"] - line["x0"] < width * .7]
                    chars = without_running_margins(pl_page.chars, height, repeated_margins)
                    layout = extract_layout(chars, width, height, index + 1,
                                            separators, annotations[index], previous_note)
                    if layout["footnotes"]:
                        previous_note = layout["footnotes"][-1]
                    if layout["warnings"]:
                        # Vision sees the printed marker while deterministic
                        # validation prevents it from rewriting digital prose.
                        metadata.update(layout)
                        metadata["anchor_repair"] = True
                        metadata["ocr_image"] = f"ocr-pages/{key}.png"
                        (job.dir / metadata["ocr_image"]).write_bytes(_render_png(page))
                        items.append((key, metadata))
                    else:
                        job.save_chunk("ingest", key, {**metadata, **layout})
                        items.append((key, None))
                    pl_page.close()
                else:
                    metadata["source_text"] = text
                    metadata["ocr_image"] = f"ocr-pages/{key}.png"
                    (job.dir / metadata["ocr_image"]).write_bytes(_render_png(page))
                    items.append((key, metadata))
            finally:
                page.close()
    finally:
        pdf.close()
        layout_pdf.close()

    def ocr(metadata: dict) -> dict:
        png = (job.dir / metadata["ocr_image"]).read_bytes()
        if not metadata.get("anchor_repair"):
            raw = api.complete(cfg.model_draft, OCR_SYSTEM,
                               f"Transcribe source page {metadata['index']} with its footnotes and visible figures.\n"
                               "The optional existing text layer below may help transcription; verify against the image:\n"
                               + metadata.get("source_text", ""),
                               images=[png], json_mode=True)
            metadata = _normalize_ocr(raw, metadata, job.dir)
            if not metadata["warnings"] or not metadata["footnotes"]:
                return metadata
        user = json.dumps({"blocks": {str(n): b["text"] for n, b in enumerate(metadata["blocks"])},
                           "notes": [{"id": note["id"], "label": note["label"], "text": note["text"]}
                                     for note in metadata["footnotes"]]},
                          ensure_ascii=False)
        raw = api.complete(cfg.model_draft, ANCHOR_SYSTEM, user, images=[png], json_mode=True)
        return _repair_anchors(raw, metadata)

    results = engine.run_chunks("ingest", items, ocr)
    pages = [results[f"p{index:04d}"] for index in range(total)]
    last_note = None
    for index, page in enumerate(pages):
        for continuation in page.get("footnote_continuations", []):
            if not continuation.get("id"):
                if not last_note:
                    raise BookTrError("bad_pdf", f"Source page {index + 1} has a continued note without an original body")
                continuation["id"] = last_note["id"]
        if page.get("footnotes"):
            last_note = page["footnotes"][-1]
        job.save_chunk("ingest", f"p{index:04d}", page)
    job.write_json("pages.json", pages)
    job.write_json("extraction-report.json", {
        "source_pages": total,
        "footnotes": sum(len(p.get("footnotes", [])) for p in pages),
        "images": sum(len(p.get("images", [])) for p in pages),
        "warnings": [warning for p in pages for warning in p.get("warnings", [])],
    })
    return pages
