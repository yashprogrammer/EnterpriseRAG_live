FROM python:3.12-slim



RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*


RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache torch torchvision \
        --extra-index-url https://download.pytorch.org/whl/cpu
RUN uv pip install --system --no-cache -e .
RUN uv pip install --system --no-cache 'psycopg[binary]'
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
RUN python -c "from pathlib import Path; from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel; RapidOcrModel.download_models('torch', local_dir=Path('/opt/docling-artifacts/RapidOcr'), lang='english')"


COPY app/ ./app/
COPY scripts/ ./scripts/
COPY seed/ ./seed/

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DOCLING_RAPIDOCR_PATH=/opt/docling-artifacts/RapidOcr

EXPOSE 8000

CMD ["python", "scripts/serve.py"]
