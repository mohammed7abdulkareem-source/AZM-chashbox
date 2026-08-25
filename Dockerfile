FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8501
EXPOSE 8501
CMD ["sh","-c","gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4"]
