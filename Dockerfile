FROM python:3.12-slim

# Fixed environment so the same seed renders the same pixels on every run.
ENV PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    REQUIRE_OCR=1

# Tesseract is used by validate/ocr.py to read the labels back out of each image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# The API key is passed at run time only:  docker run -e OPENROUTER_API_KEY ...
ENTRYPOINT ["python", "-m", "factory"]
CMD ["--help"]
