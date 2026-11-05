FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/checkrp-mpl
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py ./
COPY checkrp/ ./checkrp/
RUN mkdir -p /app/data && chown -R 10001:10001 /app/data
USER 10001
CMD ["python", "bot.py"]
