FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EQUITIES_HOME=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 bolsa \
    && mkdir /data && chown bolsa:bolsa /data
COPY equities/ ./equities/
USER bolsa
CMD ["python", "-m", "equities"]
