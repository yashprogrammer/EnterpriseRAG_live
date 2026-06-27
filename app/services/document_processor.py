import os
from pathlib import Path

from docling.chunking import HybridChunker
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from loguru import logger


def _accelerator_device() -> AcceleratorDevice:
    configured = os.getenv("DOCLING_ACCELERATOR", "cpu").strip().lower()
    devices = {
        "auto": AcceleratorDevice.AUTO,
        "cpu": AcceleratorDevice.CPU,
        "cuda": AcceleratorDevice.CUDA,
        "mps": AcceleratorDevice.MPS,
        "xpu": AcceleratorDevice.XPU,
    }
    if configured not in devices:
        logger.warning("Unknown DOCLING_ACCELERATOR={!r}; falling back to cpu", configured)
    return devices.get(configured, AcceleratorDevice.CPU)


class DocumentProcessor:
    def __init__(self):
        pipeline_options = PdfPipelineOptions()
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=8, device=_accelerator_device()
        )
        rapidocr_path = os.getenv("DOCLING_RAPIDOCR_PATH")
        if rapidocr_path:
            rapidocr_root = Path(rapidocr_path)
            pipeline_options.ocr_options = RapidOcrOptions(
                backend="torch",
                lang=["english"],
                det_model_path=str(
                    rapidocr_root / "torch/PP-OCRv4/det/en_PP-OCRv3_det_mobile.pth"
                ),
                cls_model_path=str(
                    rapidocr_root / "torch/PP-OCRv4/cls/ch_ptocr_mobile_v2.0_cls_mobile.pth"
                ),
                rec_model_path=str(
                    rapidocr_root / "torch/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.pth"
                ),
                rec_keys_path=str(
                    rapidocr_root / "paddle/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile/en_dict.txt"
                ),
                font_path=str(rapidocr_root / "resources/fonts/FZYTK.TTF"),
            )
        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        self.chunker = HybridChunker()

    def process_document(self, file_path: str) -> list[dict]:
        result = self.converter.convert(file_path)
        doc = result.document
        chunk_iter = self.chunker.chunk(doc)

        chunks = []
        source_name = Path(file_path).name

        for chunk in chunk_iter:
            meta = {"text": chunk.text, "source": source_name}
            if hasattr(chunk, "meta") and hasattr(chunk.meta, "doc_items"):
                items = chunk.meta.doc_items
                if items and hasattr(items[0], "prov") and items[0].prov:
                    meta["page_number"] = items[0].prov[0].page_no
            chunks.append(meta)
        logger.info("Processed {} chunks from {}", len(chunks), file_path)
        return chunks
