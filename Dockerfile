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


COPY app/ ./app/
COPY scripts/ ./scripts/
COPY seed/ ./seed/

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["python", "scripts/serve.py"]