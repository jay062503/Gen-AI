# Demo-mode / API image. For real inference use a CUDA host (build with --build-arg INSTALL_ML=1 on an
# NVIDIA runtime). Docker on macOS cannot use the Apple GPU - on a Mac mini run ./start.sh natively.
FROM python:3.12-slim
ARG INSTALL_ML=0
ENV PYTHONUNBUFFERED=1 MEDIA_HOST=0.0.0.0 MEDIA_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_ML" = "1" ]; then pip install --no-cache-dir -r requirements-ml.txt; fi
COPY studio ./studio
COPY run.py .env.example ./
VOLUME /data
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8090/health')" || exit 1
CMD ["python", "run.py"]
